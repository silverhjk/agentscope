# -*- coding: utf-8 -*-
"""Stateless tool-approval / ask_user resume.

The awaiting confirmation is read straight from the session state (the
single source of truth) and the run is resumed with the decision — no
server-side pending record. A decision that no longer matches an
ASKING tool call (stale click, double click) is ignored, giving
idempotency for free.
"""
from typing import Any

from ...event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from ...message import ToolCallBlock, ToolCallState
from .._ask_user import (
    ASK_USER_TOOL_NAME,
    option_at,
    option_by_id,
    parse_ask_user_input,
    result_block_for,
    spec_from_tool_call,
)
from .._bus_ops import enqueue_run_trigger
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase


async def _load_awaiting(
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> tuple[list[ToolCallBlock], str]:
    """Load a session's ASKING tool calls and its current reply id.

    Args:
        storage (`StorageBase`): Application storage.
        user_id (`str`): Owner of the session.
        agent_id (`str`): The routed agent.
        session_id (`str`): The derived session.

    Returns:
        `tuple[list[ToolCallBlock], str]`: The tool calls awaiting user
        confirmation, and ``state.reply_id`` (empty when absent).
    """
    session = await storage.get_session(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    agent = await storage.get_agent(user_id=user_id, agent_id=agent_id)
    if session is None or agent is None:
        return [], ""
    asking = [
        tc
        for tc in session.state.get_awaiting_tool_calls(agent.data.name)
        if tc.state == ToolCallState.ASKING
    ]
    return asking, session.state.reply_id


async def _load_submitted(
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> tuple[list[ToolCallBlock], str]:
    """Load SUBMITTED tool calls (external execution) and the reply id."""
    session = await storage.get_session(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    agent = await storage.get_agent(user_id=user_id, agent_id=agent_id)
    if session is None or agent is None:
        return [], ""
    submitted = [
        tc
        for tc in session.state.get_awaiting_tool_calls(agent.data.name)
        if tc.state == ToolCallState.SUBMITTED
    ]
    return submitted, session.state.reply_id


async def resume_after_decision(
    bus: MessageBus,
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    tool_call_id: str,
    approved: bool,
) -> bool:
    """Resume the run with a decision on ``tool_call_id``.

    Reads the authoritative tool call from session state (ignoring the
    caller's round-tripped copy); returns ``False`` — a no-op — when it
    is not currently ASKING (stale/already resolved).

    Args:
        bus (`MessageBus`): The application message bus.
        storage (`StorageBase`): Application storage.
        user_id (`str`): Owner of the session.
        agent_id (`str`): The routed agent.
        session_id (`str`): The derived session.
        tool_call_id (`str`): The awaiting tool call to answer.
        approved (`bool`): The user's decision.

    Returns:
        `bool`: Whether a resume was enqueued.
    """
    asking, reply_id = await _load_awaiting(
        storage,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    tool_call = next((t for t in asking if t.id == tool_call_id), None)
    if tool_call is None:
        return False
    await enqueue_run_trigger(
        bus,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        kind=MessageBusKeys.WAKEUP_KIND_RESUME,
        inputs=UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(confirmed=approved, tool_call=tool_call),
            ],
        ),
    )
    return True


def _result_from_payload(
    tool_call: ToolCallBlock,
    payload: dict[str, Any] | None,
) -> Any:
    """Build a ``ToolResultBlock`` from an ask_user card payload."""
    spec = spec_from_tool_call(tool_call) or parse_ask_user_input(
        tool_call.input,
    )
    if spec is None:
        return None
    data = payload or {}
    if bool(data.get("cancelled")):
        return result_block_for(tool_call, spec, cancelled=True)
    if spec.kind == "choice":
        option = None
        if data.get("option_id"):
            option = option_by_id(spec, str(data.get("option_id")))
        if option is None and data.get("option_index") is not None:
            try:
                option = option_at(spec, int(data.get("option_index")))
            except (TypeError, ValueError):
                option = None
        if option is None:
            return None
        return result_block_for(tool_call, spec, option=option)
    if spec.kind == "form":
        values = data.get("values")
        if not isinstance(values, dict):
            values = {}
        raw = str(data.get("raw") or "").strip() or None
        return result_block_for(
            tool_call,
            spec,
            values=values,
            raw_text=raw,
        )
    if spec.kind == "file":
        files = data.get("files")
        if not isinstance(files, list):
            files = []
        return result_block_for(tool_call, spec, files=files)
    return None


async def resume_after_external(
    bus: MessageBus,
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    tool_call_id: str,
    payload: dict[str, Any] | None,
) -> bool:
    """Resume a parked external tool (``ask_user``) with the user's answer.

    Args:
        bus (`MessageBus`): The application message bus.
        storage (`StorageBase`): Application storage.
        user_id (`str`): Owner of the session.
        agent_id (`str`): The routed agent.
        session_id (`str`): The derived session.
        tool_call_id (`str`): The SUBMITTED tool call to answer.
        payload (`dict | None`): Card / Web answer (choice / form / cancel).

    Returns:
        `bool`: Whether a resume was enqueued.
    """
    submitted, reply_id = await _load_submitted(
        storage,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    tool_call = next((t for t in submitted if t.id == tool_call_id), None)
    if tool_call is None:
        return False
    if tool_call.name != ASK_USER_TOOL_NAME:
        return False
    result = _result_from_payload(tool_call, payload)
    if result is None:
        return False
    await enqueue_run_trigger(
        bus,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        kind=MessageBusKeys.WAKEUP_KIND_RESUME,
        inputs=ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=[result],
        ),
    )
    return True
