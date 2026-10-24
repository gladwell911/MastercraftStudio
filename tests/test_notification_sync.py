import json
import pytest

from chat_store import ChatStore
from remote_nats_protocol import compact_notification, validate_v2_durable


@pytest.fixture
def store(tmp_path):
    result = ChatStore(tmp_path / "snapshot.db")
    result.initialize()
    result.upsert_chat({"id": "chat", "title": "Title"})
    result.replace_turns("chat", [{"question": f"q{i}", "answer_md": "中" * 1000,
                                   "request_status": "done"} for i in range(8)])
    return result


def page(store, **kwargs):
    return store.notification_sync(pair_id="pair", device_id="install", baseline=0,
                                   incarnation="stream-a", secret="secret", **kwargs)


def test_crash_resume_snapshot_repairs_final_outbox_gap_and_only_completes_at_end(store):
    first = page(store, limit=2)
    assert len(first["records"]) == 2 and not first["done"]
    assert first["completion_token"] == ""
    high = first["high_water"]
    assert high == 8
    # Existing finals become actual facts/outbox, independent of publication.
    assert len(store.pending_outbox(pair_id="pair")) == 8
    with pytest.raises(ValueError, match="INCOMPLETE_NOTIFICATION_SNAPSHOT"):
        store.complete_notification_sync(pair_id="pair", device_id="install", incarnation="stream-a",
                                         completion_token=first["cursor"], secret="secret")
    # A new final commits its outbox atomically after registration, beyond H.
    turns = store.load_turns("chat")
    turns.append({"question": "late", "answer_md": "late answer", "request_status": "done"})
    store.replace_turns("chat", turns)
    assert len(store.pending_outbox(pair_id="pair")) == 9
    reopened = ChatStore(store.db_path)
    seen = first["records"]
    current = first
    while not current["done"]:
        current = page(reopened, cursor=current["cursor"], limit=2)
        assert current["high_water"] == high
        seen += current["records"]
    assert len(seen) == 9  # eight notifications and one read authority
    assert reopened.complete_notification_sync(pair_id="pair", device_id="install", incarnation="stream-a",
        completion_token=current["completion_token"], secret="secret") == high
    assert store.get_chat_read_state("chat", pair_id="pair")["read_seq"] == 0


def test_projection_hash_utf8_budget_and_immutable_fact(store):
    result = page(store)
    for notification in result["records"][:-1]:
        validate_v2_durable(notification)
        assert len(notification["body"]["preview"].encode()) <= 512
        assert len(json.dumps(notification, ensure_ascii=False).encode()) < 2048
    original = json.loads(store.replay_after(pair_id="pair", domain="events", sync_sequence=0)[0])
    wire = compact_notification(original, pair_id="pair")
    assert len(original["body"]["text"]) == 1000
    assert wire["fact_hash"] == original["canonical_hash"]
    assert wire["canonical_hash"] != original["canonical_hash"]


def test_scope_incarnation_and_original_baseline_are_immutable(store):
    first = page(store, limit=1)
    with pytest.raises(ValueError, match="INVALID_NOTIFICATION_CURSOR"):
        store.notification_sync(pair_id="pair", device_id="other", baseline=0, incarnation="stream-a",
                                secret="secret", cursor=first["cursor"])
    with pytest.raises(ValueError, match="INVALID_NOTIFICATION_CURSOR"):
        store.notification_sync(pair_id="pair", device_id="install", baseline=0, incarnation="recreated",
                                secret="secret", cursor=first["cursor"])
    result = store.notification_sync(pair_id="pair", device_id="install", baseline=999,
                                     incarnation="recreated", secret="secret")
    assert result["baseline"] == 0 and len(result["records"]) == 9


def test_clear_and_delete_authority_prevent_old_final_resurrection(store):
    page(store)
    store.begin_clear_operation("chat", idempotency_key="clear", pair_id="pair")
    result = page(store)
    assert not any(row.get("kind") == "assistant_final" for row in result["records"])
    assert sum(row.get("type") == "notification_tombstone" for row in result["records"]) == 8
    store.delete_chat("chat")
    result = page(store)
    assert all(row["type"] == "notification_tombstone" for row in result["records"])


def test_long_content_utf8_continuation_rejects_wrong_owner_and_clear(store):
    text = "汉字🙂" * 40000
    prefix, cursor = store.content_continuation(chat_id="chat", text=text, secret="secret")
    assert len(prefix.encode()) <= 4096
    with pytest.raises(ValueError, match="CONTENT_OWNER_MISMATCH"):
        store.read_content_page(chat_id="other", cursor=cursor, secret="secret")
    restored = prefix
    while cursor:
        result = store.read_content_page(chat_id="chat", cursor=cursor, secret="secret")
        assert len(json.dumps(result, ensure_ascii=False).encode()) < 128 * 1024
        restored += result["text"]
        cursor = result["cursor"]
    assert restored == text
    _, cursor = store.content_continuation(chat_id="chat", text=text, secret="secret")
    store.begin_clear_operation("chat", idempotency_key="clear", pair_id="pair")
    with pytest.raises(ValueError, match="STALE_CONTENT_CURSOR"):
        store.read_content_page(chat_id="chat", cursor=cursor, secret="secret")


def test_actionable_and_failed_snapshot_identity_does_not_consume_answer_read_sequence(store):
    before = store.get_chat_read_state("chat", pair_id="pair")
    facts = [store.commit_attention_notification(pair_id="pair", chat_id="chat", kind=kind,
        operation_id="request-1", text="Needs attention") for kind in ("action_required", "failed")]
    for fact in facts:
        assert "answer_seq" not in fact["body"]
        assert "message_id" not in fact["body"]
        assert store.commit_attention_notification(pair_id="pair", chat_id="chat", kind=fact["kind"],
            operation_id="request-1", text="Needs attention")["event_id"] == fact["event_id"]
    result = page(store)
    assert {r["kind"] for r in result["records"] if r.get("kind") in {"action_required", "failed"}} == {"action_required", "failed"}
    assert store.get_chat_read_state("chat", pair_id="pair") == before
    store.begin_clear_operation("chat", idempotency_key="clear-attention", pair_id="pair")
    result = page(store)
    for fact in facts:
        assert any(r.get("type") == "notification_tombstone" and r.get("event_id") == fact["event_id"] for r in result["records"])


def test_unchanged_authority_reuses_snapshot_abandoned_rows_expire_and_content_deduplicates(store):
    first = page(store, read_only=True, limit=1)
    again = page(store, read_only=True, limit=1)
    assert first["snapshot_id"] == again["snapshot_id"]
    with store._connect() as conn:
        conn.execute("UPDATE notification_snapshots SET created_at=0 WHERE snapshot_id=?", (first["snapshot_id"],))
    rebuilt = page(store, read_only=True)
    assert rebuilt["snapshot_id"] != first["snapshot_id"]
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notification_snapshot_rows WHERE snapshot_id=?", (first["snapshot_id"],)).fetchone()[0] == 0
    for _ in range(50):
        store.content_continuation(chat_id="chat", text="long" * 5000, secret="secret")
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM remote_content_pages").fetchone()[0] == 1


def test_large_snapshot_streams_all_records_across_bounded_pages(store):
    for index in range(350):
        store.commit_attention_notification(pair_id="pair", chat_id="chat", kind="action_required",
            operation_id=f"request-{index}", text="Attention")
    current, seen = page(store, limit=7), set()
    while True:
        assert len(current["records"]) <= 7
        seen.update(record["event_id"] for record in current["records"] if record.get("kind") == "action_required")
        if current["done"]: break
        current = page(store, cursor=current["cursor"], limit=7)
    assert len(seen) == 350


def test_completed_notification_snapshot_never_replays_same_high_water(store):
    first = page(store)
    while not first["done"]:
        first = page(store, cursor=first["cursor"])
    store.complete_notification_sync(pair_id="pair", device_id="install", incarnation="stream-a", secret="secret", completion_token=first["completion_token"])
    next_snapshot = page(store)
    assert next_snapshot["snapshot_id"] != first["snapshot_id"]
    assert not any(record.get("kind") == "assistant_final" for record in next_snapshot["records"])


def test_large_canonical_content_uses_reference_without_oversize_cache(store):
    text = "z" * (65 * 1024 * 1024)
    store.replace_turns("chat", [{"question": "huge", "answer_md": text, "request_status": "done"}])
    prefix, cursor = store.content_continuation(chat_id="chat", text=text, secret="secret")
    assert prefix == text[:4096]
    with store._connect() as conn:
        assert conn.execute("SELECT SUM(length(CAST(content AS BLOB))) FROM remote_content_pages").fetchone()[0] < 1024
    chunk = store.read_content_page(chat_id="chat", cursor=cursor, secret="secret")
    assert chunk["text"] == text[4096:4096 + 16384] and chunk["cursor"]


def test_execution_only_business_changes_reuse_read_authority_snapshot(store):
    initial = page(store, read_only=True)
    before = store.paired_feed_high_water(pair_id="pair")
    store.commit_durable_fact(pair_id="pair", domain="events", envelope={"event_id": "exec-only", "kind": "execution_entry", "chat_id": "chat", "body": {"detail": "step"}}, execution=True)
    again = page(store, read_only=True)
    assert again["snapshot_id"] == initial["snapshot_id"]
    watermarks = store.paired_feed_high_water(pair_id="pair")
    assert watermarks["high_sync_sequence"] > before["high_sync_sequence"]
    assert watermarks["authority_high_sync_sequence"] == before["authority_high_sync_sequence"]


def test_authority_cache_invalidates_empty_chat_create_delete_without_business_fact(store):
    initial = page(store, read_only=True)
    business = store.paired_feed_high_water(pair_id="pair")["high_sync_sequence"]
    store.upsert_chat({"id": "empty-new", "title": "Empty"})
    created = page(store, read_only=True)
    assert created["snapshot_id"] != initial["snapshot_id"]
    assert any(row.get("chat_id") == "empty-new" for row in created["records"])
    assert store.paired_feed_high_water(pair_id="pair")["high_sync_sequence"] == business
    # Deliberately exercise a canonical mutation with no notification fact.
    with store._connect() as conn:
        conn.execute("DELETE FROM chats WHERE id='empty-new'")
    deleted = page(store, read_only=True)
    assert deleted["snapshot_id"] != created["snapshot_id"]
    assert not any(row.get("chat_id") == "empty-new" for row in deleted["records"])
    assert store.paired_feed_high_water(pair_id="pair")["high_sync_sequence"] == business


def test_raw_content_beginning_with_reference_sentinel_is_not_interpreted(store):
    for marker in ("\x00canonical:", "\x00raw:"):
        text = marker + "ordinary user text" * 1000
        prefix, cursor = store.content_continuation(chat_id="chat", text=text, secret="secret")
        restored = prefix
        while cursor:
            result = store.read_content_page(chat_id="chat", cursor=cursor, secret="secret")
            restored += result["text"]
            cursor = result["cursor"]
        assert restored == text


def test_wal_snapshot_materialization_allows_writer_and_preserves_original_authority(store, monkeypatch):
    import threading
    # Register the installation, then create new canonical readable authority so
    # this request cannot reuse the earlier snapshot.
    page(store, read_only=True)
    turns = store.load_turns("chat")
    turns.append({"question": "new", "answer_md": "new answer", "request_status": "done"})
    store.replace_turns("chat", turns)
    before = store.get_chat_read_state("chat", pair_id="pair")
    target = store.readable_answer("chat", turn_index=8)
    assert target["answer_seq"] > before["read_seq"]
    captured_high = store.paired_feed_high_water(pair_id="pair")["high_sync_sequence"]
    original_read_state = store._read_state_conn
    writer_done, errors = threading.Event(), []
    intercepted = False

    def materialize_after_writer(conn, chat_id, pair_id):
        nonlocal intercepted
        if not intercepted:
            intercepted = True
            assert conn.in_transaction
            # This SELECT is on the already established source read transaction.
            assert conn.execute("SELECT COALESCE(MAX(sync_sequence),0) FROM durable_facts WHERE pair_id='pair'").fetchone()[0] == captured_high
            def writer():
                try:
                    separate = ChatStore(store.db_path)
                    separate.upsert_chat({"id": "created-after-H", "title": "Concurrent owner"})
                    separate.mark_chat_read(pair_id="pair", chat_id="chat", generation=target["generation"],
                        message_id=target["message_id"], answer_seq=target["answer_seq"], operation_id="concurrent-read")
                except BaseException as error:
                    errors.append(error)
                finally:
                    writer_done.set()
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            # A retained BEGIN IMMEDIATE construction lock would block this
            # writer until the source closes, failing the controlled barrier.
            assert writer_done.wait(3), "writer blocked by snapshot construction"
            thread.join(timeout=1)
            assert not thread.is_alive() and not errors
        return original_read_state(conn, chat_id, pair_id)

    monkeypatch.setattr(store, "_read_state_conn", materialize_after_writer)
    first = page(store, read_only=True)
    assert intercepted and writer_done.is_set()
    assert first["high_water"] == captured_high
    states = {row["chat_id"]: row for row in first["records"]}
    assert "created-after-H" not in states
    assert states["chat"]["read_seq"] == before["read_seq"]
    following = page(store, read_only=True)
    assert following["snapshot_id"] != first["snapshot_id"]
    assert following["high_water"] > captured_high
    states = {row["chat_id"]: row for row in following["records"]}
    assert "created-after-H" in states
    assert states["chat"]["read_seq"] == target["answer_seq"]


def test_database_feed_is_persistent_and_new_database_gets_distinct_scope(store, tmp_path):
    feed = store.get_meta("notification_feed_id")
    assert feed
    store.initialize()
    reopened = ChatStore(store.db_path)
    reopened.initialize()
    assert reopened.paired_feed_high_water(pair_id="pair")["notification_feed_id"] == feed
    other = ChatStore(tmp_path / "other.db")
    other.initialize()
    assert other.get_meta("notification_feed_id") != feed


def test_feed_upgrade_preserves_legacy_progress_and_recovers_next_final_once(store):
    legacy = store.notification_sync(pair_id="pair", device_id="install", baseline=186475,
                                     incarnation="broker", secret="secret")
    feed = store.get_meta("notification_feed_id")
    scoped = dict(pair_id="pair", device_id="install", incarnation="broker", secret="secret", notification_feed_id=feed)
    current = store.notification_sync(**scoped, baseline=8)
    assert current["baseline"] == 8
    assert not any(r.get("kind") == "assistant_final" for r in current["records"])
    turns = store.load_turns("chat")
    turns.append({"question": "new", "answer_md": "new final", "request_status": "done"})
    store.replace_turns("chat", turns)
    recovered = store.notification_sync(**scoped, baseline=999999)
    notifications = [r for r in recovered["records"] if r.get("kind") == "assistant_final"]
    assert len(notifications) == 1
    assert notifications[0]["notification_feed_id"] == feed
    validate_v2_durable(notifications[0])
    store.complete_notification_sync(**scoped, completion_token=recovered["completion_token"])
    assert not any(r.get("kind") == "assistant_final" for r in store.notification_sync(**scoped, baseline=0)["records"])
    assert store.notification_sync(pair_id="pair", device_id="install", baseline=0,
                                  incarnation="new-broker", secret="secret")["baseline"] == 186475
    # A broker rebuild restarts paging, never the database baseline.
    assert store.notification_sync(**{**scoped, "incarnation": "new-broker"}, baseline=20)["baseline"] == 8
    with pytest.raises(ValueError, match="INCOMPLETE_NOTIFICATION_SNAPSHOT"):
        store.complete_notification_sync(pair_id="pair", device_id="install", incarnation="broker",
            secret="secret", completion_token=recovered["completion_token"])
    with pytest.raises(ValueError, match="STALE_NOTIFICATION_FEED"):
        store.notification_sync(**{**scoped, "notification_feed_id": "retired"}, baseline=0)
    with pytest.raises(ValueError, match="STALE_NOTIFICATION_FEED"):
        store.complete_notification_sync(**{**scoped, "notification_feed_id": "retired"}, completion_token=recovered["completion_token"])
    assert store.notification_sync(pair_id="laptop", device_id="install", baseline=42,
        incarnation="broker", secret="secret", notification_feed_id=feed)["baseline"] == 42


def test_feed_only_installation_final_is_durable_before_next_recovery_pull(store):
    feed = store.get_meta("notification_feed_id")
    store.notification_sync(pair_id="pair", device_id="scoped-only", baseline=8,
                            incarnation="broker", secret="secret", notification_feed_id=feed)
    before = {row["event_id"]: row["payload"] for row in store.pending_outbox(pair_id="pair")}
    turns = store.load_turns("chat")
    turns.append({"question": "live", "answer_md": "new final", "request_status": "done"})
    store.replace_turns("chat", turns)
    after = store.pending_outbox(pair_id="pair")
    assert len(after) == len(before) + 1
    assert all(row["payload"] == before[row["event_id"]] for row in after if row["event_id"] in before)
    assert all("notification_feed_id" not in json.loads(row["payload"]) for row in after)
