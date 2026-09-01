# -*- coding: utf-8 -*-
"""Pure routing: resolve an inbound event to ``(agent_id, session_id)``.

There is no persisted channel→session mapping table. Given the routing
rules on the channel record, both the target agent and the session id
are computed deterministically from the event — so every node derives
the same result with zero coordination, and session creation is
idempotent (``get_or_create`` on a derived id).

An optional **epoch** (from ``/new``) is folded into the hash so the same
chat can rotate onto a fresh session without losing older history.
Epoch ``0`` preserves the original id formula for existing chats.
"""
from uuid import NAMESPACE_URL, uuid5

from ..storage import ChannelRecord, ChannelBinding, SessionScope
from ._base import ChannelEvent


# Fixed namespace so derived session ids are stable across processes and
# restarts. Do not change — it would orphan every existing session.
_SESSION_NAMESPACE = uuid5(NAMESPACE_URL, "agentscope.channel.session")


def _binding_matches(event: ChannelEvent, binding: ChannelBinding) -> bool:
    """Whether ``binding`` matches ``event`` (``"*"`` matches anything).

    Args:
        event (`ChannelEvent`): The inbound event.
        binding (`ChannelBinding`): The rule; ``chat_id``/``user_id`` map
            to event fields, other keys to ``event.metadata``.
    """
    if binding.match_value == "*":
        return True
    if binding.match_key == "chat_id":
        value: str | None = event.chat_id
    elif binding.match_key == "user_id":
        value = event.channel_user_id
    else:
        raw = event.metadata.get(binding.match_key)
        value = str(raw) if raw is not None else None
    return value == binding.match_value


def resolve_binding(
    event: ChannelEvent,
    record: ChannelRecord,
) -> tuple[str, str, SessionScope]:
    """Resolve ``(agent_id, scope_key, scope)`` without hashing a session.

    Args:
        event (`ChannelEvent`): The inbound event.
        record (`ChannelRecord`): The channel's configuration.

    Returns:
        `tuple[str, str, SessionScope]`: Agent id, scope key, and scope.
    """
    binding = record.routing.bindings[-1]
    for candidate in record.routing.bindings:
        if _binding_matches(event, candidate):
            binding = candidate
            break

    # Session scope projects the (chat_id, user_id) pair; PER_CHAT is
    # naturally per-user for a DM (its chat has only that user).
    if binding.session_scope is SessionScope.PER_CHAT_USER:
        scope_key = f"{event.chat_id}:{event.channel_user_id}"
    else:
        scope_key = event.chat_id

    return binding.agent_id, scope_key, binding.session_scope


def make_session_id(
    channel_id: str,
    agent_id: str,
    scope_key: str,
    *,
    epoch: int = 0,
) -> str:
    """Stable session id from channel / agent / scope (+ optional epoch).

    Args:
        channel_id (`str`): Channel record id.
        agent_id (`str`): Bound agent id.
        scope_key (`str`): Chat (or chat:user) key.
        epoch (`int`): Session generation; ``0`` uses the legacy formula.

    Returns:
        `str`: UUID5 session id.
    """
    if epoch and epoch > 0:
        material = f"{channel_id}:{agent_id}:{scope_key}:v{epoch}"
    else:
        material = f"{channel_id}:{agent_id}:{scope_key}"
    return str(uuid5(_SESSION_NAMESPACE, material))


def resolve(
    event: ChannelEvent,
    record: ChannelRecord,
    *,
    epoch: int = 0,
) -> tuple[str, str, SessionScope]:
    """Resolve an event to ``(agent_id, session_id, scope)``.

    Args:
        event (`ChannelEvent`): The inbound event.
        record (`ChannelRecord`): The channel's configuration.
        epoch (`int`): Optional session generation from ``/new``.

    Returns:
        `tuple[str, str, SessionScope]`: ``(agent_id, session_id, scope)``.
    """
    agent_id, scope_key, scope = resolve_binding(event, record)
    session_id = make_session_id(
        record.id,
        agent_id,
        scope_key,
        epoch=epoch,
    )
    return agent_id, session_id, scope
