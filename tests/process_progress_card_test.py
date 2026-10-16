# -*- coding: utf-8 -*-
"""Process progress informational cards (start/advance; no GFM tables)."""

from __future__ import annotations

import json

from agentscope.app._process_progress import (
    format_progress_markdown,
    progress_cards_from_content,
    progress_cards_from_msg,
    progress_from_tool_result,
)
from agentscope.app.channel._dingtalk._card import _process_progress_card_data
from agentscope.app.channel._feishu._card_templates import (
    _build_process_progress_card,
)
from agentscope.message import Msg, TextBlock, ToolResultBlock

_PROGRESS = {
    "process_name": "营销线索跟进",
    "subject_display_name": "张三",
    "status": "active",
    "stage_index": 2,
    "stage_total": 5,
    "current_stage_name": "需求确认",
    "stages": [
        {"name": "线索初筛", "status": "completed"},
        {"name": "需求确认", "status": "in_progress"},
        {"name": "方案报价", "status": "pending"},
        {"name": "合同签订", "status": "pending"},
        {"name": "交付回访", "status": "pending"},
    ],
    "current_tasks": [
        {"name": "确认预算", "status": "completed"},
        {"name": "记录痛点", "status": "in_progress"},
        {"name": "对齐决策人", "status": "pending"},
    ],
}


def _start_payload(*, created: bool = True) -> dict:
    return {
        "ok": True,
        "created": created,
        "process_name": "营销线索跟进",
        "progress": _PROGRESS,
    }


def _advance_payload(*, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "process_name": "营销线索跟进",
        "progress": _PROGRESS,
    }


def test_start_created_emits_card() -> None:
    card = progress_from_tool_result("tool_process_start", _start_payload())
    assert card is not None
    assert card["process_name"] == "营销线索跟进"
    assert card["stage_index"] == 2
    assert card["stage_total"] == 5


def test_start_idempotent_does_not_emit_card() -> None:
    assert (
        progress_from_tool_result(
            "tool_process_start",
            _start_payload(created=False),
        )
        is None
    )


def test_get_does_not_emit_card() -> None:
    assert (
        progress_from_tool_result(
            "tool_process_get",
            _start_payload(),
        )
        is None
    )


def test_advance_success_emits_card() -> None:
    card = progress_from_tool_result("tool_process_advance", _advance_payload())
    assert card is not None
    assert card["current_stage_name"] == "需求确认"


def test_advance_failure_does_not_emit_card() -> None:
    assert (
        progress_from_tool_result(
            "tool_process_advance",
            _advance_payload(ok=False),
        )
        is None
    )


def test_markdown_uses_nested_lists_not_tables() -> None:
    md = format_progress_markdown(_PROGRESS)
    assert "|" not in md
    assert "**营销线索跟进 · 张三**" in md
    assert "环节 2/5 · 当前：需求确认" in md
    assert "- ✓ 线索初筛" in md
    assert "- ● 需求确认（当前）" in md
    assert "- ○ 方案报价" in md
    assert "- ✓ 确认预算" in md
    assert "- ● 记录痛点" in md
    assert "- ○ 对齐决策人" in md


def test_dingtalk_card_is_settled_list_not_table() -> None:
    data = _process_progress_card_data(_PROGRESS)
    assert "|" not in data["staticMsgContent"]
    assert "msgButtons" not in json.loads(data["sys_full_json_obj"])
    assert data["status"] == "done"
    assert "流程进度" in data["msgTitle"]
    assert "- ● 需求确认（当前）" in data["staticMsgContent"]


def test_feishu_card_has_header_markdown_no_buttons() -> None:
    raw = _build_process_progress_card(_PROGRESS)
    card = json.loads(raw)
    assert card["header"]["title"]["content"].startswith("流程进度")
    elements = card["elements"]
    assert len(elements) == 1
    assert elements[0]["tag"] == "markdown"
    assert "|" not in elements[0]["content"]
    dumped = json.dumps(card, ensure_ascii=False)
    assert '"tag": "button"' not in dumped
    assert "需求确认（当前）" in elements[0]["content"]


def test_cards_from_msg_and_content() -> None:
    payload = json.dumps(_start_payload(), ensure_ascii=False)
    reply = Msg(
        name="bot",
        role="assistant",
        content=[
            ToolResultBlock(
                id="1",
                name="tool_process_start",
                output=payload,
            ),
            TextBlock(text="已开流"),
        ],
    )
    cards = progress_cards_from_msg(reply)
    assert len(cards) == 1
    assert cards[0]["subject_display_name"] == "张三"

    persisted = progress_cards_from_content(
        [
            {
                "type": "tool_result",
                "name": "tool_process_advance",
                "output": _advance_payload(),
            },
        ],
    )
    assert len(persisted) == 1
    assert persisted[0]["stage_index"] == 2
