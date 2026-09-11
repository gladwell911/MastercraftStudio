from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
from pathlib import Path
import pytest

from chat_store import ChatStore
from chat_store import CLEAR_OPERATION_STATES, MAX_INT64, V2_MIGRATION_KEY


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


def test_clear_operation_is_atomic_idempotent_and_private(tmp_path):
    store = ChatStore(tmp_path / "clear.db")
    store.initialize()
    store.upsert_chat({"id": "chat", "codex_thread_id": "private-thread"})
    store.replace_turns("chat", [
        {"question": "/status", "local_command": "status"},
        {"question": "current edited text", "model": "codex/gpt-5", "attachments": [{"path": "secret/file.txt"}]},
    ])
    store.append_execution_step("chat", {"turn_idx": 1, "list_text": "private execution"})

    operation = store.begin_clear_operation("chat", idempotency_key="request-1", pair_id="pair")
    duplicate = store.begin_clear_operation("chat", idempotency_key="request-1", pair_id="pair")

    assert duplicate["operation_id"] == operation["operation_id"]
    assert operation["state"] == "clear_acknowledged"
    assert operation["snapshot"]["question"] == "current edited text"
    assert operation["snapshot"]["attachments"] == [{"path": "secret/file.txt"}]
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
    assert store.claim_clear_resend_dispatch(operation["operation_id"])["state"] == "resend_dispatched"
    assert store.claim_clear_resend_dispatch(operation["operation_id"]) is None
    assert store.transition_clear_operation(operation["operation_id"], "resend_blocked", failure_code="TIMEOUT")["state"] == "resend_blocked"
    assert store.transition_clear_operation(operation["operation_id"], "completed_clear_only")["terminal"] is True
    lifecycle = [json.loads(row["payload"])["body"]["state"] for row in store.pending_outbox()]
    assert lifecycle == ["requested", "clear_acknowledged", "resend_dispatched", "resend_blocked", "completed_clear_only"]
    assert all("hello" not in row["payload"].decode("utf-8") for row in store.pending_outbox())


def test_clear_operation_no_message_and_revision_overflow_are_safe(tmp_path):
    store = ChatStore(tmp_path / "empty.db")
    store.initialize()
    store.upsert_chat({"id": "chat"})
    store.replace_turns("chat", [{"question": "/help", "local_command": True}])
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
    assert store.claim_clear_resend_dispatch(first["operation_id"])
    assert store.transition_clear_operation(first["operation_id"], "resend_blocked", failure_code="TIMEOUT")["state"] == "resend_blocked"
    restarted = ChatStore(store.db_path)
    restarted.initialize()
    replay = restarted.begin_clear_operation("chat", idempotency_key="stable", pair_id="pair")
    assert replay["operation_id"] == first["operation_id"] and replay["idempotent_replay"]
    assert restarted.claim_clear_resend_dispatch(first["operation_id"]) is None
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
    for name, code in (("missing_attachment", "ATTACHMENT_UNAVAILABLE"), ("provider_reject", "PROVIDER_REJECTED")):
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
