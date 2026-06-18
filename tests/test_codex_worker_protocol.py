import json

import pytest

from codex_client import CodexEvent
from codex_worker_protocol import (
    CodexWorkerProtocolError,
    decode_worker_line,
    encode_worker_message,
    event_from_payload,
    event_to_payload,
    make_ui_request,
    make_worker_event,
    validate_chat_scoped_message,
)


def test_encode_decode_roundtrip_preserves_ascii_and_chinese_text():
    message = make_ui_request(
        "req-1",
        "start_turn",
        {
            "chat_id": "chat-c",
            "turn_idx": 2,
            "question": "继续分析这个问题",
            "model": "codex/main",
        },
    )

    line = encode_worker_message(message)
    assert line.endswith("\n")
    assert "\\u7ee7" not in line
    decoded = decode_worker_line(line)

    assert decoded == message


def test_decode_rejects_invalid_json_line():
    with pytest.raises(CodexWorkerProtocolError):
        decode_worker_line("{not json}\n")


def test_chat_scoped_message_requires_chat_id():
    message = make_worker_event(
        "event",
        {
            "turn_idx": 0,
            "event": {"type": "turn_completed", "text": "done"},
        },
    )

    with pytest.raises(CodexWorkerProtocolError):
        validate_chat_scoped_message(message)


def test_codex_event_payload_roundtrip():
    event = CodexEvent(
        type="item_completed",
        text="执行完成",
        raw_text="raw text",
        subtype="command",
        phase="final_answer",
        status="completed",
        request_id=42,
        method="item/tool/requestUserInput",
        params={"x": 1},
        data={"thread_id": "thread-1", "turn_id": "turn-1"},
    )

    payload = event_to_payload(event)
    restored = event_from_payload(payload)

    assert restored.type == "item_completed"
    assert restored.text == "执行完成"
    assert restored.raw_text == "raw text"
    assert restored.subtype == "command"
    assert restored.phase == "final_answer"
    assert restored.status == "completed"
    assert restored.request_id == 42
    assert restored.method == "item/tool/requestUserInput"
    assert restored.params == {"x": 1}
    assert restored.data == {"thread_id": "thread-1", "turn_id": "turn-1"}
