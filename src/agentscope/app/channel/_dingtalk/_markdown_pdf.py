# -*- coding: utf-8 -*-
"""Convert Markdown to PDF for DingTalk + admin downloads.

Only two engines, in this order:

1. ``md-to-pdf`` / ``npx --yes md-to-pdf`` (headless Chrome / Puppeteer)
   — skipped when no Chrome unless ``MD_TO_PDF_ALLOW_PUPPETEER_DOWNLOAD=1``
2. ``pandoc`` (uses ``--pdf-engine`` when weasyprint / wkhtmltopdf / xelatex
   is on PATH)

Production: install Chromium + CJK fonts, then set::

    MD_TO_PDF_CHROME=/usr/bin/chromium
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from glob import glob as _glob_paths
from pathlib import Path

logger = logging.getLogger(__name__)

_MD_SUFFIXES = frozenset({".md", ".markdown"})
_DEFAULT_TIMEOUT_SEC = 180

# CJK-first stack so Chrome prints Chinese without tofu boxes.
_CJK_STYLESHEET = """
@page { size: A4; margin: 16mm 14mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    "Segoe UI", sans-serif;
  font-size: 11pt;
  line-height: 1.65;
  color: #1a1a1a;
  word-wrap: break-word;
  overflow-wrap: anywhere;
}
h1, h2, h3, h4 {
  font-weight: 600;
  line-height: 1.35;
  margin: 1.1em 0 0.45em;
}
h1 { font-size: 1.55em; border-bottom: 1px solid #ddd; padding-bottom: 0.25em; }
h2 { font-size: 1.3em; }
h3 { font-size: 1.12em; }
p, ul, ol, table, blockquote, pre { margin: 0.55em 0; }
ul, ol { padding-left: 1.4em; }
li { margin: 0.2em 0; }
code {
  font-family: "SF Mono", Menlo, Consolas, "Courier New", monospace;
  font-size: 0.9em;
  background: #f4f4f5;
  padding: 0.1em 0.35em;
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-all;
}
pre {
  background: #f6f8fa;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
pre code { background: transparent; padding: 0; }
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.95em;
}
th, td {
  border: 1px solid #d1d5db;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
th { background: #f3f4f6; font-weight: 600; }
blockquote {
  border-left: 3px solid #d1d5db;
  margin-left: 0;
  padding: 0.2em 0 0.2em 0.9em;
  color: #4b5563;
}
hr { border: none; border-top: 1px solid #d1d5db; margin: 1.2em 0; }
strong { font-weight: 600; }
a { color: #1d4ed8; text-decoration: none; }
"""

_PDF_OPTIONS = {
    "format": "A4",
    "printBackground": True,
    "margin": {
        "top": "14mm",
        "bottom": "14mm",
        "left": "12mm",
        "right": "12mm",
    },
}

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/local/bin/chromium",
    "/usr/lib/chromium/chromium",
    "/opt/google/chrome/chrome",
    "/headless-shell/headless_shell",
)


def _find_chrome_executable() -> str | None:
    """Locate a system Chrome/Chromium for Puppeteer (avoid missing npx cache)."""
    for key in ("MD_TO_PDF_CHROME", "PUPPETEER_EXECUTABLE_PATH", "CHROME_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).is_file():
            return raw
    for path in _CHROME_CANDIDATES:
        if Path(path).is_file():
            return path
    # Playwright / Puppeteer cache layouts (versioned dirs).
    for pattern in (
        "/ms-playwright/chromium-*/chrome-linux/chrome",
        "/root/.cache/puppeteer/chrome/*/chrome-linux64/chrome",
        "/home/*/.cache/puppeteer/chrome/*/chrome-linux64/chrome",
    ):
        try:
            for match in sorted(_glob_paths(pattern)):
                if Path(match).is_file():
                    return match
        except OSError:
            continue
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        found = shutil.which(name)
        if found and Path(found).is_file():
            return found
    return None


def _allow_puppeteer_download() -> bool:
    """Whether to run npx md-to-pdf when no system Chrome is present (slow / often hangs)."""
    return _env_flag("MD_TO_PDF_ALLOW_PUPPETEER_DOWNLOAD")


def is_markdown_filename(file_name: str) -> bool:
    """Return whether ``file_name`` looks like a Markdown document."""
    return Path(file_name or "").suffix.lower() in _MD_SUFFIXES


def markdown_filename_to_pdf(file_name: str) -> str:
    """Replace a Markdown suffix with ``.pdf``."""
    path = Path(file_name or "document.md")
    stem = path.stem or "document"
    return f"{stem}.pdf"


def _safe_basename(file_name: str) -> str:
    name = Path(file_name or "document.md").name
    name = re.sub(r"[^\w.\u4e00-\u9fff\-]+", "_", name, flags=re.UNICODE)
    return name or "document.md"


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _chrome_md_to_pdf_cmd(
    md_path: Path,
    *,
    stylesheet: Path | None,
    document_title: str | None = None,
) -> list[str] | None:
    """Build ``md-to-pdf`` / ``npx md-to-pdf`` with CJK stylesheet + local Chrome.

    Without a Chrome binary, skip unless ``MD_TO_PDF_ALLOW_PUPPETEER_DOWNLOAD=1`` —
    otherwise ``npx`` often hangs ~minutes downloading Chromium and then fails.
    """
    chrome_bin = _find_chrome_executable()
    if not chrome_bin and not _allow_puppeteer_download():
        logger.warning(
            "md-to-pdf: no system Chrome found; skipping (set MD_TO_PDF_CHROME "
            "or install chromium). To allow Puppeteer auto-download set "
            "MD_TO_PDF_ALLOW_PUPPETEER_DOWNLOAD=1.",
        )
        return None

    pdf_opts = json.dumps(_PDF_OPTIONS, ensure_ascii=False)
    extras: list[str] = [
        "--pdf-options",
        pdf_opts,
    ]
    if stylesheet is not None:
        extras.extend(["--stylesheet", str(stylesheet)])
    if document_title:
        extras.extend(["--document-title", document_title])

    if chrome_bin:
        launch = {
            "executablePath": chrome_bin,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        extras.extend(
            [
                "--launch-options",
                json.dumps(launch, ensure_ascii=False),
            ],
        )
    else:
        logger.warning(
            "md-to-pdf: no system Chrome; relying on Puppeteer download "
            "(MD_TO_PDF_ALLOW_PUPPETEER_DOWNLOAD=1).",
        )

    if shutil.which("md-to-pdf"):
        return ["md-to-pdf", str(md_path), *extras]
    if shutil.which("npx"):
        # --cache: isolate from broken ~/.npm (root-owned cache → EACCES).
        return [
            "npx",
            "--yes",
            "--cache",
            str(md_path.parent / "npm-cache"),
            "md-to-pdf",
            str(md_path),
            *extras,
        ]
    return None


_PANDOC_ENGINES = (
    "weasyprint",
    "wkhtmltopdf",
    "xelatex",
    "lualatex",
    "pdflatex",
)


def _pandoc_cmd(md_path: Path, pdf_path: Path) -> list[str] | None:
    """Build ``pandoc`` argv; attach a PDF engine when one is on PATH."""
    if not shutil.which("pandoc"):
        return None
    cmd = ["pandoc", str(md_path), "-o", str(pdf_path)]
    for engine in _PANDOC_ENGINES:
        if shutil.which(engine):
            cmd.extend(["--pdf-engine", engine])
            break
    return cmd


def _candidate_commands(
    md_path: Path,
    pdf_path: Path,
    *,
    stylesheet: Path | None,
    document_title: str | None = None,
) -> list[list[str]]:
    """Chrome first, then pandoc."""
    cmds: list[list[str]] = []
    chrome = _chrome_md_to_pdf_cmd(
        md_path,
        stylesheet=stylesheet,
        document_title=document_title,
    )
    if chrome:
        cmds.append(chrome)
    pandoc = _pandoc_cmd(md_path, pdf_path)
    if pandoc:
        cmds.append(pandoc)
    return cmds


def convert_markdown_to_pdf(data: bytes, file_name: str = "document.md") -> bytes:
    """Convert UTF-8 Markdown bytes to PDF (Chrome, then pandoc).

    Raises:
        RuntimeError: When no converter is available or all attempts fail.
    """
    timeout = int(os.environ.get("MD_TO_PDF_TIMEOUT_SEC") or _DEFAULT_TIMEOUT_SEC)

    with tempfile.TemporaryDirectory(prefix="md2pdf-") as tmp:
        tmp_path = Path(tmp)
        safe = _safe_basename(file_name)
        if Path(safe).suffix.lower() not in _MD_SUFFIXES:
            safe = f"{Path(safe).stem or 'document'}.md"
        md_path = tmp_path / "document.md"
        pdf_path = md_path.with_suffix(".pdf")
        md_path.write_bytes(data)

        stylesheet = tmp_path / "md-to-pdf-cjk.css"
        stylesheet.write_text(_CJK_STYLESHEET, encoding="utf-8")

        doc_title = Path(safe).stem or "document"
        commands = _candidate_commands(
            md_path,
            pdf_path,
            stylesheet=stylesheet,
            document_title=doc_title,
        )
        errors: list[str] = []
        chrome_bin = _find_chrome_executable()
        run_env = dict(os.environ)
        if chrome_bin:
            run_env.setdefault("PUPPETEER_EXECUTABLE_PATH", chrome_bin)
        npm_cache = tmp_path / "npm-cache"
        npm_cache.mkdir(exist_ok=True)
        run_env["npm_config_cache"] = str(npm_cache)
        run_env["NPM_CONFIG_CACHE"] = str(npm_cache)
        if not commands:
            raise RuntimeError(
                "Markdown→PDF failed: no converter. Install Chromium and "
                "Node (`md-to-pdf` or npx), or install pandoc "
                "(plus weasyprint / wkhtmltopdf / xelatex).",
            )
        for cmd in commands:
            try:
                logger.info("Markdown→PDF via %s", " ".join(cmd[:6]))
                proc = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    timeout=timeout,
                    cwd=str(tmp_path),
                    env=run_env,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or b"").decode(
                        "utf-8",
                        errors="replace",
                    )
                    err_tail = err.strip()[-800:] if err.strip() else f"exit {proc.returncode}"
                    logger.warning(
                        "Markdown→PDF command failed (%s): %s",
                        cmd[0],
                        err_tail,
                    )
                    errors.append(f"{cmd[0]}: {err_tail}")
                    continue
                if pdf_path.is_file() and pdf_path.stat().st_size > 0:
                    return pdf_path.read_bytes()
                found = list(tmp_path.glob("*.pdf"))
                if found:
                    return found[0].read_bytes()
                errors.append(f"{cmd[0]}: completed but no PDF written")
            except subprocess.TimeoutExpired:
                errors.append(f"{cmd[0]}: timed out after {timeout}s")
            except OSError as exc:
                errors.append(f"{cmd[0]}: {exc}")

        raise RuntimeError(
            "Markdown→PDF failed. Need Chromium + `md-to-pdf`/`npx`, "
            "or pandoc. "
            + (" | ".join(errors) if errors else ""),
        )


def maybe_convert_markdown_attachment(
    data: bytes,
    file_name: str,
    media_type: str = "",
) -> tuple[bytes, str, str]:
    """If ``file_name`` / media type is Markdown, return PDF bytes and meta."""
    media = (media_type or "").lower()
    if not (
        is_markdown_filename(file_name)
        or media in {"text/markdown", "text/x-markdown"}
    ):
        return data, file_name, media_type or "application/octet-stream"
    pdf = convert_markdown_to_pdf(data, file_name)
    return pdf, markdown_filename_to_pdf(file_name), "application/pdf"
