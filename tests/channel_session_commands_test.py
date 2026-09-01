# -*- coding: utf-8 -*-
"""Tests for IM session slash commands (``/new``, ``/help``)."""
from unittest import TestCase

from agentscope.app.channel._session_commands import (
    extract_command_text,
    parse_session_command,
)
from agentscope.message import TextBlock


class ChannelSessionCommandsTest(TestCase):
    """Unit tests for command parsing helpers."""

    def test_parse_new_variants(self) -> None:
        for text in ("/new", "/NEW", "/clear", "新开会话", "/清除会话"):
            self.assertEqual(parse_session_command(text), "new", text)

    def test_parse_help_variants(self) -> None:
        for text in ("/help", "/HELP", "/帮助", "帮助"):
            self.assertEqual(parse_session_command(text), "help", text)

    def test_parse_rejects_prose(self) -> None:
        self.assertIsNone(parse_session_command("请帮我 /new 一下"))
        self.assertIsNone(parse_session_command("/new\n还有一句"))
        self.assertIsNone(parse_session_command(""))

    def test_extract_strips_at_mentions(self) -> None:
        content = [TextBlock(type="text", text="@小招 /new")]
        self.assertEqual(extract_command_text(content), "/new")
        self.assertEqual(
            parse_session_command(extract_command_text(content)),
            "new",
        )
