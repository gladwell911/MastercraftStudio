import main
import pytest
from types import SimpleNamespace


def test_remote_model_list_returns_visible_combo_order(frame):
    status, body = frame._remote_api_model_list_ui()

    assert status == 200
    assert body["accepted"] is True
    assert body["models"] == [
        {"id": model_id, "label": main.model_display_name(model_id)}
        for model_id in main.VISIBLE_MODEL_IDS
    ]


def test_remote_message_preserves_dynamic_claudecode_model(frame, monkeypatch):
    captured = {}

    def fake_submit_question(question, **kwargs):
        captured["call"] = (question, kwargs)
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)

    status, body = frame._remote_api_message_ui(
        {"chat_id": "remote-chat", "text": "hello", "model": "claudecode/opus"}
    )

    assert status == 200
    assert body["accepted"] is True
    assert captured["call"][1]["model"] == "claudecode/opus"


def test_remote_message_explicitly_switches_existing_chat_model(frame, monkeypatch):
    captured = {}

    frame.active_chat_id = "chat-gpt54"
    frame.current_chat_id = "chat-gpt54"
    frame.selected_model = "codex/gpt-5.4-medium"
    frame._current_chat_state.update(
        {
            "id": "chat-gpt54",
            "title": "gpt5.4 chat",
            "model": "codex/gpt-5.4-medium",
            "turns": [
                {
                    "question": "old",
                    "answer_md": "answer",
                    "model": "codex/gpt-5.4-medium",
                }
            ],
        }
    )

    def fake_submit_question(question, **kwargs):
        captured["call"] = (question, kwargs)
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "chat-gpt54",
            "text": "switch and continue",
            "model": "openai/gpt-5.2",
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["model"] == "openai/gpt-5.2"
    assert captured["call"][1]["model"] == "openai/gpt-5.2"
    assert frame.selected_model == "openai/gpt-5.2"


def test_remote_message_rejects_invalid_explicit_model_switch(frame, monkeypatch):
    called = {"submit": 0}

    frame.active_chat_id = "chat-gpt54"
    frame.current_chat_id = "chat-gpt54"
    frame.selected_model = "codex/gpt-5.4-medium"
    frame._current_chat_state.update(
        {
            "id": "chat-gpt54",
            "title": "gpt5.4 chat",
            "model": "codex/gpt-5.4-medium",
            "turns": [],
        }
    )

    def fake_submit_question(question, **kwargs):
        called["submit"] += 1
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "chat-gpt54",
            "text": "switch and continue",
            "model": "not-a-real-model",
            "model_change_mode": "explicit",
        }
    )

    assert status == 400
    assert body["accepted"] is False
    assert body["error"] == "invalid_explicit_model"
    assert called["submit"] == 0
    assert frame.selected_model == "codex/gpt-5.4-medium"
    assert frame.active_chat_id == "chat-gpt54"
    assert frame.current_chat_id == "chat-gpt54"


def test_remote_message_rejects_missing_explicit_model_switch(frame, monkeypatch):
    called = {"submit": 0}

    frame.active_chat_id = "chat-gpt54"
    frame.current_chat_id = "chat-gpt54"
    frame.selected_model = "codex/gpt-5.4-medium"
    frame._current_chat_state.update(
        {
            "id": "chat-gpt54",
            "title": "gpt5.4 chat",
            "model": "codex/gpt-5.4-medium",
            "turns": [],
        }
    )

    def fake_submit_question(question, **kwargs):
        called["submit"] += 1
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "chat-gpt54",
            "text": "switch and continue",
            "model_change_mode": "explicit",
        }
    )

    assert status == 400
    assert body["accepted"] is False
    assert body["error"] == "missing_explicit_model"
    assert called["submit"] == 0
    assert frame.selected_model == "codex/gpt-5.4-medium"
    assert frame.active_chat_id == "chat-gpt54"
    assert frame.current_chat_id == "chat-gpt54"


def test_remote_message_unknown_chat_id_uses_requested_model_for_new_remote_chat(frame, monkeypatch):
    captured = {}

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.selected_model = "codex/main"
    frame.active_session_turns = [{"question": "old q", "answer_md": "old a", "model": "codex/main"}]
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "current chat",
            "model": "codex/main",
            "turns": frame.active_session_turns,
        }
    )

    def fake_submit_question(question, **kwargs):
        captured["call"] = (question, kwargs)
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda *_args, **_kwargs: None)

    status, body = frame._remote_api_message_ui(
        {"chat_id": "remote-new", "text": "hello", "model": "codex/gpt-5.4-medium"}
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["model"] == "codex/gpt-5.4-medium"
    assert captured["call"][1]["chat_id"] == "remote-new"
    assert captured["call"][1]["model"] == "codex/gpt-5.4-medium"
    assert frame.active_chat_id == "remote-new"
    assert frame.current_chat_id == "remote-new"
    assert frame._current_chat_state["id"] == "remote-new"
    assert frame._current_chat_state["model"] == "codex/gpt-5.4-medium"
    assert any(chat.get("id") == "chat-current" for chat in frame.archived_chats)


def test_remote_api_state_includes_remote_runtime_status(frame):
    frame.remote_control_runtime_url = ""
    frame.remote_control_runtime_status = {
        "local_listener_ready": True,
        "public_ws_ready": False,
        "last_remote_error": "public probe failed",
        "published_url": "wss://rc.tingyou.cc/nats?token=secret",
    }

    status, body = frame._remote_api_state_ui({})

    assert status == 200
    assert body["accepted"] is True
    assert body["remote_runtime"]["public_ws_ready"] is False
    assert body["remote_runtime"]["last_remote_error"] == "public probe failed"
    assert body["remote_runtime_url"] == ""


def test_remote_pending_request_reply_uses_pending_model_after_selected_model_changes(frame, monkeypatch):
    calls = []

    class _FakeWorkerClient:
        def __init__(self, chat_id, model):
            self.chat_id = chat_id
            self.model = model

        def start(self):
            calls.append(("start", self.chat_id, self.model))

        def reply_user_input(self, chat_id, request_id, answers):
            calls.append(("reply", self.chat_id, self.model, chat_id, request_id, answers))

    frame.selected_model = "codex/new-selection"
    frame.active_codex_pending_request = {
        "chat_id": "chat-c",
        "model": "codex/original",
        "request_id": "req-remote",
    }
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _FakeWorkerClient(chat_id, model))
    monkeypatch.setattr(frame, "_save_state", lambda: calls.append(("save",)))

    ok, message = frame._handle_remote_pending_request_reply("remote ok")

    assert ok is True
    assert "已提交" in message
    assert ("start", "chat-c", "codex/original") in calls
    assert ("reply", "chat-c", "codex/original", "chat-c", "req-remote", {"reply": ["remote ok"]}) in calls


def test_remote_state_snapshot_omits_full_execution_steps_by_default(frame):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "codex/main"}]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "execution_steps": [
            {"turn_idx": 0, "list_text": f"step {idx}", "display_kind": "commentary"}
            for idx in range(500)
        ],
    }

    status, body = frame._remote_api_state_ui({"chat_id": "chat-active"})

    assert status == 200
    assert body["execution_steps"] == []
    assert body["execution_step_count"] == 500


def test_remote_api_clear_context_uses_desktop_clear_context_command(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    calls = []
    monkeypatch.setattr(
        frame,
        "_clear_context_and_start_new_chat",
        lambda: calls.append("clear") or True,
    )

    status, body = frame._remote_api_clear_context_ui({"chat_id": "chat-current"})

    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"] == "chat-current"
    assert isinstance(body["revision"], int)
    clear = body["clear_operation"]
    if clear is not None:
        assert set(clear) == {"operation_id", "revision", "state"}
        assert clear["revision"] <= body["revision"]
    assert not ({"snapshot", "attachments", "path"} & set(body))
    assert calls == ["clear"]


def test_remote_api_clear_context_rejects_missing_or_unknown_chat_id(frame):
    missing_status, missing_body = frame._remote_api_clear_context_ui({})
    unknown_status, unknown_body = frame._remote_api_clear_context_ui({"chat_id": "missing-chat"})

    assert missing_status == 400
    assert missing_body["accepted"] is False
    assert missing_body["error"] == "missing_chat_id"
    assert unknown_status == 404
    assert unknown_body["accepted"] is False
    assert unknown_body["error"] == "chat_not_found"


def test_new_chat_broadcasts_remote_history_changed(frame, monkeypatch):
    frame.active_session_turns = []
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame._current_chat_state["id"] = "chat-old"
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda *_args, **_kwargs: None)
    seen = []

    class _Transport:
        def publish_event_threadsafe(self, payload):
            seen.append(payload)

    frame._remote_nats_transport = _Transport()

    frame._on_new_chat_clicked(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)
    status, body = frame._remote_api_history_list_ui()
    assert status == 200
    assert any(chat.get("chat_id") == frame.active_chat_id for chat in body["chats"])


def test_remote_history_and_state_payloads_round_trip_detail_panel_fields(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [
            {"question": "当前问题", "answer_md": "当前回答", "model": main.DEFAULT_MODEL_ID, "created_at": 1.0}
        ],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "当前步骤"}],
    }
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "title": "归档聊天",
            "turns": [{"question": "归档问题", "answer_md": "归档回答", "model": main.DEFAULT_MODEL_ID, "created_at": 2.0}],
            "created_at": 2.0,
            "updated_at": 2.0,
            "detail_panel_mode": "execution",
            "execution_steps": [{"step": "归档步骤"}],
        }
    ]

    status, history = frame._remote_api_history_list_ui()
    assert status == 200
    current = next(chat for chat in history["chats"] if chat["chat_id"] == "chat-current")
    archived = next(chat for chat in history["chats"] if chat["chat_id"] == "chat-archived")
    assert current["detail_panel_mode"] == "execution"
    assert archived["detail_panel_mode"] == "execution"
    assert "turns" not in current
    assert "turns" not in archived
    assert "execution_steps" not in current
    assert "execution_steps" not in archived

    status, read = frame._remote_api_history_read_ui({"chat_id": "chat-archived", "include_execution_steps": True})
    assert status == 200
    assert read["chat"]["detail_panel_mode"] == "execution"
    assert read["chat"]["execution_steps"] == [{"step": "归档步骤"}]

    status, state = frame._remote_api_state_ui({"chat_id": "chat-current", "include_execution_steps": True})
    assert status == 200
    assert state["detail_panel_mode"] == "execution"
    assert state["execution_steps"] == [{"step": "当前步骤"}]


def test_remote_execution_projection_new_turn_without_steps(frame, tmp_path):
    store = main.ChatStore(tmp_path / "execution-new-turn.db")
    store.initialize()
    turns = [
        {"question": "old", "answer_md": "done", "model": main.DEFAULT_MODEL_ID, "created_at": 1.0},
        {"question": "new", "answer_md": "", "model": main.DEFAULT_MODEL_ID, "created_at": 2.0},
    ]
    store.upsert_chat({"id": "chat-new-turn", "title": "new", "model": main.DEFAULT_MODEL_ID,
                       "created_at": 1.0, "updated_at": 2.0, "turns": turns})
    store.replace_turns("chat-new-turn", turns)
    store.replace_execution_steps("chat-new-turn", [
        {"turn_idx": 0, "display_kind": "commentary", "event_id": "old-step",
         "list_text": "old step", "detail_text": "old step"},
    ])
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "chat-new-turn"
    frame.active_turn_idx = 1
    frame._current_chat_state = {"id": "chat-new-turn", "turns": store.load_turns("chat-new-turn"),
                                 "execution_steps": store.load_execution_steps("chat-new-turn")}
    frame.view_mode = "active"
    status, page = frame._remote_api_execution_page_ui(
        {"chat_id": "chat-new-turn", "body": {"limit": 10}}, secret="secret"
    )
    assert status == 200, page
    assert all("old step" not in row["list_text"] for row in page["rows"])


def test_remote_history_list_is_lightweight_without_turns_or_execution_steps(frame, monkeypatch):
    def fail_if_called(_turn):
        raise AssertionError("history_list should not render remote turn payloads")

    monkeypatch.setattr(frame, "_remote_turn_payload", fail_if_called)
    frame.archived_chats = [
        {
            "id": f"chat-{idx}",
            "title": f"聊天 {idx}",
            "turns": [
                {
                    "question": f"问题 {idx}",
                    "answer_md": "很长的 **Markdown** 回答",
                    "model": main.DEFAULT_MODEL_ID,
                    "created_at": float(idx),
                }
            ],
            "execution_steps": [{"step": "heavy"}],
            "created_at": float(idx),
            "updated_at": float(idx),
        }
        for idx in range(50)
    ]

    status, body = frame._remote_api_history_list_ui()

    assert status == 200
    assert len(body["chats"]) == 50
    assert all("turns" not in chat for chat in body["chats"])
    assert all("execution_steps" not in chat for chat in body["chats"])
    assert body["chats"][0]["turn_count"] == 1


def test_remote_history_read_supports_tail_paging(frame):
    frame.archived_chats = [
        {
            "id": "chat-paged",
            "title": "分页聊天",
            "created_at": 1.0,
            "updated_at": 6.0,
            "turns": [
                {"question": f"问题 {idx}", "answer_md": f"回答 {idx}", "model": main.DEFAULT_MODEL_ID, "created_at": float(idx)}
                for idx in range(6)
            ],
        }
    ]

    status, body = frame._remote_api_history_read_ui({"chat_id": "chat-paged", "limit": 2})

    assert status == 200
    assert body["chat"]["turn_count"] == 6
    assert [turn["question"] for turn in body["chat"]["turns"]] == ["问题 4", "问题 5"]
    assert body["has_more"] is True
    assert body["oldest_cursor"] == "4"

    status, older = frame._remote_api_history_read_ui(
        {"chat_id": "chat-paged", "limit": 2, "before_turn_index": 4}
    )

    assert status == 200
    assert [turn["question"] for turn in older["chat"]["turns"]] == ["问题 2", "问题 3"]
    assert older["has_more"] is True
    assert older["oldest_cursor"] == "2"


def test_remote_history_read_paging_only_renders_requested_turns(frame, monkeypatch):
    frame.archived_chats = [
        {
            "id": "chat-paged",
            "title": "分页聊天",
            "turns": [
                {"question": f"问题 {idx}", "answer_md": f"回答 {idx}", "model": main.DEFAULT_MODEL_ID, "created_at": float(idx)}
                for idx in range(6)
            ],
        }
    ]
    rendered = []

    def fake_turn_payload(turn):
        rendered.append(turn["question"])
        return {"question": turn["question"], "answer": "", "model": "", "created_at": 0.0}

    monkeypatch.setattr(frame, "_remote_turn_payload", fake_turn_payload)

    status, body = frame._remote_api_history_read_ui({"chat_id": "chat-paged", "limit": 2})

    assert status == 200
    assert [turn["question"] for turn in body["chat"]["turns"]] == ["问题 4", "问题 5"]
    assert rendered == ["问题 4", "问题 5"]


def test_remote_history_read_store_uses_turn_page_without_full_hydration(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.archived_chats = [
        {
            "id": "chat-heavy",
            "title": "heavy",
            "turn_count": 500,
            "created_at": 1.0,
            "updated_at": 2.0,
        }
    ]
    calls = []

    class Store:
        def load_turns_page(self, chat_id, *, limit=100, before_turn_index=None):
            calls.append((chat_id, limit, before_turn_index))
            return 500, [
                {"question": "q498", "answer_md": "a498", "model": main.DEFAULT_MODEL_ID},
                {"question": "q499", "answer_md": "a499", "model": main.DEFAULT_MODEL_ID},
            ]

        def load_chat(self, *args, **kwargs):
            raise AssertionError("remote history read should not hydrate full chat")

        def load_turns(self, *args, **kwargs):
            raise AssertionError("remote history read should not load all turns")

        def load_execution_steps(self, *args, **kwargs):
            raise AssertionError("remote history read should not load execution steps by default")

    frame.chat_store = Store()
    monkeypatch.setattr(frame, "_hydrate_chat_from_store", lambda *args, **kwargs: pytest.fail("full hydrate is too heavy"))

    status, body = frame._remote_api_history_read_ui({"chat_id": "chat-heavy", "limit": 2})

    assert status == 200
    assert calls == [("chat-heavy", 2, None)]
    assert body["chat"]["turn_count"] == 500
    assert [turn["question"] for turn in body["chat"]["turns"]] == ["q498", "q499"]
    assert body["has_more"] is True
    assert body["oldest_cursor"] == "498"


def test_remote_history_read_current_chat_uses_latest_active_session_turns(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [
            {"question": "旧问题", "answer_md": "旧回答", "model": main.DEFAULT_MODEL_ID, "created_at": 1.0}
        ],
    }
    frame.active_session_turns = [
        {"question": "新问题", "answer_md": "新回答", "model": main.DEFAULT_MODEL_ID, "created_at": 2.0}
    ]

    status, body = frame._remote_api_history_read_ui({"chat_id": "chat-current", "limit": 10})

    assert status == 200
    assert [turn["question"] for turn in body["chat"]["turns"]] == ["新问题"]


def test_remote_history_list_marks_current_chat_running_and_waiting_request_kind(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.is_running = True
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
    }

    status, body = frame._remote_api_history_list_ui()

    assert status == 200
    current = next(chat for chat in body["chats"] if chat["chat_id"] == "chat-current")
    assert current["running"] is True
    assert current["request_kind"] == "user_input"


@pytest.mark.parametrize("model", ["codex/main", "claudecode/default", "openclaw/main"])
def test_remote_turn_payload_preserves_canonical_answer_for_all_models(frame, model):
    frame.codex_answer_english_filter_enabled = True
    codex_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_cli_models",
        "model": model,
        "created_at": 1.0,
    }
    other_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_cli_models",
        "model": "openai/gpt-5.2",
        "created_at": 1.0,
    }

    codex_payload = frame._remote_turn_payload(codex_turn)
    other_payload = frame._remote_turn_payload(other_turn)

    assert codex_payload["answer"] == codex_turn["answer_md"]
    assert other_payload["answer"] == other_turn["answer_md"]


@pytest.mark.parametrize("created_at", [None, "bad", float("nan"), True])
def test_remote_turn_payload_omits_non_authoritative_timestamp(frame, created_at):
    payload = frame._remote_turn_payload({"question": "q", "answer_md": "a", "created_at": created_at})

    assert "created_at" not in payload


def test_remote_state_includes_codex_speed_options_and_current_tier(frame):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-speed",
        "title": "speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 1.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
        "codex_service_tier": "fast",
    }

    status, body = frame._remote_api_state_ui({"chat_id": "chat-speed"})

    assert status == 200
    assert body["codex_service_tier"] == "fast"
    assert body["codex_service_tier_label"] == "快速"
    assert body["codex_service_tier_options"] == [
        {"value": "standard", "label": "标准"},
        {"value": "fast", "label": "快速"},
    ]


def test_remote_set_speed_updates_chat_combo_and_pushes_state(frame, monkeypatch):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-speed",
        "title": "speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 1.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
        "codex_service_tier": "standard",
    }
    pushed = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(chat_id))

    status, body = frame._remote_api_set_speed_ui(
        {"chat_id": "chat-speed", "codex_service_tier": "fast"}
    )

    assert status == 200
    assert body["accepted"] is True
    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert frame.codex_speed_combo.GetValue() == "快速"
    assert pushed == ["chat-speed"]


def test_remote_set_speed_accepts_repeated_fast_and_standard_changes(frame, monkeypatch):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-speed",
        "title": "speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 1.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
        "codex_service_tier": "",
    }
    pushed = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(chat_id))

    status, body = frame._remote_api_set_speed_ui(
        {"chat_id": "chat-speed", "codex_service_tier": "fast"}
    )
    assert status == 200
    assert body["codex_service_tier"] == "fast"
    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert frame.codex_speed_combo.GetValue() == "快速"

    status, body = frame._remote_api_set_speed_ui(
        {"chat_id": "chat-speed", "codex_service_tier": "standard"}
    )
    assert status == 200
    assert body["codex_service_tier"] == "standard"
    assert body["codex_service_tier_label"] == "标准"
    assert frame._current_chat_state["codex_service_tier"] == ""
    assert frame.codex_speed_combo.GetValue() == "标准"

    status, body = frame._remote_api_set_speed_ui(
        {"chat_id": "chat-speed", "codex_service_tier": "fast"}
    )
    assert status == 200
    assert body["codex_service_tier"] == "fast"
    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert frame.codex_speed_combo.GetValue() == "快速"
    assert pushed == ["chat-speed", "chat-speed", "chat-speed"]


def test_remote_state_defaults_to_latest_turn_page_without_formatting_all_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": f"q {idx}", "answer_md": f"a {idx}", "model": main.DEFAULT_MODEL_ID, "created_at": float(idx)}
        for idx in range(500)
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    rendered = []

    def fake_turn_payload(turn):
        rendered.append(turn["question"])
        return {"question": turn["question"], "answer": "", "model": "", "created_at": 0.0}

    monkeypatch.setattr(frame, "_remote_turn_payload", fake_turn_payload)

    status, body = frame._remote_api_state_ui({})

    assert status == 200
    assert body["turn_count"] == 500
    assert body["has_more"] is True
    assert body["oldest_cursor"] == "400"
    assert len(body["turns"]) == 100
    assert rendered == [f"q {idx}" for idx in range(400, 500)]


def test_remote_state_archived_store_uses_turn_page_without_full_hydration(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.archived_chats = [
        {
            "id": "chat-heavy",
            "title": "heavy",
            "turn_count": 500,
            "created_at": 1.0,
            "updated_at": 2.0,
        }
    ]
    calls = []

    class Store:
        def load_turns_page(self, chat_id, *, limit=100, before_turn_index=None):
            calls.append((chat_id, limit, before_turn_index))
            return 500, [
                {"question": "q498", "answer_md": "a498", "model": main.DEFAULT_MODEL_ID},
                {"question": "q499", "answer_md": "a499", "model": main.DEFAULT_MODEL_ID},
            ]

        def load_chat(self, *args, **kwargs):
            raise AssertionError("remote state should not hydrate full archived chat")

        def load_turns(self, *args, **kwargs):
            raise AssertionError("remote state should not load all turns")

        def load_execution_steps(self, *args, **kwargs):
            raise AssertionError("remote state should not load execution steps by default")

    frame.chat_store = Store()
    monkeypatch.setattr(frame, "_hydrate_chat_from_store", lambda *args, **kwargs: pytest.fail("full hydrate is too heavy"))

    status, body = frame._remote_api_state_ui({"chat_id": "chat-heavy", "limit": 2})

    assert status == 200
    assert calls == [("chat-heavy", 2, None)]
    assert body["turn_count"] == 500
    assert [turn["question"] for turn in body["turns"]] == ["q498", "q499"]
    assert body["has_more"] is True
    assert body["oldest_cursor"] == "498"


def test_remote_turn_payload_reuses_canonical_answer_cache(frame, monkeypatch):
    turn = {"question": "q", "answer_md": "**cached**", "model": main.DEFAULT_MODEL_ID}
    calls = []
    original = main.md_to_plain

    def counting_plain(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(main, "md_to_plain", counting_plain)

    first = frame._remote_turn_payload(turn)
    second = frame._remote_turn_payload(turn)

    assert first == second
    assert calls == []
    assert first["answer"] == "**cached**"


def test_remote_state_reuses_snapshot_when_chat_has_not_changed(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": f"q {idx}", "answer_md": f"a {idx}", "model": main.DEFAULT_MODEL_ID, "created_at": float(idx)}
        for idx in range(120)
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    calls = []
    original = frame._remote_turn_payload

    def counting_payload(turn):
        calls.append(turn["question"])
        return original(turn)

    monkeypatch.setattr(frame, "_remote_turn_payload", counting_payload)

    first_status, first_body = frame._remote_api_state_ui({})
    first_calls = list(calls)
    second_status, second_body = frame._remote_api_state_ui({})

    assert first_status == second_status == 200
    assert first_body == second_body
    assert len(first_calls) == 100
    assert calls == first_calls


def test_remote_state_cache_invalidates_after_turn_change(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": main.DEFAULT_MODEL_ID}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}

    frame._remote_api_state_ui({})
    frame.active_session_turns[0]["answer_md"] = "changed"
    frame._mark_chat_turns_dirty("chat-current", 0)

    status, body = frame._remote_api_state_ui({})

    assert status == 200
    assert body["turns"][0]["answer"] == "changed"


def test_remote_offscreen_clear_real_store_stable_request_dispatches_exactly_once(frame, monkeypatch):
    archived = {"id": "route-owner", "model": main.DEFAULT_CODEX_MODEL,
                "turns": [{"question": "first payload", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}]}
    frame.archived_chats = [archived]
    frame.active_chat_id = frame.current_chat_id = "active-owner"
    frame.active_session_turns = [{"question": "active payload"}]
    frame._current_chat_state = {"id": "active-owner", "turns": frame.active_session_turns}
    starts = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: starts.append(args))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *a: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *a: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    payload = {"chat_id": "route-owner", "request_id": "stable-route-request"}
    assert frame._remote_api_clear_context_ui(payload)[0] == 200
    assert frame._remote_api_clear_context_ui(payload)[0] == 200

    assert starts == [("route-owner", 0, "first payload", main.DEFAULT_CODEX_MODEL)]
    assert frame.active_chat_id == frame.current_chat_id == "active-owner"
    assert frame.active_session_turns == [{"question": "active payload"}]
    with frame.chat_store._connect() as conn:
        rows = conn.execute("SELECT operation_id,revision FROM clear_operations WHERE chat_id='route-owner'").fetchall()
    assert len(rows) == 1


def test_remote_new_chat_from_history_clears_pending_and_history_context(frame, monkeypatch):
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史会话",
            "context_usage": {
                "used_tokens": 31000,
                "context_window": 200000,
                "source": "claudecode",
                "exact": True,
                "fresh": True,
                "model": "claudecode/default",
                "updated_at": 1.0,
            },
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame.active_session_turns = [{"question": "old question", "answer_md": "old answer"}]
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "created_at": .5,
        "updated_at": .5,
        "context_usage": {
            "used_tokens": 31000,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": "claudecode/default",
            "updated_at": 1.0,
        },
    }
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 123,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": "claudecode/default",
            "updated_at": 1.0,
        }
    }
    frame._mark_chat_turns_dirty("chat-current")
    frame._save_state()
    frame._refresh_history("chat-history")
    frame._render_answer_list()
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    titles, stops = [], []
    frame._zdsr_tts = SimpleNamespace(
        speak_chat_navigation=lambda title: titles.append(title) or True,
        speak_state=lambda: pytest.fail("remote new must settle navigation before polling"),
        stop=lambda: stops.append(True),
    )
    assert frame._navigate_history_chats(-1)
    assert titles == ["历史会话"] and frame.history_list_model.selected_id() == "chat-history"
    assert frame._chat_navigation_timer is not None and frame.view_history_id == "chat-history"
    assert any(meta[0] == "answer" and meta[2] == "old answer" for meta in frame.answer_meta)

    frame._start_remote_new_chat({"model": "openai/gpt-5.2"})

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame._pending_context_usage_by_turn == {}
    assert frame._current_chat_state.get("context_usage") is None
    assert stops == [True] and frame._chat_navigation_timer is None
    assert frame.history_list_model.selected_id() == "chat-current"
    assert frame.answer_list.FindString("暂无对话内容") != main.wx.NOT_FOUND
    assert not any(meta[0] in {"answer", "question"} for meta in frame.answer_meta)


def test_remote_rename_current_chat_matches_current_chat_id_and_invalidates_history_cache(frame, monkeypatch):
    frame.active_chat_id = ""
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "旧标题",
        "title_source": "default",
        "title_manual": False,
        "title_updated_at": 1.0,
        "title_revision": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
        "turns": [],
    }
    frame._remote_history_list_cache = {"accepted": True, "chats": [{"chat_id": "chat-current", "title": "旧标题"}]}
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    pushed = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id: pushed.append(chat_id))

    status, body = frame._remote_api_rename_chat_ui(
        {
            "chat_id": "chat-current",
            "title": "手机标题",
            "title_source": "manual",
            "title_updated_at": 10.0,
            "title_revision": 2,
        }
    )

    assert status == 200
    assert body["title"] == "手机标题"
    assert frame._current_chat_state["title"] == "手机标题"
    assert frame._remote_history_list_cache is None
    assert pushed == ["chat-current"]


def test_remote_sound_history_acceptance_and_pinned_recency(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "title": "A", "updated_at": 10.0, "turns": []}
    pinned = {"id": "p", "title": "P", "pinned": True, "updated_at": 1.0, "turns": []}
    target = {"id": "b", "title": "B", "model": main.DEFAULT_CODEX_MODEL, "updated_at": 2.0, "turns": []}
    frame.archived_chats = [target, pinned]
    frame._refresh_history("a")
    sounds = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: sounds.append("send"))
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: True)

    assert frame._submit_archived_remote_question(target, "", main.DEFAULT_CODEX_MODEL)[0] is False
    assert sounds == []
    assert frame._submit_archived_remote_question(target, "phone question", main.DEFAULT_CODEX_MODEL) == (True, "")
    frame._flush_idle_ui_refreshes()

    assert sounds == ["send"]
    assert frame.history_ids == ["p", "b", "a"]
    assert frame._get_all_chat_ids_in_order() == frame.history_ids
    assert frame.history_list_model.selected_id() == "a"
    assert frame.active_chat_id == "a"


def test_remote_sound_history_rejected_start_restores_recency(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "updated_at": 10.0, "turns": []}
    target = {"id": "b", "model": main.DEFAULT_CODEX_MODEL, "updated_at": 2.0, "turns": []}
    frame.archived_chats = [target]
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("rejected send must stay silent"))
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: False)

    assert frame._submit_archived_remote_question(target, "question", main.DEFAULT_CODEX_MODEL)[0] is False
    assert target["updated_at"] == 2.0
    assert target["turns"] == []
    assert frame._get_all_chat_ids_in_order() == ["a", "b"]


def test_remote_sound_history_old_and_cleared_results_stay_silent(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "updated_at": 10.0, "turns": []}
    old = {"question": "old", "answer_md": "old answer", "request_status": "done",
           "codex_thread_id": "old-thread", "codex_turn_id": "old-turn",
           "codex_context_generation": 0}
    cleared = {"question": "cleared", "answer_md": main.REQUESTING_TEXT,
               "request_status": "pending", "codex_thread_id": "new-thread",
               "codex_turn_id": "new-turn", "codex_context_generation": 1,
               "clear_operation_id": "cancelled-operation", "clear_revision": 3}
    chat = {"id": "b", "updated_at": 2.0, "turns": [old, cleared],
            "codex_thread_id": "new-thread", "codex_turn_id": "new-turn",
            "codex_context_generation": 1}
    frame.archived_chats = [chat]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: pytest.fail("stale result played a reply sound"))

    frame._on_codex_event_for_chat("b", main.CodexEvent(
        type="turn_completed", thread_id="old-thread", turn_id="old-turn",
        data={"turn_idx": 0, "context_generation": 0}, status="completed", text="stale"))
    frame._on_codex_event_for_chat("b", main.CodexEvent(
        type="turn_completed", thread_id="new-thread", turn_id="new-turn",
        data={"turn_idx": 1, "context_generation": 1}, status="completed", text="late"))

    assert chat["updated_at"] == 2.0
    assert old["answer_md"] == "old answer"
    assert cleared["answer_md"] == main.REQUESTING_TEXT
    assert cleared["request_status"] == "pending"
    assert frame._get_all_chat_ids_in_order() == ["a", "b"]


def test_offscreen_remote_submit_loads_full_store_turns_before_append(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({
        "id": "chat-target", "title": "Target", "model": "kimi/main", "created_at": 1.0, "updated_at": 1.0,
    })
    old_turn = {"question": "old", "answer_md": "answer", "model": "kimi/main", "created_at": 1.0}
    frame.chat_store.replace_turns("chat-target", [old_turn])
    summary = next(row for row in frame.chat_store.list_chat_summaries() if row["id"] == "chat-target")
    assert "turns" not in summary
    frame.archived_chats = [summary]
    dispatches = []
    monkeypatch.setattr(frame, "_start_kimi_worker_for_turn", lambda *args: dispatches.append(args))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    ok, message = frame._submit_archived_remote_question(summary, "new", "kimi/main")
    frame._persist_chat_history_to_store()

    assert (ok, message) == (True, "")
    assert dispatches == [("chat-target", 1, "new", "kimi/main")]
    persisted = frame.chat_store.load_turns("chat-target")
    assert [turn["question"] for turn in persisted] == ["old", "new"]


def test_remote_set_speed_does_not_refresh_history_list(frame, monkeypatch):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-speed",
        "title": "speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 1.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
        "codex_service_tier": "standard",
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda _chat_id: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("remote speed changes do not affect history rows"))

    status, body = frame._remote_api_set_speed_ui(
        {"chat_id": "chat-speed", "codex_service_tier": "fast"}
    )

    assert status == 200
    assert body["accepted"] is True
    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert frame.codex_speed_combo.GetValue() == "快速"


@pytest.mark.parametrize("kind, phase", [("item_completed", "final_answer"), ("subagent_result", "")])
def test_remote_sound_history_cancelled_clear_rejects_archived_content_before_recency(frame, monkeypatch, kind, phase):
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "turns": []}
    turn = {
        "question": "old", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL,
        "request_status": "pending", "codex_turn_id": "turn-b",
        "clear_operation_id": "cancelled-clear", "clear_revision": 1,
    }
    chat = {"id": "b", "turns": [turn], "updated_at": 1.0, "codex_thread_id": "thread-b"}
    frame.archived_chats = [chat]
    quarantined = []
    frame.chat_store = SimpleNamespace(
        get_clear_operation=lambda _op: {"state": "cancelled", "chat_id": "b", "revision": 1},
        quarantine=lambda *args: quarantined.append(args),
    )
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: pytest.fail("stale content must stay silent"))
    frame._on_codex_event_for_chat("b", main.CodexEvent(
        type=kind, phase=phase, thread_id="thread-b", turn_id="turn-b",
        data={"turn_idx": 0}, text="stale answer",
    ))

    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert chat["updated_at"] == 1.0
    assert frame._history_list_dirty is False
    assert quarantined and quarantined[0][0] == "SUPERSEDED_CLEAR_RESULT"


def test_remote_sound_history_old_generation_completion_cannot_finish_archived_turn(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "turns": []}
    turn = {
        "question": "new", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL,
        "request_status": "pending", "codex_turn_id": "turn-b",
        "codex_context_generation": 2, "codex_start_generation": 2,
    }
    chat = {
        "id": "b", "turns": [turn], "updated_at": 1.0,
        "codex_thread_id": "thread-b", "codex_context_generation": 2,
    }
    frame.archived_chats = [chat]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: pytest.fail("old generation must stay silent"))
    frame._on_codex_event_for_chat("b", main.CodexEvent(
        type="turn_completed", status="completed", thread_id="thread-b", turn_id="turn-b",
        data={"turn_idx": 0, "context_generation": 1}, text="stale answer",
    ))

    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert chat["updated_at"] == 1.0
    assert frame._history_list_dirty is False


def test_remote_offscreen_kimi_real_store_claims_accepts_and_sounds_once(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "remote-kimi.db")
    store.initialize()
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "active"
    frame.active_session_turns = [{"question": "active"}]
    frame._current_chat_state = {"id": "active", "turns": frame.active_session_turns}
    archived = {"id": "remote", "model": main.DEFAULT_KIMI_MODEL,
                "turns": [{"question": "first", "model": main.DEFAULT_KIMI_MODEL}]}
    frame.archived_chats = [archived]
    events = []
    monkeypatch.setattr(frame, "_start_kimi_worker_for_turn", lambda *_args: events.append("accepted"))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: events.append("sound"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args: None)

    status, body = frame._remote_api_clear_context_ui({"chat_id": "remote", "request_id": "remote-kimi"})
    assert status == 200 and body["accepted"] is True
    assert events == ["accepted", "sound"]
    assert store.active_clear_operation("remote")["state"] == "resend_dispatched"
    status, body = frame._remote_api_clear_context_ui({"chat_id": "remote", "request_id": "remote-kimi"})
    assert status == 200 and body["accepted"] is True
    assert events == ["accepted", "sound"]
    assert frame.active_chat_id == frame.current_chat_id == "active"
