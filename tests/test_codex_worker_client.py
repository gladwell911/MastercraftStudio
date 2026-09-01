import io
import json
import sys
import threading
import time

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


class ReplacingStdout:
    def __init__(self, replace):
        self.replace = replace

    def __iter__(self):
        self.replace()
        return iter(())


class BrokenPipeStdin(io.StringIO):
    def write(self, _text):
        raise BrokenPipeError("simulated closed pipe")


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


def test_worker_client_uses_console_worker_executable_when_running_from_frozen_executable(monkeypatch):
    proc = FakeProcess()
    commands = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    client = CodexWorkerClient(process_factory=lambda args: commands.append(args) or proc, start_reader_threads=False)

    client.start()

    assert commands == [[str(__import__("pathlib").Path(sys.executable).with_name("mc_worker.exe"))]]


def test_frozen_worker_ready_timeout_reaps_failed_process(monkeypatch):
    proc = FakeProcess()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    client = CodexWorkerClient(process_factory=lambda _args: proc, start_reader_threads=True)
    monkeypatch.setattr(client._ready_event, "wait", lambda timeout: False)

    try:
        client.start()
    except RuntimeError as exc:
        assert "did not become ready" in str(exc)
    else:
        raise AssertionError("a frozen worker without a ready handshake must fail")

    assert proc.terminated is True
    assert client.process is None


def test_worker_client_restarts_after_broken_pipe_without_resending_uncertain_message():
    failed = FakeProcess()
    failed.stdin = BrokenPipeStdin()
    replacement = FakeProcess()
    processes = iter((failed, replacement))
    client = CodexWorkerClient(process_factory=lambda _args: next(processes), start_reader_threads=False)
    client.start()

    try:
        client.start_turn(chat_id="chat-c")
    except RuntimeError as exc:
        assert "restarted; please retry" in str(exc)
    else:
        raise AssertionError("a broken write must not resend a potentially delivered turn")

    assert replacement.stdin.getvalue() == ""
    assert client.process is replacement


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


def test_worker_client_compact_thread_writes_ipc_message():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.compact_thread("thread-1", chat_id="chat-c", model="codex/main")

    message = json.loads(proc.stdin.getvalue())
    assert message["type"] == "compact_thread"
    assert message["payload"] == {"chat_id": "chat-c", "model": "codex/main", "thread_id": "thread-1"}


def test_worker_client_interrupt_turn_writes_ipc_message():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.interrupt_turn("thread-1", "turn-1", chat_id="chat-c", model="codex/main")

    message = json.loads(proc.stdin.getvalue())
    assert message["type"] == "cancel_turn"
    assert message["payload"] == {
        "chat_id": "chat-c",
        "model": "codex/main",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
    }


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
    assert event_texts == ["".join(f"delta-{idx}" for idx in range(20)), "step 1", "step 2"]
    assert event_texts[0].index("delta-0") < event_texts[0].index("delta-19")


def test_worker_client_compacts_deltas_by_event_turn_id_variants():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    for text, event_extra in (
        ("turn-a-old", {"turn_id": "turn-a"}),
        ("turn-a-new", {"turn_id": "turn-a"}),
        ("turn-b-old", {"turnId": "turn-b"}),
        ("turn-b-new", {"turnId": "turn-b"}),
        ("turn-c-old", {"data": {"turn_id": "turn-c"}}),
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
    assert "turn-a-oldturn-a-new" in event_texts
    assert "turn-b-oldturn-b-new" in event_texts
    assert "turn-c-oldturn-c-new" in event_texts
    assert "turn-a-old" not in event_texts
    assert "turn-b-old" not in event_texts
    assert "turn-c-old" not in event_texts


def test_worker_client_delta_fallback_preserves_zero_turn_idx():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)
    zero_turn_message = {
        "type": "event",
        "payload": {
            "chat_id": "chat-c",
            "turn_idx": 0,
            "event": {
                "type": "agent_message_delta",
                "text": "turn-idx-zero",
            },
        },
    }

    assert client._delta_key(zero_turn_message) == ("chat-c", "0")

    for message in (
        zero_turn_message,
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 1,
                "event": {
                    "type": "agent_message_delta",
                    "text": "turn-idx-one",
                },
            },
        },
    ):
        client._enqueue_worker_message(message)

    drained = client.drain_pending_messages(limit=10)

    event_texts = [item["payload"]["event"]["text"] for item in drained]
    assert "turn-idx-zero" in event_texts
    assert "turn-idx-one" in event_texts


def test_worker_client_merges_delta_fragments_for_same_chat_turn_and_item():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    for text, raw_text in (("hello ", "raw-a"), ("world", "raw-b")):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {
                        "type": "agent_message_delta",
                        "text": text,
                        "raw_text": raw_text,
                        "turn_id": "turn-1",
                        "item_id": "item-1",
                        "phase": "answer",
                    },
                },
            }
        )

    drained = client.drain_pending_messages(limit=10)

    assert len(drained) == 1
    event = drained[0]["payload"]["event"]
    assert event["text"] == "hello world"
    assert event["raw_text"] == "raw-araw-b"
    assert event["item_id"] == "item-1"


def test_worker_client_contiguous_deltas_with_same_key_merge_one_queue_entry():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    for text in ("A", "B", "C"):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {
                        "type": "agent_message_delta",
                        "text": text,
                        "turn_id": "turn-1",
                        "item_id": "item-1",
                    },
                },
            }
        )

    drained = client.drain_pending_messages(limit=10)

    assert [item["payload"]["event"]["text"] for item in drained] == ["ABC"]


def test_worker_client_interleaved_delta_keeps_arrival_order():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {
                    "type": "agent_message_delta",
                    "text": "A",
                    "turn_id": "turn-1",
                    "item_id": "item-1",
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
                "event": {"type": "item_completed", "text": "done"},
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
                    "type": "agent_message_delta",
                    "text": "B",
                    "turn_id": "turn-1",
                    "item_id": "item-1",
                },
            },
        }
    )

    drained = client.drain_pending_messages(limit=10)

    assert [item["payload"]["event"]["text"] for item in drained] == ["A", "done", "B"]


def test_worker_client_does_not_merge_different_item_ids_in_same_turn():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    for item_id, text in (("item-a", "delta-a"), ("item-b", "delta-b")):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {
                        "type": "agent_message_delta",
                        "text": text,
                        "turn_id": "turn-1",
                        "item_id": item_id,
                    },
                },
            }
        )

    drained = client.drain_pending_messages(limit=10)

    assert [item["payload"]["event"]["text"] for item in drained] == ["delta-a", "delta-b"]


def test_worker_client_preserves_queue_order_between_rows_and_delta_entries():
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), start_reader_threads=False)

    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_started", "text": "step 1"},
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
                    "type": "agent_message_delta",
                    "text": "answer",
                    "turn_id": "turn-1",
                    "item_id": "answer-1",
                },
            },
        }
    )

    drained = client.drain_pending_messages(limit=10)

    assert [item["payload"]["event"]["text"] for item in drained] == ["step 1", "answer"]


def test_worker_client_on_message_receives_pending_notifications_only():
    observed = []
    client = CodexWorkerClient(
        process_factory=lambda args: FakeProcess(), on_message=observed.append, start_reader_threads=False
    )

    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_started", "text": "step 1"},
            },
        }
    )
    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "agent_message_delta", "text": "delta", "turn_id": "turn-1"},
            },
        }
    )

    assert observed == [{"type": "messages_pending"}]


def test_worker_client_on_message_coalesces_until_drain():
    observed = []
    client = CodexWorkerClient(
        process_factory=lambda args: FakeProcess(), on_message=observed.append, start_reader_threads=False
    )

    for text in ("A", "B", "C"):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {"type": "agent_message_delta", "text": text, "turn_id": "turn-1"},
                },
            }
        )

    assert observed == [{"type": "messages_pending"}]

    assert client.drain_pending_messages(limit=1)

    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_completed", "text": "done"},
            },
        }
    )

    assert observed == [{"type": "messages_pending"}, {"type": "messages_pending"}]


def test_worker_client_partial_drain_notifies_for_remaining_messages():
    observed = []
    client = CodexWorkerClient(
        process_factory=lambda args: FakeProcess(), on_message=observed.append, start_reader_threads=False
    )

    for text in ("step 1", "step 2", "step 3"):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {"type": "item_started", "text": text},
                },
            }
        )

    assert observed == [{"type": "messages_pending"}]

    drained = client.drain_pending_messages(limit=1)

    assert [item["payload"]["event"]["text"] for item in drained] == ["step 1"]
    assert observed == [{"type": "messages_pending"}, {"type": "messages_pending"}]

    assert [item["payload"]["event"]["text"] for item in client.drain_pending_messages(limit=10)] == [
        "step 2",
        "step 3",
    ]

    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_completed", "text": "done"},
            },
        }
    )

    assert observed == [
        {"type": "messages_pending"},
        {"type": "messages_pending"},
        {"type": "messages_pending"},
    ]


def test_worker_client_queue_limit_counts_delta_entries_and_normal_entries():
    client = CodexWorkerClient(
        process_factory=lambda args: FakeProcess(), start_reader_threads=False, queue_limit=2
    )

    for event in (
        {"type": "item_started", "text": "step 1"},
        {"type": "agent_message_delta", "text": "answer", "turn_id": "turn-1", "item_id": "answer-1"},
        {"type": "item_completed", "text": "step 2"},
    ):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": event,
                },
            }
        )

    drained = client.drain_pending_messages(limit=10)

    assert [item["payload"]["event"]["text"] for item in drained] == ["answer", "step 2"]


def test_worker_client_stdout_loop_reports_captured_process_returncode():
    observed = []
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), on_exit=observed.append)
    first_proc = FakeProcess()
    replacement_proc = FakeProcess()
    first_proc.returncode = 11
    replacement_proc.returncode = 22
    first_proc.stdout = ReplacingStdout(lambda: setattr(client, "process", replacement_proc))
    client.process = first_proc

    client._stdout_loop()

    assert observed == [11]


def test_worker_client_concurrent_start_uses_process_factory_once_for_live_process():
    proc = FakeProcess()
    calls = []

    def process_factory(args):
        calls.append(args)
        time.sleep(0.05)
        return proc

    client = CodexWorkerClient(process_factory=process_factory, start_reader_threads=False)
    threads = [threading.Thread(target=client.start) for _ in range(5)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert len(calls) == 1
    assert client.process is proc


def test_worker_client_refuses_send_after_close_but_close_sends_shutdown():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.close()

    written_after_close = proc.stdin.getvalue()
    assert '"type":"shutdown"' in written_after_close

    try:
        client.start_turn(chat_id="chat-c")
    except RuntimeError:
        pass
    else:
        raise AssertionError("start_turn should refuse writes after close")

    assert proc.stdin.getvalue() == written_after_close
