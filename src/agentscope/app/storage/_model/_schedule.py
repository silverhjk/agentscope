# -*- coding: utf-8 -*-
"""The schedule storage model."""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from ._base import _RecordBase
from ._session import ChatModelConfig
from ....permission import PermissionMode


class ScheduleSource(str, Enum):
    """The source that created the schedule.

    Attributes:
        USER: Created manually by the user via the UI.
        AGENT: Created automatically by an agent, e.g. via a tool call.
    """

    USER = "USER"
    AGENT = "AGENT"


def _get_local_timezone() -> str:
    """Get the local timezone.

    Returns:
        `str`:
            The local timezone.
    """
    try:
        from tzlocal import get_localzone

        return str(get_localzone())
    except Exception:
        return "UTC"


class ScheduleData(BaseModel):
    """The schedule configuration data."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(
        default="",
        description="The description of the schedule, including its purpose, "
        "trigger conditions, etc.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the schedule is active. Disabled schedules are "
        "retained but will not trigger.",
    )

    timezone: str = Field(
        default=_get_local_timezone(),
        description="IANA timezone name used to evaluate the cron expression, "
        "e.g. 'America/New_York' or 'Asia/Shanghai'.",
    )

    cron_expression: str = Field(
        description=(
            "5-field cron (APScheduler day_of_week: 0=Mon … 4=Fri … 6=Sun; "
            "prefer mon/tue/…/sun). e.g. '0 9 * * fri', '50 17 * * mon-fri'."
        ),
    )

    started_at: datetime = Field(
        description="The date and time the schedule was started.",
        default_factory=datetime.now,
    )

    ended_at: datetime | None = Field(
        default=None,
        description="The date and time the schedule was ended.",
    )

    chat_model_config: ChatModelConfig = Field(
        description="Model configuration for the auto-created session.",
    )

    stateful: bool = Field(
        title="Stateful",
        default=False,
        description="Whether consecutive executions share the same session "
        "context. If not, each execution will have its own state.",
    )

    permission_mode: PermissionMode = Field(
        title="Permission mode",
        default=PermissionMode.DONT_ASK,
        description="Permission level for the agent during scheduled "
        "execution. Defaults to DONT_ASK since no user is present to "
        "answer prompts.",
    )

    source: ScheduleSource = Field(
        default=ScheduleSource.USER,
        description="Indicates how this schedule was created.",
    )

    source_session_id: str = Field(
        default="",
        description="The source session identifier, used for resource "
        "retrieval.",
    )

    creator_external_id: str = Field(
        default="",
        description=(
            "Immutable real-user id of who created this schedule "
            "(DingTalk/Feishu staff id or admin actor). Used as the actor "
            "identity when the schedule fires. Empty for legacy schedules."
        ),
    )

    creator_display_name: str = Field(
        default="",
        description="Display name of the schedule creator (immutable).",
    )

    creator_channel_id: str = Field(
        default="",
        description=(
            "Channel id of the creating session (e.g. dingtalk / feishu / "
            "admin). Copied onto fire sessions so tools can resolve the peer."
        ),
    )

    creator_chat_id: str = Field(
        default="",
        description=(
            "Chat id used for peer resolution on fire, typically "
            "``user:{creator_external_id}``."
        ),
    )


class ScheduleRecord(_RecordBase):
    """Persisted schedule record."""

    user_id: str = Field(description="Owner user id.")

    agent_id: str = Field(
        description="The agent id that will execute the schedule.",
    )

    data: ScheduleData = Field(description="Schedule configuration.")
