# -*- coding: utf-8 -*-
"""Tests for agentscope-md2pdf (reportlab CLI) and Chrome-first orchestrator."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agentscope.app.channel._dingtalk.md2pdf import convert_bytes, convert_file, main
from agentscope.app.channel._dingtalk._markdown_pdf import (
    convert_markdown_to_pdf,
    is_markdown_filename,
    markdown_filename_to_pdf,
    maybe_convert_markdown_attachment,
)


class Md2PdfReportlabTest(unittest.TestCase):
    def test_convert_bytes(self) -> None:
        pdf = convert_bytes(
            "# 场景\n\n- 线索\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n".encode(),
            "蓝图.md",
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 200)

    def test_convert_file_and_cli(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "doc.md"
            md.write_text("# hi\n\nbody\n", encoding="utf-8")
            out = convert_file(md)
            self.assertTrue(out.is_file())
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))
            out2 = Path(tmp) / "custom.pdf"
            self.assertEqual(main([str(md), "-o", str(out2)]), 0)
            self.assertTrue(out2.is_file())


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
            out = Path(cmd[cmd.index("md-to-pdf") + 1]).with_suffix(".pdf")
            # npx --yes md-to-pdf <path>
            for i, part in enumerate(cmd):
                if part.endswith(".md"):
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
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf._try_reportlab_inprocess",
            return_value=None,
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

    def test_reportlab_last_resort_when_chrome_missing(self) -> None:
        with mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.shutil.which",
            return_value=None,
        ), mock.patch(
            "agentscope.app.channel._dingtalk._markdown_pdf.subprocess.run",
            side_effect=AssertionError("should not run CLI"),
        ):
            pdf = convert_markdown_to_pdf("# 场景\n\n正文\n".encode(), "蓝图.md")
            self.assertTrue(pdf.startswith(b"%PDF"))

    def test_prefer_builtin_uses_reportlab_first(self) -> None:
        # Without PREFER_BUILTIN, Chrome may win if npx exists; force builtin.
        with mock.patch.dict(
            "os.environ",
            {"MD_TO_PDF_PREFER_BUILTIN": "1"},
            clear=False,
        ):
            data, name, media = maybe_convert_markdown_attachment(b"# x\n", "a.md")
            self.assertEqual(name, "a.pdf")
            self.assertEqual(media, "application/pdf")
            self.assertTrue(data.startswith(b"%PDF"))
