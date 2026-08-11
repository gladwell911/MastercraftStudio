import ctypes
import time

import main
from kimi_server_client import KimiEvent
from test_kimi_integration import FakeKimiServerClient, _setup_kimi_frame


def _send_window_key(window, key_code):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    scan_codes = {
        main.wx.WXK_UP: 0x48,
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_TAB: 0x0F,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_TAB: 0x09,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, wm_keydown, virtual_key, down_lparam)
    user32.SendMessageW(hwnd, wm_keyup, virtual_key, up_lparam)


def _activate_frame(frame, wx_app):
    frame.Show()
    frame.Raise()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow(int(frame.GetHandle()))
    wx_app.Yield()


def _yield_until(wx_app, predicate, timeout=2.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        wx_app.Yield()
        if predicate():
            return True
        time.sleep(0.01)
    wx_app.Yield()
    return predicate()


def _inside_frame(frame, window):
    ancestor = window
    while ancestor is not None and ancestor is not frame:
        ancestor = ancestor.GetParent()
    return ancestor is frame


def _setup_active_kimi_chat(frame, monkeypatch, *, detail_panel_mode="answers", execution_steps=None):
    frame.active_chat_id = "chat-kimi"
    frame.current_chat_id = "chat-kimi"
    frame.view_mode = "active"
    frame.active_kimi_session_id = "session-1"
    frame.active_kimi_turn_id = "turn-1"
    frame.active_kimi_turn_active = True
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "kimi 问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "kimi/main",
            "created_at": 1.0,
            "request_status": "pending",
            "kimi_session_id": "session-1",
            "kimi_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-kimi",
        "title": "kimi",
        "turns": frame.active_session_turns,
        "detail_panel_mode": detail_panel_mode,
        "execution_steps": list(execution_steps or []),
        "kimi_session_id": "session-1",
        "kimi_turn_id": "turn-1",
        "kimi_turn_active": True,
    }
    frame._kimi_active_turns["chat-kimi"] = {
        "turn_idx": 0,
        "turn_id": "turn-1",
        "session_id": "session-1",
        "model": "kimi/main",
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)


def _push_delta_events(frame, count, *, chat_id="chat-kimi"):
    for idx in range(count):
        frame._dispatch_kimi_event_to_ui(
            chat_id,
            main.CodexEvent(
                type="agent_message_delta",
                turn_id="turn-1",
                text=f"增量{idx} ",
                display_kind="assistant",
            ),
        )


def _drain_all_kimi_events(frame):
    while frame._pending_kimi_ui_events:
        frame._drain_kimi_ui_events()


# D1 — 事件风暴期间用户导航：焦点不被抢、列表选择不变
def test_event_storm_during_navigation_keeps_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch)
    # 完整事件链路：fake client → _on_kimi_client_message → _dispatch_kimi_event_to_ui
    fake = FakeKimiServerClient()
    fake.on_message = frame._on_kimi_client_message
    frame._kimi_client = fake

    frame._render_answer_list()
    frame.answer_list.SetSelection(1)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    for idx in range(500):
        fake.push_event(
            KimiEvent(
                type="agent_message_delta",
                thread_id="session-1",
                turn_id="turn-1",
                text=f"增量{idx} ",
                display_kind="assistant",
            )
        )

    # Events are coalesced before the UI loop runs. Depending on timer scheduling,
    # the navigation yields below may drain some or all batches, so assert the
    # queued state at the deterministic point before yielding.
    assert len(frame._pending_kimi_ui_events) == 500
    assert frame._kimi_ui_event_flush_scheduled

    # 用户 Tab/焦点穿越主要控件：drain 不得把焦点抢回去
    for control in (frame.input_edit, frame.model_combo, frame.answer_list):
        control.SetFocusFromKbd()
        wx_app.Yield()
        assert control.HasFocus()
        assert frame.answer_list.GetSelection() == 1

    for _ in range(3):
        focused = main.wx.Window.FindFocus()
        assert focused is not None and _inside_frame(frame, focused)
        started = time.perf_counter()
        _send_window_key(focused, main.wx.WXK_TAB)
        wx_app.Yield()
        assert time.perf_counter() - started < 0.5
        focused_after = main.wx.Window.FindFocus()
        assert focused_after is not None and _inside_frame(frame, focused_after)
        assert frame.answer_list.GetSelection() == 1

    _drain_all_kimi_events(frame)
    parts = frame._kimi_turn_answer_parts.get(("chat-kimi", "turn-1")) or []
    assert len(parts) == 500
    assert "".join(parts) == "".join(f"增量{idx} " for idx in range(500))
    assert frame.answer_list.GetSelection() == 1
    focused = main.wx.Window.FindFocus()
    assert focused is not None and _inside_frame(frame, focused)


# D2 — 3 秒阅读器安静窗口内 drain 批量小/被抑制；窗口外一次性较大冲刷
def test_quiet_window_batches_events(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    assert frame.input_edit.HasFocus()

    processed = {"n": 0}
    original_handler = frame._on_kimi_event_for_chat

    def _counting_handler(chat_id, event):
        processed["n"] += 1
        return original_handler(chat_id, event)

    monkeypatch.setattr(frame, "_on_kimi_event_for_chat", _counting_handler)

    for idx in range(40):
        frame._dispatch_kimi_event_to_ui(
            "chat-kimi",
            main.CodexEvent(type="plan_updated", turn_id="turn-1", text=f"计划步骤 {idx}"),
        )

    interactive_batch = max(1, min(main.CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE, main.CODEX_UI_EVENT_BATCH_SIZE))
    full_batch = main.CODEX_UI_EVENT_BATCH_SIZE

    # 安静窗口内 + 导航控件持有焦点：小批量，可见列表不变，条目进入尾部延迟队列
    frame._navigation_quiet_until = time.monotonic() + main.NAVIGATION_QUIET_SECONDS
    rows_before = frame.execution_list.GetCount()
    before = processed["n"]
    frame._drain_kimi_ui_events()
    assert processed["n"] - before == interactive_batch
    assert len(frame._pending_kimi_ui_events) == 40 - interactive_batch
    assert frame.execution_list.GetCount() == rows_before
    assert len(frame._pending_execution_tail_appends.get("chat-kimi") or []) == interactive_batch

    # 安静窗口外 + 焦点离开主要导航控件：批量放大，延迟条目一次性冲刷
    frame._navigation_quiet_until = 0.0
    dummy = main.wx.Panel(frame)
    dummy.Show()
    dummy.SetFocus()
    assert not frame._primary_navigation_control_has_focus()

    before = processed["n"]
    frame._drain_kimi_ui_events()
    assert processed["n"] - before == full_batch
    assert len(frame._pending_kimi_ui_events) == 40 - interactive_batch - full_batch

    before = processed["n"]
    frame._drain_kimi_ui_events()
    last_drain = 40 - interactive_batch - full_batch
    assert processed["n"] - before == last_drain
    assert not frame._pending_kimi_ui_events
    assert not (frame._pending_execution_tail_appends.get("chat-kimi") or [])

    rows = [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]
    assert len(rows) == 40
    assert all(any(f"计划步骤 {idx}" in row for row in rows) for idx in range(40))
    # 最后一次 drain 的事件按序落在尾部
    tail = rows[-last_drain:]
    for offset, row in enumerate(tail):
        assert f"计划步骤 {40 - last_drain + offset}" in row


# D3 — 无可见变化时 drain 路径不调用列表 Refresh/SetSelection
def test_background_events_do_not_repaint_lists(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch)
    archived_turns = [
        {
            "question": "后台问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "kimi/main",
            "created_at": 1.0,
            "request_status": "pending",
            "kimi_session_id": "session-bg",
            "kimi_turn_id": "bg-turn",
        }
    ]
    frame.archived_chats = [
        {
            "id": "chat-bg",
            "title": "background",
            "turns": archived_turns,
            "created_at": 1.0,
            "updated_at": 1.0,
            "kimi_session_id": "session-bg",
            "kimi_turn_id": "bg-turn",
            "kimi_turn_active": True,
            "execution_steps": [],
        }
    ]
    frame._kimi_active_turns["chat-bg"] = {
        "turn_idx": 0,
        "turn_id": "bg-turn",
        "session_id": "session-bg",
        "model": "kimi/main",
    }
    frame._render_answer_list()
    wx_app.Yield()

    repaint_calls = []

    def _spy(name):
        return lambda *args, **kwargs: repaint_calls.append(name)

    for control in (frame.answer_list, frame.execution_list, frame.history_list):
        monkeypatch.setattr(control, "Refresh", _spy(f"{control.GetName() or type(control).__name__}.Refresh"))
        monkeypatch.setattr(control, "SetSelection", _spy(f"{control.GetName() or type(control).__name__}.SetSelection"))

    # 当前聊天的流式增量：只缓冲，不重绘
    _push_delta_events(frame, 60)
    # 非可见后台聊天的事件：更新数据层，不重绘前景列表
    frame._dispatch_kimi_event_to_ui(
        "chat-bg",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="session-bg",
            turn_id="bg-turn",
            text="后台答案",
            display_kind="assistant",
        ),
    )
    frame._dispatch_kimi_event_to_ui(
        "chat-bg",
        main.CodexEvent(type="turn_completed", thread_id="session-bg", turn_id="bg-turn", status="completed"),
    )
    _drain_all_kimi_events(frame)
    wx_app.Yield()

    assert repaint_calls == []
    assert archived_turns[0]["answer_md"] == "后台答案"
    assert archived_turns[0]["request_status"] == "done"
    assert frame.answer_list.GetSelection() == -1 or frame.answer_list.GetStringSelection() != "后台答案"


# D4 — 安静窗口外执行过程条目立即追加到尾部
def test_execution_entries_append_at_tail_outside_quiet_window(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(
        frame,
        monkeypatch,
        detail_panel_mode="execution",
        execution_steps=[
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "既有步骤一", "detail_text": "既有步骤一", "turn_idx": 0},
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "既有步骤二", "detail_text": "既有步骤二", "turn_idx": 0},
        ],
    )
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    wx_app.Yield()
    assert not frame._navigation_quiet_active()

    rows_before = [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]
    assert len(rows_before) == 3
    assert "kimi 问题" in rows_before[0]
    assert "既有步骤一" in rows_before[1]
    assert "既有步骤二" in rows_before[2]

    for text in ("新步骤一", "新步骤二", "新步骤三"):
        frame._dispatch_kimi_event_to_ui(
            "chat-kimi",
            main.CodexEvent(type="plan_updated", turn_id="turn-1", text=text),
        )
    assert _yield_until(wx_app, lambda: not frame._pending_kimi_ui_events, timeout=2.0)

    rows_after = [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]
    assert len(rows_after) == 5
    assert "新步骤一" in rows_after[2]
    assert "新步骤二" in rows_after[3]
    assert "新步骤三" in rows_after[4]
    assert not (frame._pending_execution_tail_appends.get("chat-kimi") or [])
    steps = frame._current_chat_state["execution_steps"]
    tail_texts = [str(step["list_text"]) for step in steps[-3:]]
    assert all(text.endswith(expected) for text, expected in zip(tail_texts, ("新步骤一", "新步骤二", "新步骤三")))


# D5 — 活跃 turn 中关闭 frame：close 在超时内完成且 client.close() 恰好调用一次
def test_frame_close_with_active_kimi_turn_does_not_hang(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame.input_edit.SetValue("长任务")
    frame._on_send_clicked(None)
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="1"))
    assert frame.active_kimi_turn_active is True
    assert frame.is_running is True

    class _CloseEvent:
        skipped = False

        def Skip(self):
            self.skipped = True

    event = _CloseEvent()
    started = time.perf_counter()
    frame._on_close(event)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0
    assert fake.closed == 1
    assert event.skipped is True
