from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import main
from chat_store import ChatStore


class _V2Transport:
    protocol_version = 2
    subjects = SimpleNamespace(pair_id="pair-production")
    _loop = None


class _NotificationFrame(SimpleNamespace):
    _remote_terminal_history_chat = main.ChatFrame._remote_terminal_history_chat
    _read_pair_id = main.ChatFrame._read_pair_id
    _flush_chat_state_save = main.ChatFrame._flush_chat_state_save
    _save_delayed_chat_state = main.ChatFrame._save_delayed_chat_state

    def _call_after_if_alive(self, *_):
        return False

    def _refresh_chat_read_row(self, _chat_id):
        pass

    def _schedule_next_codex_tool_turn(self, *_):
        pass

    _schedule_next_kimi_tool_turn = _schedule_next_codex_tool_turn
    _schedule_next_agy_tool_turn = _schedule_next_codex_tool_turn


def _outbox_kinds(store: ChatStore) -> list[str]:
    return [
        json.loads(bytes(row["payload"]).decode("utf-8"))["kind"]
        for row in store.pending_outbox(pair_id="pair-production", domain="events")
    ]


def test_persist_dirty_chat_turns_commits_production_user_notification_fact(tmp_path):
    store = ChatStore(tmp_path / "dirty-turns.db")
    store.initialize()
    store.upsert_chat({"id": "chat-user", "title": "Canonical user owner"})
    frame = _NotificationFrame(
        _chat_turn_dirty_from={"chat-user": 0},
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-user", "title": "untrusted UI title"},
        archived_chats=[],
    )
    turns = [
        {
            "question": "Canonical user question",
            "answer_md": "",
            "question_origin": "mc",
        }
    ]

    main.ChatFrame._persist_dirty_chat_turns(frame, store, "chat-user", turns)

    assert "user_message" in _outbox_kinds(store)


def test_push_remote_final_answer_commits_production_assistant_notification_fact(tmp_path):
    store = ChatStore(tmp_path / "final-answer.db")
    store.initialize()
    store.upsert_chat({"id": "chat-final", "title": "Canonical final owner"})
    store.replace_turns(
            "chat-final", [{"question": "Question", "answer_md": "Canonical final", "request_status": "done"}]
    )
    frame = _NotificationFrame(
        chat_store=store,
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-final", "title": "untrusted UI title"},
        archived_chats=[],
    )

    main.ChatFrame._push_remote_final_answer(
        frame, "chat-final", "Canonical final", turn_index=0
    )

    assert "assistant_final" in _outbox_kinds(store)


def test_persisted_assistant_final_waits_for_done_and_uses_canonical_answer(tmp_path):
    store = ChatStore(tmp_path / "final-after-persist.db")
    store.initialize()
    store.upsert_chat({"id": "chat-race", "title": "Canonical race owner"})
    frame = _NotificationFrame(
        _chat_turn_dirty_from={"chat-race": 0},
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-race", "title": "untrusted UI title"},
        archived_chats=[],
    )
    turns = [{
        "question": "Question",
        "answer_md": main.REQUESTING_TEXT,
        "request_status": "pending",
    }]

    # An early provider callback may persist the placeholder first.  It must
    # not consume the canonical assistant-final notification id.
    main.ChatFrame._persist_dirty_chat_turns(frame, store, "chat-race", turns)
    assert "assistant_final" not in _outbox_kinds(store)

    turns[0].update(answer_md="Real final marker", request_status="done")
    frame._chat_turn_dirty_from["chat-race"] = 0
    main.ChatFrame._persist_dirty_chat_turns(frame, store, "chat-race", turns)

    facts = [
        json.loads(bytes(row["payload"]).decode("utf-8"))
        for row in store.pending_outbox(pair_id="pair-production", domain="events")
        if json.loads(bytes(row["payload"]).decode("utf-8"))["kind"] == "assistant_final"
    ]
    assert len(facts) == 1
    assert facts[0]["body"]["text"] == "Real final marker"


def test_deferred_final_is_published_after_save_with_history_once(tmp_path):
    store = ChatStore(tmp_path / "deferred-final.db")
    store.initialize()
    store.upsert_chat({"id": "chat-deferred", "title": "Deferred owner"})
    turn = {"question": "Question", "answer_md": main.REQUESTING_TEXT, "request_status": "pending"}
    store.replace_turns("chat-deferred", [turn])
    history_changes = []
    frame = _NotificationFrame(
        chat_store=store,
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-deferred", "title": "Deferred owner"},
        archived_chats=[],
        _chat_state_flush_dirty=True,
        _chat_state_flush_scheduled=True,
        _pending_remote_finals={("chat-deferred", 0)},
    )
    frame._save_state = lambda: store.replace_turns("chat-deferred", [turn])
    frame._push_remote_final_answer = lambda chat_id, text, **kwargs: main.ChatFrame._push_remote_final_answer(
        frame, chat_id, text, **kwargs
    )
    frame._push_remote_history_changed = history_changes.append
    frame._flush_pending_remote_finals = lambda: main.ChatFrame._flush_pending_remote_finals(frame)
    turn.update(answer_md="Saved final marker", request_status="done")
    assert "assistant_final" not in _outbox_kinds(store)

    main.ChatFrame._flush_chat_state_save(frame)

    persisted = store.load_turns("chat-deferred")
    assert persisted[0]["answer_md"] == "Saved final marker"
    assert _outbox_kinds(store).count("assistant_final") == 1
    assert history_changes == ["chat-deferred"]
    main.ChatFrame._flush_pending_remote_finals(frame)
    assert history_changes == ["chat-deferred"]


def test_deferred_final_retries_after_notification_commit_failure(tmp_path):
    store = ChatStore(tmp_path / "retry-final.db")
    store.initialize()
    store.upsert_chat({"id": "chat-retry", "title": "Retry owner"})
    store.replace_turns("chat-retry", [{
        "question": "Question", "answer_md": "Final answer", "request_status": "done",
    }])
    history_changes = []
    frame = _NotificationFrame(
        chat_store=store,
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-retry", "title": "Retry owner"},
        archived_chats=[],
        _pending_remote_finals={("chat-retry", 0)},
    )
    frame._push_remote_final_answer = lambda chat_id, text, **kwargs: main.ChatFrame._push_remote_final_answer(
        frame, chat_id, text, **kwargs
    )
    frame._push_remote_history_changed = history_changes.append
    scheduled = []
    frame._flush_pending_remote_finals = lambda: main.ChatFrame._flush_pending_remote_finals(frame)
    frame._retry_pending_remote_finals = lambda: main.ChatFrame._retry_pending_remote_finals(frame)
    frame._call_later_if_alive = lambda delay, callback: scheduled.append((delay, callback)) or object()
    original_commit = store.commit_message_notification_fact
    attempts = []

    def flaky_commit(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient database failure")
        return original_commit(*args, **kwargs)

    store.commit_message_notification_fact = flaky_commit
    main.ChatFrame._flush_pending_remote_finals(frame)
    assert frame._pending_remote_finals == {("chat-retry", 0)}
    assert "assistant_final" not in _outbox_kinds(store)
    assert history_changes == []
    assert len(scheduled) == 1

    scheduled.pop()[1]()
    assert frame._pending_remote_finals == set()
    assert _outbox_kinds(store).count("assistant_final") == 1
    assert history_changes == ["chat-retry"]


def test_slow_persistence_worker_defers_close_without_draining_under_live_sqlite_work():
    class _SlowWorker:
        def __init__(self):
            self.alive = True
            self.join_calls = []

        def join(self, timeout):
            self.join_calls.append(timeout)

        def is_alive(self):
            return self.alive

    class _Store:
        def __init__(self):
            self.appended = []

        def append_execution_step(self, chat_id, step):
            self.appended.append((chat_id, step))

    worker = _SlowWorker()
    store = _Store()
    frame = SimpleNamespace(
        _execution_step_persist_lock=threading.RLock(),
        _execution_step_persist_thread=worker,
        _pending_execution_step_persists=[("chat-1", {"text": "pending"})],
        _execution_step_persist_scheduled=True,
        _persist_execution_step=main.ChatFrame._persist_execution_step,
        chat_store=store,
    )

    frame._persist_queued_execution_step = main.ChatFrame._persist_queued_execution_step.__get__(frame)

    assert (
        main.ChatFrame._flush_execution_step_persists_sync(
            frame, worker_timeout=0.001
        )
        is False
    )
    assert frame._pending_execution_step_persists == [
        ("chat-1", {"text": "pending"})
    ]
    assert store.appended == []

    worker.alive = False
    assert main.ChatFrame._flush_execution_step_persists_sync(frame) is True
    assert store.appended == [("chat-1", {"text": "pending"})]


def test_close_vetoes_reentry_without_restarting_cleanup():
    class _CloseEvent:
        def __init__(self):
            self.vetoed = False

        def Veto(self):
            self.vetoed = True

    frame = SimpleNamespace(_close_started=True)
    event = _CloseEvent()

    main.ChatFrame._on_close(frame, event)

    assert event.vetoed is True
