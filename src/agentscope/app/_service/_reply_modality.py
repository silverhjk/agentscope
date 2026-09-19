# -*- coding: utf-8 -*-
"""Decide when a digital-employee turn should synthesize speech."""
from __future__ import annotations

from enum import Enum
from typing import Any

from ...message import DataBlock, Msg


class ReplyTtsMode(str, Enum):
    """Which TTS config to attach for this turn."""

    NONE = "none"
    NON_REALTIME = "non_realtime"
    REALTIME = "realtime"


def inbound_has_audio(input_msg: Any) -> bool:
    """True when the user turn includes an audio ``DataBlock``."""
    if input_msg is None:
        return False
    msgs: list[Any]
    if isinstance(input_msg, list):
        msgs = list(input_msg)
    else:
        msgs = [input_msg]
    for msg in msgs:
        if isinstance(msg, Msg):
            meta = msg.metadata if isinstance(msg.metadata, dict) else {}
            if meta.get("inbound_has_audio") is True:
                return True
            content = msg.content
        elif isinstance(msg, dict):
            meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
            if meta.get("inbound_has_audio") is True:
                return True
            content = msg.get("content")
        else:
            continue
        if _content_has_audio(content):
            return True
    return False


def _content_has_audio(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        media = ""
        if isinstance(block, DataBlock):
            src = getattr(block, "source", None)
            media = str(getattr(src, "media_type", "") or "")
        elif isinstance(block, dict) and block.get("type") == "data":
            src = block.get("source") if isinstance(block.get("source"), dict) else {}
            media = str(src.get("media_type") or "")
        if media.lower().startswith("audio/"):
            return True
    return False


def resolve_reply_tts_mode(
    *,
    source_channel_id: str | None,
    input_msg: Any,
) -> ReplyTtsMode:
    """Channel + inbound modality → TTS mode (never LLM-chosen).

    - Xiaozhi device → always realtime TTS
    - IM (``im-*`` / channel sessions): audio inbound → non-realtime; text → none
    - Admin / console / other → none
    """
    channel = (source_channel_id or "").strip().lower()
    if channel == "xiaozhi" or channel.startswith("xiaozhi"):
        return ReplyTtsMode.REALTIME
    if channel.startswith("im-") or channel in {"dingtalk", "feishu"}:
        if inbound_has_audio(input_msg):
            return ReplyTtsMode.NON_REALTIME
        return ReplyTtsMode.NONE
    return ReplyTtsMode.NONE
