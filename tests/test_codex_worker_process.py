import io

from codex_worker_process import CodexWorkerRuntime
from codex_worker_protocol import decode_worker_line, encode_worker_message, make_ui_request


class FakeCodexClient:
    def __init__(self, on_event=None, codex_model="codex/main"):
        self.on_event = on_event
        self.codex_model = codex_model
        self.started_threads = []
        self.started_turns = []
        self.replies = []
        self.closed = False

    def start_thread(self, **kwargs):
        self.started_threads.append(kwargs)
        return {"thread": {"id": "thread-1"}}

    def start_turn_items(self, thread_id, items, service_tier=None):
        self.started_turns.append((thread_id, items, service_tier))
        return {"turn": {"id": "turn-1"}}

    def respond_tool_request_user_input(self, request_id, answers):
        self.replies.append((request_id, answers))

    def close(self):
        self.closed = True


def test_worker_runtime_start_turn_emits_thread_state_and_turn_finished():
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
    assert any(item["type"] == "thread_state" and item["payload"]["chat_id"] == "chat-c" for item in messages)
    assert any(item["type"] == "turn_finished" and item["payload"]["turn_id"] == "turn-1" for item in messages)
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


def test_worker_process_source_does_not_import_wx():
    import pathlib

    source = pathlib.Path("codex_worker_process.py").read_text(encoding="utf-8")
    assert "import wx" not in source
    assert "from wx" not in source
