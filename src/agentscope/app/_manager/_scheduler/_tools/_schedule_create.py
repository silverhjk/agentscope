# -*- coding: utf-8 -*-
"""The schedule create tool."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
)
from .....state import AgentState
from .....tool import ToolBase, ToolChunk
from ....storage import (
    ScheduleData,
    ScheduleRecord,
    ScheduleSource,
    ChatModelConfig,
)
from .._creator import resolve_creator_from_session


class _ScheduleCreateParams(BaseModel):
    """The params for the schedule create tool."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(
        default="",
        description="Description of the schedule, including its purpose.",
    )

    cron_expression: str = Field(
        description=(
            "5-field cron (minute hour day month day_of_week), evaluated by "
            "APScheduler. day_of_week: 0=Mon … 4=Fri … 6=Sun (NOT Unix crontab "
            "where 0=Sun). Prefer names: mon,tue,wed,thu,fri,sat,sun. "
            "Examples: '0 9 * * fri' (every Friday 09:00), "
            "'50 17 * * mon-fri' (weekdays 17:50). Do NOT use 5 for Friday "
            "(that is Saturday)."
        ),
    )

    timezone: str = Field(
        default="Asia/Shanghai",
        description="IANA timezone name used to evaluate the cron expression, "
        "e.g. 'Asia/Shanghai' or 'UTC'. Defaults to Asia/Shanghai.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the schedule is active immediately after "
        "creation. Set to False to create a disabled schedule.",
    )

    started_at: datetime | None = Field(
        default=None,
        description="ISO-8601 datetime at which the schedule becomes active. "
        "Defaults to the current time when not specified.",
    )

    ended_at: datetime | None = Field(
        default=None,
        description="ISO-8601 datetime at which the schedule stops firing. "
        "If not set the schedule runs indefinitely.",
    )

    stateful: bool = Field(
        default=False,
        description="If True, consecutive executions share the same session "
        "context. If False, each execution gets a fresh session.",
    )

    permission_mode: str = Field(
        default=PermissionMode.DONT_ASK.value,
        description=(
            "Permission mode for the agent during scheduled execution. "
            f"Allowed values: {[m.value for m in PermissionMode]}. "
            "Defaults to 'dont_ask' since no user is present."
        ),
    )


class ScheduleCreate(ToolBase):
    """The schedule create tool.

    Creates a new scheduled task that will execute the current agent at a
    given cron interval.  The record is persisted to storage and immediately
    registered with the in-memory APScheduler.

    The schedule inherits the model configuration of the current session.
    The agent that creates the schedule is also the agent that will be run
    on each trigger.
    """

    name: str = "ScheduleCreate"

    description: str = """Create a new recurring scheduled task for yourself. \
You will be notified in a new session each time the schedule is triggered.

**Creator identity (CRITICAL):**
- The current human speaker is recorded as the **immutable creator**.
- When the schedule fires, you act **as that creator** for DingTalk / Feishu \
/ business-data tools (approvals, IM, org directory, etc.).
- Creation fails if the current session has no resolvable real-user identity.
- Later **delete / change** of this schedule may only be instructed by the \
**same creator**. Anyone else asking to modify it: refuse and tell them to \
ask the creator (or recreate a new schedule under their own identity).

**About the cron expression (CRITICAL — weekday numbering):**
- Format: 5 fields `minute hour day-of-month month day-of-week`.
- Timezone defaults to Asia/Shanghai; confirm with the user if unsure.
- day_of_week uses **APScheduler** semantics (same as Python APScheduler \
CronTrigger), **not** classic Unix crontab:
  - Numbers: **0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, \
5=Saturday, 6=Sunday**
  - Prefer English names to avoid mistakes: `mon,tue,wed,thu,fri,sat,sun` \
(ranges like `mon-fri` are OK).
- Common mistakes (FORBIDDEN):
  - Writing `5` for 周五/Friday → that is **Saturday**. Friday is `4` or `fri`.
  - Writing `1-5` for 工作日 → that is **Tue–Sat**. Weekdays are `0-4` or \
`mon-fri`.
- Good examples:
  - Every Friday 09:00 → `0 9 * * fri` (or `0 9 * * 4`)
  - Weekdays 17:50 → `50 17 * * mon-fri` (or `50 17 * * 0-4`)
  - Every Monday 09:00 → `0 9 * * mon` (or `0 9 * * 0`)
- For a one-off task, query the current time first and set the cron \
expression to fire at that specific moment; set `started_at` / `ended_at` \
when needed. When in doubt, ask before creating.

**About the description field:**
- The `description` is the only context available to you when the \
schedule fires in a new session. Include all necessary details: the goal, \
expected output, constraints, relevant file paths, and anything else needed \
to complete the task independently.
"""

    input_schema: dict = _ScheduleCreateParams.model_json_schema()

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
        chat_model_config: ChatModelConfig,
        storage: Any,
        scheduler_manager: Any,
    ) -> None:
        """Initialize the schedule create tool.

        Args:
            user_id (`str`):
                The authenticated user who owns this schedule.
            agent_id (`str`):
                The agent that will be executed on each trigger.
            chat_model_config (`ChatModelConfig`):
                Model configuration inherited from the current session.
            storage (`Any`):
                The storage backend used to persist the schedule record.
            scheduler_manager (`Any`):
                The scheduler manager, used to tell the timer-owning node
                that a schedule changed. Must expose a
                ``notify_changed(schedule_id)`` coroutine.
        """
        self._user_id = user_id
        self._agent_id = agent_id
        self._chat_model_config = chat_model_config
        self._storage = storage
        self._scheduler_manager = scheduler_manager

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

    async def __call__(  # type: ignore[override]
        self,
        name: str,
        cron_expression: str,
        description: str = "",
        timezone: str = "Asia/Shanghai",
        enabled: bool = True,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        stateful: bool = False,
        permission_mode: str = PermissionMode.DONT_ASK.value,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Create a new scheduled task.

        Args:
            name (`str`):
                Display name of the schedule.
            cron_expression (`str`):
                5-field cron; day_of_week is APScheduler style (0=Mon … 4=Fri),
                e.g. ``'0 9 * * fri'`` or ``'50 17 * * mon-fri'``.
            description (`str`, optional):
                Human-readable description of what this schedule does.
            timezone (`str`, optional):
                IANA timezone name, e.g. ``'Asia/Shanghai'``.
            enabled (`bool`, optional):
                Whether the schedule is active immediately after creation.
            started_at (`datetime | None`, optional):
                Datetime at which the schedule becomes active. Defaults to
                the current time when not specified.
            ended_at (`datetime | None`, optional):
                Datetime at which the schedule stops firing. If not set the
                schedule runs indefinitely.
            stateful (`bool`, optional):
                Whether consecutive executions share the same session context.
            permission_mode (`str`, optional):
                Permission mode value string.
            _agent_state (`AgentState | None`, optional):
                Injected agent state; provides the source session ID.

        Returns:
            `ToolChunk`:
                A chunk with the new schedule ID on success, or an error
                description on failure.
        """
        try:
            perm_mode = PermissionMode(permission_mode)
        except ValueError:
            perm_mode = PermissionMode.DONT_ASK

        source_session_id = (
            _agent_state.session_id if _agent_state is not None else ""
        )

        (
            creator_external_id,
            creator_display_name,
            creator_channel_id,
            creator_chat_id,
        ) = await resolve_creator_from_session(
            self._storage,
            self._user_id,
            self._agent_id,
            source_session_id,
        )
        if not creator_external_id:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "ScheduleCreateError: cannot resolve a real-user "
                            "creator from the current session. Schedules must "
                            "be created by a human (IM / console) so DingTalk "
                            "/ Feishu tools have an actor identity at fire "
                            "time. Ask the user to create the schedule from "
                            "their own chat."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        record = ScheduleRecord(
            user_id=self._user_id,
            agent_id=self._agent_id,
            data=ScheduleData(
                name=name,
                description=description,
                enabled=enabled,
                cron_expression=cron_expression,
                timezone=timezone,
                started_at=started_at or datetime.now(),
                ended_at=ended_at,
                stateful=stateful,
                permission_mode=perm_mode,
                source=ScheduleSource.AGENT,
                source_session_id=source_session_id,
                chat_model_config=self._chat_model_config,
                creator_external_id=creator_external_id,
                creator_display_name=creator_display_name,
                creator_channel_id=creator_channel_id,
                creator_chat_id=creator_chat_id,
            ),
        )

        self._scheduler_manager.validate_schedule(record)
        await self._storage.upsert_schedule(self._user_id, record)
        await self._scheduler_manager.notify_changed(record.id)

        creator_label = creator_display_name or creator_external_id
        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Schedule {name!r} created successfully.\n"
                        f"Schedule ID: {record.id}\n"
                        f"Cron: {cron_expression} (timezone: {timezone})\n"
                        f"Enabled: {enabled}\n"
                        f"Started at: {record.data.started_at}\n"
                        f"Ended at: {ended_at or '(no end time)'}\n"
                        f"Stateful: {stateful}\n"
                        f"Creator: {creator_label} "
                        f"({creator_external_id}) — immutable; only this "
                        f"person may delete/change this schedule."
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
