import time

import wx
import pytest

import main
from codex_client import CodexAppServerClient, CodexEvent, resolve_codex_launch_command
import codex_client

TEST_THREAD_ID = "019d36ab-804a-73a2-a2dd-7a17e181628f"
TEST_TURN_ID = "019d36b3-0a1c-7c61-aed9-387f6afbb9f9"


@pytest.mark.parametrize("archived", [False, True])
def test_codex_steer_completed_items_accumulate_and_reload(frame, monkeypatch, archived):
    chat_id = "steer-owner"
    turns = [{"question": question, "answer_md": answer, "model": main.DEFAULT_CODEX_MODEL,
              "request_status": status, "codex_turn_id": TEST_TURN_ID,
              "codex_thread_id": TEST_THREAD_ID, "codex_start_generation": 2,
              "codex_context_generation": 2}
             for question, answer, status in [("old question", "old answer", "done"),
                 ("group 187387007", main.REQUESTING_TEXT, "pending"),
                 ("third accepted input", main.REQUESTING_TEXT, "pending")]]
    chat = {"id": chat_id, "title": "steer", "model": main.DEFAULT_CODEX_MODEL,
            "turns": turns, "codex_thread_id": TEST_THREAD_ID, "codex_turn_id": TEST_TURN_ID,
            "codex_context_generation": 2}
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    foreground_turns = frame.active_session_turns
    if archived:
        frame.archived_chats = [chat]
    else:
        frame.active_chat_id = frame.current_chat_id = chat_id
        frame.active_session_turns = turns
        frame._current_chat_state = chat
    frame.chat_store.upsert_chat(chat)
    def deliver(kind, text="", item_id="", idx=1, generation=2, **data):
        frame._on_codex_event_for_chat(chat_id, CodexEvent(type=kind, text=text, item_id=item_id,
            thread_id=TEST_THREAD_ID, turn_id=TEST_TURN_ID, phase="final_answer", subtype="agentMessage",
            status="completed", data={"turn_idx": idx, "context_generation": generation,
                "model": main.DEFAULT_CODEX_MODEL, **data}))
    deliver("item_completed", "wrong generation", "bad", generation=1)
    assert turns[1]["answer_md"] == main.REQUESTING_TEXT
    deliver("item_completed", "group connected; collecting", "ask")
    deliver("agent_message_delta", "partial must not overwrite", "final")
    assert turns[1]["answer_md"] == "group connected; collecting"
    deliver("item_completed", "true final answer", "final")
    deliver("item_completed", "true final answer", "final")
    deliver("item_completed", "old late overwrite", "old", idx=0)
    assert turns[0]["answer_md"] == "old answer"
    deliver("turn_completed", "true final answer", completion_owners=[
        {"turn_idx": idx, "context_generation": 2} for idx in range(3)])
    assert turns[1]["answer_md"] == "group connected; collecting\n\ntrue final answer"
    assert [turn["request_status"] for turn in turns] == ["done", "done", "done"]
    if archived:
        assert frame.active_session_turns is foreground_turns
    frame._persist_chat_history_to_store()
    loaded = main.ChatStore(frame.chat_store.db_path).load_chat(chat_id)["turns"]
    assert [turn["question"] for turn in loaded] == [turn["question"] for turn in turns]
    assert [turn["answer_md"] for turn in loaded] == [turn["answer_md"] for turn in turns]
    assert [turn["request_status"] for turn in loaded] == ["done", "done", "done"]
    assert loaded[1]["codex_completed_answers"] == turns[1]["codex_completed_answers"]


def test_codex_legacy_completed_text_deduplicates_without_item_id_and_kimi_keeps_first_answer(frame):
    turn = {"answer_md": main.REQUESTING_TEXT, "request_status": "pending"}
    for text in ("question", "question", "final", "final"):
        frame._apply_codex_completed_answer_to_turn(turn, CodexEvent(type="item_completed", text=text))
    assert turn["answer_md"] == "question\n\nfinal"
    kimi = {"answer_md": "first answer"}
    assert not frame._apply_kimi_final_answer_to_turn(kimi, "second answer")
    assert kimi["answer_md"] == "first answer"


def test_codex_native_completion_does_not_finish_unrelated_or_failed_owner(frame, monkeypatch):
    turns = [{"question": "input", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL,
              "request_status": status, "codex_turn_id": native, "codex_thread_id": TEST_THREAD_ID,
              "codex_start_generation": generation, "codex_context_generation": 2}
             for status, native, generation in [("pending", TEST_TURN_ID, 2),
                 ("pending", "other-native", 2), ("failed", TEST_TURN_ID, 2),
                 ("pending", TEST_TURN_ID, 3)]]
    frame.active_session_turns = turns
    frame._current_chat_state.update({"turns": turns, "codex_context_generation": 2})
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    frame._on_codex_event_for_chat(frame.active_chat_id, CodexEvent(type="turn_completed",
        thread_id=TEST_THREAD_ID, turn_id=TEST_TURN_ID, status="completed",
        data={"turn_idx": 0, "context_generation": 2, "completion_owners": [
            {"turn_idx": idx, "context_generation": 2} for idx in range(4)]}))
    assert [turn["request_status"] for turn in turns] == ["done", "pending", "failed", "pending"]


def test_codex_archived_completion_records_only_new_authoritative_activity(frame, monkeypatch):
    foreground = frame._current_chat_state
    foreground["updated_at"] = 10.0
    turn = {"question": "background", "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending",
            "codex_turn_id": TEST_TURN_ID, "codex_start_generation": 2,
            "codex_context_generation": 2}
    archived = {"id": "background", "title": "background", "updated_at": 1.0,
                "codex_context_generation": 2, "turns": [turn]}
    frame.archived_chats = [archived]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    def deliver(kind, generation=2):
        frame._on_codex_event_for_chat("background", CodexEvent(
            type=kind, turn_id=TEST_TURN_ID, phase="final_answer", text="accepted answer",
            status="completed", data={"turn_idx": 0, "context_generation": generation},
        ))
    deliver("item_completed")
    assert archived["updated_at"] == 1.0
    deliver("turn_completed", 1)
    assert archived["updated_at"] == 1.0
    assert turn["request_status"] == "pending"
    deliver("turn_completed")
    accepted_at = archived["updated_at"]
    assert accepted_at > 1.0 and turn["request_status"] == "done"
    assert foreground["updated_at"] == 10.0
    deliver("turn_completed")
    assert archived["updated_at"] == accepted_at
    assert foreground["updated_at"] == 10.0


def test_codex_origin_timestamp_normalization_matches_provider_contract():
    seconds = 1_795_000_000.0
    assert codex_client._provider_origin_timestamp({"timestamp": seconds}) == seconds
    assert codex_client._provider_origin_timestamp({"created_at": str(int(seconds * 1000))}) == seconds
    assert codex_client._provider_origin_timestamp({"createdAt": "2026-09-21T08:30:00Z"}) is not None
    for invalid in (True, float("nan"), float("inf"), "2026-09-21T08:30:00", 12.5):
        assert codex_client._provider_origin_timestamp({"timestamp": invalid}) is None
    assert codex_client._provider_origin_timestamp({"time": seconds}) is None
    assert codex_client._provider_origin_timestamp(
        {"timestamp": seconds}, {"timestamp": seconds + 1}
    ) == seconds


class _ImmediateThread:
    def __init__(self, target=None, args=None, kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def test_model_combo_contains_codex(frame):
    choices = [frame.model_combo.GetString(i) for i in range(frame.model_combo.GetCount())]
    assert "codex" in choices
    assert "codex/main" not in choices


def test_send_click_routes_codex_start(frame, monkeypatch):
    frame.model_combo.SetValue("codex")
    frame.selected_model = "codex/main"
    frame._active_request_count = 0
    frame.active_codex_thread_id = ""
    frame.active_codex_turn_id = ""
    frame.active_codex_turn_active = False
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)

    seen = {}

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            seen["payload"] = payload
            return "request-1"

    frame._get_or_create_codex_client = lambda _chat_id, _model="": _Client()
    frame.input_edit.SetValue("鍐欎竴涓?hello world")

    frame._on_send_clicked(None)

    payload = seen["payload"]
    assert payload["chat_id"] == frame.active_chat_id
    assert payload["thread_id"] == ""
    assert payload["question"] == "鍐欎竴涓?hello world"
    assert payload["should_steer"] is False
    assert payload["cwd"] == frame._workspace_dir_for_codex()
    assert frame.active_session_turns[-1]["answer_md"] == main.REQUESTING_TEXT
    assert frame._active_request_count == 1


def test_send_click_routes_codex_steer_when_pending_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex")
    frame.selected_model = "codex/main"
    frame.active_codex_thread_id = TEST_THREAD_ID
    frame.active_codex_turn_id = TEST_TURN_ID
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵"
    frame._active_request_count = 1
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)

    seen = {}

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            seen["payload"] = payload
            return "request-1"

    frame._get_or_create_codex_client = lambda _chat_id, _model="": _Client()
    frame.input_edit.SetValue("src/app.py")

    frame._on_send_clicked(None)

    payload = seen["payload"]
    assert payload["thread_id"] == TEST_THREAD_ID
    assert payload["turn_id"] == TEST_TURN_ID
    assert payload["question"] == "src/app.py"
    assert payload["should_steer"] is True
    assert frame._active_request_count == 1
    assert frame.active_session_turns[-1]["question"] == "src/app.py"


def test_send_click_routes_codex_steer_when_waiting_on_user_input_without_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex")
    frame.selected_model = "codex/main"
    frame.active_codex_thread_id = TEST_THREAD_ID
    frame.active_codex_turn_id = TEST_TURN_ID
    frame.active_codex_turn_active = True
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_pending_prompt = ""
    frame._active_request_count = 1
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)

    seen = {}

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            seen["payload"] = payload
            return "request-1"

    frame._get_or_create_codex_client = lambda _chat_id, _model="": _Client()
    frame.input_edit.SetValue("缁х画澶勭悊")

    frame._on_send_clicked(None)

    payload = seen["payload"]
    assert payload["thread_id"] == TEST_THREAD_ID
    assert payload["turn_id"] == TEST_TURN_ID
    assert payload["question"] == "缁х画澶勭悊"
    assert payload["should_steer"] is True
    assert frame._active_request_count == 1
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_session_turns[-1]["question"] == "缁х画澶勭悊"


def test_send_click_routes_codex_start_when_turn_is_inactive_even_with_stale_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex")
    frame.selected_model = "codex/main"
    frame.active_codex_thread_id = TEST_THREAD_ID
    frame.active_codex_turn_id = TEST_TURN_ID
    frame.active_codex_turn_active = False
    frame.active_codex_pending_prompt = "璇锋彁渚涘唴瀹?中文"
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame._active_request_count = 1
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)

    seen = {}

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            seen["payload"] = payload
            return "request-1"

    frame._get_or_create_codex_client = lambda _chat_id, _model="": _Client()
    frame.input_edit.SetValue("新的补充")

    frame._on_send_clicked(None)

    payload = seen["payload"]
    assert payload["thread_id"] == TEST_THREAD_ID
    assert payload["question"] == "新的补充"
    assert payload["should_steer"] is False
    assert frame.active_session_turns[-1]["question"] == "新的补充"


def test_load_chat_clears_invalid_codex_runtime_state(frame):
    frame._load_chat_as_current(
        {
            "id": "chat-a",
            "model": "codex/main",
            "turns": [{"question": "q", "answer_md": "a", "model": "codex/main", "created_at": time.time()}],
            "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "codex_turn_active": True,
            "codex_pending_prompt": "",
            "codex_thread_flags": ["waitingOnUserInput"],
        }
    )

    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_codex_thread_flags == []


def test_codex_final_question_sets_pending_prompt_and_updates_answer(frame):
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame.active_codex_turn_active = True
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_session_turns = [
        {
            "question": "甯垜淇敼閰嶇疆",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
            "codex_turn_id": TEST_TURN_ID,
        }
    ]
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["turns"] = frame.active_session_turns

    frame._on_codex_event(
        CodexEvent(
            type="item_completed",
            status="agentMessage",
            phase="final_answer",
            text="璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵",
            turn_id=TEST_TURN_ID,
            data={"turn_idx": 0},
        )
    )

    assert frame.active_codex_pending_prompt == "璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵"
    assert frame.active_session_turns[0]["answer_md"] == "璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵"


def test_codex_final_answer_appends_and_focuses_when_final_answer_arrives(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {
            "question": "淇椤圭洰",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
            "codex_turn_id": TEST_TURN_ID,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    rendered = {"n": 0}
    focused = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.__setitem__("n", focused["n"] + 1))

    frame._on_codex_event(
        CodexEvent(
            type="item_completed",
            status="agentMessage",
            phase="final_answer",
            text="done",
            turn_id=TEST_TURN_ID,
            data={"turn_idx": 0},
        )
    )

    assert frame.active_session_turns[0]["answer_md"] == "done"
    assert rendered["n"] == 0
    assert focused["n"] == 1


def test_codex_execution_step_replaces_empty_placeholder(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = []
    frame.active_turn_idx = -1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    assert list(frame.execution_list.GetStrings()) == ["暂无执行过程"]

    assert frame._append_execution_entry_to_chat(
        "chat-current",
        {
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：正在检查项目文件",
            "detail_text": "正在检查项目文件",
        },
    )

    # Time separators are independent reachable rows; the placeholder must be
    # replaced by the execution content, not necessarily be the only row.
    assert list(frame.execution_list.GetStrings())[-1] == "计划：正在检查项目文件"
    assert [meta[0] for meta in frame.execution_meta if meta[0] != "time"] == ["execution"]



def test_codex_request_user_input_dialog_returns_answers(frame, monkeypatch):
    replies = []

    class _Client:
        def start(self):
            pass

        def reply_user_input(self, chat_id, request_id, answers):
            replies.append((chat_id, request_id, answers))

    class _Dialog:
        def __init__(self, _parent, _questions):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def get_answers(self):
            return {"q1": ["閫夐」A"]}

        def Destroy(self):
            pass

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(
        frame,
        "_ensure_codex_client",
        lambda *a, **k: pytest.fail("dialog reply must not use singleton codex client"),
    )
    monkeypatch.setattr(main, "CodexUserInputDialog", _Dialog)
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"

    frame._handle_codex_request_dialog(
        {
            "request_id": "ask-1",
            "method": "item/tool/requestUserInput",
            "chat_id": "chat-current",
            "params": {"questions": [{"id": "q1", "header": "闂", "question": "璇烽€夋嫨"}]},
        }
    )

    assert replies == [("chat-current", "ask-1", {"q1": ["閫夐」A"]})]


def test_codex_request_user_input_dialog_replies_through_worker(frame, monkeypatch):
    replies = []

    class FakeWorker:
        def start(self):
            pass

        def reply_user_input(self, chat_id, request_id, answers):
            replies.append((chat_id, request_id, answers))

    class _Dialog:
        def __init__(self, _parent, _questions):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def get_answers(self):
            return {"reply": ["ok"]}

        def Destroy(self):
            pass

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    monkeypatch.setattr(
        frame,
        "_ensure_codex_client",
        lambda *a, **k: pytest.fail("dialog reply must not use singleton codex client"),
    )
    monkeypatch.setattr(main, "CodexUserInputDialog", _Dialog)

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._handle_codex_request_dialog(
        {
            "request_id": "ask-1",
            "method": "item/tool/requestUserInput",
            "params": {"questions": []},
            "chat_id": "chat-current",
        }
    )

    assert replies == [("chat-current", "ask-1", {"reply": ["ok"]})]


def test_codex_turn_completed_clears_busy_state(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._active_request_count = 1
    frame.active_codex_turn_active = True
    frame.active_turn_idx = 0
    frame.active_session_turns = [{"codex_turn_id": TEST_TURN_ID, "model": "codex/main", "request_status": "pending", "answer_md": main.REQUESTING_TEXT}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))

    frame._on_codex_event(
        CodexEvent(type="turn_completed", thread_id=TEST_THREAD_ID, turn_id=TEST_TURN_ID, status="completed", data={"turn_idx": 0})
    )

    assert frame._active_request_count == 0
    assert frame.active_codex_turn_active is False
    assert frame.is_running is False
    assert played["n"] == 1


def test_ambiguous_codex_turn_id_does_not_target_a_turn(frame):
    turns = [
        {"codex_turn_id": TEST_TURN_ID, "answer_md": "first"},
        {"codex_turn_id": TEST_TURN_ID, "answer_md": "second"},
    ]
    event = CodexEvent(type="item_completed", phase="final_answer", turn_id=TEST_TURN_ID, text="final")
    assert frame._event_scoped_turn_index(turns, event) == -1
    assert frame._event_turn_index(turns, event) == -1


def test_execution_filters_only_empty_not_loaded_placeholder(frame):
    placeholder = CodexEvent(type="item_completed", display_kind="info", text="Not Loaded")
    failure = CodexEvent(type="item_completed", display_kind="error", status="failed", text="Not Loaded")
    assert frame._build_execution_entry(placeholder) is None
    assert frame._build_execution_entry(failure) is not None
    for text in ("Not Loaded", "notLoaded"):
        assert frame._build_execution_entry(CodexEvent(type="item_completed", display_kind="info", text=text)) is None
        assert not frame._should_show_execution_step({"text": text, "display_kind": "info"})
        assert frame._should_show_execution_step({"text": text, "exit_code": 1})
        assert frame._should_show_execution_step({"text": text, "title": "useful description"})


def test_codex_server_request_plays_finish_sound(frame, monkeypatch):
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    monkeypatch.setattr(frame, "_handle_codex_request_dialog", lambda request: None)

    frame._on_codex_event(
        CodexEvent(
            type="server_request",
            request_id=7,
            method="item/commandExecution/requestApproval",
            params={
                "command": "pytest",
                "reason": "Need approval",
            },
        )
    )

    assert played["n"] == 1
    assert frame.active_codex_pending_request is None


def test_codex_app_server_real_roundtrip():
    resolve_codex_launch_command()
    seen = []
    client = CodexAppServerClient(on_event=seen.append, timeout=90)
    try:
        result = client.start_thread(
            cwd=r"c:\code\codex",
            approval_policy="never",
            sandbox="danger-full-access",
            personality="pragmatic",
        )
        thread_id = str((result.get("thread") or {}).get("id") or "")
        assert thread_id

        turn = client.start_turn(thread_id, "Reply with exactly OK and nothing else.")
        turn_id = str((turn.get("turn") or {}).get("id") or "")
        assert turn_id

        deadline = time.time() + 45
        while time.time() < deadline:
            if any(
                event.type == "item_completed"
                and event.turn_id == turn_id
                and event.status == "userMessage"
                for event in seen
            ):
                break
            time.sleep(0.2)

        assert any(event.type == "thread_started" and event.thread_id == thread_id for event in seen)
        assert any(event.type == "turn_started" and event.turn_id == turn_id for event in seen)
        assert any(
            event.type == "item_completed"
            and event.turn_id == turn_id
            and event.status == "userMessage"
            for event in seen
        )
    finally:
        client.close()


def test_multiple_chat_ids_get_distinct_codex_clients(frame):
    frame.active_chat_id = "chat-a"
    client_a = frame._get_or_create_codex_client("chat-a")
    client_b = frame._get_or_create_codex_client("chat-b")

    assert client_a is not client_b
    assert frame._codex_clients["chat-a"] is client_a
    assert frame._codex_clients["chat-b"] is client_b


def test_codex_event_for_stale_chat_id_with_current_thread_id_is_ignored(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.active_codex_thread_id = TEST_THREAD_ID
    frame.active_codex_turn_id = TEST_TURN_ID
    frame.active_session_turns = [
        {
            "question": "淇褰撳墠鑱婂ぉ鍒锋柊",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
            "codex_turn_id": TEST_TURN_ID,
        }
    ]
    frame.archived_chats = [
        {
            "id": "chat-stale",
            "title": "stale chat",
            "turns": [{"question": "old question", "answer_md": main.REQUESTING_TEXT, "model": "codex/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "other-thread",
            "codex_turn_id": "other-turn",
        }
    ]
    rendered = {"n": 0}
    focused = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.__setitem__("n", focused["n"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-stale",
        CodexEvent(
            type="item_completed",
            thread_id=TEST_THREAD_ID,
            turn_id=TEST_TURN_ID,
            status="agentMessage",
            phase="final_answer",
            text="final answer",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert frame.archived_chats[0]["turns"][0]["answer_md"] == main.REQUESTING_TEXT
    assert rendered["n"] == 0
    assert focused["n"] == 0


def test_background_codex_progress_event_defers_ui_feedback_and_state_flush(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-stale",
            "title": "stale chat",
            "turns": [{"question": "old question", "answer_md": main.REQUESTING_TEXT, "model": "codex/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "other-thread",
            "codex_turn_id": "other-turn",
        }
    ]
    statuses = []
    saved = {"n": 0}
    refreshed = {"n": 0}
    scheduled = []

    frame.state_path = type(
        "_StatePath",
        (),
        {"write_text": lambda self, *_args, **_kwargs: saved.__setitem__("n", saved["n"] + 1)},
    )()
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: refreshed.__setitem__("n", refreshed["n"] + 1))
    frame.SetStatusText = lambda text: statuses.append(text)
    monkeypatch.setattr(main.wx, "CallLater", lambda delay, fn, *args, **kwargs: scheduled.append((delay, fn, args, kwargs)))

    frame._on_codex_event_for_chat(
        "chat-stale",
        CodexEvent(
            type="item_completed",
            thread_id="other-thread",
            turn_id="other-turn",
            status="agentMessage",
            phase="analysis",
            text="background follow-up",
        ),
    )

    frame._on_codex_event_for_chat(
        "chat-stale",
        CodexEvent(
            type="plan_updated",
            thread_id="other-thread",
            turn_id="other-turn",
            text="鍚庡彴璁″垝鏇存柊",
        ),
    )

    assert statuses == []
    assert saved["n"] == 0
    assert refreshed["n"] == 0
    assert len(scheduled) == 1

    _delay, fn, args, kwargs = scheduled[0]
    fn(*args, **kwargs)

    assert saved["n"] == 1
    assert refreshed["n"] == 0


def test_background_codex_progress_event_skips_remote_pushes_until_flush(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-stale",
            "title": "stale chat",
            "turns": [{"question": "old question", "answer_md": main.REQUESTING_TEXT, "model": "codex/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "other-thread",
            "codex_turn_id": "other-turn",
        }
    ]
    pushed_status = []
    pushed_state = []
    scheduled = []

    monkeypatch.setattr(frame, "_push_remote_status", lambda *args, **kwargs: pushed_status.append((args, kwargs)))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: pushed_state.append((args, kwargs)))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda delay, fn, *args, **kwargs: scheduled.append((delay, fn, args, kwargs)))

    frame._on_codex_event_for_chat(
        "chat-stale",
        CodexEvent(
            type="item_completed",
            thread_id="other-thread",
            turn_id="other-turn",
            status="agentMessage",
            phase="analysis",
            text="background follow-up",
        ),
    )
    frame._on_codex_event_for_chat(
        "chat-stale",
        CodexEvent(
            type="plan_updated",
            thread_id="other-thread",
            turn_id="other-turn",
            text="鍚庡彴璁″垝鏇存柊",
        ),
    )

    assert pushed_status == []
    assert pushed_state == []
    assert len(scheduled) == 1


def test_new_chat_preserves_previous_codex_session(frame, monkeypatch):
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue("codex/main")
    frame.active_chat_id = "chat-a"
    frame.active_codex_thread_id = TEST_THREAD_ID
    frame.active_session_turns = [
        {"question": "闂A", "answer_md": "鍥炵瓟A", "model": "codex/main", "created_at": time.time()}
    ]
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)

    frame._on_new_chat_clicked(None)

    assert frame.active_chat_id
    assert frame.active_chat_id != "chat-a"
    assert frame.active_session_turns == []
    archived = frame._find_archived_chat("chat-a")
    assert archived is not None
    assert archived["codex_thread_id"] == TEST_THREAD_ID
    assert archived["turns"][0]["question"] == "闂A"



def test_codex_startup_failure_callback_rejects_reused_turn(frame, monkeypatch):
    frame.model_combo.SetValue("codex")
    frame.selected_model = "codex/main"
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    frame._refresh_openclaw_sync_lifecycle = lambda **kw: None
    frame._play_send_sound = lambda: None
    callbacks = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *a, **kw: callbacks.append((fn,a,kw)))
    class Client:
        def start(self):
            raise ConnectionError("old codex startup")
    frame._get_or_create_codex_client = lambda *a: Client()
    frame.input_edit.SetValue("old request")
    frame._on_send_clicked(None)
    replacement = dict(frame.active_session_turns[0], question="new request", request_status="pending", answer_md=main.REQUESTING_TEXT)
    frame.active_session_turns[:] = [replacement]
    for fn, a, kw in callbacks:
        fn(*a, **kw)
    assert replacement["request_status"] == "pending"


def test_retired_codex_client_error_does_not_fail_current_turn(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question": "new", "request_status": "pending", "answer_md": main.REQUESTING_TEXT, "codex_context_generation": 2}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "scope-chat", "turns": frame.active_session_turns, "codex_context_generation": 2}
    old_client, new_client = object(), object()
    frame._codex_clients["scope-chat"] = new_client
    frame._on_codex_worker_message("scope-chat", {"type":"error", "payload":{"chat_id":"scope-chat","turn_idx":0,"context_generation":1,"message":"old failure"}}, old_client)
    assert turn["request_status"] == "pending"


def test_queued_codex_final_rechecks_source_client_before_application(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question":"new", "request_status":"pending", "answer_md":main.REQUESTING_TEXT,
            "codex_context_generation":3, "codex_start_generation":2,
            "codex_turn_id":"native-new", "codex_thread_id":"thread-new"}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id":"scope-chat", "turns":frame.active_session_turns,
        "codex_context_generation":3, "codex_thread_id":"thread-new", "codex_turn_id":"native-new"}
    old, current = object(), object()
    frame._codex_clients["scope-chat"] = old
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: True)
    event = CodexEvent(type="item_completed", thread_id="thread-new", turn_id="native-new",
        phase="final_answer", text="queued final", status="completed",
        data={"turn_idx":0, "context_generation":2})
    message = {"type":"event", "payload":{"chat_id":"scope-chat", "turn_idx":0,
        "context_generation":2, "event":main.codex_worker_protocol.event_to_payload(event)}}
    frame._on_codex_worker_message("scope-chat", message, old)
    assert len(frame._pending_codex_ui_events) == 1
    frame._codex_clients["scope-chat"] = current
    frame._drain_codex_ui_events()
    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT
    frame._on_codex_worker_message("scope-chat", message, current)
    frame._drain_codex_ui_events()
    assert turn["answer_md"] == "queued final"


def test_old_codex_generation_error_does_not_fail_current_turn(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question": "new", "request_status": "pending", "answer_md": main.REQUESTING_TEXT, "codex_context_generation": 2}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "scope-chat", "turns": frame.active_session_turns, "codex_context_generation": 2}
    frame._on_codex_worker_message("scope-chat", {"type":"error", "payload":{"chat_id":"scope-chat","turn_idx":0,"context_generation":1,"message":"old failure"}})
    assert turn["request_status"] == "pending"


def test_codex_current_start_generation_error_ends_bound_request(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question":"new", "request_status":"pending", "answer_md":main.REQUESTING_TEXT,
            "codex_context_generation":3, "codex_start_generation":2, "codex_turn_id":"native-new"}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id":"scope-chat", "turns":frame.active_session_turns, "codex_context_generation":3}
    frame._on_codex_worker_message("scope-chat", {"type":"error", "payload":{
        "chat_id":"scope-chat", "turn_idx":0, "turn_id":"native-new", "context_generation":2,"message":"current startup failed"}})
    assert turn["request_status"] == "failed"
    assert turn["request_error"] == "current startup failed"


def test_codex_error_invalid_generation_or_conflicting_turn_is_ignored(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question":"new", "request_status":"pending", "answer_md":main.REQUESTING_TEXT,
            "codex_context_generation":3, "codex_start_generation":2, "codex_turn_id":"native-new"}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id":"scope-chat", "turns":frame.active_session_turns, "codex_context_generation":3}
    for generation, turn_id in [("invalid", "native-new"), (True, "native-new"), (2, "native-old")]:
        frame._on_codex_worker_message("scope-chat", {"type":"error", "payload":{
            "chat_id":"scope-chat", "turn_idx":0, "turn_id":turn_id,"context_generation":generation,"message":"stale"}})
        assert turn["request_status"] == "pending"


def test_retired_codex_client_exit_keeps_current_request_pending(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question":"new", "request_status":"pending", "answer_md":main.REQUESTING_TEXT}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id":"scope-chat", "turns":frame.active_session_turns}
    frame._codex_worker_active_turns["scope-chat"] = {"turn_idx":0}
    old, current = object(), object()
    frame._codex_clients["scope-chat"] = current
    frame._on_codex_worker_exit("scope-chat", 23, old)
    assert turn["request_status"] == "pending"
    frame._on_codex_worker_exit("scope-chat", 23, current)
    assert turn["request_status"] == "failed"


def test_late_final_cannot_revive_failed_codex_request(frame):
    frame.active_chat_id = frame.current_chat_id = "scope-chat"
    turn = {"question":"new", "request_status":"failed", "request_error":"startup buffer overflow", "answer_md":"startup buffer overflow",
            "codex_context_generation":3, "codex_start_generation":2, "codex_turn_id":"native-new", "codex_thread_id":"thread-new"}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id":"scope-chat", "turns":frame.active_session_turns, "codex_context_generation":3, "codex_thread_id":"thread-new", "codex_turn_id":"native-new"}
    for kind in ["item_completed", "turn_completed"]:
        event = CodexEvent(type=kind, thread_id="thread-new", turn_id="native-new", phase="final_answer", text="late final", status="completed", data={"turn_idx":0,"context_generation":2})
        frame._on_codex_event_for_chat("scope-chat", event)
    assert turn["request_status"] == "failed"
    assert turn["answer_md"] == "startup buffer overflow"
