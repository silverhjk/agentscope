# -*- coding: utf-8 -*-
"""Channel-agnostic ``ask_user`` protocol.

The digital employee calls ``ask_user`` (an always-on external tool). The
run parks on ``RequireExternalExecutionEvent``. IM cards / the Web preview
collect a choice, a short form, or a file and resume with
``ExternalExecutionResultEvent``. A follow-up user ``Msg`` can also
complete the parked call (typed option, form text, inbound file) instead
of superseding it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..message import (
    DataBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
)

ASK_USER_TOOL_NAME = "ask_user"
ASK_USER_KINDS = frozenset({"choice", "form", "file"})
MAX_OPTIONS = 8
MAX_FIELDS = 6
_CANCEL_TEXTS = frozenset(
    {"取消", "cancel", "算了", "不了", "不用了", "算了吧"},
)

ASK_USER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["choice", "form", "file"],
            "description": (
                "choice=让用户点选一个选项; "
                "form=短表单(少量文本/下拉); "
                "file=等待用户发送文件或图片"
            ),
        },
        "prompt": {
            "type": "string",
            "description": "展示给用户的问题或说明",
        },
        "options": {
            "type": "array",
            "description": "kind=choice 时必填，2-8 个选项",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["id", "label"],
            },
        },
        "fields": {
            "type": "array",
            "description": "kind=form 时的字段，建议不超过 6 个",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "textarea", "select", "number"],
                    },
                    "required": {"type": "boolean"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["id", "label"],
                        },
                    },
                },
                "required": ["name", "label"],
            },
        },
        "accept": {
            "type": "string",
            "description": "kind=file 时的类型提示，如 image/* 或 .pdf",
        },
        "multiple": {
            "type": "boolean",
            "description": "kind=file 时是否允许多文件",
        },
    },
    "required": ["kind", "prompt"],
}

ASK_USER_DESCRIPTION = (
    "向当前对话用户提问并等待回答。用于必须由用户做选择、填短表单、"
    "或发送文件才能继续的步骤。"
    "kind=choice 时提供 options（id+label）；"
    "kind=form 时提供 fields（少量文本/下拉/数字，不要做复杂多页表单）；"
    "kind=file 时等待用户发送文件或图片（IM 卡片无法直接上传，用户下一条消息带附件即可）。"
    "不要用这个工具闲聊；不要把 bot_id 等配置项丢给用户填。"
)


@dataclass(frozen=True, slots=True)
class AskUserOption:
    """One selectable option."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class AskUserField:
    """One short-form field."""

    name: str
    label: str
    type: str = "text"
    required: bool = False
    options: tuple[AskUserOption, ...] = ()


@dataclass(frozen=True, slots=True)
class AskUserSpec:
    """Normalised ask_user arguments."""

    kind: str
    prompt: str
    options: tuple[AskUserOption, ...] = ()
    fields: tuple[AskUserField, ...] = ()
    accept: str = ""
    multiple: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-serialisable spec for the Web preview / stream payload."""
        data: dict[str, Any] = {
            "kind": self.kind,
            "prompt": self.prompt,
        }
        if self.options:
            data["options"] = [
                {"id": o.id, "label": o.label} for o in self.options
            ]
        if self.fields:
            data["fields"] = [
                {
                    "name": f.name,
                    "label": f.label,
                    "type": f.type,
                    "required": f.required,
                    "options": [
                        {"id": o.id, "label": o.label} for o in f.options
                    ],
                }
                for f in self.fields
            ]
        if self.accept:
            data["accept"] = self.accept
        if self.kind == "file":
            data["multiple"] = self.multiple
        return data


def parse_tool_input(raw: Any) -> dict[str, Any]:
    """Parse a tool-call ``input`` JSON string or mapping."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def parse_ask_user_input(raw: Any) -> AskUserSpec | None:
    """Normalise tool-call input into an :class:`AskUserSpec`.

    Returns ``None`` when ``kind`` / ``prompt`` are missing or invalid.
    """
    data = parse_tool_input(raw)
    kind = str(data.get("kind") or "").strip().lower()
    prompt = str(data.get("prompt") or "").strip()
    if kind not in ASK_USER_KINDS or not prompt:
        return None
    options = _parse_options(data.get("options"))[:MAX_OPTIONS]
    fields = _parse_fields(data.get("fields"))[:MAX_FIELDS]
    accept = str(data.get("accept") or "").strip()
    multiple = bool(data.get("multiple"))
    if kind == "choice" and len(options) < 2:
        return None
    if kind == "form" and not fields:
        return None
    return AskUserSpec(
        kind=kind,
        prompt=prompt,
        options=tuple(options),
        fields=tuple(fields),
        accept=accept,
        multiple=multiple,
    )


def spec_from_tool_call(tool: ToolCallBlock) -> AskUserSpec | None:
    """Parse ``ask_user`` arguments from a parked tool call."""
    if tool.name != ASK_USER_TOOL_NAME:
        return None
    return parse_ask_user_input(tool.input)


def is_cancel_text(text: str) -> bool:
    """Whether ``text`` is a user cancel of the parked ask."""
    return (text or "").strip().lower() in _CANCEL_TEXTS


def match_choice(spec: AskUserSpec, text: str) -> AskUserOption | None:
    """Match free-text to an option (id, label, or 1-based index)."""
    needle = (text or "").strip()
    if not needle or spec.kind != "choice":
        return None
    lowered = needle.lower()
    for opt in spec.options:
        if needle == opt.id or lowered == opt.label.lower():
            return opt
    if re.fullmatch(r"[1-9]\d*", needle):
        idx = int(needle) - 1
        if 0 <= idx < len(spec.options):
            return spec.options[idx]
    return None


def option_at(spec: AskUserSpec, index: int) -> AskUserOption | None:
    """Return the option at ``index``, or ``None``."""
    if 0 <= index < len(spec.options):
        return spec.options[index]
    return None


def option_by_id(spec: AskUserSpec, option_id: str) -> AskUserOption | None:
    """Return the option whose id matches, or ``None``."""
    wanted = (option_id or "").strip()
    if not wanted:
        return None
    for opt in spec.options:
        if opt.id == wanted:
            return opt
    return None


def build_result_json(
    spec: AskUserSpec,
    *,
    cancelled: bool = False,
    option: AskUserOption | None = None,
    values: dict[str, Any] | None = None,
    files: list[dict[str, Any]] | None = None,
    raw_text: str | None = None,
) -> str:
    """Serialize the tool result body as JSON text."""
    payload: dict[str, Any] = {"kind": spec.kind, "cancelled": bool(cancelled)}
    if cancelled:
        return json.dumps(payload, ensure_ascii=False)
    if spec.kind == "choice" and option is not None:
        payload["value"] = option.id
        payload["label"] = option.label
    elif spec.kind == "form":
        payload["values"] = values or {}
        if raw_text:
            payload["raw"] = raw_text
    elif spec.kind == "file":
        payload["files"] = files or []
    return json.dumps(payload, ensure_ascii=False)


def result_block_for(
    tool: ToolCallBlock,
    spec: AskUserSpec,
    *,
    cancelled: bool = False,
    option: AskUserOption | None = None,
    values: dict[str, Any] | None = None,
    files: list[dict[str, Any]] | None = None,
    raw_text: str | None = None,
    extra_blocks: list[TextBlock | DataBlock] | None = None,
) -> ToolResultBlock:
    """Build the tool result that resumes a parked ``ask_user`` call."""
    text = build_result_json(
        spec,
        cancelled=cancelled,
        option=option,
        values=values,
        files=files,
        raw_text=raw_text,
    )
    output: str | list[TextBlock | DataBlock]
    if extra_blocks:
        output = [TextBlock(text=text), *extra_blocks]
    else:
        output = text
    return ToolResultBlock(
        id=tool.id,
        name=tool.name,
        output=output,
        state=ToolResultState.SUCCESS,
    )


def file_blocks_from_content(
    content: list[Any] | None,
) -> list[DataBlock]:
    """Collect ``DataBlock``s from a message / channel event."""
    blocks: list[DataBlock] = []
    for block in content or []:
        if isinstance(block, DataBlock):
            blocks.append(block)
    return blocks


def text_from_content(content: list[Any] | None) -> str:
    """Concatenate text blocks."""
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, TextBlock) and block.text.strip():
            parts.append(block.text)
    return "\n".join(parts).strip()


def file_meta(block: DataBlock) -> dict[str, Any]:
    """Compact file metadata for the JSON result (no bytes)."""
    media = ""
    size = 0
    source = block.source
    if hasattr(source, "media_type"):
        media = str(getattr(source, "media_type") or "")
    data = getattr(source, "data", None)
    if isinstance(data, str):
        size = len(data)
    return {
        "name": block.name or "file",
        "media_type": media,
        "size": size,
    }


def try_complete_from_msg(
    tool: ToolCallBlock,
    msg: Msg,
) -> ToolResultBlock | None:
    """Complete a parked ``ask_user`` from a follow-up user message.

    Returns ``None`` when the message should supersede the park instead
    (unrelated text while waiting for a choice, etc.).
    """
    spec = spec_from_tool_call(tool)
    if spec is None:
        return None
    text = text_from_content(list(msg.content or []))
    files = file_blocks_from_content(list(msg.content or []))
    if is_cancel_text(text) and not files:
        return result_block_for(tool, spec, cancelled=True)
    if spec.kind == "file":
        if files:
            return result_block_for(
                tool,
                spec,
                files=[file_meta(b) for b in files],
                extra_blocks=list(files),
            )
        return None
    if spec.kind == "choice":
        option = match_choice(spec, text)
        if option is not None:
            return result_block_for(tool, spec, option=option)
        return None
    if spec.kind == "form":
        if not text:
            return None
        return result_block_for(tool, spec, values={}, raw_text=text)
    return None


def first_submitted_ask_user(
    awaiting: list[ToolCallBlock],
) -> ToolCallBlock | None:
    """The first parked ``ask_user`` call, if any."""
    for tc in awaiting:
        if tc.name != ASK_USER_TOOL_NAME:
            continue
        state = tc.state
        if state in (ToolCallState.SUBMITTED, ToolCallState.SUBMITTED.value):
            return tc
    return None


def prompt_fallback_text(spec: AskUserSpec) -> str:
    """Plain-text prompt when the channel cannot render a card."""
    lines = [spec.prompt]
    if spec.kind == "choice" and spec.options:
        lines.append("")
        for i, opt in enumerate(spec.options, start=1):
            lines.append(f"{i}. {opt.label}")
        lines.append("请点击上方选项，或直接回复序号 / 选项内容。回复「取消」可放弃。")
    elif spec.kind == "form" and spec.fields:
        lines.append("")
        for f in spec.fields:
            req = "（必填）" if f.required else ""
            hint = ""
            if f.options:
                hint = "：" + " / ".join(o.label for o in f.options)
            lines.append(f"- {f.label}{req}{hint}")
        lines.append("请按字段逐条回复，或回复「取消」。")
    elif spec.kind == "file":
        extra = f"（{spec.accept}）" if spec.accept else ""
        lines.append(f"请发送文件或图片{extra}。回复「取消」可放弃。")
    return "\n".join(lines)


def _parse_options(raw: Any) -> list[AskUserOption]:
    items: list[AskUserOption] = []
    if not isinstance(raw, list):
        return items
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        oid = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or oid).strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        items.append(AskUserOption(id=oid, label=label or oid))
    return items


def _parse_fields(raw: Any) -> list[AskUserField]:
    items: list[AskUserField] = []
    if not isinstance(raw, list):
        return items
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        label = str(entry.get("label") or name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ftype = str(entry.get("type") or "text").strip().lower()
        if ftype not in {"text", "textarea", "select", "number"}:
            ftype = "text"
        items.append(
            AskUserField(
                name=name,
                label=label or name,
                type=ftype,
                required=bool(entry.get("required")),
                options=tuple(_parse_options(entry.get("options"))),
            ),
        )
    return items
