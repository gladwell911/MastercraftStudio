from chat_store import ChatStore


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
            "turn_idx": 0,
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：检查",
            "detail_text": "检查 main.py",
        }
    ]


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
