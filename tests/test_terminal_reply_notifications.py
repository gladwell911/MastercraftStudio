import pytest

from chat_store import ChatStore
from codex_history import CodexHistoryReader
from native_history import NativeSessionIdentity, NativeSessionSnapshot


def test_standalone_codex_keeps_native_history_without_notification_facts(tmp_path, monkeypatch):
    import codex_history

    db = tmp_path / "history.db"
    store = ChatStore(db)
    store.initialize()
    identity = NativeSessionIdentity("default", "codex", "fixture-storage", "original-thread")
    turns = []
    epoch = 1_770_000_000

    class Provider:
        def read_thread(self, thread_id):
            assert thread_id == identity.session_id
            return {"thread": {"id": thread_id, "cwd": str(tmp_path), "turns": list(turns)}}

    monkeypatch.setattr(codex_history, "codex_history_account", lambda client: "owner")
    monkeypatch.setattr(codex_history, "CodexAppServerClient", lambda **kwargs: Provider())
    reader = CodexHistoryReader(tmp_path, False, identity.pair_id, store.list_chat_summaries)

    def append(turn_id, start, end, *, phase="finalAnswer", status="completed"):
        turns.append({"id": turn_id, "status": status, "startedAt": epoch + start if start is not None else None,
                      "completedAt": epoch + end,
                      "items": [{"type": "userMessage", "id": "user-" + turn_id,
                                 "content": [{"type": "text", "text": "Identical question"}]},
                                {"type": "agentMessage", "id": "final-" + turn_id,
                                 "phase": phase, "text": "Identical reply"}]})

    def observe(updated_at, started_at, finished_at):
        snapshot = NativeSessionSnapshot(identity, "owner", "codex/gpt-5.4-medium",
                                         str(tmp_path), "Native chat", epoch + updated_at)
        reader.read_session(snapshot)
        return store.merge_native_history(snapshot, expected_chat_id=identity.chat_id,
            expected_revision=store.get_clear_reconciliation_authority(identity.chat_id)["revision"],
            observed_between=(epoch + started_at, epoch + finished_at))

    def notifications(pair="default"):
        return store.pending_outbox(pair_id=pair, lane="notification")

    def register(pair):
        return store.notification_sync(pair_id=pair, device_id="phone", baseline=0,
                                       incarnation="fixture", secret="synthetic")

    register("default")
    register("laptop")
    append("old", 20, 30)
    assert observe(30, 100, 101)["turn_count"] == 1
    assert store.load_turns(identity.chat_id)[0]["answer_md"] == "Identical reply"
    assert notifications() == []
    old_id = store.readable_answer(identity.chat_id, turn_index=0)["message_id"]
    with pytest.raises(ValueError, match="TERMINAL_HISTORY_NOTIFICATION"):
        store.commit_message_notification_fact(
            pair_id="default", domain="events", chat_id=identity.chat_id,
            message_id=old_id, notification_kind="assistant_final", text="ignored", chat_title="ignored",
        )

    # Late indexing can append a preexisting completed turn after the first read.
    append("delayed", 50, 90)
    append("new-a", 120, 130)
    append("unclassified", None, 140, phase=None)
    append("tool-commentary", 145, 150, phase="commentary")
    append("new-b", 160, 170)
    assert observe(170, 180, 181)["turn_count"] == 6
    assert observe(170, 182, 183) is None

    assert not store.replay_after(pair_id="default", domain="events", sync_sequence=0)
    assert not notifications()
    assert not notifications("laptop")
    for index in (2, 5):
        assert store.readable_answer(identity.chat_id, turn_index=index) is not None

    store = ChatStore(db)
    store.initialize()
    assert observe(170, 190, 191) is None
    assert not any(record.get("kind") == "assistant_final" for record in register("default")["records"])


@pytest.mark.parametrize(("stored_status", "stored_answer"), [
    ("pending", "Transient partial"),
    ("done", "正在请求..."),
])
def test_managed_codex_restart_reuses_pending_canonical_message(tmp_path, stored_status, stored_answer):
    db = tmp_path / "history.db"
    store = ChatStore(db)
    store.initialize()
    identity = NativeSessionIdentity("laptop", "codex", "storage", "thread")
    store.upsert_chat({"id": identity.chat_id, "title": "Terminal owner", "model": "codex/gpt-5.4-medium"})
    persisted = {"question": "Question", "answer_md": stored_answer, "request_status": stored_status,
                 "question_origin": "terminal", "terminal_final_pending": True,
                 "terminal_notification_pair": "laptop", "model": "codex/gpt-5.4-medium",
                 "codex_thread_id": "thread", "codex_turn_id": "native-turn"}
    store.replace_turns(identity.chat_id, [persisted])
    before = store.resolve_canonical_message_by_turn(identity.chat_id, role="assistant", turn_index=0)
    assert not store.pending_outbox(lane="notification")

    reopened = ChatStore(db)
    reopened.initialize()
    read = dict(persisted, provider="codex", answer_md="Complete native final", request_status="done",
                provider_message_id="native-answer", native_final_explicit=True)
    later = dict(read, question="Later question", answer_md="Later final",
                 codex_turn_id="later-turn", provider_message_id="later-answer")
    snapshot = NativeSessionSnapshot(identity, "owner", "codex/gpt-5.4-medium", str(tmp_path), "Terminal owner",
                                     1_770_000_042, turns=[read, later])
    assert reopened.merge_native_history(snapshot, expected_chat_id=identity.chat_id)["turn_count"] == 2
    assert reopened.load_turns(identity.chat_id)[1]["answer_md"] == "Later final"
    deduped = reopened.readable_answer(identity.chat_id, turn_index=0)
    assert deduped["message_id"] == before
    assert reopened.load_turns(identity.chat_id)[0]["answer_md"] == "Complete native final"
    revision = reopened.get_clear_reconciliation_authority(identity.chat_id)["revision"]
    assert reopened.merge_native_history(snapshot, expected_chat_id=identity.chat_id,
                                         expected_revision=revision) is None
    assert not reopened.pending_outbox(lane="notification")


def test_kimi_readback_tracks_the_last_complete_public_frame(tmp_path):
    from kimi_history import KimiHistoryReader

    identity = NativeSessionIdentity("laptop", "kimi", "fixture-storage", "session")
    store = ChatStore(tmp_path / "history.db")
    store.initialize()
    epoch = 1_770_000_000
    items = []

    class Client:
        def get_transcript(self, session_id, *, before_turn):
            assert session_id == identity.session_id
            assert before_turn is None
            return {"items": list(items)}

    def add(turn, offset, *, final_id, with_tool=False):
        frames = [{"kind": "text", "role": "assistant", "text": "Identical reply", "frameId": final_id}]
        if with_tool:
            frames.append({"kind": "tool", "frameId": "tool", "name": "Inspect"})
        items.append({"kind": "turn", "turnId": turn, "triggerPromptId": "question-" + turn,
                      "prompt": "Identical question", "state": "completed", "origin": "user",
                      "startedAt": epoch + offset, "endedAt": epoch + offset + 5, "steps": [
                          {"ordinal": 0, "state": "completed",
                           "frames": [{"kind": "text", "role": "assistant", "text": "Tool commentary",
                                       "frameId": "commentary"}]},
                          {"ordinal": 1, "state": "completed", "frames": frames},
                      ]})

    def observe(offset):
        snapshot = NativeSessionSnapshot(identity, "owner", "kimi/main", str(tmp_path),
                                         "Kimi terminal session", epoch + offset)
        KimiHistoryReader(Client(), "laptop", lambda row: row["origin"] == "user").read_session(snapshot)
        revision = store.get_clear_reconciliation_authority(identity.chat_id)["revision"]
        return store.merge_native_history(snapshot, expected_chat_id=identity.chat_id, expected_revision=revision,
                                          observed_between=(epoch + offset, epoch + offset + 1))

    add("initial", 10, final_id="frame-initial")
    assert observe(20)["turn_count"] == 1
    assert not store.pending_outbox(lane="notification")
    add("new", 40, final_id="frame-new")
    add("unidentified", 50, final_id=None)
    add("only-tool", 60, final_id="tool-text", with_tool=True)
    assert observe(70)["turn_count"] == 3
    assert [turn["answer_md"] for turn in store.load_turns(identity.chat_id)] == ["Identical reply"] * 3
    assert store.readable_answer(identity.chat_id, turn_index=1) is not None
    assert not store.pending_outbox(lane="notification")
