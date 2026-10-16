# -*- coding: utf-8 -*-
"""Parse process-tool results into an informational progress card payload.

Cards are emitted on ``tool_process_start`` (created=true) and
``tool_process_advance`` (ok). ``tool_process_get`` carries ``progress``
for the model but does not post a card.
"""

from __future__ import annotations

import json
from typing import Any

from ..message import Msg, ToolResultBlock

PROCESS_START = "tool_process_start"
PROCESS_ADVANCE = "tool_process_advance"
PROCESS_CARD_TOOLS = frozenset({PROCESS_START, PROCESS_ADVANCE})

_STAGE_MARK = {
    "completed": "✓",
    "in_progress": "●",
    "skipped": "–",
    "pending": "○",
}
_TASK_MARK = {
    "completed": "✓",
    "in_progress": "●",
    "skipped": "–",
    "failed": "✗",
    "pending": "○",
}


def parse_tool_output(output: Any) -> dict[str, Any] | None:
    """Turn a tool result output into a dict, if possible."""
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        text = output.strip()
        if not text.startswith("{") and not text.startswith("["):
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
            elif hasattr(item, "text"):
                texts.append(str(getattr(item, "text") or ""))
            elif isinstance(item, str):
                texts.append(item)
        joined = "\n".join(t for t in texts if t.strip())
        return parse_tool_output(joined)
    return None


def progress_from_tool_result(name: str | None, output: Any) -> dict[str, Any] | None:
    """Return a progress payload when this result should post a card."""
    tool = (name or "").strip()
    if tool not in PROCESS_CARD_TOOLS:
        return None
    parsed = parse_tool_output(output)
    if not parsed or parsed.get("ok") is False:
        return None
    if tool == PROCESS_START and parsed.get("created") is False:
        return None
    progress = parsed.get("progress")
    if not isinstance(progress, dict):
        return None
    if not progress.get("process_name"):
        return None
    return progress


def progress_cards_from_msg(reply: Msg | None) -> list[dict[str, Any]]:
    """Collect progress cards from tool results on an accumulated reply."""
    if reply is None:
        return []
    cards: list[dict[str, Any]] = []
    for block in reply.content:
        if not isinstance(block, ToolResultBlock):
            continue
        card = progress_from_tool_result(block.name, block.output)
        if card is not None:
            cards.append(card)
    return cards


def progress_cards_from_content(content: Any) -> list[dict[str, Any]]:
    """Collect progress cards from a persisted Msg.content list."""
    if not isinstance(content, list):
        return []
    cards: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        card = progress_from_tool_result(
            str(block.get("name") or ""),
            block.get("output", block.get("content")),
        )
        if card is not None:
            cards.append(card)
    return cards


def format_progress_markdown(progress: dict[str, Any]) -> str:
    """Nested-list markdown for Feishu / Discord / DingTalk (no GFM tables)."""
    name = str(progress.get("process_name") or "流程").strip() or "流程"
    subject = str(progress.get("subject_display_name") or "").strip()
    title = f"{name} · {subject}" if subject else name
    idx = progress.get("stage_index") or 0
    total = progress.get("stage_total") or 0
    current = str(progress.get("current_stage_name") or "").strip()
    status = str(progress.get("status") or "").strip()
    if status == "completed":
        headline = f"环节 {idx}/{total} · 已完成"
    elif current:
        headline = f"环节 {idx}/{total} · 当前：{current}"
    else:
        headline = f"环节 {idx}/{total}"

    lines = [f"**{title}**", headline, "", "环节："]
    stages = progress.get("stages") if isinstance(progress.get("stages"), list) else []
    if not stages:
        lines.append("- （暂无环节）")
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        st = str(stage.get("status") or "pending")
        mark = _STAGE_MARK.get(st, "○")
        label = str(stage.get("name") or "").strip() or "未命名"
        suffix = "（当前）" if st == "in_progress" else ""
        lines.append(f"- {mark} {label}{suffix}")

    lines.extend(["", "当前环节任务："])
    tasks = (
        progress.get("current_tasks")
        if isinstance(progress.get("current_tasks"), list)
        else []
    )
    if not tasks:
        lines.append("- （本环暂无任务）")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        st = str(task.get("status") or "pending")
        mark = _TASK_MARK.get(st, "○")
        label = str(task.get("name") or "").strip() or "未命名"
        lines.append(f"- {mark} {label}")
    return "\n".join(lines)
