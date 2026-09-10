from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
from pathlib import Path
import pytest

from chat_store import ChatStore
from chat_store import MAX_INT64, V2_MIGRATION_KEY


def test_chat_store_initializes_schema_and_lists_summaries(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat(
        {
            "id": "chat-1",
            "title": "First",
            "model": "codex/main",
            "created_at": 1.0,
            "updated_at": 2.0,
            "pinned": False,
            "detail_panel_mode": "answers",
        }
    )

    summaries = store.list_chat_summaries()

    assert summaries == [
        {
            "id": "chat-1",
            "title": "First",
            "model": "codex/main",
            "created_at": 1.0,
            "updated_at": 2.0,
            "pinned": False,
            "title_manual": False,
            "title_source": "default",
            "title_updated_at": 2.0,
            "title_revision": 1,
            "detail_panel_mode": "answers",
            "turn_count": 0,
        }
    ]


def test_chat_store_replaces_and_loads_turns(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    turns = [
        {"question": "q1", "answer_md": "a1", "model": "codex/main", "created_at": 1.0},
        {"question": "q2", "answer_md": "a2", "model": "codex/main", "created_at": 2.0},
    ]

    store.replace_turns("chat-1", turns)

    assert store.load_turns("chat-1") == turns
    assert store.list_chat_summaries()[0]["turn_count"] == 2


def test_chat_store_replaces_turn_suffix_without_rewriting_prefix(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    store.replace_turns(
        "chat-1",
        [
            {"question": "q0", "answer_md": "a0"},
            {"question": "q1", "answer_md": "a1"},
            {"question": "q2", "answer_md": "a2"},
        ],
    )

    store.replace_turns_from(
        "chat-1",
        [
            {"question": "q1 changed", "answer_md": "a1 changed"},
            {"question": "q2 changed", "answer_md": "a2 changed"},
        ],
        start_index=1,
    )

    assert store.count_turns("chat-1") == 3
    assert store.load_turns("chat-1") == [
        {"question": "q0", "answer_md": "a0"},
        {"question": "q1 changed", "answer_md": "a1 changed"},
        {"question": "q2 changed", "answer_md": "a2 changed"},
    ]


def test_chat_store_loads_turn_page_without_full_turn_scan(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    store.replace_turns(
        "chat-1",
        [{"question": f"q{idx}", "answer_md": f"a{idx}"} for idx in range(6)],
    )

    total, rows = store.load_turns_page("chat-1", limit=2)

    assert total == 6
    assert rows == [
        {"question": "q4", "answer_md": "a4"},
        {"question": "q5", "answer_md": "a5"},
    ]

    total, older = store.load_turns_page("chat-1", limit=2, before_turn_index=4)

    assert total == 6
    assert older == [
        {"question": "q2", "answer_md": "a2"},
        {"question": "q3", "answer_md": "a3"},
    ]


def test_chat_store_appends_and_loads_execution_steps(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    store.append_execution_step(
        "chat-1",
        {
            "turn_idx": 0,
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：检查",
            "detail_text": "检查 main.py",
        },
    )

    assert store.load_execution_steps("chat-1", turn_idx=0) == [
        {
            "_store_step_index": 0,
            "turn_idx": 0,
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：检查",
            "detail_text": "检查 main.py",
        }
    ]


def test_chat_store_can_load_chat_without_execution_steps(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    store.replace_turns("chat-1", [{"question": "q", "answer_md": "a"}])
    store.append_execution_step("chat-1", {"turn_idx": 0, "list_text": "heavy step"})

    chat = store.load_chat("chat-1", include_execution_steps=False)

    assert chat is not None
    assert chat["turns"] == [{"question": "q", "answer_md": "a"}]
    assert "execution_steps" not in chat


def test_chat_store_prunes_execution_steps_per_turn(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db", max_execution_steps_per_turn=3)
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    for idx in range(5):
        store.append_execution_step("chat-1", {"turn_idx": 0, "list_text": f"step {idx}"})

    assert [step["list_text"] for step in store.load_execution_steps("chat-1", turn_idx=0)] == [
        "step 2",
        "step 3",
        "step 4",
    ]


def test_chat_store_loads_recent_execution_steps_with_total_count(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    for idx in range(150):
        store.append_execution_step("chat-1", {"turn_idx": 2, "list_text": f"step {idx}"})

    total, rows = store.load_recent_execution_steps("chat-1", turn_idx=2, limit=10)

    assert total == 150
    assert [row["list_text"] for row in rows] == [f"step {idx}" for idx in range(140, 150)]


def test_chat_store_execution_pages_keep_duplicate_rows_distinct(tmp_path):
    store = ChatStore(tmp_path / "execution.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    for _ in range(5):
        store.append_execution_step("chat", {"turn_idx": 0, "step": "same"})
    total, tail = store.load_recent_execution_steps("chat", limit=2)
    assert total == 5
    remaining, previous = store.load_recent_execution_steps("chat", limit=2, before_step_index=tail[0]["_store_step_index"])
    assert remaining == 3
    assert [row["_store_step_index"] for row in previous + tail] == [1, 2, 3, 4]
    assert all(row["step"] == "same" for row in previous + tail)


def test_chat_store_concurrent_execution_step_appends_get_unique_indexes(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    def append_steps(worker_idx):
        for step_idx in range(25):
            store.append_execution_step(
                "chat-1",
                {"turn_idx": 0, "list_text": f"{worker_idx}-{step_idx}"},
            )

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(append_steps, idx) for idx in range(20)]
        for future in as_completed(futures):
            future.result()

    steps = store.load_execution_steps("chat-1", turn_idx=0)
    assert len(steps) == 500


def test_chat_store_replace_execution_steps_and_meta_round_trip(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    store.replace_execution_steps(
        "chat-1",
        [
            {"turn_idx": 0, "list_text": "old"},
            {"turn_idx": 1, "list_text": "new"},
        ],
    )
    store.set_meta("legacy_json_migration_complete", "1")

    assert [step["list_text"] for step in store.load_execution_steps("chat-1")] == ["old", "new"]
    assert store.get_meta("legacy_json_migration_complete") == "1"


def test_execution_cursor_avoids_repeated_count_and_shares_full_loader_identity(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "cursor.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    for i in range(5):
        store.append_execution_step("chat", {"item_id": "repeated", "step": str(i)})
    full = store.load_execution_steps("chat")
    sql = []
    connect = store._connect
    def traced():
        with connect() as conn:
            conn.set_trace_callback(sql.append)
            yield conn
    monkeypatch.setattr(store, "_connect", contextmanager(traced))
    _total, tail = store.load_recent_execution_steps("chat", limit=2, include_total=False)
    _total, previous = store.load_recent_execution_steps(
        "chat", limit=2, before_step_index=tail[0]["_store_step_index"], include_total=False)
    assert [row["_store_step_index"] for row in previous + tail] == [row["_store_step_index"] for row in full[1:]]
    assert not any("COUNT(" in statement.upper() for statement in sql)
    assert len(tail) == len(previous) == 2


def test_v2_identity_replay_conflict_outbox_and_retention(tmp_path):
    store = ChatStore(tmp_path / "v2.db")
    store.initialize()
    fact = {"event_id": "stable", "kind": "future_kind", "chat_id": "off-screen", "body": {"value": 1}}
    first = store.commit_durable_fact(pair_id="pair", domain="events", envelope=fact)
    assert store.commit_durable_fact(pair_id="pair", domain="events", envelope=fact) == first
    assert len(store.pending_outbox(pair_id="pair")) == 1
    with pytest.raises(ValueError, match="EVENT_ID_CONFLICT"):
        store.commit_durable_fact(pair_id="pair", domain="events", envelope={**fact, "body": {"value": 2}})
    with store._connect() as conn:
        assert conn.execute("SELECT reason FROM identity_quarantine").fetchone()["reason"] == "EVENT_ID_CONFLICT"
        conn.execute("UPDATE v2_feed_state SET retained_from=3 WHERE pair_id='pair' AND domain='__pair__'")
    with pytest.raises(ValueError, match="SNAPSHOT_REQUIRED"):
        store.replay_after(pair_id="pair", domain="events", sync_sequence=0)


def test_shared_v2_identity_matrix_matches_store(tmp_path):
    matrix = json.loads((Path(__file__).parent / "fixtures" / "remote_protocol_v2_contract_matrix.json").read_text(encoding="utf-8"))
    store = ChatStore(tmp_path / "matrix.db")
    store.initialize()
    for row in matrix["identity"]:
        store.commit_durable_fact(pair_id="pair", domain="events", envelope=row["first"])
        if row["result"] == "exact":
            store.commit_durable_fact(pair_id="pair", domain="events", envelope=row["second"])
        else:
            with pytest.raises(ValueError, match="EVENT_ID_CONFLICT"):
                store.commit_durable_fact(pair_id="pair", domain="events", envelope=row["second"])


def test_v2_migration_restarts_backfill_then_enters_read_only_recovery_on_validation_failure(tmp_path):
    path = tmp_path / "restart.db"
    store = ChatStore(path)
    store.initialize()
    store.upsert_chat({"id": "legacy", "title": "Legacy"})
    store.replace_turns("legacy", [{"question": "q", "answer_md": "a", "session_id": "provider-session", "turn_id": "provider-turn"}])
    with store._connect() as conn:
        conn.execute("DELETE FROM canonical_messages")
        conn.execute("DELETE FROM canonical_turns")
        conn.execute("UPDATE meta SET value='backfill' WHERE key=?", (V2_MIGRATION_KEY,))
    ChatStore(path).initialize()
    with store._connect() as conn:
        turn = conn.execute("SELECT * FROM canonical_turns").fetchone()
        messages = conn.execute("SELECT role,message_id FROM canonical_messages ORDER BY role").fetchall()
    assert turn["provider_session_id"] == "provider-session"
    assert turn["provider_turn_id"] == "provider-turn"
    assert {row["role"] for row in messages} == {"user", "assistant"}
    assert store.v2_writes_enabled

    with store._connect() as conn:
        conn.execute("INSERT INTO chats(id) VALUES('')")
        conn.execute("UPDATE meta SET value='validate' WHERE key=?", (V2_MIGRATION_KEY,))
    ChatStore(path).initialize()
    assert store.get_meta(V2_MIGRATION_KEY) == "read-only-recovery"
    assert not store.v2_writes_enabled
    with pytest.raises(RuntimeError, match="V2_READ_ONLY_RECOVERY"):
        store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id":"blocked","kind":"status","chat_id":"legacy","body":{}})


def test_v2_signed_int64_sync_and_execution_overflow_roll_back_both_halves(tmp_path):
    store = ChatStore(tmp_path / "overflow.db")
    store.initialize()
    with store._connect() as conn:
        conn.execute("INSERT INTO v2_feed_state(pair_id,domain,sync_sequence) VALUES('pair','__pair__',?)", (MAX_INT64,))
    with pytest.raises(OverflowError, match="SYNC_SEQUENCE_OVERFLOW"):
        store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id":"sync-overflow","kind":"status","chat_id":"chat","body":{}})
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM durable_facts").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM publication_outbox").fetchone()["n"] == 0
        conn.execute("UPDATE v2_feed_state SET sync_sequence=0 WHERE pair_id='pair'")
        conn.execute("INSERT INTO v2_chat_state(chat_id,execution_sequence) VALUES('chat',?)", (MAX_INT64,))
    with pytest.raises(OverflowError, match="EXECUTION_SEQUENCE_OVERFLOW"):
        store.commit_durable_fact(pair_id="pair", domain="events", execution=True, envelope={"event_id":"execution-overflow","kind":"execution_entry","chat_id":"chat","body":{}})
    with store._connect() as conn:
        assert conn.execute("SELECT sync_sequence FROM v2_feed_state WHERE pair_id='pair'").fetchone()["sync_sequence"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM durable_facts").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM publication_outbox").fetchone()["n"] == 0


def test_v2_replay_scope_validation_unknown_feed_and_identity_reconciliation(tmp_path):
    store = ChatStore(tmp_path / "scope.db")
    store.initialize()
    assert store.replay_after(pair_id="unknown", domain="events", sync_sequence=0) == []
    fact = {"event_id":"scoped","kind":"status","chat_id":"chat","body":{}}
    store.commit_durable_fact(pair_id="pair-a", domain="events", envelope=fact)
    with pytest.raises(ValueError, match="EVENT_ID_CONFLICT"):
        store.commit_durable_fact(pair_id="pair-b", domain="events", envelope=fact)
    with pytest.raises(ValueError, match="FORBIDDEN_DURABLE_METADATA"):
        store.commit_durable_fact(pair_id="pair-a", domain="events", envelope={"event_id":"epoch","kind":"status","chat_id":"chat","epoch":"x","body":{}})
    with pytest.raises(ValueError, match="INVALID_CANONICAL_VALUE"):
        store.commit_durable_fact(pair_id="pair-a", domain="events", envelope={"event_id":"nan","kind":"status","chat_id":"chat","body":{"value":float("nan")}})
    store.replace_turns("chat", [{"question":"old","answer_md":"answer"}])
    with store._connect() as conn:
        old_ids = {r["message_id"] for r in conn.execute("SELECT message_id FROM canonical_messages WHERE chat_id='chat'")}
    store.replace_turns("chat", [{"question":"new","answer_md":"answer"}])
    with store._connect() as conn:
        new_ids = {r["message_id"] for r in conn.execute("SELECT message_id FROM canonical_messages WHERE chat_id='chat'")}
    assert old_ids.isdisjoint(new_ids)
    store.delete_chat("chat")
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM canonical_turns WHERE chat_id='chat'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM v2_chat_state WHERE chat_id='chat'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM durable_facts WHERE event_id='scoped'").fetchone()["n"] == 1
    assert store.replay_after(pair_id="pair-a", domain="events", sync_sequence=0)
