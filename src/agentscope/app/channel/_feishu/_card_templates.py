# -*- coding: utf-8 -*-
"""Feishu interactive-card helpers for the tool-approval flow.

The card round-trips lookup keys (``tool_call_id``, ``chat_id`` and the
resolved ``agent_id`` / ``session_id``) plus the click's approve/deny —
the authoritative tool call is read from session state on resume, never
trusted from the card.
"""
import json
from typing import Any

_ACTION_TYPE = "tool_guard_approval"
_APPROVE = "approve"
_DENY = "deny"


def _build_approval_card(
    tool_call_id: str,
    chat_id: str,
    tool_name: str,
    summary: str,
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build the approval card (JSON string) for a pending tool call.

    Args:
        tool_call_id (`str`): The awaiting tool call the buttons answer.
        chat_id (`str`): Chat the card is sent to, echoed on click for
            session routing.
        tool_name (`str`): Name of the tool, shown in the card body.
        summary (`str`): A rendering of the tool arguments (truncated).
        agent_id (`str`): Target agent, echoed on click to resume the
            exact run without re-resolving routing.
        session_id (`str`): Target session, echoed on click alongside
            ``agent_id``.

    Returns:
        `str`: The card as a JSON string.
    """
    base = {
        "type": _ACTION_TYPE,
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "session_id": session_id,
    }
    body = f"**Tool:** `{tool_name}`"
    if summary:
        shown = summary if len(summary) <= 800 else summary[:799] + "…"
        body += f"\n**Arguments:** {shown}"
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "🛡️ Tool execution needs approval",
            },
        },
        "elements": [
            {"tag": "markdown", "content": body},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ Allow"},
                        "type": "primary",
                        "value": {**base, "action": _APPROVE},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "❌ Deny"},
                        "type": "danger",
                        "value": {**base, "action": _DENY},
                    },
                ],
            },
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def _resolved_card(approved: bool) -> dict:
    """Build the post-decision card object that replaces the approval card.

    Args:
        approved (`bool`): The decision, selecting the colour and text.

    Returns:
        `dict`: The card object (schema 1.0).
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if approved else "red",
            "title": {
                "tag": "plain_text",
                "content": "✅ Allowed" if approved else "🚫 Denied",
            },
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "The tool was allowed to run."
                    if approved
                    else "The tool was denied."
                ),
            },
        ],
    }


def _parse_action(
    value: Any,
) -> tuple[str, str, bool, str, str] | None:
    """Parse a card button's value into ``(tool_call_id, chat_id,
    approved, agent_id, session_id)``.

    Args:
        value (`Any`): The clicked button's ``value`` — a dict (or JSON
            string) carrying ``type`` / ``tool_call_id`` / ``chat_id`` /
            ``action`` / ``agent_id`` / ``session_id``.

    Returns:
        `tuple[str, str, bool, str, str] | None`: ``(tool_call_id,
        chat_id, approved, agent_id, session_id)`` for a valid button,
        or ``None`` if not one of ours.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or value.get("type") != _ACTION_TYPE:
        return None
    tool_call_id = str(value.get("tool_call_id") or "").strip()
    chat_id = str(value.get("chat_id") or "").strip()
    action = str(value.get("action") or "").strip().lower()
    if not tool_call_id or action not in (_APPROVE, _DENY):
        return None
    agent_id = str(value.get("agent_id") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    return tool_call_id, chat_id, action == _APPROVE, agent_id, session_id


def _build_toast(approved: bool) -> Any:
    """Build a toast-only card-callback response (no card update).

    Used for clicks we cannot act on (e.g. an unparseable button), where
    the card should stay as-is.

    Args:
        approved (`bool`):
            The decision, selecting the toast style and text.

    Returns:
        `Any`:
            A ``P2CardActionTriggerResponse`` when lark_oapi is
            importable, else a plain dict with the same shape.
    """
    return _wrap_response({"toast": _toast(approved)})


def _build_action_response(approved: bool) -> Any:
    """Build the card-callback response for a decision: a toast plus the
    resolved card, so Feishu updates the clicked card in place — no
    separate, rate-limit-racing PATCH.

    Args:
        approved (`bool`): The decision, selecting toast and card.

    Returns:
        `Any`: A ``P2CardActionTriggerResponse`` (or dict fallback).
    """
    return _wrap_response(
        {
            "toast": _toast(approved),
            "card": {"type": "raw", "data": _resolved_card(approved)},
        },
    )


def _toast(approved: bool) -> dict:
    """The toast payload for a decision.

    Args:
        approved (`bool`): The decision, selecting style and text.
    """
    return {
        "type": "success" if approved else "info",
        "content": "Allowed" if approved else "Denied",
    }


def _wrap_response(body: dict) -> Any:
    """Wrap a response body in ``P2CardActionTriggerResponse`` when the
    SDK is importable, else return the plain dict.

    Args:
        body (`dict`): The callback response body (``toast`` / ``card``).
    """
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        return P2CardActionTriggerResponse(body)
    except (ImportError, AttributeError):
        return body


# -- ask_user interactive cards ------------------------------------------

_ASK_USER_TYPE = "ask_user"
_ASK_CHOICE = "choice"
_ASK_FORM = "form"
_ASK_CANCEL = "cancel"


def _ask_user_base(
    tool_call_id: str,
    chat_id: str,
    agent_id: str,
    session_id: str,
) -> dict[str, str]:
    return {
        "type": _ASK_USER_TYPE,
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "session_id": session_id,
    }


def _build_ask_user_choice_card(
    tool_call_id: str,
    chat_id: str,
    prompt: str,
    options: list[dict[str, str]],
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build a Feishu card with one button per choice option."""
    base = _ask_user_base(tool_call_id, chat_id, agent_id, session_id)
    shown = prompt if len(prompt) <= 800 else prompt[:799] + "…"
    actions: list[dict[str, Any]] = []
    for i, opt in enumerate(options[:8]):
        label = str(opt.get("label") or opt.get("id") or f"选项{i + 1}")
        oid = str(opt.get("id") or "")
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label[:40]},
                "type": "primary" if i == 0 else "default",
                "value": {
                    **base,
                    "action": _ASK_CHOICE,
                    "option_id": oid,
                    "option_index": str(i),
                },
            },
        )
    actions.append(
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "取消"},
            "type": "danger",
            "value": {**base, "action": _ASK_CANCEL},
        },
    )
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "请选择"},
        },
        "elements": [
            {"tag": "markdown", "content": shown},
            {"tag": "hr"},
            {"tag": "action", "actions": actions},
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def _build_ask_user_form_card(
    tool_call_id: str,
    chat_id: str,
    prompt: str,
    fields: list[dict[str, Any]],
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build a Feishu card with short form inputs + submit."""
    base = _ask_user_base(tool_call_id, chat_id, agent_id, session_id)
    shown = prompt if len(prompt) <= 800 else prompt[:799] + "…"
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": shown},
        {"tag": "hr"},
    ]
    for field in fields[:6]:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        label = str(field.get("label") or name)
        ftype = str(field.get("type") or "text")
        options = field.get("options") if isinstance(field.get("options"), list) else []
        if ftype == "select" and options:
            elements.append(
                {
                    "tag": "select_static",
                    "name": name,
                    "title": {"tag": "plain_text", "content": label[:40]},
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "请选择",
                    },
                    "options": [
                        {
                            "text": {
                                "tag": "plain_text",
                                "content": str(
                                    o.get("label") or o.get("id") or "",
                                )[:40],
                            },
                            "value": str(o.get("id") or ""),
                        }
                        for o in options
                        if isinstance(o, dict) and o.get("id")
                    ],
                },
            )
        else:
            elements.append(
                {
                    "tag": "input",
                    "name": name,
                    "title": {"tag": "plain_text", "content": label[:40]},
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "请输入",
                    },
                },
            )
    elements.append(
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "提交"},
                    "type": "primary",
                    "value": {**base, "action": _ASK_FORM},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消"},
                    "type": "default",
                    "value": {**base, "action": _ASK_CANCEL},
                },
            ],
        },
    )
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"tag": "plain_text", "content": "请填写"},
        },
        "elements": elements,
    }
    return json.dumps(card, ensure_ascii=False)


def _build_ask_user_file_card(
    tool_call_id: str,
    chat_id: str,
    prompt: str,
    accept: str = "",
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build a Feishu card telling the user to send a file next."""
    base = _ask_user_base(tool_call_id, chat_id, agent_id, session_id)
    shown = prompt if len(prompt) <= 800 else prompt[:799] + "…"
    extra = f"\n类型：`{accept}`" if accept else ""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": "请发送文件"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": shown + extra + "\n\n请在本对话中发送文件或图片。",
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "type": "danger",
                        "value": {**base, "action": _ASK_CANCEL},
                    },
                ],
            },
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def _ask_user_resolved_card(cancelled: bool) -> dict:
    """In-place replacement after the user answers or cancels."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey" if cancelled else "green",
            "title": {
                "tag": "plain_text",
                "content": "已取消" if cancelled else "已提交",
            },
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "已取消。" if cancelled else "已收到你的回复。",
            },
        ],
    }


def _parse_ask_user_action(
    value: Any,
    form_value: Any = None,
) -> tuple[str, str, str, str, dict[str, Any]] | None:
    """Parse an ask_user card click.

    Returns:
        ``(tool_call_id, chat_id, agent_id, session_id, payload)`` or
        ``None`` if this is not an ask_user button.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or value.get("type") != _ASK_USER_TYPE:
        return None
    tool_call_id = str(value.get("tool_call_id") or "").strip()
    chat_id = str(value.get("chat_id") or "").strip()
    if not tool_call_id:
        return None
    action = str(value.get("action") or "").strip().lower()
    agent_id = str(value.get("agent_id") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    payload: dict[str, Any]
    if action == _ASK_CANCEL:
        payload = {"cancelled": True}
    elif action == _ASK_CHOICE:
        idx_raw = value.get("option_index")
        try:
            option_index = int(idx_raw) if idx_raw is not None else None
        except (TypeError, ValueError):
            option_index = None
        payload = {
            "kind": "choice",
            "option_id": str(value.get("option_id") or ""),
            "option_index": option_index,
        }
    elif action == _ASK_FORM:
        payload = {
            "kind": "form",
            "values": _flatten_form_value(form_value),
        }
    else:
        return None
    return tool_call_id, chat_id, agent_id, session_id, payload


def _flatten_form_value(form_value: Any) -> dict[str, Any]:
    """Normalise Feishu ``form_value`` into a plain string map."""
    data = _as_mapping(form_value)
    out: dict[str, Any] = {}
    for key, raw in data.items():
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(raw, dict):
            if "value" in raw:
                out[name] = raw.get("value")
            elif "option" in raw:
                out[name] = raw.get("option")
            else:
                out[name] = raw
        else:
            out[name] = raw
    return out


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    dump = getattr(value, "model_dump", None) or getattr(value, "dict", None)
    if callable(dump):
        try:
            data = dump()
        except TypeError:
            data = None
        if isinstance(data, dict):
            return data
    return {}


def _build_ask_user_action_response(cancelled: bool) -> Any:
    """Toast + in-place card update for an ask_user click."""
    return _wrap_response(
        {
            "toast": {
                "type": "info" if cancelled else "success",
                "content": "已取消" if cancelled else "已提交",
            },
            "card": {
                "type": "raw",
                "data": _ask_user_resolved_card(cancelled),
            },
        },
    )
