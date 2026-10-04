from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path
import pytest

from chat_store import ChatStore
from chat_store import CLEAR_OPERATION_STATES, MAX_INT64, V2_MIGRATION_KEY


def test_answer_times_survive_store_and_recover_only_owner_final(tmp_path):
    store = ChatStore(tmp_path / "answer-time.db")
    store.initialize()
    turns = [
        {"question": "q0", "answer_md": "a0", "created_at": 100, "answer_at": 400},
        {"question": "q1", "answer_md": "a1", "created_at": 500},
        {"question": "q2", "answer_md": "a2", "created_at": 900},
    ]
    store.replace_turns("owner", turns)
    store.replace_execution_steps("other", [{"turn_idx": 2, "raw_kind": "final", "ts": 9999}])
    store.replace_execution_steps("owner", [{"turn_idx": 1, "raw_kind": "final", "ts": 800}])
    loaded = store.load_turns("owner")
    assert [turn.get("answer_at") for turn in loaded] == [400, 800, None]
    assert [turn["created_at"] for turn in loaded] == [100, 500, 900]
    total, page = store.load_turns_page("owner", limit=2)
    assert total == 3
    assert [turn.get("answer_at") for turn in page] == [800, None]


@pytest.mark.parametrize("active_pinned", [False, True])
def test_activity_timestamp_is_monotonic_and_pinned_first(tmp_path, active_pinned):
    store = ChatStore(tmp_path / "activity.db")
    store.initialize()
    for chat in [
        {"id": "pinned", "pinned": True, "updated_at": 20.0},
        {"id": "recent", "updated_at": 15.0},
        {"id": "owner", "pinned": active_pinned, "updated_at": 1.0},
    ]:
        store.upsert_chat(dict(title=chat["id"], model="codex/main", created_at=1.0, **chat))
    activity = {"id": "owner", "title": "owner", "model": "codex/main",
                "created_at": 1.0, "updated_at": 30.0, "pinned": active_pinned}
    store.upsert_chat(activity)
    expected = ["owner", "pinned", "recent"] if active_pinned else ["pinned", "owner", "recent"]
    assert [chat["id"] for chat in store.list_chat_summaries()] == expected
    for stamp in [30.0, 2.0]:
        store.upsert_chat(dict(activity, updated_at=stamp))
        assert store.load_chat("owner")["updated_at"] == 30.0
        assert [chat["id"] for chat in store.list_chat_summaries()] == expected


def test_execution_replace_is_atomic_and_source_read_has_one_owner(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "atomic-execution.db")
    store.initialize()
    store.replace_execution_steps("owner", [{"list_text": "old"}])
    store.replace_execution_steps("other", [{"list_text": "other"}])
    original = store._append_execution_step_on_conn
    calls = 0

    def fail_second(conn, chat_id, step):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("replacement interrupted")
        return original(conn, chat_id, step)

    monkeypatch.setattr(store, "_append_execution_step_on_conn", fail_second)
    with pytest.raises(RuntimeError, match="replacement interrupted"):
        store.replace_execution_steps("owner", [{"list_text": "new-1"}, {"list_text": "new-2"}])
    source = store.read_execution_projection_source("owner")
    assert [step["list_text"] for step in source["steps"]] == ["old"]
    assert source["chat_id"] == "owner"
    assert [step["list_text"] for step in store.read_execution_projection_source("other")["steps"]] == ["other"]


def test_execution_projection_snapshot_freezes_pages_and_scopes_cursor(tmp_path):
    store = ChatStore(tmp_path / "projection-page.db")
    store.initialize()
    store.replace_execution_steps("owner", [{"list_text": str(i)} for i in range(5)])
    source = store.read_execution_projection_source("owner")
    rows = [{"row_id": f"row-{i}", "list_text": str(i)} for i in range(5)]
    snapshot = store.create_execution_projection_snapshot(
        chat_id="owner", revision=source["revision"],
        source_hash=source["source_hash"], rows=rows,
    )
    tail = store.load_execution_projection_page(
        pair_id="pair", domain="events", chat_id="owner", secret="secret",
        snapshot_id=snapshot, limit=2,
    )
    assert [row["row_id"] for row in tail["rows"]] == ["row-3", "row-4"]
    store.replace_execution_steps("owner", [{"list_text": "mutated"}])
    older = store.load_execution_projection_page(
        pair_id="pair", domain="events", chat_id="owner", secret="secret",
        snapshot_id=snapshot, limit=2, cursor=tail["cursor"],
    )
    assert [row["row_id"] for row in older["rows"]] == ["row-1", "row-2"]
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        store.load_execution_projection_page(
            pair_id="other", domain="events", chat_id="owner", secret="secret",
            snapshot_id=snapshot, cursor=tail["cursor"],
        )
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        store.create_execution_projection_snapshot(
            chat_id="owner", revision=source["revision"],
            source_hash=source["source_hash"], rows=rows,
        )
    with pytest.raises(ValueError, match="SNAPSHOT_MISSING"):
        store.load_execution_projection_page(
            pair_id="pair", domain="events", chat_id="other", secret="secret",
            snapshot_id=snapshot, cursor=tail["cursor"],
        )
    with pytest.raises(ValueError, match="EXPIRED"):
        store.load_execution_projection_page(
            pair_id="pair", domain="events", chat_id="owner", secret="secret",
            snapshot_id=snapshot, cursor=tail["cursor"], now=time.time() + 901,
        )
    with store._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO v2_chat_state(chat_id,revision,execution_sequence) VALUES(?,?,?)",
                     ("owner", source["revision"] + 1, 0))
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        store.load_execution_projection_page(
            pair_id="pair", domain="events", chat_id="owner", secret="secret",
            snapshot_id=snapshot, cursor=tail["cursor"],
        )


def test_clear_reconciliation_fixture_is_shared_byte_for_byte():
    local = Path(__file__).parent / "fixtures" / "clear_reconciliation_contract_matrix.json"
    remote = Path(__file__).parents[2] / "rc" / "test" / "fixtures" / "clear_reconciliation_contract_matrix.json"
    assert local.read_bytes() == remote.read_bytes()
    matrix = json.loads(local.read_text(encoding="utf-8"))
    assert matrix["version"] == 2
    for case in matrix["cases"]:
        assert isinstance(case["given"], dict) and case["given"]["owner"] == "a"
        assert case.get("events") or case.get("actions")
        assert isinstance(case["expected"], dict) and case["expected"]
    assert {case["id"] for case in matrix["cases"]} == {
        "ordered_clear", "resend_before_clear", "higher_revision_gap", "stale_data",
        "duplicate_lifecycle", "terminal_outcomes", "supersession", "other_owner",
        "stale_reconnect_history", "buffer_capacity_timeout",
        "cosmetic_empty_history", "invalid_clear_contract",
    }


def test_clear_reconciliation_authority_is_content_free_and_revision_consistent(tmp_path):
    store = ChatStore(tmp_path / "authority.db")
    store.initialize()
    store.upsert_chat({"id": "owner-a", "title": "A", "model": "codex/main",
                       "created_at": 1.0, "updated_at": 1.0, "turns": []})
    operation = store.begin_clear_operation(
        "owner-a", idempotency_key="request-a", operation_id="operation-a"
    )
    authority = store.get_clear_reconciliation_authority("owner-a")
    assert authority == {
        "revision": operation["revision"],
        "clear_operation": {
            "operation_id": "operation-a",
            "revision": operation["revision"],
            "state": "completed_no_message",
        },
    }
    encoded = json.dumps(authority)
    assert "snapshot" not in encoded and "attachments" not in encoded and "path" not in encoded


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


def test_chat_models_round_trip_for_empty_and_populated_owners_after_restart(tmp_path):
    path = tmp_path / "chat-models.db"
    store = ChatStore(path)
    store.initialize()
    store.upsert_chat({"id": "chat-a", "title": "A", "model": "codex/main", "created_at": 1.0, "updated_at": 1.0})
    store.replace_turns("chat-a", [{"question": "q", "answer_md": "a", "model": "codex/main"}])
    store.upsert_chat({"id": "chat-b", "title": "B", "model": "kimi/main", "created_at": 2.0, "updated_at": 2.0})
    restarted = ChatStore(path)
    restarted.initialize()

    summaries = {row["id"]: row for row in restarted.list_chat_summaries()}
    assert summaries["chat-a"]["model"] == "codex/main"
    assert summaries["chat-b"]["model"] == "kimi/main"
    assert summaries["chat-b"]["turn_count"] == 0
    assert restarted.load_chat("chat-a")["model"] == "codex/main"
    assert restarted.load_chat("chat-b")["model"] == "kimi/main"


def test_empty_chat_title_metadata_survives_restart_and_delete_or_rename_releases_name(tmp_path):
    path = tmp_path / "empty-title-restart.db"
    store = ChatStore(path)
    store.initialize()
    store.upsert_chat(
        {
            "id": "chat-base",
            "title": "新聊天",
            "model": "codex/main",
            "created_at": 1.0,
            "updated_at": 1.0,
            "title_source": "default",
            "title_revision": 1,
        }
    )
    store.upsert_chat(
        {
            "id": "chat-one",
            "title": "新聊天1",
            "model": "kimi/main",
            "created_at": 2.0,
            "updated_at": 2.0,
            "title_source": "default",
            "title_revision": 1,
        }
    )

    restarted = ChatStore(path)
    restarted.initialize()
    loaded = restarted.load_chat("chat-one")
    assert (loaded["id"], loaded["title"], loaded["model"], loaded["title_revision"], loaded["turns"]) == (
        "chat-one", "新聊天1", "kimi/main", 1, []
    )

    loaded.update({"title": "manual", "title_manual": True, "title_source": "manual", "title_revision": 2})
    restarted.upsert_chat(loaded)
    assert {row["title"] for row in restarted.list_chat_summaries()} == {"新聊天", "manual"}
    restarted.delete_chat("chat-base")
    assert [row["title"] for row in restarted.list_chat_summaries()] == ["manual"]


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


def test_chat_store_replaces_kimi_lifecycle_with_one_completed_result(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    identity = {"turn_idx": 0, "thread_id": "session-1", "turn_id": "turn-1", "item_id": "tool-1"}
    store.append_execution_step("chat-1", {
        **identity, "event_type": "item_started", "source_kind": "tool.call.started",
        "list_text": "running", "_execution_uid": "started",
    })
    completed = {
        **identity, "event_type": "item_completed", "source_kind": "tool.result",
        "list_text": "completed", "detail_text": "result output", "status": "completed",
        "event_id": "session-1:turn-1:tool-1:completed", "_execution_uid": "completed",
    }

    assert store.replace_execution_lifecycle_step("chat-1", completed) is True

    reloaded = store.load_execution_steps("chat-1")
    assert len(reloaded) == 1
    assert reloaded[0]["event_type"] == "item_completed"
    assert reloaded[0]["status"] == "completed"
    assert reloaded[0]["detail_text"] == "result output"
    assert reloaded[0]["event_id"] == "session-1:turn-1:tool-1:completed"


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


def test_clear_operation_is_atomic_idempotent_and_private(tmp_path):
    store = ChatStore(tmp_path / "clear.db")
    store.initialize()
    store.upsert_chat({"id": "chat", "codex_thread_id": "private-thread"})
    store.replace_turns("chat", [
        {"question": "/status", "local_command": "status"},
        {"question": "", "attachments": [{"path": "secret/earlier.txt"}]},
        {"question": "", "voice_metadata": {"format": "wav"}},
        {"question": "", "import_metadata": {"source": "archive"}},
        {"question": "current edited text", "model": "codex/gpt-5", "attachments": [{"path": "secret/file.txt"}]},
    ])
    store.append_execution_step("chat", {"turn_idx": 1, "list_text": "private execution"})

    operation = store.begin_clear_operation("chat", idempotency_key="request-1", pair_id="pair")
    duplicate = store.begin_clear_operation("chat", idempotency_key="request-1", pair_id="pair")

    assert duplicate["operation_id"] == operation["operation_id"]
    assert operation["state"] == "clear_acknowledged"
    assert operation["snapshot"]["question"] == "current edited text"
    assert "attachments" not in operation["snapshot"]
    assert store.load_turns("chat") == []
    assert store.load_execution_steps("chat") == []
    outbox = store.pending_outbox(pair_id="pair")
    assert len(outbox) == 2
    assert b"current edited text" not in outbox[0]["payload"]
    assert b"secret/file.txt" not in outbox[0]["payload"]


def test_clear_operation_dispatch_claim_and_recovery_transitions(tmp_path):
    store = ChatStore(tmp_path / "claim.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    store.replace_turns("chat", [{"question": "hello"}])
    operation = store.begin_clear_operation("chat", idempotency_key="request")
    assert store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="chat", revision=operation["revision"])["state"] == "resend_dispatched"
    assert store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="chat", revision=operation["revision"]) is None
    assert store.transition_clear_operation(operation["operation_id"], "resend_blocked", failure_code="TIMEOUT")["state"] == "resend_blocked"
    assert store.transition_clear_operation(operation["operation_id"], "completed_clear_only")["terminal"] is True
    lifecycle = [json.loads(row["payload"])["body"]["state"] for row in store.pending_outbox()]
    assert lifecycle == ["requested", "clear_acknowledged", "resend_dispatched", "resend_blocked", "completed_clear_only"]
    assert all("hello" not in row["payload"].decode("utf-8") for row in store.pending_outbox())


def test_clear_resend_sound_consumption_is_owner_revision_scoped_and_restart_safe(tmp_path):
    store = ChatStore(tmp_path / "sound.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="sound")

    # Permission cannot be consumed before synchronous worker acceptance.
    assert store.consume_clear_resend_sound(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    ) is None
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    assert store.consume_clear_resend_sound(
        operation["operation_id"], chat_id="wrong", revision=operation["revision"]
    ) is None
    assert store.consume_clear_resend_sound(
        operation["operation_id"], chat_id="owner", revision=operation["revision"] + 1
    ) is None

    def consume():
        return store.consume_clear_resend_sound(
            operation["operation_id"], chat_id="owner", revision=operation["revision"]
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: consume(), range(16)))
    grants = [result for result in results if result is not None]
    assert len(grants) == 1
    assert grants[0]["sound_consumed_at"] is not None

    restarted = ChatStore(store.db_path)
    restarted.initialize()
    assert restarted.consume_clear_resend_sound(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    ) is None


def test_legacy_clear_operation_row_decodes_but_has_no_replayable_sound_permission(tmp_path):
    store = ChatStore(tmp_path / "legacy-sound.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first", "attachments": [{"path": "old"}]}])
    operation = store.begin_clear_operation("owner", idempotency_key="legacy")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    with store._connect() as conn:
        conn.execute(
            "UPDATE clear_operations SET sound_permission=0,snapshot_json=? WHERE operation_id=?",
            (json.dumps({"question": "first", "attachments": [{"path": "old"}]}), operation["operation_id"]),
        )

    decoded = store.get_clear_operation(operation["operation_id"])
    assert decoded["snapshot"]["question"] == "first"
    assert decoded["snapshot"]["attachments"] == [{"path": "old"}]
    assert store.consume_clear_resend_sound(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    ) is None


def test_initialize_alters_actual_pre_sound_schema_and_preserves_legacy_row(tmp_path):
    path = tmp_path / "pre-sound.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE clear_operations (operation_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, "
            "revision INTEGER NOT NULL, idempotency_key TEXT NOT NULL, state TEXT NOT NULL, "
            "snapshot_json TEXT, source_turn_id TEXT, source_message_id TEXT, "
            "dispatch_claimed_at REAL, failure_code TEXT NOT NULL DEFAULT '', "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "UNIQUE(chat_id,idempotency_key), UNIQUE(chat_id,revision))"
        )
        conn.execute(
            "INSERT INTO clear_operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-op", "legacy-owner", 2, "legacy-key", "resend_dispatched",
             json.dumps({"question": "legacy", "attachments": [{"path": "old"}]}),
             None, None, 1.0, "", 1.0, 1.0),
        )

    store = ChatStore(path)
    store.initialize()
    legacy = store.get_clear_operation("legacy-op")
    assert legacy["snapshot"]["question"] == "legacy"
    assert legacy["sound_permission"] == 0
    assert legacy["sound_consumed_at"] is None
    assert store.consume_clear_resend_sound("legacy-op", chat_id="legacy-owner", revision=2) is None

    store.upsert_chat({"id": "new-owner"})
    store.replace_turns("new-owner", [{"question": "new"}])
    created = store.begin_clear_operation("new-owner", idempotency_key="new")
    assert created["sound_permission"] == 1


@pytest.mark.parametrize("terminal", ["resend_blocked", "completed_with_resend", "superseded"])
def test_terminal_clear_operation_denies_first_sound_consumption(tmp_path, terminal):
    store = ChatStore(tmp_path / f"terminal-{terminal}.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="first")
    store.claim_clear_resend_dispatch(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    )
    if terminal == "superseded":
        store.replace_turns("owner", [{"question": "newer"}])
        store.begin_clear_operation("owner", idempotency_key="newer")
    else:
        store.transition_clear_operation(operation["operation_id"], terminal)
    assert store.consume_clear_resend_sound(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    ) is None


def test_dispatch_claim_requires_matching_owner_and_revision(tmp_path):
    store = ChatStore(tmp_path / "claim-identity.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    assert store.claim_clear_resend_dispatch(
        operation["operation_id"], chat_id="wrong", revision=operation["revision"]
    ) is None
    assert store.claim_clear_resend_dispatch(
        operation["operation_id"], chat_id="owner", revision=operation["revision"] + 1
    ) is None
    assert store.get_clear_operation(operation["operation_id"])["state"] == "clear_acknowledged"


def test_clear_operation_reconciliation_requires_one_matching_persisted_done_turn(tmp_path):
    store = ChatStore(tmp_path / "reconcile.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    operation_id = operation["operation_id"]
    revision = operation["revision"]
    store.claim_clear_resend_dispatch(operation_id, chat_id="owner", revision=revision)
    store.replace_turns("owner", [{
        "question": "first",
        "request_status": "done",
        "clear_operation_id": operation_id,
        "clear_revision": str(revision),
    }])

    assert store.reconcile_completed_clear_operation(
        operation_id, chat_id="owner", revision="not-a-revision"
    ) is None
    assert store.reconcile_completed_clear_operation(
        operation_id, chat_id="owner", revision=1.5
    ) is None
    assert store.reconcile_completed_clear_operation(
        operation_id, chat_id="other", revision=revision
    ) is None
    assert store.reconcile_completed_clear_operation(
        operation_id, chat_id="owner", revision=revision + 1
    ) is None
    completed = store.reconcile_completed_clear_operation(
        operation_id, chat_id="owner", revision=revision
    )
    assert completed["state"] == "completed_with_resend"
    assert store.reconcile_completed_clear_operation(
        operation_id, chat_id="owner", revision=revision
    )["state"] == "completed_with_resend"
    lifecycle = [json.loads(row["payload"])["body"]["state"] for row in store.pending_outbox()]
    assert lifecycle.count("completed_with_resend") == 1
    assert all("first" not in row["payload"].decode("utf-8") for row in store.pending_outbox())


def test_clear_operation_reconciliation_only_repairs_uncertain_blocked_delivery(tmp_path):
    store = ChatStore(tmp_path / "reconcile-blocked.db")
    store.initialize()
    for owner, failure_code in (("uncertain", "DELIVERY_OUTCOME_UNCERTAIN"), ("failed", "PROVIDER_FAILURE")):
        store.upsert_chat({"id": owner})
        store.replace_turns(owner, [{"question": owner}])
        operation = store.begin_clear_operation(owner, idempotency_key=owner)
        store.claim_clear_resend_dispatch(operation["operation_id"], chat_id=owner, revision=operation["revision"])
        store.transition_clear_operation(
            operation["operation_id"], "resend_blocked", failure_code=failure_code
        )
        store.replace_turns(owner, [{
            "request_status": "done",
            "clear_operation_id": operation["operation_id"],
            "clear_revision": operation["revision"],
        }])
        reconciled = store.reconcile_completed_clear_operation(
            operation["operation_id"], chat_id=owner, revision=operation["revision"]
        )
        if owner == "uncertain":
            assert reconciled["state"] == "completed_with_resend"
        else:
            assert reconciled is None
            assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_blocked"


def test_clear_operation_reconciliation_refuses_ambiguous_completed_turns(tmp_path):
    store = ChatStore(tmp_path / "reconcile-ambiguous.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    evidence = {
        "request_status": "done",
        "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    store.replace_turns("owner", [dict(evidence), dict(evidence)])

    assert store.reconcile_completed_clear_operation(
        operation["operation_id"], chat_id="owner", revision=operation["revision"]
    ) is None
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_dispatched"


def test_recoverable_clear_operations_reconciles_and_omits_persisted_done_turn(tmp_path):
    store = ChatStore(tmp_path / "recoverable-reconcile.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    store.replace_turns("owner", [{
        "request_status": "done",
        "clear_operation_id": operation["operation_id"],
        "clear_revision": str(operation["revision"]),
    }])

    assert store.recoverable_clear_operations() == []
    assert store.get_clear_operation(operation["operation_id"])["state"] == "completed_with_resend"


def test_clear_operation_no_message_and_revision_overflow_are_safe(tmp_path):
    store = ChatStore(tmp_path / "empty.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    store.replace_turns("chat", [
        {"question": "/help", "local_command": True},
        {"attachments": [{"path": "only.txt"}]},
        {"voice_metadata": {"format": "wav"}},
        {"import_metadata": {"source": "archive"}},
    ])
    operation = store.begin_clear_operation("chat", idempotency_key="empty")
    assert operation["state"] == "completed_no_message"
    with store._connect() as conn:
        conn.execute("UPDATE v2_chat_state SET revision=? WHERE chat_id='chat'", (MAX_INT64,))
        before = conn.execute("SELECT COUNT(*) n FROM clear_operations").fetchone()["n"]
    with pytest.raises(OverflowError, match="CHAT_REVISION_OVERFLOW"):
        store.begin_clear_operation("chat", idempotency_key="overflow")
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM clear_operations").fetchone()["n"] == before


def test_clear_operation_contract_fixture_every_row_is_executable(tmp_path):
    matrix = json.loads((Path(__file__).parent / "fixtures" / "clear_operation_contract_matrix.json").read_text(encoding="utf-8"))
    assert set(matrix["states"]) == CLEAR_OPERATION_STATES
    for case in matrix["payload_cases"]:
        assert ChatStore._eligible_clear_payload(case["payload"]) is case["eligible"]

    store = ChatStore(tmp_path / "matrix-clear.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    store.replace_turns("chat", [{"question": "private current payload"}])
    first = store.begin_clear_operation("chat", idempotency_key="stable", pair_id="pair")
    assert store.claim_clear_resend_dispatch(first["operation_id"], chat_id="chat", revision=first["revision"])
    assert store.transition_clear_operation(first["operation_id"], "resend_blocked", failure_code="TIMEOUT")["state"] == "resend_blocked"
    restarted = ChatStore(store.db_path)
    restarted.initialize()
    replay = restarted.begin_clear_operation("chat", idempotency_key="stable", pair_id="pair")
    assert replay["operation_id"] == first["operation_id"] and replay["idempotent_replay"]
    assert restarted.claim_clear_resend_dispatch(first["operation_id"], chat_id="chat", revision=first["revision"]) is None
    restarted.upsert_chat({"id": "chat", "updated_at": 1})
    restarted.replace_turns("chat", [{"question": "new clear"}])
    newer = restarted.begin_clear_operation("chat", idempotency_key="newer", pair_id="pair")
    assert restarted.get_clear_operation(first["operation_id"])["state"] == "superseded"
    assert newer["revision"] > first["revision"]
    payloads = b"".join(row["payload"] for row in restarted.pending_outbox(pair_id="pair"))
    assert b"private current payload" not in payloads
    observed = {
        "provider_timeout": "resend_blocked",
        "concurrent_supersession": restarted.get_clear_operation(first["operation_id"])["state"],
        "privacy": "private_payload_excluded" if b"private current payload" not in payloads else "leaked",
        "restart_reconnect": "same_operation_no_redispatch" if replay["operation_id"] == first["operation_id"] else "duplicate",
    }
    for name, code in (("provider_reject", "PROVIDER_REJECTED"),):
        chat_id = f"chat-{name}"
        restarted.upsert_chat({"id": chat_id})
        restarted.replace_turns(chat_id, [{"question": name}])
        op = restarted.begin_clear_operation(chat_id, idempotency_key=name, pair_id="pair")
        observed[name] = restarted.transition_clear_operation(op["operation_id"], "resend_blocked", failure_code=code)["state"]
    overflow_store = ChatStore(tmp_path / "matrix-overflow.db")
    overflow_store.initialize()
    overflow_store.upsert_chat({"id": "overflow"})
    with overflow_store._connect() as conn:
        conn.execute("INSERT INTO v2_chat_state(chat_id,revision) VALUES('overflow',?)", (MAX_INT64,))
    with pytest.raises(OverflowError, match="CHAT_REVISION_OVERFLOW"):
        overflow_store.begin_clear_operation("overflow", idempotency_key="overflow")
    observed["revision_overflow"] = "CHAT_REVISION_OVERFLOW"
    assert observed == {row["name"]: row["outcome"] for row in matrix["lifecycle_cases"]}


def test_clear_snapshot_sanitizes_runtime_fields_and_operation_id_collision(tmp_path):
    store = ChatStore(tmp_path / "sanitize-clear.db")
    store.initialize()
    store.upsert_chat({"id": "one"})
    store.replace_turns("one", [{"question": "send", "model": "codex/main", "origin": "import",
                                  "answer_md": "private answer", "request_error": "private failure",
                                  "codex_thread_id": "private session", "attachments": []}])
    operation = store.begin_clear_operation("one", idempotency_key="one", operation_id="fixed")
    assert operation["snapshot"]["question"] == "send"
    assert operation["snapshot"]["origin"] == "import"
    assert not ({"answer_md", "request_error", "codex_thread_id"} & operation["snapshot"].keys())
    for invalid in ("", "bad\x00id"):
        store.upsert_chat({"id": f"invalid-{len(invalid)}"})
        with pytest.raises(ValueError, match="INVALID_OPERATION_ID"):
            store.begin_clear_operation(f"invalid-{len(invalid)}", idempotency_key="invalid", operation_id=invalid)
    store.upsert_chat({"id": "two"})
    store.replace_turns("two", [{"question": "other"}])
    with pytest.raises(ValueError, match="OPERATION_ID_CONFLICT"):
        store.begin_clear_operation("two", idempotency_key="two", operation_id="fixed")
def test_execution_timeline_contract_matrix_and_authenticated_pages(tmp_path):
    import json
    from pathlib import Path
    matrix = json.loads((Path(__file__).parent / "fixtures" / "execution_timeline_contract_matrix.json").read_text(encoding="utf-8"))
    assert {case["id"] for case in matrix["cases"]} == {
        "complete_projection", "initial_and_older_paging", "history_live_overlap",
        "sequence_gap", "recovery_exhaustion", "cross_owner_delivery",
        "clear_revision_race", "event_id_conflict",
    }
    store = ChatStore(tmp_path / "timeline.db")
    store.initialize()
    store.upsert_chat({"id": "owner-a"})
    store.upsert_chat({"id": "owner-b"})
    for index in range(105):
        store.commit_durable_fact(pair_id="pair", domain="events", execution=True,
            envelope={"event_id": f"a-{index}", "kind": "execution_entry",
                      "chat_id": "owner-a", "body": {"kind": "future-kind", "title": str(index)}})
    store.commit_durable_fact(pair_id="pair", domain="events", execution=True,
        envelope={"event_id": "b-1", "kind": "execution_entry", "chat_id": "owner-b",
                  "body": {"kind": "status", "title": "off screen"}})
    tail = store.load_execution_page(pair_id="pair", domain="events", chat_id="owner-a",
                                     secret="secret", limit=100, now=10)
    assert len(tail["entries"]) == 100
    assert [row["execution_sequence"] for row in tail["entries"]] == list(range(6, 106))
    older = store.load_execution_page(pair_id="pair", domain="events", chat_id="owner-a",
                                      secret="secret", cursor=tail["cursor"], now=11)
    assert [row["event_id"] for row in older["entries"]] == [f"a-{i}" for i in range(5)]
    assert store.load_execution_page(pair_id="pair", domain="events", chat_id="owner-b",
                                     secret="secret")["entries"][0]["event_id"] == "b-1"
    with pytest.raises(ValueError, match="INVALID_EXECUTION_CURSOR"):
        store.load_execution_page(pair_id="pair", domain="events", chat_id="owner-a",
                                  secret="forged", cursor=tail["cursor"], now=11)
def test_repeated_turn_save_preserves_canonical_ids_and_projection_is_idempotent(tmp_path):
    store = ChatStore(tmp_path / "stable-projection.db")
    store.initialize()
    store.upsert_chat({"id":"chat","turns":[]})
    pending = {"question":"q","answer_md":"璇锋眰涓?..","model":"codex/main"}
    store.replace_turns("chat", [pending])
    question_id = store.resolve_canonical_message_by_turn("chat", role="user", turn_index=0)
    assistant_id = store.resolve_canonical_message_by_turn("chat", role="assistant", turn_index=0)
    first = store.commit_message_execution_projection(pair_id="p",domain="events",chat_id="chat",
                                                       message_id=question_id,projection_kind="question")
    store.replace_turns("chat", [{**pending,"answer_md":"done"}])
    assert store.resolve_canonical_message_by_turn("chat", role="user", turn_index=0) == question_id
    assert store.resolve_canonical_message_by_turn("chat", role="assistant", turn_index=0) == assistant_id
    again = store.commit_message_execution_projection(pair_id="p",domain="events",chat_id="chat",
                                                       message_id=question_id,projection_kind="question")
    assert again["event_id"] == first["event_id"]
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM durable_facts WHERE event_id=?", (first["event_id"],)).fetchone()["n"] == 1
def test_execution_logical_identity_upsert_preserves_first_metadata(tmp_path):
    store = ChatStore(tmp_path / "logical.db")
    store.initialize()
    scope = {"chat_id": "chat", "revision": 1, "thread_id": "session", "turn_id": "turn",
             "provider": "codex", "agent_id": "", "native_id": "item"}
    logical_key = store.execution_logical_key(scope)
    first = {
        "logical_key": logical_key, "logical_scope": scope, "logical_scope_hash": logical_key,
        "canonical_item_id": f"execution-{logical_key}", "provider": "codex", "thread_id": "session",
        "agent_id": "", "event_id": "canonical-event",
        "_execution_uid": "stable-row", "created_at": 10.0, "revision": 1,
        "execution_sequence": 7, "item_id": "item", "turn_id": "turn",
        "list_text": "started", "detail_text": "a",
    }
    store.append_execution_step("chat", first)
    update = dict(first, event_id="replacement", _execution_uid="replacement", created_at=20.0,
                  execution_sequence=9, list_text="completed", detail_text="ab")
    assert store.update_execution_step_by_identity("chat", update)
    rows = store.load_execution_steps("chat")
    assert len(rows) == 1
    assert rows[0]["list_text"] == "completed"
    assert rows[0]["event_id"] == "canonical-event"
    assert rows[0]["_execution_uid"] == "stable-row"
    assert rows[0]["created_at"] == 10.0
    assert rows[0]["execution_sequence"] == 7


def test_durable_execution_assembler_replay_overlap_gap_and_restart(tmp_path):
    path = tmp_path / "assembler.db"
    store = ChatStore(path)
    store.initialize()
    scope = {"chat_id": "c", "revision": 2, "thread_id": "s", "turn_id": "t",
             "provider": "kimi", "agent_id": "main", "native_id": "m"}
    key = store.execution_logical_key(scope)
    assert store.apply_execution_fragment("c", key, scope, fragment_id="f1", offset=0, text="abc")["assembled"] == "abc"
    replay = store.apply_execution_fragment("c", key, scope, fragment_id="f1", offset=0, text="abc")
    assert replay["replay"] and not replay["accepted"]
    overlap = store.apply_execution_fragment("c", key, scope, fragment_id="f2", offset=2, text="cde")
    assert overlap["assembled"] == "abcde"
    gap = store.apply_execution_fragment("c", key, scope, fragment_id="f3", offset=7, text="hi")
    assert gap["pending_gap"] and gap["assembled"] == "abcde"
    restarted = ChatStore(path)
    fill = restarted.apply_execution_fragment("c", key, scope, fragment_id="f4", offset=5, text="fg")
    assert not fill["pending_gap"] and fill["assembled"] == "abcdefghi"


def test_durable_execution_assembler_persists_conflicts_without_mutation(tmp_path):
    store = ChatStore(tmp_path / "conflict.db")
    store.initialize()
    scope = {"chat_id": "c", "revision": 1, "thread_id": "s", "turn_id": "t",
             "provider": "codex", "agent_id": "", "native_id": "m"}
    key = store.execution_logical_key(scope)
    store.apply_execution_fragment("c", key, scope, fragment_id="f", offset=0, text="abc")
    conflict = store.apply_execution_fragment("c", key, scope, fragment_id="f", offset=0, text="axc")
    assert conflict["conflict"] and conflict["assembled"] == "abc"
    records = store.load_execution_fragment_conflicts("c", key)
    assert records[-1]["reason"] == "FRAGMENT_ID_CONFLICT"


def test_assembler_transaction_materializes_projection_and_same_start_ranges(tmp_path):
    path = tmp_path / "atomic-assembler.db"
    store = ChatStore(path)
    store.initialize()
    scope = {"chat_id": "c", "revision": 4, "thread_id": "s", "turn_id": "0",
             "provider": "kimi", "agent_id": "main", "native_id": "tool-1"}
    key = store.execution_logical_key(scope)
    safe_seed = {"event_type": "agent_message_delta", "display_kind": "commentary",
                 "list_text": "正在处理", "detail_text": ""}
    store.apply_execution_fragment("c", key, scope, fragment_id="long", offset=0, text="abcdef",
                                   projection_seed=safe_seed)
    # Simulate a crash before the UI projection code runs: the same SQLite
    # transaction already left a stable, reloadable canonical item.
    reloaded = ChatStore(path).load_execution_steps("c")
    assert len(reloaded) == 1 and reloaded[0]["list_text"] == "正在处理"
    assert reloaded[0]["detail_text"] == ""
    stable = (reloaded[0]["event_id"], reloaded[0]["created_at"], reloaded[0]["_execution_uid"])
    shorter = store.apply_execution_fragment("c", key, scope, fragment_id="short", offset=0, text="abc")
    assert shorter["assembled"] == "abcdef"
    longer = store.apply_execution_fragment("c", key, scope, fragment_id="longer", offset=0, text="abcdefgh")
    assert longer["assembled"] == "abcdefgh"
    after = store.load_execution_steps("c")[0]
    assert (after["event_id"], after["created_at"], after["_execution_uid"]) == stable
    conflict = store.apply_execution_fragment("c", key, scope, fragment_id="bad", offset=0, text="abX")
    assert conflict["conflict"] and conflict["assembled"] == "abcdefgh"


def test_projection_rejects_logical_key_and_scope_tampering(tmp_path):
    store = ChatStore(tmp_path / "tamper.db")
    store.initialize()
    scope = {"chat_id": "c", "revision": 1, "thread_id": "s", "turn_id": "t",
             "provider": "codex", "agent_id": "", "native_id": "tool"}
    key = store.execution_logical_key(scope)
    result = store.apply_execution_fragment("c", key, scope, fragment_id="f", offset=0, text="x")
    projection = result["projection"]
    with pytest.raises(ValueError, match="LOGICAL_KEY_MISMATCH"):
        store.upsert_execution_step("c", dict(projection, logical_scope={**scope, "provider": "kimi"}))
    with pytest.raises(ValueError, match="SCOPE_CONFLICT"):
        store.upsert_execution_step("c", dict(projection, provider="kimi"))


def test_crash_projection_seed_never_leaks_private_or_raw_command_into_list(tmp_path):
    store = ChatStore(tmp_path / "safe-crash.db")
    store.initialize()
    private_scope = {"chat_id": "c", "revision": 1, "thread_id": "s", "turn_id": "t",
                     "provider": "kimi", "agent_id": "main", "native_id": "thinking"}
    private_key = store.execution_logical_key(private_scope)
    store.apply_execution_fragment("c", private_key, private_scope, fragment_id="p", offset=0,
        text="secret reasoning /private/path", projection_seed={"event_type":"agent_message_delta",
        "display_kind":"thinking", "list_text":"正在分析问题", "detail_text":"", "source_detail":{},
        "private_reasoning":True})
    command_scope = {**private_scope, "native_id": "tool", "provider": "codex"}
    command_key = store.execution_logical_key(command_scope)
    store.apply_execution_fragment("c", command_key, command_scope, fragment_id="c", offset=0,
        text="Running: cat C:\\secret\\token.txt", projection_seed={"event_type":"agent_message_delta",
        "display_kind":"command", "list_text":"正在执行命令", "detail_text":""})
    rows = ChatStore(store.db_path).load_execution_steps("c")
    assert [row["list_text"] for row in rows] == ["正在分析问题", "正在执行命令"]
    assert rows[0]["detail_text"] == "" and rows[0]["source_detail"] == {}
    assert all("secret" not in row["list_text"] and "Running:" not in row["list_text"] for row in rows)


def test_hidden_crash_projection_is_promoted_safely_by_exact_replay(tmp_path):
    path = tmp_path / "hidden-replay.db"
    store = ChatStore(path)
    store.initialize()
    scope = {"chat_id": "c", "revision": 1, "thread_id": "s", "turn_id": "t",
             "provider": "kimi", "agent_id": "main", "native_id": "thinking"}
    key = store.execution_logical_key(scope)
    store.apply_execution_fragment("c", key, scope, fragment_id="f", offset=0, text="PRIVATE RAW")
    assert ChatStore(path).load_execution_steps("c") == []
    replay = ChatStore(path).apply_execution_fragment("c", key, scope, fragment_id="f", offset=0,
        text="PRIVATE RAW", projection_seed={"event_type":"agent_message_delta", "display_kind":"thinking",
        "list_text":"正在分析问题", "detail_text":"", "private_reasoning":True, "source_detail":{}})
    assert replay["replay"] and replay["projection"]["projection_ready"]
    rows = ChatStore(path).load_execution_steps("c")
    assert len(rows) == 1 and rows[0]["list_text"] == "正在分析问题"
    assert rows[0]["detail_text"] == "" and "PRIVATE RAW" not in json.dumps(rows[0], ensure_ascii=False)


def test_leading_offset_gap_persists_until_origin_is_filled_after_restart(tmp_path):
    path = tmp_path / "leading-gap.db"
    store = ChatStore(path)
    store.initialize()
    scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
             "provider":"codex","agent_id":"","native_id":"stream"}
    key = store.execution_logical_key(scope)
    late = store.apply_execution_fragment("c", key, scope, fragment_id="late", offset=3, text="def",
        projection_seed={"event_type":"agent_message_delta","display_kind":"commentary",
                         "list_text":"working","detail_text":""})
    assert late["pending_gap"] and late["assembled"] == ""
    assert ChatStore(path).load_execution_steps("c")[0]["incomplete"] is True
    filled = ChatStore(path).apply_execution_fragment("c", key, scope, fragment_id="first", offset=0, text="abc",
        projection_seed={"event_type":"agent_message_delta","display_kind":"commentary",
                         "list_text":"working","detail_text":"abcdef"})
    assert not filled["pending_gap"] and filled["assembled"] == "abcdef"


def test_recent_execution_sql_excludes_hidden_rows_from_total_and_window(tmp_path):
    store = ChatStore(tmp_path / "hidden-page.db")
    store.initialize()
    store.append_execution_step("c", {"list_text":"ready-1","detail_text":"ready-1"})
    def hidden(native_id):
        scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
                 "provider":"kimi","agent_id":"main","native_id":native_id}
        store.apply_execution_fragment("c", store.execution_logical_key(scope), scope,
                                       fragment_id="f", offset=0, text="hidden")
    hidden("hidden-middle")
    store.append_execution_step("c", {"list_text":"ready-2","detail_text":"ready-2"})
    hidden("hidden-latest")
    total, latest = store.load_recent_execution_steps("c", limit=1)
    assert total == 2 and [row["list_text"] for row in latest] == ["ready-2"]
    total, older = store.load_recent_execution_steps("c", limit=10,
                                                      before_step_index=latest[0]["_store_step_index"])
    assert total == 1 and [row["list_text"] for row in older] == ["ready-1"]


def test_private_latch_redacts_prior_public_state_and_survives_restart(tmp_path):
    path = tmp_path / "privacy-transition.db"
    store = ChatStore(path); store.initialize()
    scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
             "provider":"kimi","agent_id":"main","native_id":"thinking"}
    key = store.execution_logical_key(scope)
    public_seed = {"event_type":"agent_message_delta","display_kind":"thinking",
                   "list_text":"正在分析问题","detail_text":"PUBLIC RAW","source_detail":{"raw":"PUBLIC RAW"}}
    private_seed = {"event_type":"agent_message_delta","display_kind":"thinking",
                    "list_text":"正在分析问题","detail_text":"","private_reasoning":True,"source_detail":{}}
    store.apply_execution_fragment("c", key, scope, fragment_id="public", offset=0, text="PUBLIC", projection_seed=public_seed)
    store.apply_execution_fragment("c", key, scope, fragment_id="private", offset=6, text="SECRET", projection_seed=private_seed)
    with store._connect() as conn:
        raw_state = conn.execute("SELECT state_json FROM execution_assemblers WHERE chat_id='c' AND logical_key=?", (key,)).fetchone()["state_json"]
    assert "PUBLIC" not in raw_state and "SECRET" not in raw_state
    row = ChatStore(path).load_execution_steps("c")[0]
    assert row["private_reasoning"] and row["detail_text"] == "" and row["source_detail"] == {}
    resumed = ChatStore(path).apply_execution_fragment("c", key, scope, fragment_id="later", offset=3,
        text="LIC-CHANGED", projection_seed=public_seed)
    assert resumed["accepted"] and resumed["private_reasoning"] and not resumed.get("conflict")
    with store._connect() as conn:
        raw_state = conn.execute("SELECT state_json FROM execution_assemblers WHERE chat_id='c' AND logical_key=?", (key,)).fetchone()["state_json"]
    assert "CHANGED" not in raw_state


def test_private_first_restart_then_public_overlap_never_downgrades(tmp_path):
    path = tmp_path / "privacy-first.db"
    store = ChatStore(path); store.initialize()
    scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
             "provider":"kimi","agent_id":"main","native_id":"thinking"}
    key = store.execution_logical_key(scope)
    private_seed = {"event_type":"agent_message_delta","display_kind":"thinking",
                    "list_text":"正在分析问题","detail_text":"","private_reasoning":True,"source_detail":{}}
    store.apply_execution_fragment("c", key, scope, fragment_id="private", offset=0, text="SECRET", projection_seed=private_seed)
    public_seed = {"event_type":"agent_message_delta","display_kind":"thinking",
                   "list_text":"正在分析问题","detail_text":"PUBLIC","source_detail":{"raw":"PUBLIC"}}
    resumed = ChatStore(path).apply_execution_fragment("c", key, scope, fragment_id="public", offset=3,
        text="RET-PUBLIC", projection_seed=public_seed)
    assert resumed["private_reasoning"] and not resumed.get("conflict")
    row = ChatStore(path).load_execution_steps("c")[0]
    assert row["private_reasoning"] and row["detail_text"] == "" and row["source_detail"] == {}


def test_exact_replay_can_escalate_privacy_before_restart_and_continuation(tmp_path):
    path = tmp_path / "exact-private.db"
    store = ChatStore(path); store.initialize()
    scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
             "provider":"kimi","agent_id":"main","native_id":"thinking"}
    key = store.execution_logical_key(scope)
    public = {"event_type":"agent_message_delta","display_kind":"thinking","list_text":"正在分析问题",
              "detail_text":"PUBLIC","source_detail":{"raw":"PUBLIC"}}
    private = {"event_type":"agent_message_delta","display_kind":"thinking","list_text":"正在分析问题",
               "detail_text":"","private_reasoning":True,"source_detail":{}}
    store.apply_execution_fragment("c", key, scope, fragment_id="same", offset=0, text="PUBLIC", projection_seed=public)
    replay = store.apply_execution_fragment("c", key, scope, fragment_id="same", offset=0, text="PUBLIC", projection_seed=private)
    assert replay["replay"] and replay["private_reasoning"]
    with store._connect() as conn:
        state_json = conn.execute("SELECT state_json FROM execution_assemblers WHERE chat_id='c' AND logical_key=?", (key,)).fetchone()["state_json"]
    assert "PUBLIC" not in state_json and json.loads(state_json)["private_reasoning"] is True
    row = ChatStore(path).load_execution_steps("c")[0]
    assert row["private_reasoning"] and row["detail_text"] == "" and row["source_detail"] == {}
    continuation = ChatStore(path).apply_execution_fragment("c", key, scope, fragment_id="next", offset=3,
        text="LIC-CONTINUE", projection_seed=public)
    assert continuation["private_reasoning"] and not continuation.get("conflict")
    with store._connect() as conn:
        state_json = conn.execute("SELECT state_json FROM execution_assemblers WHERE chat_id='c' AND logical_key=?", (key,)).fetchone()["state_json"]
    assert "CONTINUE" not in state_json


def test_hidden_exact_private_replay_persists_private_assembler_state(tmp_path):
    path = tmp_path / "hidden-exact-private.db"
    store = ChatStore(path); store.initialize()
    scope = {"chat_id":"c","revision":1,"thread_id":"s","turn_id":"t",
             "provider":"kimi","agent_id":"main","native_id":"thinking"}
    key = store.execution_logical_key(scope)
    store.apply_execution_fragment("c", key, scope, fragment_id="same", offset=0, text="SECRET")
    private = {"event_type":"agent_message_delta","display_kind":"thinking","list_text":"正在分析问题",
               "detail_text":"","private_reasoning":True,"source_detail":{}}
    replay = store.apply_execution_fragment("c", key, scope, fragment_id="same", offset=0, text="SECRET", projection_seed=private)
    assert replay["replay"] and replay["private_reasoning"] and replay["projection"]["projection_ready"]
    with store._connect() as conn:
        state_json = conn.execute("SELECT state_json FROM execution_assemblers WHERE chat_id='c' AND logical_key=?", (key,)).fetchone()["state_json"]
    assert "SECRET" not in state_json and json.loads(state_json)["private_reasoning"] is True


def test_outbox_lane_orphans_and_sparse_checkpoint(tmp_path):
    store = ChatStore(tmp_path / "lane-store.db")
    store.initialize()
    first = store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id":"early","kind":"status","chat_id":"chat","body":{}})
    store.commit_durable_fact(pair_id="pair", domain="files", envelope={"event_id":"file","kind":"file_offer","chat_id":"chat","body":{}})
    final = store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id":"late","kind":"assistant_final","chat_id":"chat","body":{}})
    store.mark_outbox_acked(final["sync_sequence"], pair_id="pair", domain="events", consumer_id="publisher:pair")
    assert store.get_checkpoint("publisher:pair", "events", pair_id="pair") == 0
    store.mark_outbox_acked(first["sync_sequence"], pair_id="pair", domain="events", consumer_id="publisher:pair")
    assert store.get_checkpoint("publisher:pair", "events", pair_id="pair") == final["sync_sequence"]
    with store._connect() as conn:
        conn.execute("DELETE FROM durable_facts WHERE event_id='file'")
    assert [r["event_id"] for r in store.pending_outbox(lane="other")] == ["file"]
    assert store.pending_outbox(lane="notification") == []


def test_outbox_checkpoint_includes_legal_max_int64_ack(tmp_path):
    store = ChatStore(tmp_path / "max-checkpoint.db")
    store.initialize()
    with store._connect() as conn:
        conn.execute("INSERT INTO v2_feed_state(pair_id,domain,sync_sequence) VALUES('pair','__pair__',?)", (MAX_INT64 - 1,))
    final = store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id":"last","kind":"assistant_final","chat_id":"chat","body":{}})
    assert final["sync_sequence"] == MAX_INT64
    store.mark_outbox_acked(MAX_INT64, pair_id="pair", domain="events", consumer_id="publisher:pair")
    assert store.get_checkpoint("publisher:pair", "events", pair_id="pair") == MAX_INT64


def test_visible_execution_tail_skips_hidden_suffix_and_preserves_owner_and_cursor(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "visible-tail.db", max_execution_steps_per_turn=5000)
    store.initialize()
    for owner in ("owner", "other"):
        store.append_execution_step(owner, {"turn_idx": 0, "display_kind": "commentary", "list_text": owner + " real visible"})
    store.replace_execution_steps("owner", [{"turn_idx": 0, "display_kind": "commentary", "list_text": "owner real visible"}]
        + [{"turn_idx": 0, "display_kind": "commentary", "text": "notLoaded", "list_text": "notLoaded"} for _ in range(3000)])
    _, raw = store.load_recent_execution_steps("owner", limit=101, include_total=False)
    assert len(raw) == 101 and all(row["text"] == "notLoaded" for row in raw)
    import chat_store
    monkeypatch.setattr(chat_store, "should_show_execution_step", lambda _row: (_ for _ in ()).throw(AssertionError("read invoked predicate")))
    with store._connect() as conn:
        query_plan = conn.execute("EXPLAIN QUERY PLAN SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? AND visible=1 ORDER BY step_index DESC LIMIT ?", ("owner", 101)).fetchall()
    assert any("idx_execution_visible_tail" in row["detail"] for row in query_plan)
    _, visible = store.load_recent_execution_steps("owner", limit=101, include_total=False, visible_only=True)
    assert [row["list_text"] for row in visible] == ["owner real visible"]
    assert visible[0]["_store_step_index"] == 0
    _, empty = store.load_recent_execution_steps("owner", limit=101, include_total=False,
        visible_only=True, before_step_index=visible[0]["_store_step_index"])
    assert empty == []


def test_execution_visibility_migration_backfills_once_and_rolls_back_failure(tmp_path, monkeypatch):
    import chat_store
    store = ChatStore(tmp_path / "visibility-migration.db")
    store.initialize()
    payloads = [{"display_kind": "commentary", "list_text": "visible"},
        {"display_kind": "commentary", "list_text": "notLoaded", "text": "notLoaded"},
        {"display_kind": "commentary", "list_text": "unready", "projection_ready": False}]
    store.replace_execution_steps("owner", payloads)
    with store._connect() as conn:
        original = [tuple(row) for row in conn.execute("SELECT step_index,payload_json FROM execution_steps ORDER BY step_index")]
        conn.execute("DROP INDEX idx_execution_visible_tail")
        conn.execute("ALTER TABLE execution_steps DROP COLUMN visible")
    predicate = chat_store.should_show_execution_step
    monkeypatch.setattr(chat_store, "should_show_execution_step", lambda _p: (_ for _ in ()).throw(ValueError("migration failure")))
    with pytest.raises(ValueError, match="migration failure"):
        store.initialize()
    with store._connect() as conn:
        assert "visible" not in {row["name"] for row in conn.execute("PRAGMA table_info(execution_steps)")}
        assert [tuple(row) for row in conn.execute("SELECT step_index,payload_json FROM execution_steps ORDER BY step_index")] == original
    calls = []
    monkeypatch.setattr(chat_store, "should_show_execution_step", lambda p: calls.append(p) or predicate(p))
    store.initialize()
    assert len(calls) == 2  # unready rows are rejected before the shared predicate
    with store._connect() as conn:
        assert [row["visible"] for row in conn.execute("SELECT visible FROM execution_steps ORDER BY step_index")] == [1, 0, 0]
        assert [tuple(row) for row in conn.execute("SELECT step_index,payload_json FROM execution_steps ORDER BY step_index")] == original
    calls.clear()
    store.initialize()
    assert calls == []


@pytest.mark.parametrize("writer", ["append", "logical_append", "upsert", "identity", "logical_identity", "lifecycle"])
def test_execution_visibility_writers_track_hidden_and_readiness_transitions(tmp_path, writer):
    store = ChatStore(tmp_path / (writer + ".db"))
    store.initialize()
    scope = {"chat_id": "owner", "revision": 1, "thread_id": "session", "turn_id": "turn",
        "provider": "kimi", "agent_id": "", "native_id": "item"}
    key = store.execution_logical_key(scope)
    base = {"turn_idx": 0, "thread_id": "session", "turn_id": "turn", "item_id": "item",
        "event_type": "item_started", "source_kind": "tool.call.started", "display_kind": "commentary"}
    if writer in {"logical_append", "upsert", "logical_identity"}:
        base.update(logical_key=key, logical_scope=scope, provider="kimi", revision=1, agent_id="")
    def write(payload, initial=False):
        if writer == "append": store.replace_execution_steps("owner", [payload])
        elif writer == "logical_append":
            with store._connect() as conn: store._append_execution_step_on_conn(conn, "owner", payload)
        elif writer == "upsert": store.upsert_execution_step("owner", payload)
        elif initial: store.append_execution_step("owner", payload)
        elif writer == "lifecycle":
            store.replace_execution_steps("owner", [dict(payload, event_type="item_started")])
            assert store.replace_execution_lifecycle_step("owner", dict(payload, event_type="item_completed"))
        else: assert store.update_execution_step_by_identity("owner", payload)
    for index, (ready, text, expected) in enumerate([(False, "real", 0), (True, "real", 1), (True, "notLoaded", 0), (True, "real again", 1)]):
        write(dict(base, list_text=text, text=text, projection_ready=ready), initial=index == 0)
        with store._connect() as conn:
            rows = conn.execute("SELECT visible,payload_json FROM execution_steps WHERE chat_id='owner'").fetchall()
        assert len(rows) == 1 and rows[0]["visible"] == expected
        assert "visible" not in json.loads(rows[0]["payload_json"])
        _, visible = store.load_recent_execution_steps("owner", visible_only=True, include_total=False)
        assert len(visible) == expected


def test_execution_visibility_fragment_seed_replay_and_existing_projection(tmp_path):
    store = ChatStore(tmp_path / "visible-fragment.db")
    store.initialize()
    scope = {"chat_id": "owner", "revision": 1, "thread_id": "session", "turn_id": "turn",
        "provider": "kimi", "agent_id": "", "native_id": "item"}
    key = store.execution_logical_key(scope)
    def visible():
        with store._connect() as conn:
            return conn.execute("SELECT visible FROM execution_steps WHERE chat_id='owner'").fetchone()["visible"]
    store.apply_execution_fragment("owner", key, scope, fragment_id="first", offset=0, text="a")
    assert visible() == 0
    store.apply_execution_fragment("owner", key, scope, fragment_id="first", offset=0, text="a",
        projection_seed={"list_text": "real", "text": "real", "display_kind": "commentary"})
    assert visible() == 1
    store.apply_execution_fragment("owner", key, scope, fragment_id="next", offset=1, text="b",
        projection_seed={"list_text": "notLoaded", "text": "notLoaded", "display_kind": "commentary"})
    assert visible() == 0
    store.apply_execution_fragment("owner", key, scope, fragment_id="last", offset=2, text="c",
        projection_seed={"list_text": "real again", "text": "real again", "display_kind": "commentary"})
    assert visible() == 1
