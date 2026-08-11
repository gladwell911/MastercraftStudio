"""Mapping-table tests for kimi_server_client.map_session_event (plan section B).

Real server messages come from tests/fixtures/kimi_server_events.jsonl
(captured by the Step-0 probe); event types absent from the fixture
(interrupt/approval/error paths) use synthetic messages in the same envelope.
"""

import json
from pathlib import Path

import pytest

from kimi_server_client import (
    KimiEvent,
    KimiServerError,
    event_from_payload,
    event_to_payload,
    map_session_event,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "kimi_server_events.jsonl"


def _load_fixture_messages():
    messages = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            messages.append(json.loads(line)["msg"])
    return messages


FIXTURE_MESSAGES = _load_fixture_messages()


def fixture_msg(event_type):
    for message in FIXTURE_MESSAGES:
        if message.get("type") == event_type:
            return message
    raise AssertionError(f"fixture has no {event_type} message")


def make_msg(body_type, payload_extra=None, *, session_id="session-x", seq=42):
    payload = {"type": body_type}
    payload.update(payload_extra or {})
    return {"type": body_type, "seq": seq, "session_id": session_id, "payload": payload}


# ----------------------------------------------------------------------
# fixture sanity + per-type mapping overview


def test_fixture_covers_expected_event_types():
    types = {m["type"] for m in FIXTURE_MESSAGES}
    assert {
        "server_hello",
        "ack",
        "session.meta.updated",
        "turn.started",
        "agent.status.updated",
        "event.session.work_changed",
        "context.spliced",
        "turn.step.started",
        "thinking.delta",
        "assistant.delta",
        "turn.step.completed",
        "turn.ended",
        "prompt.completed",
        "tool.call.delta",
        "tool.call.started",
        "tool.result",
    } <= types


@pytest.mark.parametrize(
    "event_type,expected",
    [
        ("assistant.delta", "agent_message_delta"),
        ("thinking.delta", "agent_message_delta"),
        ("turn.started", "turn_started"),
        ("turn.ended", "turn_completed"),
        ("turn.step.started", "item_started"),
        ("turn.step.completed", "item_completed"),
        ("tool.call.started", "item_started"),
        ("tool.result", "item_completed"),
        ("prompt.completed", "turn_completed"),
        ("agent.status.updated", "thread_status_changed"),
        ("session.meta.updated", "notification"),
        ("context.spliced", "notification"),
        ("event.session.work_changed", "notification"),
    ],
)
def test_fixture_type_maps_to_expected_event_type(event_type, expected):
    event = map_session_event(fixture_msg(event_type))
    assert event is not None
    assert event.type == expected


def test_every_fixture_message_maps_without_error():
    for message in FIXTURE_MESSAGES:
        map_session_event(message)  # must not raise


# ----------------------------------------------------------------------
# B1-B2: assistant/thinking deltas


def test_assistant_delta_maps_to_agent_message_delta():
    msg = fixture_msg("assistant.delta")

    event = map_session_event(msg)

    assert event.type == "agent_message_delta"
    assert event.text == msg["payload"]["delta"]
    assert event.raw_text == msg["payload"]["delta"]
    assert event.thread_id == msg["session_id"]
    assert event.turn_id == str(msg["payload"]["turnId"])
    assert event.display_kind == "assistant"


def test_thinking_delta_maps_to_agent_message_delta_with_thinking_kind():
    msg = fixture_msg("thinking.delta")

    event = map_session_event(msg)

    assert event.type == "agent_message_delta"
    assert event.text == msg["payload"]["delta"]
    assert event.raw_text == msg["payload"]["delta"]
    assert event.thread_id == msg["session_id"]
    assert event.turn_id == str(msg["payload"]["turnId"])
    assert event.display_kind == "thinking"


# ----------------------------------------------------------------------
# B3-B4: turn lifecycle


def test_turn_started_maps_to_turn_started():
    msg = fixture_msg("turn.started")

    event = map_session_event(msg)

    assert event.type == "turn_started"
    assert event.turn_id  # present even for the server's numeric turnId 0
    assert event.turn_id == str(msg["payload"]["turnId"])
    assert event.text == msg["payload"]["prompt"]
    assert event.thread_id == msg["session_id"]


def test_turn_ended_completed_maps_to_turn_completed():
    msg = fixture_msg("turn.ended")

    event = map_session_event(msg)

    assert event.type == "turn_completed"
    assert event.status == "completed"
    assert event.turn_id
    assert event.turn_id == str(msg["payload"]["turnId"])


def test_turn_ended_failed_carries_error_message():
    event = map_session_event(
        make_msg("turn.ended", {"turnId": 3, "reason": "failed", "error": {"message": "model exploded"}})
    )

    assert event.type == "turn_completed"
    assert event.status == "failed"
    assert event.text == "model exploded"
    assert event.turn_id == "3"


# ----------------------------------------------------------------------
# B5: turn steps


def test_turn_step_started_maps_to_item_started():
    msg = fixture_msg("turn.step.started")

    event = map_session_event(msg)

    assert event.type == "item_started"
    assert event.display_kind == "step"
    assert event.item_id == msg["payload"]["stepId"]
    assert event.title == "step %s" % msg["payload"]["step"]
    assert event.status == "running"


def test_turn_step_completed_maps_to_item_completed_with_usage():
    msg = fixture_msg("turn.step.completed")

    event = map_session_event(msg)

    assert event.type == "item_completed"
    assert event.display_kind == "step"
    assert event.item_id == msg["payload"]["stepId"]
    assert event.status == "completed"
    assert event.usage == msg["payload"]["usage"]
    assert event.usage["output"] == 18


def test_turn_step_interrupted_maps_to_interrupted_item_completed():
    event = map_session_event(
        make_msg(
            "turn.step.interrupted",
            {"turnId": 2, "step": 1, "stepId": "step-1", "message": "aborted by user"},
        )
    )

    assert event.type == "item_completed"
    assert event.display_kind == "step"
    assert event.status == "interrupted"
    assert event.text == "aborted by user"


# ----------------------------------------------------------------------
# B6-B8: tool calls


def test_fixture_tool_call_started_maps_to_item_started():
    msg = fixture_msg("tool.call.started")

    event = map_session_event(msg)

    assert event.type == "item_started"
    assert event.title == msg["payload"]["description"]
    assert event.item_id == msg["payload"]["toolCallId"]
    assert event.display_kind == "file"  # fixture display.kind is file_io
    assert event.status == "running"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("command", "command"),
        ("process", "command"),
        ("file_io", "file"),
        ("diff", "diff"),
        ("search", "search"),
        ("url_fetch", "fetch"),
        ("agent_call", "agent"),
        ("skill_call", "skill"),
    ],
)
def test_tool_call_started_display_kind_mapping(kind, expected):
    event = map_session_event(
        make_msg(
            "tool.call.started",
            {"turnId": 1, "toolCallId": "call-1", "description": "doing things", "display": {"kind": kind}},
        )
    )

    assert event.type == "item_started"
    assert event.display_kind == expected


def test_tool_call_delta_returns_none():
    assert map_session_event(fixture_msg("tool.call.delta")) is None


@pytest.mark.parametrize("body_type", ["tool.progress", "shell.output"])
def test_tool_progress_maps_to_commentary_delta(body_type):
    event = map_session_event(make_msg(body_type, {"turnId": 1, "toolCallId": "c1", "output": "chunk"}))

    assert event.type == "agent_message_delta"
    assert event.display_kind == "commentary"
    assert event.text == "chunk"


def test_tool_result_maps_to_item_completed_with_truncated_output():
    msg = fixture_msg("tool.result")

    event = map_session_event(msg)

    assert event.type == "item_completed"
    assert event.item_id == msg["payload"]["toolCallId"]
    # fixture output is 3796 chars; the mapper caps text at 2000
    assert event.text == msg["payload"]["output"][:2000]
    assert len(event.text) == 2000
    assert event.status == "completed"


def test_tool_result_short_output_kept_verbatim():
    event = map_session_event(make_msg("tool.result", {"toolCallId": "c1", "output": "tiny"}))

    assert event.text == "tiny"


def test_tool_result_preserves_exit_code():
    event = map_session_event(make_msg("tool.result", {"toolCallId": "c1", "exitCode": 3, "output": "oops"}))

    assert event.exit_code == 3


# ----------------------------------------------------------------------
# B9: subagents


def test_subagent_started_maps_to_item_started():
    event = map_session_event(make_msg("subagent.started", {"subagentId": "a1", "title": "researcher"}))

    assert event.type == "item_started"
    assert event.display_kind == "agent"
    assert event.title == "researcher"


def test_subagent_completed_maps_to_subagent_result():
    event = map_session_event(
        make_msg("subagent.completed", {"subagentId": "a1", "title": "researcher", "summary": "done"})
    )

    assert event.type == "subagent_result"
    assert event.title == "researcher"
    assert event.text == "done"


# ----------------------------------------------------------------------
# B10: prompt completion/abort


def test_prompt_completed_maps_to_turn_completed():
    msg = fixture_msg("prompt.completed")

    event = map_session_event(msg)

    assert event.type == "turn_completed"
    assert event.status == "completed"
    assert event.data.get("prompt_id") == msg["payload"]["promptId"]


def test_prompt_aborted_maps_to_interrupted_turn_completed():
    event = map_session_event(make_msg("prompt.aborted", {"turnId": 5}))

    assert event.type == "turn_completed"
    assert event.status == "interrupted"
    assert event.turn_id == "5"


# ----------------------------------------------------------------------
# B11-B13: compaction / goal / error / warning notifications


@pytest.mark.parametrize("body_type", ["compaction.started", "compaction.completed"])
def test_compaction_maps_to_notification(body_type):
    event = map_session_event(make_msg(body_type, {}))

    assert event.type == "notification"
    assert event.display_kind == "compaction"


def test_goal_updated_maps_to_goal_notification():
    event = map_session_event(make_msg("goal.updated", {"goal": {"status": "active", "objective": "ship it"}}))

    assert event.type == "notification"
    assert event.display_kind == "goal"
    assert event.status == "active"
    assert event.text == "ship it"


def test_error_maps_to_error_event():
    event = map_session_event(make_msg("error", {"turnId": 1, "message": "rate limited", "code": "RATE_LIMIT"}))

    assert event.type == "error"
    assert event.text == "rate limited"
    assert event.subtype == "RATE_LIMIT"


def test_warning_maps_to_notification():
    event = map_session_event(make_msg("warning", {"message": "context almost full"}))

    assert event.type == "notification"
    assert event.display_kind == "warning"
    assert event.text == "context almost full"


# ----------------------------------------------------------------------
# B14: agent.status.updated


def test_agent_status_awaiting_approval_maps_to_server_request():
    event = map_session_event(
        make_msg(
            "agent.status.updated",
            {
                "phase": {"kind": "awaiting_approval", "turnId": 2},
                "approval": {"id": "ap-1", "kind": "command"},
                "contextTokens": 1200,
                "maxContextTokens": 262144,
            },
        )
    )

    assert event.type == "server_request"
    assert event.method == "approval"
    assert event.turn_id == "2"
    assert event.params.get("approval") == {"id": "ap-1", "kind": "command"}
    assert event.usage == {"context_tokens": 1200, "max_context_tokens": 262144}


def test_agent_status_running_maps_to_thread_status_changed():
    event = map_session_event(fixture_msg("agent.status.updated"))

    assert event.type == "thread_status_changed"
    assert event.status == "running"


def test_agent_status_usage_snapshot_maps_to_thread_status_changed_with_usage():
    msg = next(
        m
        for m in FIXTURE_MESSAGES
        if m["type"] == "agent.status.updated" and "contextTokens" in (m.get("payload") or {})
    )

    event = map_session_event(msg)

    assert event.type == "thread_status_changed"
    assert event.usage == {
        "context_tokens": msg["payload"]["contextTokens"],
        "max_context_tokens": msg["payload"]["maxContextTokens"],
    }


# ----------------------------------------------------------------------
# session housekeeping + unknown types


@pytest.mark.parametrize("event_type", ["session.meta.updated", "context.spliced", "event.session.work_changed"])
def test_session_housekeeping_maps_to_session_notification(event_type):
    event = map_session_event(fixture_msg(event_type))

    assert event.type == "notification"
    assert event.display_kind == "session"
    assert event.subtype == event_type


def test_unknown_type_maps_to_unmapped_notification():
    event = map_session_event(make_msg("brand.new.event", {"foo": 1}))

    assert event.type == "notification"
    assert event.display_kind == "unmapped"
    assert event.subtype == "brand.new.event"
    assert event.data["unmapped"] is True


# ----------------------------------------------------------------------
# control messages


@pytest.mark.parametrize("event_type", ["ack", "server_hello"])
def test_fixture_control_messages_return_none(event_type):
    assert map_session_event(fixture_msg(event_type)) is None


def test_pong_returns_none():
    assert map_session_event({"type": "pong", "payload": {}}) is None
    assert map_session_event({"type": "pong"}) is None


# ----------------------------------------------------------------------
# B16: payload roundtrip


def test_fixture_events_payload_roundtrip():
    seen = 0
    for message in FIXTURE_MESSAGES:
        event = map_session_event(message)
        if event is None:
            continue
        seen += 1
        payload = event_to_payload(event)
        restored = event_from_payload(payload)
        assert event_to_payload(restored) == payload
    assert seen > 0


def test_event_payload_roundtrip_keeps_all_fields():
    event = KimiEvent(
        type="item_completed",
        thread_id="s",
        turn_id="t",
        item_id="i",
        text="done",
        raw_text="raw",
        title="cmd",
        command="ls",
        exit_code=0,
        subtype="sub",
        display_kind="command",
        phase="p",
        status="completed",
        flags=["a"],
        request_id=7,
        method="m",
        params={"k": 1},
        data={"d": 2},
        usage={"u": 3},
    )

    payload = event_to_payload(event)
    restored = event_from_payload(payload)

    assert event_to_payload(restored) == payload


def test_event_from_payload_requires_dict_and_type():
    with pytest.raises(KimiServerError):
        event_from_payload(["not", "a", "dict"])
    with pytest.raises(KimiServerError):
        event_from_payload({"text": "no type"})
