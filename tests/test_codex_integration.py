import time

import wx

import main
from codex_client import CodexAppServerClient, CodexEvent, resolve_codex_launch_command

TEST_THREAD_ID = "019d36ab-804a-73a2-a2dd-7a17e181628f"
TEST_TURN_ID = "019d36b3-0a1c-7c61-aed9-387f6afbb9f9"


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
    frame.model_combo.SetValue("codex/main")
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
        def start_thread(self, **kwargs):
            seen["thread_started"] = kwargs
            return {"thread": {"id": TEST_THREAD_ID}}

        def start_turn(self, thread_id, text):
            seen["turn"] = (thread_id, text)
            return {"turn": {"id": TEST_TURN_ID}}

        def steer_turn(self, thread_id, expected_turn_id, text):
            raise AssertionError("should not steer")

    frame._ensure_codex_client = lambda: _Client()
    frame.input_edit.SetValue("鍐欎竴涓?hello world")

    frame._on_send_clicked(None)

    assert seen["turn"] == (TEST_THREAD_ID, "鍐欎竴涓?hello world")
    assert seen["thread_started"]["cwd"] == frame._workspace_dir_for_codex()
    assert seen["thread_started"]["approval_policy"] == "never"
    assert seen["thread_started"]["sandbox"] == "danger-full-access"
    assert frame.active_codex_thread_id == TEST_THREAD_ID
    assert frame.active_codex_turn_id == TEST_TURN_ID
    assert frame.active_codex_turn_active is True
    assert frame.active_session_turns[-1]["answer_md"] == main.REQUESTING_TEXT
    assert frame._active_request_count == 1


def test_send_click_routes_codex_steer_when_pending_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex/main")
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
        def start_thread(self, **kwargs):
            raise AssertionError("should not create new thread")

        def start_turn(self, thread_id, text):
            raise AssertionError("should not start new turn")

        def steer_turn(self, thread_id, expected_turn_id, text):
            seen["steer"] = (thread_id, expected_turn_id, text)
            return {"turnId": expected_turn_id}

    frame._ensure_codex_client = lambda: _Client()
    frame.input_edit.SetValue("src/app.py")

    frame._on_send_clicked(None)

    assert seen["steer"] == (TEST_THREAD_ID, TEST_TURN_ID, "src/app.py")
    assert frame._active_request_count == 1
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_session_turns[-1]["question"] == "src/app.py"


def test_send_click_routes_codex_steer_when_waiting_on_user_input_without_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex/main")
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
        def start_thread(self, **kwargs):
            raise AssertionError("should not create new thread")

        def start_turn(self, thread_id, text):
            raise AssertionError("should not start new turn")

        def steer_turn(self, thread_id, expected_turn_id, text):
            seen["steer"] = (thread_id, expected_turn_id, text)
            return {"turnId": expected_turn_id}

    frame._ensure_codex_client = lambda: _Client()
    frame.input_edit.SetValue("缁х画澶勭悊")

    frame._on_send_clicked(None)

    assert seen["steer"] == (TEST_THREAD_ID, TEST_TURN_ID, "缁х画澶勭悊")
    assert frame._active_request_count == 1
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_session_turns[-1]["question"] == "缁х画澶勭悊"


def test_send_click_routes_codex_start_when_turn_is_inactive_even_with_stale_prompt(frame, monkeypatch):
    frame.model_combo.SetValue("codex/main")
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
        def start_thread(self, **kwargs):
            raise AssertionError("should not create new thread")

        def start_turn(self, thread_id, text):
            seen["turn"] = (thread_id, text)
            return {"turn": {"id": TEST_TURN_ID}}

        def steer_turn(self, thread_id, expected_turn_id, text):
            raise AssertionError("should not steer when the turn is inactive")

    frame._ensure_codex_client = lambda: _Client()
    frame.input_edit.SetValue("新的补充")

    frame._on_send_clicked(None)

    assert seen["turn"] == (TEST_THREAD_ID, "新的补充")
    assert frame.active_codex_turn_active is True
    assert frame.active_codex_pending_prompt == ""
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
    frame.active_codex_turn_active = True
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_session_turns = [
        {
            "question": "甯垜淇敼閰嶇疆",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._on_codex_event(
        CodexEvent(
            type="item_completed",
            status="agentMessage",
            phase="final_answer",
            text="璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵",
        )
    )

    assert frame.active_codex_pending_prompt == "璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵"
    assert frame.active_session_turns[0]["answer_md"] == "璇锋彁渚涚洰鏍囨枃浠惰矾寰勶紵"


def test_codex_final_answer_rerenders_and_focuses_when_final_answer_arrives(frame, monkeypatch):
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {
            "question": "淇椤圭洰",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]
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
        )
    )

    assert frame.active_session_turns[0]["answer_md"] == "done"
    assert rendered["n"] == 1
    assert focused["n"] == 1



def test_codex_request_user_input_dialog_returns_answers(frame, monkeypatch):
    seen = {}

    class _Client:
        def respond_tool_request_user_input(self, request_id, answers):
            seen["request"] = (request_id, answers)

    class _Dialog:
        def __init__(self, _parent, _questions):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def get_answers(self):
            return {"q1": ["閫夐」A"]}

        def Destroy(self):
            pass

    frame._ensure_codex_client = lambda: _Client()
    monkeypatch.setattr(main, "CodexUserInputDialog", _Dialog)

    frame._handle_codex_request_dialog(
        {
            "request_id": 7,
            "method": "item/tool/requestUserInput",
            "params": {"questions": [{"id": "q1", "header": "闂", "question": "璇烽€夋嫨"}]},
        }
    )

    assert seen["request"] == (7, {"q1": ["閫夐」A"]})


def test_codex_turn_completed_clears_busy_state(frame, monkeypatch):
    frame._active_request_count = 1
    frame.active_codex_turn_active = True
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))

    frame._on_codex_event(
        CodexEvent(type="turn_completed", thread_id=TEST_THREAD_ID, turn_id=TEST_TURN_ID, status="completed")
    )

    assert frame._active_request_count == 0
    assert frame.active_codex_turn_active is False
    assert frame.is_running is False
    assert played["n"] == 1


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


def test_codex_event_for_stale_chat_id_still_refreshes_current_chat(frame, monkeypatch):
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

    assert frame.active_session_turns[0]["answer_md"] == "final answer"
    assert rendered["n"] == 1
    assert focused["n"] == 1


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

