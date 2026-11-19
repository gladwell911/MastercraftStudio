import main
import pytest
import wx
from main_test_support import (
    _prepare_switched_chat_final,
    _switched_chat_final_event,
    assert_request_metadata,
)
from types import SimpleNamespace


def test_codex_submit_sets_resume_recovery_mode(frame, monkeypatch):
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(main.time, "time", lambda: 2345.5)

    frame.active_codex_thread_id = "11111111-1111-1111-1111-111111111111"
    frame.active_codex_turn_id = "22222222-2222-2222-2222-222222222222"
    frame.active_codex_turn_active = False

    ok, message = frame._submit_question("Codex 续问", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_CODEX_MODEL,
        question="Codex 续问",
        recoverable=True,
        mode="resume",
        resume_token={
            "thread_id": "11111111-1111-1111-1111-111111111111",
            "turn_id": "22222222-2222-2222-2222-222222222222",
        },
        attempt_count=1,
    )
    assert turn["request_resume_token"] == {
        "thread_id": "11111111-1111-1111-1111-111111111111",
        "turn_id": "22222222-2222-2222-2222-222222222222",
    }


def test_codex_worker_uses_chat_bound_client_for_current_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A 正在执行",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
        }
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天 A",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: "C:\\code\\sj\\mc")
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda func, *args, **kwargs: func(*args, **kwargs))

    bound_calls = []
    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda *_args, **_kwargs: pytest.fail("current chat turns need a chat-bound Codex client"))
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": bound_calls.append((chat_id, model)) or _Client())

    frame._run_codex_turn_worker("chat-a", 0, "A 正在执行", main.DEFAULT_CODEX_MODEL)

    assert bound_calls == [("chat-a", "")]
    assert sent
    assert sent[0]["chat_id"] == "chat-a"
    assert sent[0]["turn_idx"] == 0
    assert sent[0]["question"] == "A 正在执行"
    assert sent[0]["model"] == main.DEFAULT_CODEX_MODEL
    assert sent[0]["cwd"] == "C:\\code\\sj\\mc"
    assert sent[0]["thread_id"] == ""
    assert sent[0]["turn_id"] == ""
    assert sent[0]["input_items"][0]["type"] == "text"
    assert sent[0]["input_items"][0]["text"] == "A 正在执行"
    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0].get("request_error", "") == ""


def test_codex_request_user_input_replies_through_matching_chat_worker(frame, monkeypatch):
    calls = []

    class _FakeWorkerClient:
        def __init__(self, chat_id):
            self.chat_id = chat_id

        def start(self):
            calls.append(("start", self.chat_id))

        def reply_user_input(self, chat_id, request_id, answers):
            calls.append(("reply", self.chat_id, chat_id, request_id, answers))

    class _FakeDialog:
        def __init__(self, parent, questions):
            self.parent = parent
            self.questions = questions

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            calls.append(("destroy",))

        def get_answers(self):
            return {"reply": ["ok"]}

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda *_args, **_kwargs: pytest.fail("reply must use chat-bound worker client"))
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _FakeWorkerClient(chat_id))
    monkeypatch.setattr(main, "CodexUserInputDialog", _FakeDialog)

    frame._handle_codex_request_dialog(
        {
            "chat_id": "chat-c",
            "model": main.DEFAULT_CODEX_MODEL,
            "method": "item/tool/requestUserInput",
            "request_id": "req-1",
            "params": {"questions": [{"id": "reply"}]},
        }
    )

    assert ("start", "chat-c") in calls
    assert ("reply", "chat-c", "chat-c", "req-1", {"reply": ["ok"]}) in calls


def test_codex_worker_event_bridge_ignores_events_after_wx_app_gone(frame, monkeypatch):
    seen = {"called": False}

    monkeypatch.setattr(main.wx, "GetApp", lambda: None)

    def _call_after(*_args, **_kwargs):
        raise AssertionError("CallAfter should not run without wx.App")

    monkeypatch.setattr(main.wx, "CallAfter", _call_after)
    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda *_args, **_kwargs: seen.__setitem__("called", True))

    frame._on_codex_worker_message(
        "chat-a",
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-a",
                "event": {"type": "stderr", "text": "late event"},
            },
        },
    )

    assert seen["called"] is False


def test_ui_get_or_create_codex_client_returns_worker_client(frame, monkeypatch):
    created = []

    class FakeWorkerClient:
        codex_model = main.DEFAULT_CODEX_MODEL

        def __init__(self, *args, **kwargs):
            created.append(kwargs)
            self.closed = False

        def start(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(main, "CodexWorkerClient", FakeWorkerClient)
    monkeypatch.setattr(
        main,
        "CodexAppServerClient",
        lambda *a, **k: pytest.fail("UI process must not instantiate CodexAppServerClient"),
        raising=False,
    )

    client = frame._get_or_create_codex_client("chat-c", main.DEFAULT_CODEX_MODEL)

    assert isinstance(client, FakeWorkerClient)
    assert created


def test_get_or_create_codex_client_reuses_chat_worker_across_models(frame, monkeypatch):
    created = []

    class FakeWorkerClient:
        codex_model = main.DEFAULT_CODEX_MODEL

        def __init__(self, *args, **kwargs):
            created.append(self)
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(main, "CodexWorkerClient", FakeWorkerClient)

    first = frame._get_or_create_codex_client("chat-c", "codex/model-a")
    second = frame._get_or_create_codex_client("chat-c", "codex/model-b")

    assert first is second
    assert first.closed is False
    assert len(created) == 1


def test_codex_worker_message_pending_drains_and_dispatches_event(frame, monkeypatch):
    dispatched = []

    class _FakeWorkerClient:
        def drain_pending_messages(self, limit=100):
            return [
                {
                    "type": "event",
                    "payload": {
                        "chat_id": "chat-c",
                        "event": {
                            "type": "plan_updated",
                            "text": "worker step",
                            "thread_id": "thread-c",
                            "turn_id": "turn-c",
                        },
                    },
                }
            ]

    monkeypatch.setattr(frame, "_dispatch_codex_event_to_ui", lambda chat_id, event: dispatched.append((chat_id, event)))

    client = _FakeWorkerClient()
    frame._codex_clients["chat-c"] = client
    frame._on_codex_worker_message("chat-c", {"type": "messages_pending"}, client=client)

    assert len(dispatched) == 1
    assert dispatched[0][0] == "chat-c"
    assert dispatched[0][1] == main.CodexEvent(
        type="plan_updated",
        text="worker step",
        thread_id="thread-c",
        turn_id="turn-c",
    )


def test_codex_worker_event_bridge_preserves_outer_turn_idx_and_model(frame, monkeypatch):
    dispatched = []
    monkeypatch.setattr(frame, "_dispatch_codex_event_to_ui", lambda chat_id, event: dispatched.append((chat_id, event)))

    frame._on_codex_worker_message(
        "chat-c",
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 3,
                "model": "codex/original",
                "event": {
                    "type": "plan_updated",
                    "text": "worker step",
                    "data": {"existing": True},
                },
            },
        },
    )

    assert len(dispatched) == 1
    assert dispatched[0][0] == "chat-c"
    assert dispatched[0][1].data["existing"] is True
    assert dispatched[0][1].data["turn_idx"] == 3
    assert dispatched[0][1].data["model"] == "codex/original"


def test_active_codex_final_answer_uses_worker_turn_idx_scope(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "first",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "request_error": "",
        },
        {
            "question": "second",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "request_error": "",
        },
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "execution_steps": []}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_update_active_answer_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_append_completed_answer_to_answer_list", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(frame, "_render_answer_list_compat", lambda *_args, **_kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(type="item_completed", phase="final_answer", text="first answer", data={"turn_idx": 0}),
    )

    assert frame.active_session_turns[0]["answer_md"] == "first answer"
    assert frame.active_session_turns[1]["answer_md"] == main.REQUESTING_TEXT


def test_active_codex_turn_completed_uses_worker_turn_idx_scope(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "first",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "request_error": "",
        },
        {
            "question": "second",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "request_error": "",
        },
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "execution_steps": []}
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_update_active_answer_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_append_completed_answer_to_answer_list", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *_args, **_kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(type="turn_completed", status="completed", text="first done", data={"turn_idx": 0}),
    )

    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[0]["answer_md"] == "first done"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert frame.active_session_turns[1]["answer_md"] == main.REQUESTING_TEXT


def test_active_codex_execution_entry_uses_worker_turn_idx_scope(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL},
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(type="stderr", text="scoped stderr", data={"turn_idx": 0}),
    )

    assert frame._current_chat_state["execution_steps"][0]["turn_idx"] == 0
    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "scoped stderr"


def test_active_codex_delta_flush_preserves_worker_turn_idx_scope(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL},
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(type="agent_message_delta", text="delta text", item_id="item-1", data={"turn_idx": 0, "model": "codex/original"}),
    )
    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(type="stderr", text="flush", data={"turn_idx": 1}),
    )

    assert frame._current_chat_state["execution_steps"][0]["turn_idx"] == 0
    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "delta text"


def test_codex_worker_thread_state_from_reader_thread_schedules_ui_apply(frame, monkeypatch):
    scheduled = []
    message = {
        "type": "thread_state",
        "payload": {
            "chat_id": "chat-c",
            "turn_idx": 0,
            "thread_id": "thread-c",
            "turn_id": "turn-c",
            "active": True,
        },
    }
    monkeypatch.setattr(main.threading, "current_thread", lambda: SimpleNamespace(name="codex-stdout-thread"))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: scheduled.append((fn, args)) or True)
    monkeypatch.setattr(
        frame,
        "_apply_codex_worker_thread_state",
        lambda *_args, **_kwargs: pytest.fail("worker reader thread must not mutate chat state inline"),
    )

    frame._on_codex_worker_message("chat-c", message)

    assert scheduled == [(frame._on_codex_worker_message, ("chat-c", message, None))]


def test_codex_worker_turn_started_ack_does_not_mark_request_done(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "active",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_thread_id": "",
            "codex_turn_id": "",
            "codex_context_generation": 0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "turns": frame.active_session_turns,
        "codex_thread_id": "",
        "codex_turn_id": "",
        "codex_context_generation": 0,
        "codex_turn_active": False,
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-c",
        {
            "type": "turn_started_ack",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "thread_id": "thread-c",
                "turn_id": "turn-c",
                "context_generation": 0,
                "active": True,
            },
        },
    )

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_codex_turn_active is True


def test_codex_worker_protocol_error_does_not_mutate_active_chat_state(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_codex_thread_id = "thread-a"
    frame.active_codex_turn_id = "turn-a"
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {
            "question": "active",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "request_error": "",
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-a",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-a",
        "codex_turn_id": "turn-a",
        "codex_turn_active": True,
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: pytest.fail("protocol_error should not save state"))

    frame._on_codex_worker_message(
        "chat-c",
        {
            "type": "protocol_error",
            "payload": {"message": "bad request"},
        },
    )

    assert frame.active_codex_thread_id == "thread-a"
    assert frame.active_codex_turn_id == "turn-a"
    assert frame.active_codex_turn_active is True
    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0]["request_error"] == ""


def test_codex_worker_error_with_only_chat_id_does_not_fail_latest_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [
        {"question": "old", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "codex_turn_id": "turn-old"},
        {"question": "current", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-current"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-current"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: pytest.fail("unscoped worker error should not save state"))

    frame._on_codex_worker_message("chat-c", {"type": "error", "payload": {"chat_id": "chat-c", "message": "boom"}})

    assert [turn["request_status"] for turn in frame.active_session_turns] == ["pending", "pending"]
    assert frame.active_session_turns[1]["request_error"] == ""


def test_codex_worker_error_with_stale_turn_id_does_not_fail_current_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 0
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [
        {"question": "current", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-current"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-current"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: pytest.fail("stale worker error should not save state"))

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "error", "payload": {"chat_id": "chat-c", "turn_id": "turn-stale", "message": "boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0]["request_error"] == ""


def test_codex_worker_error_with_matching_turn_idx_fails_only_that_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-2"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "error", "payload": {"chat_id": "chat-c", "turn_idx": 0, "message": "idx boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert frame.active_session_turns[0]["request_error"] == "idx boom"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert frame.active_session_turns[1]["request_error"] == ""


def test_codex_worker_error_keeps_current_chat_running_when_other_turn_pending(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.is_running = True
    frame._active_request_count = 2
    frame.active_codex_turn_active = True
    frame.active_codex_pending_request = {"request_id": "turn-2-input"}
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-2", "codex_turn_active": True}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "error", "payload": {"chat_id": "chat-c", "turn_idx": 0, "message": "idx boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert frame.is_running is True
    assert frame._active_request_count == 1
    assert frame.active_codex_turn_active is True
    assert frame.active_codex_pending_request == {"request_id": "turn-2-input"}


def test_codex_worker_error_with_matching_turn_id_fails_only_that_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_turn_id = "turn-2"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-2"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "error", "payload": {"chat_id": "chat-c", "turn_id": "turn-2", "message": "id boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0]["request_error"] == ""
    assert frame.active_session_turns[1]["request_status"] == "failed"
    assert frame.active_session_turns[1]["request_error"] == "id boom"


def test_codex_worker_fatal_with_tracked_metadata_fails_matching_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-2"}
    frame._codex_worker_active_turns = {"chat-c": {"turn_idx": 1, "turn_id": "turn-2"}}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "fatal", "payload": {"chat_id": "chat-c", "message": "fatal boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0]["request_error"] == ""
    assert frame.active_session_turns[1]["request_status"] == "failed"
    assert frame.active_session_turns[1]["request_error"] == "fatal boom"


def test_codex_worker_fatal_without_metadata_does_not_fail_latest_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 1
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-2"}
    frame._codex_worker_active_turns = {}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: pytest.fail("unscoped fatal should not save state"))

    frame._on_codex_worker_message(
        "chat-c",
        {"type": "fatal", "payload": {"message": "global fatal"}},
    )

    assert [turn["request_status"] for turn in frame.active_session_turns] == ["pending", "pending"]
    assert [turn["request_error"] for turn in frame.active_session_turns] == ["", ""]


def test_codex_worker_error_for_archived_chat_does_not_fail_active_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "running",
            "request_error": "",
            "codex_turn_id": "turn-a",
        }
    ]
    frame._current_chat_state = {"id": "chat-a", "turns": frame.active_session_turns, "codex_turn_id": "turn-a"}
    chat_b = {
        "id": "chat-b",
        "title": "B",
        "turns": [
            {
                "question": "B",
                "answer_md": main.REQUESTING_TEXT,
                "model": main.DEFAULT_CODEX_MODEL,
                "request_status": "running",
                "request_error": "",
                "codex_turn_id": "turn-b",
            }
        ],
        "codex_turn_id": "turn-b",
    }
    frame.archived_chats = [chat_b]
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_codex_worker_message(
        "chat-b",
        {"type": "error", "payload": {"chat_id": "chat-b", "turn_id": "turn-b", "message": "archived boom"}},
    )

    assert frame.active_session_turns[0]["request_status"] == "running"
    assert frame.active_session_turns[0]["request_error"] == ""
    assert chat_b["turns"][0]["request_status"] == "failed"
    assert chat_b["turns"][0]["request_error"] == "archived boom"


def test_codex_worker_error_from_reader_thread_schedules_ui_apply(frame, monkeypatch):
    scheduled = []
    message = {
        "type": "error",
        "payload": {
            "chat_id": "chat-c",
            "turn_idx": 0,
            "turn_id": "turn-c",
            "message": "boom",
        },
    }
    monkeypatch.setattr(main.threading, "current_thread", lambda: SimpleNamespace(name="codex-stdout-thread"))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: scheduled.append((fn, args)) or True)
    monkeypatch.setattr(
        frame,
        "_apply_codex_worker_error",
        lambda *_args, **_kwargs: pytest.fail("worker reader thread must not mutate error state inline"),
    )

    frame._on_codex_worker_message("chat-c", message)

    assert scheduled == [(frame._on_codex_worker_message, ("chat-c", message, None))]


def test_codex_worker_exit_with_active_metadata_fails_matching_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_session_turns = [
        {"question": "first", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-1"},
        {"question": "second", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-2"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-1"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._apply_codex_worker_thread_state(
        "chat-c",
        {"chat_id": "chat-c", "turn_idx": 0, "turn_id": "turn-1", "active": True},
    )
    frame._on_codex_worker_exit("chat-c", -9)

    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert "exited with code -9" in frame.active_session_turns[0]["request_error"]
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert frame.active_session_turns[1]["request_error"] == ""


def test_codex_worker_exit_without_active_metadata_does_not_fail_latest_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "current", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_error": "", "codex_turn_id": "turn-current"},
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_id": "turn-current"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: pytest.fail("unscoped worker exit should not save state"))

    frame._on_codex_worker_exit("chat-c", -9)

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[0]["request_error"] == ""


def test_codex_worker_exit_from_reader_thread_schedules_ui_apply(frame, monkeypatch):
    scheduled = []
    monkeypatch.setattr(main.threading, "current_thread", lambda: SimpleNamespace(name="codex-exit-thread"))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: scheduled.append((fn, args)) or True)
    monkeypatch.setattr(
        frame,
        "_apply_codex_worker_error",
        lambda *_args, **_kwargs: pytest.fail("worker exit reader thread must not mutate error state inline"),
    )

    frame._on_codex_worker_exit("chat-c", -9)

    assert scheduled == [(frame._on_codex_worker_exit, ("chat-c", -9))]


def test_background_codex_worker_without_thread_does_not_reuse_active_chat_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_session_turns = [
        {
            "question": "问题 d",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "rclone上传R2",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "切换代码目录",
            "turns": [
                {
                    "question": "问题 b",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "execution_steps": [],
        }
    ]
    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._run_codex_turn_worker("chat-a", 0, "问题 b", main.DEFAULT_CODEX_MODEL)

    archived = frame._find_archived_chat("chat-a")
    assert sent[0]["chat_id"] == "chat-a"
    assert sent[0]["thread_id"] == ""
    assert sent[0]["turn_id"] == ""
    assert sent[0]["input_items"][0]["text"] == "问题 b"
    assert archived.get("codex_thread_id", "") == ""
    assert archived.get("codex_turn_id", "") == ""
    assert frame.active_codex_thread_id == "thread-c"
    assert frame.active_session_turns[0]["codex_thread_id"] == "thread-c"


def test_codex_worker_for_missing_background_chat_does_not_use_active_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_session_turns = [
        {
            "question": "问题 d",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "rclone上传R2",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    frame.archived_chats = []
    calls = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            calls.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: fn(*args, **kwargs) or True)

    frame._run_codex_turn_worker("chat-missing", 0, "问题 b", main.DEFAULT_CODEX_MODEL)

    assert calls == []
    assert frame.active_codex_thread_id == "thread-c"
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert frame.active_session_turns[0]["codex_thread_id"] == "thread-c"


def test_codex_worker_for_background_chat_without_loaded_turns_does_not_touch_active_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_session_turns = [
        {
            "question": "问题 d",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "rclone上传R2",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "切换代码目录",
            "codex_thread_id": "",
            "codex_turn_id": "",
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    calls = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            calls.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: fn(*args, **kwargs) or True)

    frame._run_codex_turn_worker("chat-a", 0, "问题 b", main.DEFAULT_CODEX_MODEL)

    assert calls == []
    assert "turns" not in frame.archived_chats[0]
    assert frame.active_session_turns[0]["codex_thread_id"] == "thread-c"
    assert frame.active_session_turns[0]["codex_turn_id"] == "turn-c"


def test_explicit_background_codex_event_with_active_thread_id_does_not_update_visible_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "问题 d",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "rclone上传R2",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "切换代码目录",
            "turns": [
                {
                    "question": "问题 b",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-a",
                    "codex_turn_id": "turn-a",
                }
            ],
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
            "created_at": 1.0,
            "updated_at": 1.0,
            "execution_steps": [],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-a",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-c",
            turn_id="turn-c",
            text="串线回答",
        ),
    )

    archived = frame._find_archived_chat("chat-a")
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert archived["turns"][0]["answer_md"] == main.REQUESTING_TEXT


def test_background_codex_compact_command_without_thread_does_not_use_active_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame._current_chat_state = {"id": "chat-c", "codex_thread_id": "thread-c", "codex_turn_id": "turn-c", "turns": []}
    frame.archived_chats = [{"id": "chat-a", "title": "后台聊天", "turns": []}]
    compact_calls = []
    done = []

    class _Client:
        def compact_thread(self, thread_id):
            compact_calls.append(thread_id)

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_ensure_codex_client", lambda _model="": _Client())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: done.append(args) or True)

    frame._run_codex_local_command_worker("chat-a", 0, "compact", "", main.DEFAULT_CODEX_MODEL)

    assert compact_calls == []
    assert done
    assert "还没有 Codex 线程" in done[0][1]


def test_codex_event_from_background_thread_queues_instead_of_touching_ui(frame, monkeypatch):
    dispatched = []
    handled = []

    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_codex_thread_id = "thread-active"
    frame.active_codex_turn_id = "turn-active"
    monkeypatch.setattr(
        main.threading,
        "current_thread",
        lambda: SimpleNamespace(name="codex-stdout-thread"),
    )
    monkeypatch.setattr(frame, "_dispatch_codex_event_to_ui", lambda chat_id, event: dispatched.append((chat_id, event.type)))
    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda chat_id, event: handled.append((chat_id, event.type)))

    frame._on_codex_event(
        main.CodexEvent(
            type="plan_updated",
            thread_id="thread-active",
            turn_id="turn-active",
            text="background progress",
        )
    )

    assert dispatched == [("chat-active", "plan_updated")]
    assert handled == []


def test_run_codex_turn_worker_sends_start_turn_to_worker(frame, monkeypatch):
    sent = []

    class FakeWorker:
        codex_model = main.DEFAULT_CODEX_MODEL

        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda func, *args, **kwargs: True)
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["codex_context_generation"] = 3
    frame._current_chat_state["turns"] = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL,
         "codex_context_generation": 3}
    ]
    frame.active_session_turns = frame._current_chat_state["turns"]

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 0, "问题", main.DEFAULT_CODEX_MODEL)

    assert sent
    assert sent[0]["chat_id"] == "chat-current"
    assert sent[0]["turn_idx"] == 0
    assert sent[0]["context_generation"] == 3
    assert sent[0]["question"] == "问题"
    assert sent[0]["input_items"][0]["type"] == "text"


def test_run_codex_turn_worker_schedules_request_state_mutation(frame, monkeypatch):
    sent = []
    scheduled = []

    class FakeWorker:
        codex_model = main.DEFAULT_CODEX_MODEL

        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    in_scheduled_mutation = {"value": False}
    defer_calls = []

    def defer_save():
        if not in_scheduled_mutation["value"]:
            pytest.fail("state save must be scheduled onto UI thread")
        defer_calls.append(True)

    monkeypatch.setattr(frame, "_defer_codex_state_save", defer_save)

    def schedule(func, *args, **kwargs):
        scheduled.append((func, args, kwargs))
        return True

    monkeypatch.setattr(frame, "_call_after_if_alive", schedule)
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update({"id": "chat-current", "codex_service_tier": ""})
    frame._current_chat_state["turns"] = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1}
    ]
    frame.active_session_turns = frame._current_chat_state["turns"]

    frame._run_codex_turn_worker("chat-current", 0, "问题", main.DEFAULT_CODEX_MODEL)

    assert sent
    assert scheduled
    assert frame._current_chat_state["codex_service_tier"] == ""
    assert frame.active_session_turns[0].get("request_recovery_mode") is None

    in_scheduled_mutation["value"] = True
    scheduled[0][0](*scheduled[0][1], **scheduled[0][2])
    in_scheduled_mutation["value"] = False

    assert frame.active_session_turns[0]["request_recovery_mode"] == "resume"
    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert defer_calls == [True]


def test_codex_worker_recovers_missing_thread_by_creating_new_one(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "继续当前 Codex 对话",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
        }
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state["codex_thread_id"] = "thread-old"
    frame._current_chat_state["codex_turn_id"] = "turn-old"
    frame._current_chat_state["codex_turn_active"] = False
    frame._current_chat_state["codex_pending_prompt"] = "stale prompt"
    frame._current_chat_state["codex_thread_flags"] = ["waitingOnUserInput"]
    frame.active_codex_thread_id = "thread-old"
    frame.active_codex_turn_id = "turn-old"
    frame.active_codex_turn_active = False
    frame.active_codex_pending_prompt = "stale prompt"
    frame.active_codex_thread_flags = ["waitingOnUserInput"]

    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 0, "新的 Codex 问题", "codex/main")

    assert sent
    assert sent[0]["thread_id"] == "thread-old"
    assert sent[0]["turn_id"] == "turn-old"
    assert sent[0]["service_tier"] == ""
    assert sent[0]["should_steer"] is False
    assert sent[0]["history_turns"] == []
    assert frame.active_codex_thread_id == "thread-old"
    assert frame.active_codex_turn_id == "turn-old"


def test_codex_worker_resumes_existing_thread_after_restart(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "第一轮",
            "answer_md": "第一轮回答",
            "model": "codex/main",
            "created_at": 1.0,
        },
        {
            "question": "第二轮",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 2.0,
        },
    ]
    frame.active_turn_idx = 1
    frame._current_chat_state["codex_thread_id"] = "thread-saved"
    frame.active_codex_thread_id = "thread-saved"
    frame.active_codex_turn_id = ""
    frame.active_codex_turn_active = False

    seen = {"started": 0, "payloads": []}

    class _Client:
        def start(self):
            seen["started"] += 1

        def start_turn(self, **payload):
            seen["payloads"].append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[1].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 1, "第三轮", "codex/main")

    assert seen["started"] == 1
    assert len(seen["payloads"]) == 1
    assert seen["payloads"][0]["chat_id"] == "chat-current"
    assert seen["payloads"][0]["turn_idx"] == 1
    assert seen["payloads"][0]["question"] == "第三轮"
    assert seen["payloads"][0]["thread_id"] == "thread-saved"
    assert seen["payloads"][0]["turn_id"] == ""
    assert frame.active_codex_thread_id == "thread-saved"
    assert frame.active_codex_turn_id == ""


def test_codex_worker_passes_saved_fast_service_tier_to_thread_and_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "model": "codex/main",
            "codex_service_tier": "fast",
        }
    )
    frame.active_session_turns = [
        {
            "question": "快速执行",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_service_tier": "fast",
        }
    ]
    frame.active_turn_idx = 0
    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 0, "快速执行", "codex/main")

    assert sent[0]["service_tier"] == "fast"
    assert frame.active_session_turns[0]["codex_service_tier"] == "fast"


def test_codex_worker_rebuilds_context_when_saved_rollout_is_missing(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "第一轮问题",
            "answer_md": "第一轮回答",
            "model": "codex/main",
            "created_at": 1.0,
        },
        {
            "question": "第二轮问题",
            "answer_md": "第二轮回答",
            "model": "codex/main",
            "created_at": 2.0,
        },
        {
            "question": "第三轮问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 3.0,
        },
    ]
    frame.active_turn_idx = 2
    frame._current_chat_state["codex_thread_id"] = "thread-stale"
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = ""
    frame.active_codex_turn_active = False

    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[2].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 2, "第三轮问题", "codex/main")

    assert sent[0]["thread_id"] == "thread-stale"
    assert sent[0]["history_turns"] == frame.active_session_turns[:2]
    assert sent[0]["input_items"][0]["text"] == "第三轮问题"
    assert frame.active_codex_thread_id == "thread-stale"
    assert frame.active_codex_turn_id == ""


def test_codex_worker_sends_local_image_items_for_successful_attachments(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "worker-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "worker-image.png 图片已成功上传",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    frame.active_turn_idx = 0
    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request("chat-current")
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker("chat-current", 0, "", "codex/main")

    assert sent[0]["input_items"] == [{"type": "localImage", "path": str(image_path)}]
    assert sent[0]["attachments"][0]["path"] == str(image_path)


def test_codex_status_command_worker_reports_status_without_codex_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "codex_thread_flags": ["waitingOnUserInput"],
            "context_usage": {
                "source": "codex",
                "used_tokens": 1000,
                "total_tokens": 200000,
                "model": "gpt-5.5",
            },
        }
    )
    frame.active_session_turns = [
        {
            "question": "/status",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
        }
    ]
    done = []

    class _Client:
        def read_account(self, refresh_token=False):
            return {"account": {"type": "chatgpt", "email": "user@example.com", "planType": "plus"}}

        def read_rate_limits(self):
            return {
                "rateLimits": {
                    "limitName": "Codex",
                    "primary": {"percentUsed": 25.0},
                }
            }

        def read_thread(self, thread_id, include_turns=True):
            return {"thread": {"id": thread_id, "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]}}}

        def start_turn(self, *_args, **_kwargs):
            raise AssertionError("/status must not start a Codex turn")

        def start_turn_items(self, *_args, **_kwargs):
            raise AssertionError("/status must not start a Codex turn")

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda _model="": _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_on_done", lambda *args: done.append(args))

    frame._run_codex_local_command_worker("chat-current", 0, "status", "", "codex/main")

    assert len(done) == 1
    turn_idx, answer, error, model, _extra, chat_id = done[0]
    assert (turn_idx, error, model, chat_id) == (0, "", "codex/main", "chat-current")
    assert "Codex 状态" in answer
    assert "thread-1" in answer
    assert "waitingOnUserInput" in answer
    assert "user@example.com" in answer


def test_codex_help_command_worker_lists_supported_commands(frame, monkeypatch):
    done = []
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_on_done", lambda *args: done.append(args))

    frame._run_codex_local_command_worker("chat-current", 0, "help", "", "codex/main")

    assert len(done) == 1
    assert "/status" in done[0][1]
    assert "/compact" in done[0][1]
    assert done[0][2] == ""


def test_codex_unknown_slash_command_worker_reports_unsupported_without_starting_turn(frame, monkeypatch):
    done = []

    class _Client:
        def start_turn(self, *_args, **_kwargs):
            raise AssertionError("unknown slash command must not start a Codex turn")

        def start_turn_items(self, *_args, **_kwargs):
            raise AssertionError("unknown slash command must not start a Codex turn")

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda _model="": _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_on_done", lambda *args: done.append(args))

    frame._run_codex_local_command_worker("chat-current", 0, "not-a-real-command", "arg", "codex/main")

    assert len(done) == 1
    assert "暂不支持" in done[0][1]
    assert "/not-a-real-command" in done[0][1]
    assert done[0][2] == ""


def test_codex_compact_command_worker_calls_app_server_compact(frame, monkeypatch):
    frame._current_chat_state.update({"id": "chat-current", "codex_thread_id": "thread-1"})
    calls = []
    done = []

    class _Client:
        def compact_thread(self, thread_id, **payload):
            calls.append((thread_id, payload))
            return "req-compact"

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda _model="": _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_on_done", lambda *args: done.append(args))

    frame._run_codex_local_command_worker("chat-current", 0, "compact", "now", "codex/main")

    assert calls == [("thread-1", {"chat_id": "chat-current", "model": "codex/main"})]
    assert "已开始压缩" in done[0][1]
    assert done[0][2] == ""


def test_codex_stop_command_worker_sends_interrupt_turn(frame, monkeypatch):
    frame._current_chat_state.update(
        {"id": "chat-current", "model": "codex/main", "codex_thread_id": "thread-1", "codex_turn_id": "turn-1"}
    )
    calls = []
    done = []

    class _Client:
        def interrupt_turn(self, thread_id, turn_id, **payload):
            calls.append((thread_id, turn_id, payload))
            return "req-stop"

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda _model="": _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_on_done", lambda *args: done.append(args))

    frame._run_codex_local_command_worker("chat-current", 0, "stop", "", "codex/main")

    assert calls == [("thread-1", "turn-1", {"chat_id": "chat-current", "model": "codex/main"})]
    assert "已请求中断" in done[0][1]
    assert done[0][2] == ""


@pytest.mark.parametrize("clear_context", [False, True])
def test_switched_chat_final_rejects_stale_generation(frame, monkeypatch, clear_context):
    _prepare_switched_chat_final(frame, monkeypatch)
    archived = frame._archive_active_session(quick_title=True)
    frame.active_chat_id = frame.current_chat_id = "other-owner"
    frame._current_chat_state = {"id": "other-owner", "turns": []}
    if clear_context:
        assert frame._clear_context_for_chat_id("switch-owner", auto_resend_first=False) == "cleared"
        assert archived["turns"] == []
    before = [(turn.get("answer_md"), turn.get("request_status")) for turn in archived["turns"]]
    generation = 1 if clear_context else 0
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "item_completed", generation, phase="final_answer", text="stale final"))
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "turn_completed", generation, status="completed"))
    assert [(turn.get("answer_md"), turn.get("request_status")) for turn in archived["turns"]] == before
