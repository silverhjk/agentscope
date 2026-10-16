# -*- coding: utf-8 -*-
"""Parse ask_user Feishu / DingTalk card callbacks."""

from agentscope.app.channel._dingtalk._card import (
    _parse_ask_user_callback,
    _tool_call_id,
    _tracking_id,
)
from agentscope.app.channel._feishu._card_templates import (
    _parse_ask_user_action,
)


def test_feishu_choice_click() -> None:
    parsed = _parse_ask_user_action(
        {
            "type": "ask_user",
            "tool_call_id": "tc1",
            "chat_id": "oc_1",
            "agent_id": "ag",
            "session_id": "s1",
            "action": "choice",
            "option_id": "yes",
            "option_index": "0",
        },
    )
    assert parsed is not None
    tool_call_id, chat_id, agent_id, session_id, payload = parsed
    assert tool_call_id == "tc1"
    assert chat_id == "oc_1"
    assert agent_id == "ag"
    assert session_id == "s1"
    assert payload["option_id"] == "yes"
    assert payload["option_index"] == 0


def test_feishu_form_submit() -> None:
    parsed = _parse_ask_user_action(
        {
            "type": "ask_user",
            "tool_call_id": "tc2",
            "chat_id": "oc_2",
            "action": "form",
        },
        {"reason": "加班", "days": {"value": "2"}},
    )
    assert parsed is not None
    payload = parsed[4]
    assert payload["kind"] == "form"
    assert payload["values"]["reason"] == "加班"
    assert payload["values"]["days"] == "2"


def test_feishu_cancel() -> None:
    parsed = _parse_ask_user_action(
        {
            "type": "ask_user",
            "tool_call_id": "tc3",
            "chat_id": "oc_3",
            "action": "cancel",
        },
    )
    assert parsed is not None
    assert parsed[4]["cancelled"] is True


def test_dingtalk_option_click() -> None:
    track = _tracking_id("call99")
    payload = {
        "userId": "staff1",
        "outTrackId": track,
        "spaceType": "IM_ROBOT",
        "content": {
            "cardPrivateData": {
                "params": {"id": "opt1"},
            },
        },
    }
    decision = _parse_ask_user_callback(payload)
    assert decision is not None
    assert decision.tool_call_id == "call99" or decision.tool_call_id == _tool_call_id(
        track,
    )
    assert decision.option_index == 1
    assert decision.cancelled is False


def test_dingtalk_cancel_click() -> None:
    track = _tracking_id("call99")
    decision = _parse_ask_user_callback(
        {
            "userId": "staff1",
            "outTrackId": track,
            "spaceType": "IM_ROBOT",
            "content": {
                "cardPrivateData": {"params": {"id": "ask_cancel"}},
            },
        },
    )
    assert decision is not None
    assert decision.cancelled is True
