from concurrent.futures import ThreadPoolExecutor, as_completed

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
