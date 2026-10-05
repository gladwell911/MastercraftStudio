import sqlite3
import threading

import pytest

from chat_store import ChatStore
from remote_nats_protocol import validate_v2_durable


def turn(question, status="done"):
    return {"question": question, "answer_md": "answer " + question, "request_status": status}


@pytest.fixture
def store(tmp_path):
    result = ChatStore(tmp_path / "chats.db")
    result.initialize()
    result.upsert_chat({"id": "a", "title": "A"})
    return result


def mark(store, index, pair="p"):
    target = store.readable_answer("a", turn_index=index)
    return store.mark_chat_read(pair_id=pair, chat_id="a", operation_id="op", **target)


def test_read_target_monotonic_and_pair_scoped(store):
    store.replace_turns("a", [turn("1"), turn("2"), turn("3", "pending")])
    assert store.get_chat_read_state("a", pair_id="p")["read_seq"] == 0
    first = mark(store, 0)
    assert first["latest_readable_seq"] > first["read_seq"]
    second = mark(store, 1)
    assert mark(store, 0)["read_seq"] == second["read_seq"]
    assert store.get_chat_read_state("a", pair_id="other")["read_seq"] == 0
    assert store.readable_answer("a", turn_index=2) is None
    facts = [row for row in store.pending_outbox(pair_id="p")]
    assert len(facts) == 2
    for raw in store.replay_after(pair_id="p", domain="events", sync_sequence=0):
        import json
        validate_v2_durable(json.loads(raw))
    store.replace_turns("a", [turn("1"), turn("2"), turn("3")])
    assert store.get_chat_read_state("a", pair_id="p")["latest_readable_seq"] > second["read_seq"]


def test_identity_position_validation_and_revision_does_not_reset(store):
    store.replace_turns("a", [turn("1"), turn("2")])
    target = store.readable_answer("a", turn_index=0)
    for operation_id in (None, "", "  ", 123):
        with pytest.raises(ValueError, match="INVALID_READ_SCOPE"):
            store.mark_chat_read(pair_id="p", chat_id="a", operation_id=operation_id, **target)
    with pytest.raises(ValueError, match="INVALID_READ_TARGET"):
        store.mark_chat_read(pair_id="p", chat_id="a", operation_id="op", **{**target, "answer_seq": target["answer_seq"] + 1})
    result = mark(store, 0)
    store.allocate_chat_revision("a")
    assert store.get_chat_read_state("a", pair_id="p")["read_seq"] == result["read_seq"]


def test_clear_rejects_old_generation(store):
    store.replace_turns("a", [turn("1")])
    target = store.readable_answer("a", turn_index=0)
    store.begin_clear_operation("a", idempotency_key="clear", pair_id="p")
    store.replace_turns("a", [turn("new")])
    with pytest.raises(ValueError, match="STALE_READ_GENERATION"):
        store.mark_chat_read(pair_id="p", chat_id="a", operation_id="old", **target)
    assert store.get_chat_read_state("a", pair_id="p")["read_seq"] == 0


def test_upgrade_initializes_old_only_once(store):
    store.replace_turns("a", [turn("old"), turn("pending", "pending")])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM meta WHERE key='read_state_initialized'")
    store.initialize()
    old = store.get_chat_read_state("a", pair_id="p")
    assert old["read_seq"] == old["latest_readable_seq"]
    store.replace_turns("a", [turn("old"), turn("pending")])
    store.initialize()
    state = store.get_chat_read_state("a", pair_id="p")
    assert state["read_seq"] == old["read_seq"]
    assert state["latest_readable_seq"] > state["read_seq"]


def test_initialization_cutoff_excludes_completion_waiting_on_transaction(store, monkeypatch):
    store.replace_turns("a", [turn("old"), turn("new", "pending")])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM meta WHERE key='read_state_initialized'")
    original = store._backfill_canonical_turns_conn
    started, completed = threading.Event(), threading.Event()
    errors = []
    workers = []
    def complete():
        started.set()
        try:
            store.replace_turns("a", [turn("old"), turn("new")])
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()
    def backfill(conn, *args, **kwargs):
        result = original(conn, *args, **kwargs)
        if not workers:
            worker = threading.Thread(target=complete)
            workers.append(worker)
            worker.start()
            assert started.wait(2)
            assert not completed.wait(.05), "completion must wait behind the migration transaction"
        return result
    monkeypatch.setattr(store, "_backfill_canonical_turns_conn", backfill)
    store.initialize()
    workers[0].join(5)
    assert completed.is_set() and not errors
    old = store.readable_answer("a", turn_index=0)
    new = store.readable_answer("a", turn_index=1)
    state = store.get_chat_read_state("a", pair_id="p")
    assert state["read_seq"] == old["answer_seq"] < new["answer_seq"]
