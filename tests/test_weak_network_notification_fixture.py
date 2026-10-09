from chat_store import ChatStore
import asyncio
import pytest
from scripts import story4_notification_e2e_desktop as fixture
from scripts.story4_notification_e2e_desktop import append_fixture_turn, fixture_history_chat


def test_offline_read_snapshot_and_history_share_canonical_answer_identity(tmp_path):
    store = ChatStore(tmp_path / "isolated-notification.db")
    store.initialize()
    store.upsert_chat({"id": "offline", "title": "Offline owner"})
    append_fixture_turn(store, "offline", "Offline answer", "assistant_final")
    answer = store.readable_answer("offline", turn_index=0)
    assert answer is not None
    fact = store.commit_message_notification_fact(
        pair_id="default", domain="events", chat_id="offline", message_id=answer["message_id"],
        notification_kind="assistant_final", text="ignored", chat_title="ignored",
    )
    assert fact["body"]["text"] == "Offline answer"
    summary = store.list_chat_summaries()[0]
    before = fixture_history_chat(store, summary, "default")["read_state"]
    assert before["read_seq"] == 0
    assert before["latest_readable_seq"] == answer["answer_seq"]

    store.mark_chat_read(
        pair_id="default", chat_id="offline", generation=answer["generation"],
        message_id=answer["message_id"], answer_seq=answer["answer_seq"], operation_id="offline-read",
    )
    snapshot = fixture_history_chat(store, summary, "default")
    loaded = fixture_history_chat(store, store.load_chat("offline"), "default")
    assert snapshot["read_state"] == loaded["read_state"]
    assert snapshot["read_state"]["read_seq"] == answer["answer_seq"]
    assert loaded["turns"][0]["message_id"] == answer["message_id"]
    assert loaded["turns"][0]["generation"] == answer["generation"]
    assert loaded["turns"][0]["answer_seq"] == answer["answer_seq"]
    assert loaded["turns"][0]["answer"] == "Offline answer"
    # The same chat id on the second source must not inherit the desktop cursor.
    laptop = fixture_history_chat(store, summary, "laptop")["read_state"]
    assert laptop["read_seq"] == 0
    assert laptop["latest_readable_seq"] == answer["answer_seq"]


@pytest.mark.parametrize("endpoint", ["", "nats://public.example:4222"])
def test_fixture_requires_explicit_isolated_endpoint_before_network_or_database(endpoint, monkeypatch):
    monkeypatch.setenv("NATS_E2E_ENDPOINT", endpoint)
    monkeypatch.setenv("NATS_E2E_TOKEN", "isolated-test")
    def unexpected(*args, **kwargs):
        raise AssertionError("An invalid fixture endpoint must not start any external work")
    monkeypatch.setattr(fixture.nats, "connect", unexpected)
    monkeypatch.setattr(fixture.tempfile, "mkdtemp", unexpected)
    with pytest.raises(RuntimeError, match="Explicit isolated"):
        asyncio.run(fixture.run())
