# -*- coding: utf-8 -*-
"""Tests for Chrome/pandoc Markdown→PDF orchestrator."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agentscope.app.channel._dingtalk._markdown_pdf import (
    convert_markdown_to_pdf,
    is_markdown_filename,
    markdown_filename_to_pdf,
    maybe_convert_markdown_attachment,
)


class MarkdownPdfOrchestratorTest(unittest.TestCase):
    def test_filename_helpers(self) -> None:
        self.assertTrue(is_markdown_filename("蓝图.md"))
        self.assertEqual(
            markdown_filename_to_pdf("营销助手-场景蓝图.pdf".replace(".pdf", ".md")),
            "营销助手-场景蓝图.pdf",
        )

    def test_prefers_chrome_md_to_pdf(self) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            seen.append(list(cmd))
            out = Path("document.pdf")
            for part in cmd:
                if str(part).endswith(".md"):
                    out = Path(part).with_suffix(".pdf")
                    break
            out.write_bytes(b"%PDF-1.4 chrome")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.dict("os.environ", {}, clear=False), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.shutil.which",
            side_effect=lambda name: "/usr/local/bin/npx" if name == "npx" else None,
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf._find_chrome_executable",
            return_value="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.subprocess.run",
            side_effect=fake_run,
        ):
            pdf = convert_markdown_to_pdf(b"# hi\n\n**bold**\n", "doc.md")
            self.assertEqual(pdf, b"%PDF-1.4 chrome")
            self.assertTrue(seen)
            self.assertEqual(seen[0][0], "npx")
            self.assertIn("md-to-pdf", seen[0])
            self.assertIn("--stylesheet", seen[0])
            self.assertIn("--launch-options", seen[0])
            self.assertIn("--document-title", seen[0])
            self.assertIn("--cache", seen[0])

    def test_falls_back_to_pandoc(self) -> None:
        seen: list[list[str]] = []

        def fake_which(name: str) -> str | None:
            if name == "pandoc":
                return "/usr/bin/pandoc"
            if name == "weasyprint":
                return "/usr/bin/weasyprint"
            return None

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            seen.append(list(cmd))
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"%PDF-1.4 pandoc")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.shutil.which",
            side_effect=fake_which,
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf._find_chrome_executable",
            return_value=None,
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.subprocess.run",
            side_effect=fake_run,
        ):
            pdf = convert_markdown_to_pdf(b"# hi\n", "doc.md")
            self.assertEqual(pdf, b"%PDF-1.4 pandoc")
            self.assertEqual(seen[0][0], "pandoc")
            self.assertIn("--pdf-engine", seen[0])
            self.assertIn("weasyprint", seen[0])

    def test_raises_when_no_converter(self) -> None:
        with mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.shutil.which",
            return_value=None,
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf._find_chrome_executable",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                convert_markdown_to_pdf(b"# hi\n", "doc.md")
            self.assertIn("no converter", str(ctx.exception).lower())

    def test_maybe_convert_passthrough_non_markdown(self) -> None:
        data, name, media = maybe_convert_markdown_attachment(
            b"hello",
            "note.txt",
            "text/plain",
        )
        self.assertEqual(data, b"hello")
        self.assertEqual(name, "note.txt")
        self.assertEqual(media, "text/plain")
