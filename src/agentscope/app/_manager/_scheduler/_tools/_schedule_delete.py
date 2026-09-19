# -*- coding: utf-8 -*-
"""Schedule delete tool – removes a job from the scheduler and storage."""
from typing import Any

from pydantic import BaseModel, Field
from apscheduler.jobstores.base import JobLookupError

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....state import AgentState
from .....tool import ToolBase, ToolChunk
from ....message_bus import MessageBus
from ....storage._base import StorageBase
from .._creator import resolve_creator_from_session, same_creator


class _ScheduleDeleteParams(BaseModel):
    """The params for the schedule delete tool."""

    schedule_id: str = Field(
        description="The schedule ID to delete (permanently remove).",
    )


class ScheduleDelete(ToolBase):
    """The schedule delete tool.

    Permanently removes the given scheduled job from APScheduler,
    storage, and the message bus. Every execution session spawned by
    the schedule is cancelled (if running) and has its bus state
    purged. The job cannot be recovered after removal.

    Only the schedule's recorded creator may delete it.
    """

    name: str = "ScheduleDelete"

    description: str = (
        "Permanently delete a scheduled task by its schedule ID. "
        "Only the **creator** of that schedule may delete it; if someone "
        "else asks, refuse and tell them to ask the creator. "
        "After a successful call the task will no longer run and its "
        "record is removed from storage."
    )
    input_schema: dict = _ScheduleDeleteParams.model_json_schema()

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        user_id: str,
        agent_id: str,
        scheduler: Any,
        storage: StorageBase,
        message_bus: MessageBus,
    ) -> None:
        """Initialize the schedule delete tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage deletion.
            agent_id (`str`):
                Current agent id (needed to resolve the speaking peer).
            scheduler (`Any`):
                The ``AsyncIOScheduler`` instance whose job will be removed.
            storage (`StorageBase`):
                The storage backend used to delete the persisted record.
            message_bus (`MessageBus`):
                The message bus used to cancel in-flight chat runs for
                any execution session spawned by this schedule and to
                purge their per-session bus state.
        """
        self._user_id = user_id
        self._agent_id = agent_id
        self._scheduler = scheduler
        self._storage = storage
        self._message_bus = message_bus

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(
        self,
        schedule_id: str,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:  # type: ignore[override]
        """Permanently delete the scheduled task with the given ID.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to delete.
            _agent_state (`AgentState | None`, optional):
                Injected agent state; used to resolve the current speaker.

        Returns:
            `ToolChunk`:
                A chunk describing the result of the delete operation.
        """
        record = await self._storage.get_schedule(self._user_id, schedule_id)
        if record is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"ScheduleNotFoundError: Schedule with id "
                            f"{schedule_id!r} not found in storage."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        creator_id = (record.data.creator_external_id or "").strip()
        if creator_id:
            session_id = (
                _agent_state.session_id if _agent_state is not None else ""
            )
            (
                peer_id,
                peer_name,
                _,
                _,
            ) = await resolve_creator_from_session(
                self._storage,
                self._user_id,
                self._agent_id,
                session_id,
            )
            if not same_creator(creator_id, peer_id):
                creator_label = (
                    record.data.creator_display_name or creator_id
                )
                speaker = peer_name or peer_id or "(unknown)"
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"ScheduleDeleteDenied: only the creator "
                                f"({creator_label}) may delete or change "
                                f"this schedule. Current speaker={speaker}. "
                                f"Ask the creator to delete it, or create "
                                f"a new schedule under your own identity."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

        # Remove from the in-memory scheduler (best-effort; may already be
        # absent if the job finished naturally or the server restarted)
        try:
            self._scheduler.remove_job(schedule_id)
        except JobLookupError:
            pass

        # Local import to avoid a circular dependency between
        # ``_manager`` and ``_service`` at module load.
        from ...._service import SessionService  # noqa: PLC0415

        session_service = SessionService(
            storage=self._storage,
            message_bus=self._message_bus,
        )
        deleted = await session_service.delete_schedule(
            self._user_id,
            schedule_id,
        )

        if not deleted:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"ScheduleNotFoundError: Schedule with id "
                            f"{schedule_id!r} not found in storage."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Schedule {schedule_id!r} has been permanently "
                        f"deleted."
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
