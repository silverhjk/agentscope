# -*- coding: utf-8 -*-
"""Resolve the human creator of a schedule from the creating session."""
from __future__ import annotations

import json
from typing import Any

_MSG_KEY = "agentscope:user:{user_id}:session:{session_id}:messages"


async def resolve_creator_from_session(
    storage: Any,
    owner_user_id: str,
    agent_id: str,
    session_id: str,
) -> tuple[str, str, str, str]:
    """Return ``(external_id, display_name, channel_id, chat_id)``.

    Used when creating a schedule so later cron runs can impersonate the
    same real user for DingTalk / Feishu / business-data tools.

    Empty strings mean the peer could not be resolved.
    """
    if not session_id:
        return "", "", "", ""
    try:
        session = await storage.get_session(
            owner_user_id,
            agent_id,
            session_id,
        )
    except Exception:  # noqa: BLE001
        return "", "", "", ""
    if session is None:
        return "", "", "", ""

    channel_id = (session.source_channel_id or "").strip()
    chat_id = (session.source_chat_id or "").strip()
    display = (session.source_chat_name or "").strip()
    peer_id = ""

    if chat_id.startswith("user:"):
        peer_id = chat_id[5:].strip()
    elif chat_id.startswith("group:"):
        peer_id, msg_display = await _latest_user_peer(
            storage,
            owner_user_id,
            session_id,
        )
        if msg_display and not display:
            display = msg_display
        # For tool peer resolution on fire, use 1:1 style chat id.
        if peer_id:
            chat_id = f"user:{peer_id}"
    elif chat_id and not chat_id.startswith("chat:"):
        peer_id = chat_id
        chat_id = f"user:{peer_id}"
    else:
        peer_id, msg_display = await _latest_user_peer(
            storage,
            owner_user_id,
            session_id,
        )
        if msg_display and not display:
            display = msg_display
        if peer_id:
            chat_id = f"user:{peer_id}"

    if not display:
        name = getattr(getattr(session, "config", None), "name", None) or ""
        if isinstance(name, str) and "/" in name:
            display = name.rsplit("/", 1)[-1].strip()

    return peer_id or "", display or "", channel_id, chat_id


async def _latest_user_peer(
    storage: Any,
    owner_user_id: str,
    session_id: str,
) -> tuple[str, str]:
    redis = getattr(storage, "_client", None)
    if redis is None or not session_id:
        return "", ""
    key = _MSG_KEY.format(user_id=owner_user_id, session_id=session_id)
    try:
        raw_list = await redis.lrange(key, -20, -1)
    except Exception:  # noqa: BLE001
        return "", ""
    for raw in reversed(raw_list or []):
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("role") != "user":
            continue
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        user_id = str(meta.get("channel_user_id") or "").strip()
        user_name = str(meta.get("channel_user_name") or "").strip()
        fallback = str(data.get("name") or "").strip()
        if not user_id:
            user_id = fallback
        if not user_name:
            user_name = fallback if fallback != user_id else ""
        if user_id or user_name:
            return user_id, user_name
    return "", ""


def same_creator(stored_external_id: str, current_external_id: str) -> bool:
    """Case-sensitive equality for IM / admin actor ids."""
    a = (stored_external_id or "").strip()
    b = (current_external_id or "").strip()
    if not a or not b:
        return False
    return a == b
