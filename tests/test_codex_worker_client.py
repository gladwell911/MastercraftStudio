import io
import json

from codex_worker_client import CodexWorkerClient


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class GracefulExitAfterShutdownProcess(FakeProcess):
    def __init__(self):
        super().__init__()
        self.wait_calls = []

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return self.returncode


def test_worker_client_send_start_turn_writes_json_line():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.start_turn(
        chat_id="chat-c",
        turn_idx=0,
        question="\u95ee\u9898",
        model="codex/main",
        cwd="c:/code/sj",
        thread_id="",
        turn_id="",
        input_items=[{"type": "text", "text": "\u95ee\u9898"}],
        attachments=[],
        service_tier="fast",
    )

    written = proc.stdin.getvalue()
    assert '"type":"start_turn"' in written
    assert '"chat_id":"chat-c"' in written
    assert '"service_tier":"fast"' in written


def test_worker_client_reply_user_input_writes_ipc_message():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.reply_user_input("chat-c", "request-7", {"reply": ["ok"]})

    written = proc.stdin.getvalue()
    message = json.loads(written)
    assert '"type":"reply_user_input"' in written
    assert '"request_id":"request-7"' in written
    assert message["payload"]["answers"] == {"reply": ["ok"]}


def test_worker_client_close_terminates_process():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.close()

    assert proc.terminated is True


def test_worker_client_close_waits_for_graceful_shutdown_before_terminate():
    proc = GracefulExitAfterShutdownProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.close()

    written = proc.stdin.getvalue()
    assert '"type":"shutdown"' in written
    assert proc.wait_calls == [1]
    assert proc.terminated is False
    assert proc.killed is False


def test_worker_client_enqueue_compacts_answer_delta_but_keeps_execution_rows():
    observed = []
    client = CodexWorkerClient(
        process_factory=lambda args: FakeProcess(), on_message=observed.append, start_reader_threads=False
    )

    for idx in range(20):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {
                        "type": "agent_message_delta",
                        "text": f"delta-{idx}",
                        "data": {"turn_id": "turn-1"},
                    },
                },
            }
        )
    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {
                    "type": "item_started",
                    "text": "step 1",
                    "data": {"turn_id": "turn-1", "step_seq": 1},
                },
            },
        }
    )
    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {
                    "type": "item_completed",
                    "text": "step 2",
                    "data": {"turn_id": "turn-1", "step_seq": 2},
                },
            },
        }
    )

    drained = client.drain_pending_messages(limit=10)

    event_texts = [item["payload"]["event"].get("text") for item in drained if item["type"] == "event"]
    assert "delta-19" in event_texts
    assert "delta-0" not in event_texts
    assert "step 1" in event_texts
    assert "step 2" in event_texts


def test_worker_client_compacts_deltas_by_event_turn_id_variants():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    for text, event_extra in (
        ("turn-a-old", {"turn_id": "turn-a"}),
        ("turn-b-old", {"turnId": "turn-b"}),
        ("turn-c-old", {"data": {"turn_id": "turn-c"}}),
        ("turn-a-new", {"turn_id": "turn-a"}),
        ("turn-b-new", {"turnId": "turn-b"}),
        ("turn-c-new", {"data": {"turn_id": "turn-c"}}),
    ):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {
                        "type": "agent_message_delta",
                        "text": text,
                        **event_extra,
                    },
                },
            }
        )

    drained = client.drain_pending_messages(limit=10)

    event_texts = [item["payload"]["event"]["text"] for item in drained]
    assert "turn-a-new" in event_texts
    assert "turn-b-new" in event_texts
    assert "turn-c-new" in event_texts
    assert "turn-a-old" not in event_texts
    assert "turn-b-old" not in event_texts
    assert "turn-c-old" not in event_texts
