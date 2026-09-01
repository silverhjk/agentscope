# -*- coding: utf-8 -*-
"""IM slash commands for rotating channel-bound sessions (``/new``).

Session ids for Feishu/DingTalk are normally a stable hash of
``(channel, agent, chat)``. That makes history accumulate forever in one
DM. Commands bump a per-chat **epoch** stored on the message bus; the
hash includes the epoch so a fresh empty session is used afterwards.
Epoch ``0`` (missing) keeps the original id for backward compatibility.
"""
from __future__ import annotations

import re
from typing import Literal

from ..message_bus import MessageBus, MessageBusKeys

SessionCommand = Literal["new", "help"]

# Exact full-message matches (after strip); slash forms are case-insensitive.
_NEW_COMMANDS = frozenset(
    {
        "/new",
        "/clear",
        "/reset",
        "新开会话",
        "清除会话",
        "清除上下文",
        "/新开会话",
        "/清除会话",
    },
)
_HELP_COMMANDS = frozenset(
    {
        "/help",
        "/帮助",
        "帮助",
    },
)

_AT_PREFIX = re.compile(
    r"^(?:@\S+\s+)+",
)

NEW_SESSION_ACK = (
    "已新开会话：之前的对话上下文不会再带入本轮。"
    "直接发下一条消息即可继续。发送 /help 查看可用指令。"
)

HELP_TEXT = (
    "可用指令：\n"
    "· /new（或 /clear、新开会话）— 新开会话，清空本聊天的上下文\n"
    "· /help — 查看本说明"
)


def extract_command_text(content: list) -> str:
    """Join text blocks from channel content into one stripped string.

    Args:
        content: Channel event content blocks (``TextBlock`` etc.).

    Returns:
        `str`: Combined text with leading @mentions stripped.
    """
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    raw = "\n".join(parts).strip()
    if not raw:
        return ""
    # Group chats often prefix "@机器人 " — strip for command matching.
    return _AT_PREFIX.sub("", raw).strip()


def parse_session_command(text: str) -> SessionCommand | None:
    """Return the command name when ``text`` is exactly a known command.

    Args:
        text (`str`): User message text (already stripped of @mentions).

    Returns:
        `SessionCommand | None`: ``\"new\"`` / ``\"help\"``, or ``None``.
    """
    if not text:
        return None
    # Single-line commands only — avoid matching long prose.
    if "\n" in text:
        return None
    key = text.strip()
    lowered = key.lower()
    if lowered in {c.lower() for c in _NEW_COMMANDS} or key in _NEW_COMMANDS:
        return "new"
    if lowered in {c.lower() for c in _HELP_COMMANDS} or key in _HELP_COMMANDS:
        return "help"
    return None


def epoch_field(agent_id: str, scope_key: str) -> str:
    """Registry field for one chat's session epoch."""
    return f"{agent_id}:{scope_key}"


async def get_session_epoch(
    bus: MessageBus,
    *,
    channel_id: str,
    agent_id: str,
    scope_key: str,
) -> int:
    """Read the current epoch (``0`` when unset)."""
    raw = await bus.registry_get(
        MessageBusKeys.channel_session_epoch(channel_id),
        epoch_field(agent_id, scope_key),
    )
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def bump_session_epoch(
    bus: MessageBus,
    *,
    channel_id: str,
    agent_id: str,
    scope_key: str,
) -> int:
    """Increment and persist the epoch; return the new value (≥ 1)."""
    current = await get_session_epoch(
        bus,
        channel_id=channel_id,
        agent_id=agent_id,
        scope_key=scope_key,
    )
    nxt = current + 1
    await bus.registry_set(
        MessageBusKeys.channel_session_epoch(channel_id),
        epoch_field(agent_id, scope_key),
        str(nxt),
    )
    return nxt
