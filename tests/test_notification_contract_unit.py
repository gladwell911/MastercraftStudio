from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_store import ChatStore
from remote_nats_protocol import validate_v2_durable


FIXTURE = Path(__file__).parent / "fixtures" / "notification_contract.json"
RC_FIXTURE = Path(__file__).parents[2] / "rc" / "test" / "fixtures" / "notification_contract.json"


def test_notification_fixture_is_byte_identical_between_clients():
    assert FIXTURE.read_bytes() == RC_FIXTURE.read_bytes()


def test_notification_fixture_encodes_watermark_domain_origin_and_kind_filters():
    matrix = json.loads(FIXTURE.read_text(encoding="utf-8"))
    handshake = matrix["handshake"]
    decisions = []
    for row in matrix["events"]:
        payload = row["payload"]
        event = validate_v2_durable(payload, verify_hash=True)
        notify = (
            event["sequence_domain"] == handshake["sequence_domain"]
            and event["sync_sequence"] > handshake["high_sync_sequence"]
            and event["origin_client"] == "mc"
            and event["kind"] == "assistant_final"
            and bool(event["body"].get("chat_title"))
            and bool(event["body"].get("text"))
        )
        decisions.append(notify)
        assert notify is row["notify"], row["name"]
    assert any(decisions), "the negative matrix must include a positive control"


def test_notification_fact_is_pair_scoped_and_backed_by_canonical_message(tmp_path):
    store = ChatStore(tmp_path / "notification.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "Owner title"})
    store.replace_turns("chat-1", [{"question": "Question", "answer_md": "Answer"}])
    user_message_id = store.resolve_canonical_message_by_turn(
        "chat-1", role="user", turn_index=0
    )
    assistant_message_id = store.resolve_canonical_message_by_turn(
        "chat-1", role="assistant", turn_index=0
    )

    question = store.commit_message_notification_fact(
        pair_id="pair-a",
        domain="events",
        chat_id="chat-1",
        message_id=user_message_id,
        notification_kind="user_message",
        text="Question",
        chat_title="Owner title",
        origin_client="mc",
    )
    answer = store.commit_message_notification_fact(
        pair_id="pair-a",
        domain="events",
        chat_id="chat-1",
        message_id=assistant_message_id,
        notification_kind="assistant_final",
        text="Answer",
        chat_title="Owner title",
        origin_client="mc",
    )

    assert question["sequence_domain"] == answer["sequence_domain"] == "events"
    assert question["sync_sequence"] < answer["sync_sequence"]
    assert question["event_id"].startswith("notification:pair-a:")
    assert answer["origin_client"] == "mc"
    assert len(store.pending_outbox(pair_id="pair-a", domain="events")) == 2
    with pytest.raises(ValueError, match="STALE_CANONICAL_NOTIFICATION"):
        store.commit_message_notification_fact(
            pair_id="pair-a",
            domain="events",
            chat_id="chat-1",
            message_id=user_message_id,
            notification_kind="assistant_final",
            text="Question",
            chat_title="Owner title",
        )


def test_notification_fact_repair_uses_new_pair_identity(tmp_path):
    store = ChatStore(tmp_path / "repair.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "Owner title"})
    store.replace_turns("chat-1", [{"question": "Question", "answer_md": ""}])
    message_id = store.resolve_canonical_message_by_turn(
        "chat-1", role="user", turn_index=0
    )

    first = store.commit_message_notification_fact(
        pair_id="pair-a",
        domain="events",
        chat_id="chat-1",
        message_id=message_id,
        notification_kind="user_message",
        text="Question",
        chat_title="Owner title",
    )
    second = store.commit_message_notification_fact(
        pair_id="pair-b",
        domain="events",
        chat_id="chat-1",
        message_id=message_id,
        notification_kind="user_message",
        text="Question",
        chat_title="Owner title",
    )
    assert first["event_id"] != second["event_id"]
    assert first["sync_sequence"] == second["sync_sequence"] == 1


def test_notification_fact_derives_title_and_text_from_canonical_rows(tmp_path):
    store = ChatStore(tmp_path / "canonical-content.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "Canonical owner"})
    store.replace_turns(
        "chat-1", [{"question": "Canonical question", "answer_md": "Canonical answer"}]
    )
    message_id = store.resolve_canonical_message_by_turn(
        "chat-1", role="assistant", turn_index=0
    )

    fact = store.commit_message_notification_fact(
        pair_id="pair-canonical",
        domain="events",
        chat_id="chat-1",
        message_id=message_id,
        notification_kind="assistant_final",
        text="forged text must be ignored",
        chat_title="forged title must be ignored",
    )

    assert fact["body"] == {
        "message_id": message_id,
        "text": "Canonical answer",
        "chat_title": "Canonical owner",
    }


def test_invalid_notification_validation_and_fact_insert_are_atomic(tmp_path):
    store = ChatStore(tmp_path / "atomic.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "Canonical owner"})
    before = store.paired_feed_high_water(pair_id="pair-atomic")

    with pytest.raises(ValueError, match="STALE_CANONICAL_NOTIFICATION"):
        store.commit_message_notification_fact(
            pair_id="pair-atomic",
            domain="events",
            chat_id="chat-1",
            message_id="missing-message",
            notification_kind="assistant_final",
            text="forged",
            chat_title="forged",
        )

    assert store.pending_outbox(pair_id="pair-atomic", domain="events") == []
    assert store.paired_feed_high_water(pair_id="pair-atomic") == before


def test_rename_replay_is_immutable_and_new_answers_use_each_current_owner(tmp_path):
    store = ChatStore(tmp_path / "rename.db")
    store.initialize()
    turns = [{"question": "Q1", "answer_md": "A1"},
             {"question": "Q2", "answer_md": "A2"}]
    for owner, title in [("chat-a", "First title"), ("chat-b", "Second title")]:
        store.upsert_chat({"id": owner, "title": title})
        store.replace_turns(owner, turns)

    def commit(owner, index=0, **overrides):
        arguments = dict(pair_id="pair-a", domain="events", chat_id=owner,
                         message_id=store.resolve_canonical_message_by_turn(
                             owner, role="assistant", turn_index=index),
                         notification_kind="assistant_final", text="stale input",
                         chat_title="stale input")
        return store.commit_message_notification_fact(**{**arguments, **overrides})

    first = commit("chat-a")
    second = commit("chat-b")
    before = store.paired_feed_high_water(pair_id="pair-a")
    store.upsert_chat({"id": "chat-a", "title": "Renamed first"})
    assert commit("chat-a") == first
    assert commit("chat-b") == second
    assert store.paired_feed_high_water(pair_id="pair-a") == before
    new = commit("chat-a", 1)
    assert new["body"]["chat_title"] == "Renamed first"
    assert new["chat_id"] == "chat-a"
    assert second["body"]["chat_title"] == "Second title"
    assert len(store.pending_outbox(pair_id="pair-a")) == 3
    for overrides in [dict(domain="files"), dict(origin_client="rc")]:
        with pytest.raises(ValueError, match="EVENT_ID_CONFLICT"):
            commit("chat-a", **overrides)
    with pytest.raises(ValueError, match="STALE_CANONICAL_NOTIFICATION"):
        commit("chat-b", message_id=first["body"]["message_id"])


def test_replay_rejects_mutated_canonical_message_content(tmp_path):
    store = ChatStore(tmp_path / "conflict.db")
    store.initialize()
    store.upsert_chat({"id": "chat-a", "title": "Title"})
    store.replace_turns("chat-a", [{"question": "Q", "answer_md": "Answer"}])
    message_id = store.resolve_canonical_message_by_turn("chat-a", role="assistant", turn_index=0)
    arguments = dict(pair_id="pair-a", domain="events", chat_id="chat-a",
                     message_id=message_id, notification_kind="assistant_final",
                     text="ignored", chat_title="ignored")
    store.commit_message_notification_fact(**arguments)
    # Simulate storage corruption while preserving the original identity.
    with store._connect() as conn:
        conn.execute("UPDATE turns SET payload_json=? WHERE chat_id=?",
                     (json.dumps({"question": "Q", "answer_md": "Mutated"}), "chat-a"))
    with pytest.raises(ValueError, match="EVENT_ID_CONFLICT"):
        store.commit_message_notification_fact(**arguments)


def test_notification_fixture_rename_preserves_metadata_and_replay_title(tmp_path):
    from scripts.story4_notification_e2e_desktop import update_fixture_chat_title
    store = ChatStore(tmp_path / "fixture-rename.db")
    store.initialize()
    update_fixture_chat_title(store, "owner", "Original")
    chat = store.load_chat("owner")
    chat["model"] = "codex"
    store.upsert_chat(chat)
    store.replace_turns("owner", [{"question": "Question", "answer_md": "Answer"}])
    message_id = store.resolve_canonical_message_by_turn("owner", role="assistant", turn_index=0)
    args = dict(pair_id="pair-fixture", domain="events", chat_id="owner", message_id=message_id,
                notification_kind="assistant_final", text="ignored", chat_title="ignored")
    original = store.commit_message_notification_fact(**args)
    before = store.load_chat("owner")
    update_fixture_chat_title(store, "owner", "Renamed")
    after = store.load_chat("owner")
    assert after["model"] == before["model"]
    assert after["turns"] == before["turns"]
    assert after["title_source"] == "manual"
    assert after["title_revision"] == before["title_revision"] + 1
    assert after["title_updated_at"] > before["title_updated_at"]
    update_fixture_chat_title(store, "owner", "Renamed")
    assert store.load_chat("owner") == after
    assert store.commit_message_notification_fact(**args) == original
    assert store.load_chat("owner")["title"] == "Renamed"


def test_notification_fixture_invalid_kind_does_not_create_or_mutate_chat(tmp_path):
    from scripts.story4_notification_e2e_desktop import prepare_fixture_title, update_fixture_chat_title
    store = ChatStore(tmp_path / "fixture-guard.db")
    store.initialize()
    update_fixture_chat_title(store, "existing", "Original")
    before = store.load_chat("existing")
    for owner in ["existing", "missing"]:
        assert prepare_fixture_title(store, {"chat_id": owner, "title": "Changed", "kind": "unsupported"}, None) is None
    assert store.load_chat("existing") == before
    assert store.load_chat("missing") is None


def test_notification_fixture_wrong_owner_rename_cannot_mutate_or_create_chat(tmp_path):
    from scripts.story4_notification_e2e_desktop import prepare_fixture_title, update_fixture_chat_title
    store = ChatStore(tmp_path / "fixture-owner.db")
    store.initialize()
    for owner in ["original", "other"]:
        update_fixture_chat_title(store, owner, owner)
    store.replace_turns("original", [{"question": "Q", "answer_md": "A"}])
    fact = store.commit_message_notification_fact(
        pair_id="pair", domain="events", chat_id="original",
        message_id=store.resolve_canonical_message_by_turn("original", role="assistant", turn_index=0),
        notification_kind="assistant_final", text="ignored", chat_title="ignored")
    before = {owner: store.load_chat(owner) for owner in ["original", "other"]}
    for owner in ["other", "missing"]:
        assert prepare_fixture_title(store, {"chat_id": owner, "title": "Changed", "replay": True, "rename": True}, fact) is None
    assert {owner: store.load_chat(owner) for owner in before} == before
    assert store.load_chat("missing") is None
