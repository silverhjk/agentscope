# -*- coding: utf-8 -*-
"""Markdown → PDF via reportlab (library + CLI).

Library::

    from agentscope.app.channel._dingtalk.md2pdf import convert_bytes, convert_file
    pdf = convert_bytes(b"# hi\\n", "doc.md")

CLI (after ``pip install 'agentscope[channel]'`` / editable install)::

    agentscope-md2pdf designs/蓝图.md
    agentscope-md2pdf designs/蓝图.md -o /tmp/蓝图.pdf
    python -m agentscope.app.channel._dingtalk.md2pdf designs/蓝图.md

Also usable with uv from the agentscope checkout::

    uv run agentscope-md2pdf path/to/file.md
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from io import BytesIO
from pathlib import Path


def convert_bytes(data: bytes, file_name: str = "document.md") -> bytes:
    """Render UTF-8 Markdown to a basic PDF (Chinese CID font)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "reportlab is required. Install with: "
            "pip install 'agentscope[channel]'  or  pip install reportlab",
        ) from exc

    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    registered = set(pdfmetrics.getRegisteredFontNames())
    if "STSong-Light" not in registered:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    font = "STSong-Light"
    title = Path(file_name or "document.md").stem or "文档"
    styles = {
        "title": ParagraphStyle(
            "MdTitle",
            fontName=font,
            fontSize=16,
            leading=22,
            spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "MdH1",
            fontName=font,
            fontSize=14,
            leading=20,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "MdH2",
            fontName=font,
            fontSize=12,
            leading=18,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "MdH3",
            fontName=font,
            fontSize=11,
            leading=16,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "MdBody",
            fontName=font,
            fontSize=10,
            leading=15,
            spaceAfter=4,
        ),
        "code": ParagraphStyle(
            "MdCode",
            fontName=font,
            fontSize=9,
            leading=12,
            leftIndent=8,
            spaceBefore=4,
            spaceAfter=6,
        ),
    }

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    story: list = [Paragraph(html.escape(title), styles["title"]), Spacer(1, 6)]

    lines = text.split("\n")
    i = 0
    in_code = False
    code_buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                block = "\n".join(code_buf) or " "
                story.append(Preformatted(block, styles["code"]))
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if (
            "|" in line
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1] or "")
        ):
            header = _split_table_row(line)
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                row = _split_table_row(lines[i])
                parts = []
                for idx, cell in enumerate(row):
                    key = header[idx] if idx < len(header) else f"列{idx + 1}"
                    parts.append(
                        f"<b>{html.escape(key)}</b>：{html.escape(cell)}",
                    )
                story.append(Paragraph("；".join(parts) or "—", styles["body"]))
                i += 1
            continue

        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 4))
            i += 1
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(_inline_md(stripped[4:]), styles["h3"]))
        elif stripped.startswith("## "):
            story.append(Paragraph(_inline_md(stripped[3:]), styles["h2"]))
        elif stripped.startswith("# "):
            story.append(Paragraph(_inline_md(stripped[2:]), styles["h1"]))
        elif re.match(r"^[-*+]\s+", stripped):
            story.append(
                Paragraph(
                    "• " + _inline_md(re.sub(r"^[-*+]\s+", "", stripped)),
                    styles["body"],
                ),
            )
        elif re.match(r"^\d+\.\s+", stripped):
            story.append(Paragraph(_inline_md(stripped), styles["body"]))
        else:
            story.append(Paragraph(_inline_md(stripped), styles["body"]))
        i += 1

    if in_code and code_buf:
        story.append(Preformatted("\n".join(code_buf), styles["code"]))

    doc.build(story)
    return buf.getvalue()


def convert_file(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Convert a Markdown file on disk; return the written PDF path."""
    src = Path(input_path)
    if not src.is_file():
        raise FileNotFoundError(f"Markdown file not found: {src}")
    out = Path(output_path) if output_path else src.with_suffix(".pdf")
    pdf = convert_bytes(src.read_bytes(), src.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)
    return out


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [c.strip() for c in text.split("|")]


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _CODE_RE.sub(r"<font face='Courier'>\1</font>", escaped)
    return escaped


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``agentscope-md2pdf INPUT [-o OUTPUT]``."""
    parser = argparse.ArgumentParser(
        prog="agentscope-md2pdf",
        description="Convert Markdown to PDF (reportlab, CJK via STSong-Light).",
    )
    parser.add_argument("input", help="Path to a .md / .markdown file")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output PDF path (default: same stem as input)",
    )
    args = parser.parse_args(argv)
    try:
        out = convert_file(args.input, args.output)
    except Exception as exc:  # noqa: BLE001
        print(f"agentscope-md2pdf: {exc}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
