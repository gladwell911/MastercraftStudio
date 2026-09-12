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
            and event["kind"] in {"user_message", "assistant_final"}
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
