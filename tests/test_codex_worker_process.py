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
        self.steered_turns = []
        self.resumed_threads = []
        self.compacted_threads = []
        self.interrupted_turns = []
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

    def steer_turn_items(self, thread_id, turn_id, items):
        self.steered_turns.append((thread_id, turn_id, items))
        return {"turn": {"id": "turn-steered"}}

    def respond_tool_request_user_input(self, request_id, answers):
        self.replies.append((request_id, answers))

    def compact_thread(self, thread_id):
        self.compacted_threads.append(thread_id)
        return {}

    def interrupt_turn(self, thread_id, turn_id):
        self.interrupted_turns.append((thread_id, turn_id))
        return {}

    def close(self):
        self.closed = True


class RaisingTurnCodexClient(FakeCodexClient):
    def start_turn_items(self, thread_id, items, service_tier=None):
        raise RuntimeError("turn failed")


class EarlyUsageCodexClient(FakeCodexClient):
    def start_turn_items(self, thread_id, items, service_tier=None):
        self.on_event(CodexEvent(type="token_count", thread_id=thread_id, turn_id="turn-early"))
        return {"turn": {"id": "turn-early"}}


class UniqueTurnCodexClient(FakeCodexClient):
    def start_turn_items(self, thread_id, items, service_tier=None):
        self.started_turns.append((thread_id, items, service_tier))
        return {"turn": {"id": f"turn-{len(self.started_turns)}"}}


class InformationCodexClient(FakeCodexClient):
    def read_account(self, refresh_token=False):
        return {"account": {"type": "chatgpt", "id": "account-1"}}

    def read_rate_limits(self):
        return {"rateLimits": {"limitId": "codex", "secondary": {
            "windowDurationMins": 10080, "usedPercent": 25, "resetsAt": 1770000000,
        }}}


class RaisingReplyCodexClient(FakeCodexClient):
    def respond_tool_request_user_input(self, request_id, answers):
        raise RuntimeError("reply failed")


class MissingResumeCodexClient(FakeCodexClient):
    def __init__(self, on_event=None, codex_model="codex/main", message="thread not found"):
        super().__init__(on_event, codex_model)
        self.message = message

    def start_thread(self, **kwargs):
        self.started_threads.append(kwargs)
        return {"thread": {"id": "thread-recovered"}}

    def resume_thread(self, thread_id, **kwargs):
        self.resumed_threads.append((thread_id, kwargs))
        raise RuntimeError(self.message)


class NoActiveSteerCodexClient(FakeCodexClient):
    def steer_turn_items(self, thread_id, turn_id, items):
        self.steered_turns.append((thread_id, turn_id, items))
        raise RuntimeError("no active turn to steer")


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
    assert created[0].started_threads[0]["approval_policy"] == "never"
    assert created[0].started_threads[0]["sandbox"] == "danger-full-access"
    assert created[0].started_threads[0]["personality"] == "pragmatic"
    assert created[0].started_turns[0][2] == "fast"


def test_worker_events_keep_thread_scope_when_new_turn_or_model_starts():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )
    starts = [
        ("codex/main", "thread-old", 0, 0),
        ("codex/main", "thread-new", 1, 1),
        ("codex/other", "thread-new", 2, 2),
    ]
    for model, thread_id, turn_idx, generation in starts:
        runtime.handle_message(make_ui_request(
            f"req-{turn_idx}", "start_turn",
            {"chat_id": "chat-c", "model": model, "thread_id": thread_id,
             "turn_idx": turn_idx, "context_generation": generation,
             "input_items": [{"type": "text", "text": "q"}]},
        ))

    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-old"))
    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-new"))
    created[1].on_event(CodexEvent(type="token_count", thread_id="thread-new"))
    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-unknown"))
    events = [decode_worker_line(line + "\n")["payload"] for line in output.getvalue().splitlines()
              if decode_worker_line(line + "\n")["type"] == "event"]
    assert [(event.get("turn_idx"), event.get("context_generation")) for event in events] == [
        (0, 0), (1, 1), (2, 2), (None, None),
    ]


def test_worker_early_token_event_has_scope_before_thread_ack():
    output = io.StringIO()
    runtime = CodexWorkerRuntime(client_factory=lambda callback, model: EarlyUsageCodexClient(callback, model), output=output)
    runtime.handle_message(make_ui_request(
        "req-early", "start_turn",
        {"chat_id": "chat-c", "model": "codex/main", "turn_idx": 4,
         "context_generation": 7, "input_items": [{"type": "text", "text": "q"}]},
    ))
    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert [item["type"] for item in messages[:3]] == ["event", "thread_state", "turn_started_ack"]
    assert messages[0]["payload"]["turn_idx"] == 4
    assert messages[0]["payload"]["context_generation"] == 7
    assert messages[1]["payload"]["context_generation"] == 7


def test_worker_old_turn_on_same_thread_keeps_its_original_scope():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda callback, model: created.append(UniqueTurnCodexClient(callback, model)) or created[-1],
        output=output,
    )
    for idx, generation in [(0, 4), (1, 5)]:
        runtime.handle_message(make_ui_request(
            f"req-{idx}", "start_turn",
            {"chat_id": "chat-c", "model": "codex/main", "thread_id": "thread-1",
             "turn_idx": idx, "context_generation": generation,
             "input_items": [{"type": "text", "text": "q"}]},
        ))
    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1"))
    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-2"))
    events = [decode_worker_line(line + "\n")["payload"] for line in output.getvalue().splitlines()
              if decode_worker_line(line + "\n")["type"] == "event"]
    assert [(event.get("turn_idx"), event.get("context_generation")) for event in events] == [(0, 4), (1, 5)]


def test_worker_event_scopes_are_bounded_and_evicted_events_are_unscoped():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda callback, model: created.append(UniqueTurnCodexClient(callback, model)) or created[-1],
        output=output,
    )
    for idx in range(runtime.MAX_EVENT_SCOPES + 1):
        runtime.handle_message(make_ui_request(
            f"req-{idx}", "start_turn",
            {"chat_id": "chat-c", "model": "codex/main", "thread_id": f"thread-{idx}",
             "turn_idx": idx, "context_generation": idx,
             "input_items": [{"type": "text", "text": "q"}]},
        ))
    assert len(runtime._thread_turn_scopes) == runtime.MAX_EVENT_SCOPES
    assert len(runtime._turn_id_scopes) == runtime.MAX_EVENT_SCOPES
    created[0].on_event(CodexEvent(type="token_count", thread_id="thread-0", turn_id="turn-1"))
    created[0].on_event(CodexEvent(type="token_count", thread_id=f"thread-{runtime.MAX_EVENT_SCOPES}",
                                   turn_id=f"turn-{runtime.MAX_EVENT_SCOPES + 1}"))
    events = [decode_worker_line(line + "\n")["payload"] for line in output.getvalue().splitlines()
              if decode_worker_line(line + "\n")["type"] == "event"]
    assert [(event.get("turn_idx"), event.get("context_generation")) for event in events] == [
        (None, None), (runtime.MAX_EVENT_SCOPES, runtime.MAX_EVENT_SCOPES),
    ]


def test_worker_reads_chat_information_with_request_identity():
    output = io.StringIO()
    runtime = CodexWorkerRuntime(client_factory=lambda callback, model: InformationCodexClient(callback, model), output=output)
    runtime.handle_message(make_ui_request(
        "req-info", "read_chat_information", {"chat_id": "chat-1", "model": "codex/main",
                                               "identity": ["chat-1", "codex/main", "thread-1", "account-1"],
                                               "generation": 3},
    ))
    message = decode_worker_line(output.getvalue())
    assert message["type"] == "chat_information"
    assert message["id"] == "req-info"
    assert message["payload"]["identity"] == ["chat-1", "codex/main", "thread-1", "account-1"]
    assert message["payload"]["generation"] == 3
    assert message["payload"]["account"]["account"]["id"] == "account-1"
    assert message["payload"]["rate_limits"]["rateLimits"]["secondary"]["usedPercent"] == 25


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


def test_worker_runtime_reply_user_input_failure_emits_scoped_error_not_fatal():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(RaisingReplyCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )
    runtime.handle_message(
        make_ui_request(
            "req-start",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 4,
                "question": "闂",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "闂"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    runtime.handle_message(
        make_ui_request(
            "req-reply",
            "reply_user_input",
            {"chat_id": "chat-c", "request_id": "ask-1", "answers": {"reply": ["ok"]}},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    scoped_errors = [
        item
        for item in messages
        if item["type"] == "error"
        and item.get("id") == "req-reply"
        and item["payload"]["chat_id"] == "chat-c"
        and item["payload"]["turn_idx"] == 4
        and item["payload"]["model"] == "model-a"
        and "reply failed" in item["payload"]["message"]
    ]
    assert scoped_errors
    assert not any(item["type"] == "fatal" for item in messages)


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


def test_worker_runtime_resume_thread_not_found_starts_new_thread_and_emits_state():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(
            MissingResumeCodexClient(on_event, codex_model, "thread not found: thread-stale")
        )
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-recover",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 2,
                "question": "第三轮问题",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "thread-stale",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "第三轮问题"}],
                "attachments": [],
                "service_tier": "",
                "history_turns": [{"question": "第一轮问题", "answer_md": "第一轮回答"}],
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].resumed_threads[0][0] == "thread-stale"
    assert created[0].started_threads
    assert created[0].started_turns[0][0] == "thread-recovered"
    assert "第一轮问题" in created[0].started_turns[0][1][0]["text"]
    assert "第三轮问题" in created[0].started_turns[0][1][0]["text"]
    assert any(
        item["type"] == "thread_state"
        and item["payload"]["thread_id"] == "thread-recovered"
        and item["payload"]["active"] is True
        for item in messages
    )


def test_worker_runtime_resume_no_rollout_found_starts_new_thread():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(
            MissingResumeCodexClient(on_event, codex_model, "no rollout found for thread id thread-stale")
        )
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-rollout",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 1,
                "question": "继续",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "thread-stale",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "继续"}],
                "attachments": [],
                "service_tier": "fast",
                "history_turns": [],
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].started_threads[0]["service_tier"] == "fast"
    assert created[0].started_turns[0][0] == "thread-recovered"
    assert any(item["type"] == "thread_state" and item["payload"]["thread_id"] == "thread-recovered" for item in messages)


def test_worker_runtime_start_turn_steers_existing_active_turn():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-steer",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "继续",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "thread-existing",
                "turn_id": "turn-active",
                "input_items": [{"type": "text", "text": "继续"}],
                "attachments": [],
                "service_tier": "",
                "should_steer": True,
                "history_turns": [],
            },
        )
    )

    assert created[0].steered_turns == [("thread-existing", "turn-active", [{"type": "text", "text": "继续"}])]
    assert created[0].started_turns == []


def test_worker_runtime_no_active_steer_falls_back_to_start_turn_items():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(NoActiveSteerCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-steer-fallback",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "继续",
                "model": "model-a",
                "cwd": "c:/code/sj",
                "thread_id": "thread-existing",
                "turn_id": "turn-active",
                "input_items": [{"type": "text", "text": "继续"}],
                "attachments": [],
                "service_tier": "fast",
                "should_steer": True,
                "history_turns": [],
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].steered_turns == [("thread-existing", "turn-active", [{"type": "text", "text": "继续"}])]
    assert created[0].started_turns == [("thread-existing", [{"type": "text", "text": "继续"}], "fast")]
    assert any(item["type"] == "turn_started_ack" for item in messages)


def test_worker_runtime_compact_thread_calls_client_without_protocol_error():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-compact",
            "compact_thread",
            {"chat_id": "chat-c", "model": "model-a", "thread_id": "thread-1"},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].compacted_threads == ["thread-1"]
    assert not any(item["type"] == "protocol_error" for item in messages)
    assert not any(item["type"] == "error" for item in messages)


def test_worker_runtime_cancel_turn_calls_interrupt_without_protocol_error():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model))
        or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-cancel",
            "cancel_turn",
            {"chat_id": "chat-c", "model": "model-a", "thread_id": "thread-1", "turn_id": "turn-1"},
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].interrupted_turns == [("thread-1", "turn-1")]
    assert not any(item["type"] == "protocol_error" for item in messages)
    assert not any(item["type"] == "error" for item in messages)


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
