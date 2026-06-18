import io
import threading
import time

from codex_client import CodexEvent
from codex_worker_process import CodexWorkerRuntime
from codex_worker_protocol import decode_worker_line, encode_worker_message, make_ui_request, validate_chat_scoped_message


class FakeCodexClient:
    def __init__(self, on_event=None, codex_model="codex/main"):
        self.on_event = on_event
        self.codex_model = codex_model
        self.started_threads = []
        self.started_turns = []
        self.resumed_threads = []
        self.replies = []
        self.closed = False

    def start_thread(self, **kwargs):
        self.started_threads.append(kwargs)
        return {"thread": {"id": "thread-1"}}

    def resume_thread(self, thread_id, **kwargs):
        self.resumed_threads.append((thread_id, kwargs))
        return {"thread": {"id": thread_id}}

    def start_turn_items(self, thread_id, items, service_tier=None):
        self.started_turns.append((thread_id, items, service_tier))
        return {"turn": {"id": "turn-1"}}

    def respond_tool_request_user_input(self, request_id, answers):
        self.replies.append((request_id, answers))

    def close(self):
        self.closed = True


class RaisingTurnCodexClient(FakeCodexClient):
    def start_turn_items(self, thread_id, items, service_tier=None):
        raise RuntimeError("turn failed")


class OverlapDetectingOutput:
    def __init__(self):
        self.lines = []
        self.overlapped = False
        self._active_writes = 0
        self._first_write_entered = threading.Event()
        self._guard = threading.Lock()

    def write(self, text):
        with self._guard:
            self._active_writes += 1
            if self._active_writes > 1:
                self.overlapped = True
            self._first_write_entered.set()
        time.sleep(0.02)
        self.lines.append(text)
        with self._guard:
            self._active_writes -= 1

    def flush(self):
        pass


def test_worker_runtime_start_turn_emits_active_thread_state_and_turn_started_ack():
    output = io.StringIO()
    created = []

    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-1",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "codex/main",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "fast",
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert any(
        item["type"] == "thread_state"
        and item["payload"]["chat_id"] == "chat-c"
        and item["payload"]["active"] is True
        for item in messages
    )
    assert any(item["type"] == "turn_started_ack" and item["payload"]["turn_id"] == "turn-1" for item in messages)
    assert not any(item["type"] == "turn_finished" for item in messages)
    assert created[0].started_threads[0]["cwd"] == "c:/code/sj"
    assert created[0].started_turns[0][2] == "fast"


def test_worker_runtime_reply_user_input_routes_to_matching_chat_client():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )
    runtime.handle_message(
        make_ui_request(
            "req-1",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "codex/main",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    runtime.handle_message(
        make_ui_request(
            "req-2",
            "reply_user_input",
            {"chat_id": "chat-c", "request_id": "ask-1", "answers": {"reply": ["ok"]}},
        )
    )

    assert created[0].replies == [("ask-1", {"reply": ["ok"]})]


def test_worker_runtime_reply_user_input_uses_request_id_mapping_before_latest_chat_model():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    for idx, model in enumerate(("model-a", "model-b")):
        runtime.handle_message(
            make_ui_request(
                f"req-start-{idx}",
                "start_turn",
                {
                    "chat_id": "chat-c",
                    "turn_idx": idx,
                    "question": "问题",
                    "model": model,
                    "cwd": "c:/code/sj",
                    "thread_id": "",
                    "turn_id": "",
                    "input_items": [{"type": "text", "text": "问题"}],
                    "attachments": [],
                    "service_tier": "",
                },
            )
        )

    created[0].on_event(CodexEvent(type="server_request", request_id="ask-a", method="item/tool/requestUserInput"))

    runtime.handle_message(
        make_ui_request(
            "req-reply",
            "reply_user_input",
            {"chat_id": "chat-c", "request_id": "ask-a", "answers": {"reply": ["ok"]}},
        )
    )

    assert created[0].codex_model == "model-a"
    assert created[1].codex_model == "model-b"
    assert created[0].replies == [("ask-a", {"reply": ["ok"]})]
    assert created[1].replies == []


def test_worker_runtime_reply_user_input_with_unknown_request_id_errors_when_chat_has_multiple_clients():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    for idx, model in enumerate(("model-a", "model-b")):
        runtime.handle_message(
            make_ui_request(
                f"req-start-{idx}",
                "start_turn",
                {
                    "chat_id": "chat-c",
                    "turn_idx": idx,
                    "question": "问题",
                    "model": model,
                    "cwd": "c:/code/sj",
                    "thread_id": "",
                    "turn_id": "",
                    "input_items": [{"type": "text", "text": "问题"}],
                    "attachments": [],
                    "service_tier": "",
                },
            )
        )

    runtime.handle_message(
        make_ui_request(
            "req-reply",
            "reply_user_input",
            {"chat_id": "chat-c", "request_id": "unknown", "answers": {"reply": ["ok"]}},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].replies == []
    assert created[1].replies == []
    assert any(item["type"] == "error" and item.get("id") == "req-reply" for item in messages)


def test_worker_runtime_pending_input_mapping_is_scoped_by_chat_id():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    for chat_id, model in (("chat-a", "model-a"), ("chat-b", "model-b")):
        runtime.handle_message(
            make_ui_request(
                f"req-start-{chat_id}",
                "start_turn",
                {
                    "chat_id": chat_id,
                    "turn_idx": 0,
                    "question": "问题",
                    "model": model,
                    "cwd": "c:/code/sj",
                    "thread_id": "",
                    "turn_id": "",
                    "input_items": [{"type": "text", "text": "问题"}],
                    "attachments": [],
                    "service_tier": "",
                },
            )
        )

    created[0].on_event(CodexEvent(type="server_request", request_id="same-id", method="item/tool/requestUserInput"))
    created[1].on_event(CodexEvent(type="server_request", request_id="same-id", method="item/tool/requestUserInput"))

    runtime.handle_message(
        make_ui_request(
            "req-reply-a",
            "reply_user_input",
            {"chat_id": "chat-a", "request_id": "same-id", "answers": {"reply": ["a"]}},
        )
    )
    runtime.handle_message(
        make_ui_request(
            "req-reply-b",
            "reply_user_input",
            {"chat_id": "chat-b", "request_id": "same-id", "answers": {"reply": ["b"]}},
        )
    )

    assert created[0].replies == [("same-id", {"reply": ["a"]})]
    assert created[1].replies == [("same-id", {"reply": ["b"]})]


def test_worker_runtime_reply_user_input_without_chat_id_errors_even_when_request_id_is_known():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )
    runtime.handle_message(
        make_ui_request(
            "req-start",
            "start_turn",
            {
                "chat_id": "chat-a",
                "turn_idx": 0,
                "question": "问题",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )
    created[0].on_event(CodexEvent(type="server_request", request_id="ask-a", method="item/tool/requestUserInput"))

    runtime.handle_message(
        make_ui_request(
            "req-reply",
            "reply_user_input",
            {"request_id": "ask-a", "answers": {"reply": ["ok"]}},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].replies == []
    protocol_errors = [item for item in messages if item["type"] == "protocol_error" and item.get("id") == "req-reply"]
    assert protocol_errors
    validate_chat_scoped_message(protocol_errors[0])


def test_worker_runtime_pending_input_mapping_marks_same_chat_request_id_collision_ambiguous():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    for idx, model in enumerate(("model-a", "model-b")):
        runtime.handle_message(
            make_ui_request(
                f"req-start-{idx}",
                "start_turn",
                {
                    "chat_id": "chat-c",
                    "turn_idx": idx,
                    "question": "问题",
                    "model": model,
                    "cwd": "c:/code/sj",
                    "thread_id": "",
                    "turn_id": "",
                    "input_items": [{"type": "text", "text": "问题"}],
                    "attachments": [],
                    "service_tier": "",
                },
            )
        )

    created[0].on_event(CodexEvent(type="server_request", request_id="same-id", method="item/tool/requestUserInput"))
    created[1].on_event(CodexEvent(type="server_request", request_id="same-id", method="item/tool/requestUserInput"))

    runtime.handle_message(
        make_ui_request(
            "req-reply",
            "reply_user_input",
            {"chat_id": "chat-c", "request_id": "same-id", "answers": {"reply": ["ok"]}},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].replies == []
    assert created[1].replies == []
    assert any(item["type"] == "error" and item.get("id") == "req-reply" for item in messages)


def test_worker_runtime_start_turn_with_empty_chat_id_emits_error_without_creating_client():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-empty-chat",
            "start_turn",
            {
                "chat_id": "",
                "turn_idx": 3,
                "question": "问题",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created == []
    protocol_errors = [
        item for item in messages if item["type"] == "protocol_error" and item.get("id") == "req-empty-chat"
    ]
    assert protocol_errors
    validate_chat_scoped_message(protocol_errors[0])


def test_worker_runtime_start_turn_failure_emits_scoped_error():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(RaisingTurnCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-fail",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 7,
                "question": "问题",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    scoped_errors = [
        item
        for item in messages
        if item["type"] == "error"
        and item.get("id") == "req-fail"
        and item["payload"]["chat_id"] == "chat-c"
        and item["payload"]["turn_idx"] == 7
        and item["payload"]["model"] == "model-a"
    ]
    assert scoped_errors
    validate_chat_scoped_message(scoped_errors[0])
    assert any(
        item["type"] == "error"
        and item.get("id") == "req-fail"
        and item["payload"]["chat_id"] == "chat-c"
        and item["payload"]["turn_idx"] == 7
        and item["payload"]["model"] == "model-a"
        for item in messages
    )
    assert not any(item["type"] == "thread_state" and item["payload"].get("active") is True for item in messages)


def test_worker_runtime_start_turn_resumes_existing_thread_before_turn_items():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-resume",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "thread-existing",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "fast",
            },
        )
    )

    assert created[0].started_threads == []
    assert created[0].resumed_threads == [
        (
            "thread-existing",
            {
                "approval_policy": "never",
                "sandbox": "danger-full-access",
                "personality": "pragmatic",
                "cwd": "c:/code/sj",
                "service_tier": "fast",
            },
        )
    ]
    assert created[0].started_turns[0][0] == "thread-existing"


def test_worker_runtime_emit_serializes_concurrent_writes():
    output = OverlapDetectingOutput()
    runtime = CodexWorkerRuntime(output=output)

    first = threading.Thread(target=lambda: runtime.emit("pong", {"seq": 1}))
    second = threading.Thread(target=lambda: runtime.emit("pong", {"seq": 2}))

    first.start()
    assert output._first_write_entered.wait(timeout=1)
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert output.overlapped is False
    assert [decode_worker_line(line)["type"] for line in output.lines] == ["pong", "pong"]


def test_worker_runtime_unsupported_message_type_emits_valid_protocol_error():
    output = io.StringIO()
    runtime = CodexWorkerRuntime(output=output)

    runtime.handle_message(make_ui_request("req-unsupported", "unknown_command", {}))

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert len(messages) == 1
    assert messages[0]["type"] == "protocol_error"
    assert messages[0]["id"] == "req-unsupported"
    validate_chat_scoped_message(messages[0])


def test_worker_process_source_does_not_import_wx():
    import pathlib

    source = pathlib.Path("codex_worker_process.py").read_text(encoding="utf-8")
    assert "import wx" not in source
    assert "from wx" not in source
