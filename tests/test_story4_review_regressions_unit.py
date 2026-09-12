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


def _outbox_kinds(store: ChatStore) -> list[str]:
    return [
        json.loads(bytes(row["payload"]).decode("utf-8"))["kind"]
        for row in store.pending_outbox(pair_id="pair-production", domain="events")
    ]


def test_persist_dirty_chat_turns_commits_production_user_notification_fact(tmp_path):
    store = ChatStore(tmp_path / "dirty-turns.db")
    store.initialize()
    store.upsert_chat({"id": "chat-user", "title": "Canonical user owner"})
    frame = SimpleNamespace(
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
        "chat-final", [{"question": "Question", "answer_md": "Canonical final"}]
    )
    frame = SimpleNamespace(
        chat_store=store,
        _remote_nats_transport=_V2Transport(),
        _current_chat_state={"id": "chat-final", "title": "untrusted UI title"},
        archived_chats=[],
    )

    main.ChatFrame._push_remote_final_answer(
        frame, "chat-final", "Canonical final", turn_index=0
    )

    assert "assistant_final" in _outbox_kinds(store)


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
        chat_store=store,
    )

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


def test_close_vetoes_and_schedules_retry_while_persistence_worker_is_live():
    class _CloseEvent:
        def __init__(self):
            self.vetoed = False

        def Veto(self):
            self.vetoed = True

    deferred = []
    frame = SimpleNamespace(
        _release_execution_focus_lease=lambda: None,
        _invalidate_execution_scan=lambda: None,
        _flush_chat_state_save=lambda: None,
        _flush_execution_step_persists_sync=lambda: False,
        _defer_close_for_persistence_worker=lambda: deferred.append(True),
    )
    event = _CloseEvent()

    main.ChatFrame._on_close(frame, event)

    assert event.vetoed is True
    assert deferred == [True]
