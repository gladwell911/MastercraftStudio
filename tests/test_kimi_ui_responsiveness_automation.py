import ctypes
import json
import sqlite3
import threading
import time

import pytest

import main
from chat_store import ChatStore
from kimi_server_client import KimiEvent, KimiServerClient, event_to_payload
from kimi_test_support import FakeKimiServerClient, _setup_kimi_frame, _sdk_push
from test_codex_ui_responsiveness_automation import _track_ui_timers_for_test


@pytest.fixture(autouse=True)
def _cleanup_kimi_ui_timers(monkeypatch, request):
    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue("frame")
    yield
    cleanup(frame)


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


def test_native_navigation_precedes_sqlite_writer_release(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution", execution_steps=[
        {"event_type": "plan_updated", "display_kind": "plan", "list_text": "first", "detail_text": "first", "turn_idx": 0},
        {"event_type": "plan_updated", "display_kind": "plan", "list_text": "second", "detail_text": "second", "turn_idx": 0},
    ])
    monkeypatch.setattr(frame, "_maybe_trigger_kimi_thinking_sync", lambda *args: None)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    control = frame.execution_list
    control.SetSelection(1)
    control.SetFocusFromKbd()
    wx_app.Yield()
    assert control.HasFocus()
    order = []
    focus_events = []
    selection_events = []

    def record_focus(event):
        focus_events.append(event.GetEventObject())
        event.Skip()

    def record_selection(event):
        selection_events.append(event.GetSelection())
        event.Skip()

    for window in (frame.input_edit, frame.send_button, frame.history_list, frame.model_combo):
        window.Bind(main.wx.EVT_SET_FOCUS, record_focus)
    control.Bind(main.wx.EVT_LISTBOX, record_selection)

    def navigate():
        order.append("heartbeat")
        _send_window_key(control, main.wx.WXK_DOWN)
        order.append(("down", control.GetSelection()))
        _send_window_key(control, main.wx.WXK_UP)
        order.append(("up", control.GetSelection()))
        _send_window_key(control, main.wx.WXK_TAB)
        order.append(("tab", not control.HasFocus()))

    apply_fragment = frame.chat_store.apply_execution_fragment
    write_threads = []

    def observe_writer(*args, **kwargs):
        if not write_threads:
            write_threads.append(threading.get_ident())
            main.wx.CallAfter(navigate)
        return apply_fragment(*args, **kwargs)

    monkeypatch.setattr(frame.chat_store, "apply_execution_fragment", observe_writer)
    lock_ready = threading.Event()
    unlock = threading.Event()

    def hold_write_lock():
        with sqlite3.connect(frame.chat_store.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            lock_ready.set()
            unlock.wait(2.0)
            order.append("unlock")

    locker = threading.Thread(target=hold_write_lock)
    locker.start()
    try:
        assert lock_ready.wait(2.0)
        transport = KimiServerClient()
        for kind, payload in (
            ("turn.step.started", {"step": 1, "stepId": "locked-step"}),
            ("thinking.delta", {"offset": 0, "delta": "contiguous public thinking"}),
        ):
            transport._handle_ws_message({
                "type": kind, "session_id": "session-1", "epoch": "sdk-epoch", "seq": 1,
                "payload": {"type": kind, "agentId": "main", "turnId": "turn-1", **payload},
            })
            for message in transport.drain_pending_messages(100):
                frame._dispatch_kimi_event_to_ui("chat-kimi", main.CodexEvent(**message["payload"]["event"]))
        assert _yield_until(wx_app, lambda: ("tab", True) in order or "heartbeat" in order)
        assert order[:4] == ["heartbeat", ("down", 2), ("up", 1), ("tab", True)]
        assert "unlock" not in order
        assert selection_events == [2, 1]
        assert focus_events
        assert write_threads == [write_threads[0]] and write_threads[0] != threading.get_ident()
    finally:
        unlock.set()
        locker.join(3.0)
    assert _yield_until(wx_app, lambda: any(meta[3] == "contiguous public thinking" for meta in frame.execution_meta))
    assert frame._flush_execution_step_persists_sync()
    reopened = ChatStore(frame.chat_store.db_path)
    assert any(step.get("detail_text") == "contiguous public thinking" for step in reopened.load_execution_steps("chat-kimi"))


def test_long_rest_sync_yields_to_native_navigation_and_repeat_is_quiet(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    initial = [
        {"event_type": "plan_updated", "display_kind": "plan", "list_text": f"previous {index}",
         "detail_text": f"previous {index}", "turn_idx": 0}
        for index in range(340)
    ]
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution", execution_steps=initial)
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    frame.execution_visible_row_limit = 500
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    owner = dict(frame._kimi_active_turns["chat-kimi"], chat_id="chat-kimi", prompt_id="prompt-1", revision=1)
    messages = [{"id": "prompt-1", "role": "user", "content": []}] + [
        {"id": f"answer-{index}", "role": "assistant", "session_id": "session-1",
         "content": [{"type": "thinking", "text": f"public {index}\nfull detail {index}"}]}
        for index in range(100)
    ]

    class TranscriptClient:
        def list_messages(self, session_id, **kwargs):
            assert session_id == "session-1"
            return messages

    monkeypatch.setattr(frame, "_kimi_client", TranscriptClient())
    control = frame.execution_list
    control.SetSelection(1)
    retained_row = frame.execution_list_model.selected_id()
    control.SetFocusFromKbd()
    wx_app.Yield()
    assert control.HasFocus()
    selected = []
    focus = []

    def selection_event(event):
        selected.append(event.GetSelection())
        event.Skip()

    def focus_event(event):
        focus.append(event.GetEventObject())
        event.Skip()

    control.Bind(main.wx.EVT_LISTBOX, selection_event)
    for window in (frame.input_edit, frame.send_button, frame.history_list, frame.model_combo):
        window.Bind(main.wx.EVT_SET_FOCUS, focus_event)
    observations = []
    scheduled = []
    call_later = frame._call_later_if_alive

    def navigate():
        observations.append(len(frame._current_chat_state["execution_steps"]))
        _send_window_key(control, main.wx.WXK_DOWN)
        _send_window_key(control, main.wx.WXK_UP)
        _send_window_key(control, main.wx.WXK_TAB)
        observations.append(main.wx.Window.FindFocus())

    def watch_batches(delay, callback, *args, **kwargs):
        if callback == frame._continue_kimi_thinking_sync:
            assert wx_app.IsMainLoopRunning()
            scheduled.append(True)
            if len(scheduled) == 1:
                main.wx.CallAfter(navigate)
        return call_later(delay, callback, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_later_if_alive", watch_batches)
    worker = threading.Thread(target=frame._kimi_thinking_fetch_worker, args=(owner,))
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive()
    completed = []

    def check_done():
        if (len(frame._current_chat_state["execution_steps"]) == len(initial) + 100
                and not frame._kimi_thinking_sync_counts and not frame._kimi_execution_write_counts):
            completed.append(True)
            wx_app.ExitMainLoop()
        else:
            main.wx.CallLater(10, check_done)

    main.wx.CallAfter(check_done)
    timeout = main.wx.CallLater(8000, wx_app.ExitMainLoop)
    try:
        wx_app.MainLoop()
    finally:
        timeout.Stop()
    assert completed and len(scheduled) > 1
    assert observations[0] < len(initial) + 100
    assert selected[:2] == [2, 1]
    assert focus and observations[1] is not control
    assert main.wx.Window.FindFocus() is observations[1]
    assert frame.execution_list_model.selected_id() == retained_row
    assert frame._flush_execution_step_persists_sync()
    reopened = ChatStore(frame.chat_store.db_path)
    assert [step["detail_text"] for step in reopened.load_execution_steps("chat-kimi")] == [
        f"public {index}\nfull detail {index}" for index in range(100)
    ]

    repaint = []
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_args: repaint.append(True))
    done = []
    updates = frame._iter_kimi_thinking_updates("chat-kimi", owner, frame._prepare_kimi_thinking_rows(messages))

    def repeat():
        yield from updates
        done.append(True)

    frame._continue_kimi_thinking_sync(repeat())
    assert _yield_until(wx_app, lambda: bool(done))
    assert not repaint
    assert main.wx.Window.FindFocus() is observations[1]
    assert frame.execution_list_model.selected_id() == retained_row


def test_fragment_retry_settles_before_terminal_and_close(frame, wx_app, monkeypatch, caplog):
    _activate_frame(frame, wx_app)
    save_state = frame._save_state
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    monkeypatch.setattr(frame, "_save_state", save_state)
    monkeypatch.setattr(frame, "_maybe_trigger_kimi_thinking_sync", lambda *args: None)
    entered = threading.Event()
    release = threading.Event()
    attempts = []
    real_write = frame.chat_store.apply_execution_fragment

    def blocked_write(*args, **kwargs):
        attempts.append(threading.get_ident())
        if len(attempts) < 5:
            raise sqlite3.OperationalError("test transient failure")
        if len(attempts) == 5:
            entered.set()
            assert release.wait(3.0)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(frame.chat_store, "apply_execution_fragment", blocked_write)
    closes = []
    monkeypatch.setattr(frame, "_stop_remote_servers", lambda: closes.append(True))

    class CloseEvent:
        def __init__(self):
            self.vetoed = False
            self.skipped = False

        def Veto(self):
            self.vetoed = True

        def Skip(self):
            self.skipped = True

    transport = KimiServerClient()
    _sdk_push(frame, transport, "session-1", "turn.step.started", 1, turnId="turn-1", step=1, stepId="retry-step")
    try:
        _sdk_push(frame, transport, "session-1", "thinking.delta", 1, turnId="turn-1", offset=0, delta="retry once")
        assert entered.wait(2.0)
        frame._on_kimi_event_for_chat("chat-kimi", main.CodexEvent(
            type="turn_completed", thread_id="session-1", turn_id="turn-1", status="completed", text="final answer",
        ))
        first, second = CloseEvent(), CloseEvent()
        frame._on_close(first)
        frame._on_close(second)
        assert first.vetoed and second.vetoed and not frame.IsShown()
        assert not getattr(frame, "_close_complete", False)
        assert frame.active_session_turns[0]["request_status"] == "pending"
    finally:
        release.set()

    assert _yield_until(wx_app, lambda: not frame._kimi_execution_write_counts and not frame._kimi_execution_boundaries)
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False), timeout=5)
    assert closes == [True]
    assert len(attempts) == 5 and len(set(attempts)) == 1 and attempts[0] != threading.get_ident()
    assert "Retrying failed Kimi execution write" in caplog.text
    reopened = ChatStore(frame.chat_store.db_path)
    assert [step["detail_text"] for step in reopened.load_execution_steps("chat-kimi")] == ["retry once", "final answer"]
    assert reopened.load_chat("chat-kimi")["turns"][0]["answer_md"] == "final answer"


@pytest.mark.parametrize("action", ["clear", "close"])
def test_clear_between_rest_batches_cannot_restore_retired_projection(frame, wx_app, monkeypatch, action):
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    chat_id = "chat-kimi"
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    frame.chat_store.upsert_chat(frame._current_chat_state)
    revision = frame.chat_store.allocate_chat_revision(chat_id)
    frame._current_chat_state["revision"] = revision
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=revision)
    messages = [{"id": "prompt-1", "role": "user", "content": []}] + [
        {"id": f"row-{index}", "role": "assistant", "content": [{"type": "thinking", "text": f"retired {index}"}]}
        for index in range(64)
    ]
    blocked = []
    call_after = frame._call_after_if_alive

    def pause_batch(callback, *args, **kwargs):
        if callback == frame._continue_kimi_thinking_sync and not blocked:
            blocked.append((callback, args, kwargs))
            return True
        return call_after(callback, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_after_if_alive", pause_batch)
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    assert _yield_until(wx_app, lambda: bool(blocked))
    assert 0 < len(frame._current_chat_state["execution_steps"]) < len(messages) - 1
    assert frame._flush_execution_step_persists_sync()
    callback, args, kwargs = blocked[0]
    if action == "close":
        class Close:
            vetoed = skipped = False

            def Veto(self):
                self.vetoed = True

            def Skip(self):
                self.skipped = True

        first = Close()
        frame._on_close(first)
        assert first.vetoed and not frame.IsShown() and frame._kimi_thinking_sync_counts
        callback(*args, **kwargs)
        assert _yield_until(wx_app, lambda: not frame._kimi_thinking_sync_counts and not frame._kimi_execution_write_counts)
        assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False), timeout=5)
        stored = ChatStore(frame.chat_store.db_path).load_execution_steps(chat_id)
        assert len(stored) == 64 and stored[-1]["detail_text"] == "retired 63"
        return
    operation = frame.chat_store.begin_clear_operation(chat_id, idempotency_key="rest-clear")
    frame._clear_context_chat_state(frame._current_chat_state, chat_id, time.time())
    frame._current_chat_state["revision"] = operation["revision"]
    callback(*args, **kwargs)
    assert _yield_until(wx_app, lambda: not frame._kimi_thinking_sync_counts)
    assert not frame._current_chat_state["execution_steps"]
    assert frame._flush_execution_step_persists_sync()
    assert not ChatStore(frame.chat_store.db_path).load_execution_steps(chat_id)


def test_rest_withdrawal_invalidates_visible_and_frozen_details(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    chat_id = "chat-kimi"
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    messages = [{"id": "prompt-1", "role": "user", "content": []}, {
        "id": "rest-1", "role": "assistant", "content": [{"type": "thinking", "text": "withdrawn detail"}],
    }]
    old = frame._prepare_kimi_thinking_rows(messages)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert frame._flush_execution_step_persists_sync()
    projected = frame.chat_store.load_execution_steps(chat_id)[0]
    frame.chat_store.commit_durable_fact(pair_id="test", domain="events", execution=True, envelope={
        "event_id": "public-execution", "chat_id": chat_id, "kind": "execution_entry",
        "body": {"canonical_item_id": projected["canonical_item_id"], "detail": projected["detail_text"]},
    })
    source = frame.chat_store.read_execution_projection_source(chat_id)
    snapshot = frame.chat_store.create_execution_projection_snapshot(
        chat_id=chat_id, revision=source["revision"], source_hash=source["source_hash"],
        rows=[{"row_id": "old", "detail_text": "withdrawn detail"}],
    )
    index = next(i for i, meta in enumerate(frame.execution_meta) if meta[3] == "withdrawn detail")
    frame.execution_list.SetSelection(index)
    frame._navigation_quiet_until = time.monotonic() + main.NAVIGATION_QUIET_SECONDS
    messages[1]["content"][0]["hidden"] = True
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert "withdrawn detail" not in str(frame.execution_meta)
    assert not any(frame._iter_kimi_thinking_updates(chat_id, owner, old))
    assert frame._flush_execution_step_persists_sync()
    reopened = ChatStore(frame.chat_store.db_path)
    assert "withdrawn detail" not in str(reopened.load_execution_steps(chat_id))
    assert not reopened.load_execution_page(pair_id="test", domain="events", chat_id=chat_id, secret="test")["entries"]
    with pytest.raises(ValueError, match="SNAPSHOT_REQUIRED"):
        reopened.load_execution_range(
            pair_id="test", domain="events", chat_id=chat_id, revision=source["revision"], start_sequence=1, end_sequence=1,
        )
    with pytest.raises(ValueError):
        reopened.load_execution_projection_page(
            pair_id="test", domain="events", chat_id=chat_id, secret="test", snapshot_id=snapshot,
        )
    with reopened._connect() as connection:
        assert not connection.execute("SELECT row_json FROM execution_projection_rows WHERE snapshot_id=?", (snapshot,)).fetchall()
        assert "withdrawn detail" not in str([row[0] for row in connection.execute("SELECT state_json FROM execution_assemblers")])

    # A newly opened visible tail contains no private rows. A stale REST
    # delivery must still discover the withdrawal from the reopened store.
    frame._current_chat_state["execution_steps"] = []
    frame._kimi_thinking_synced.clear()
    frame._kimi_rest_withdrawals.clear()
    monkeypatch.setattr(frame, "chat_store", reopened)
    messages[1]["content"][0].pop("hidden")
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    wx_app.Yield()
    assert _yield_until(wx_app, lambda: not frame._kimi_thinking_sync_counts)
    assert not frame._current_chat_state["execution_steps"]
    assert "withdrawn detail" not in str(reopened.load_execution_steps(chat_id))


def test_rest_withdraws_pruned_native_row_and_missing_sibling_after_reopen(frame, wx_app, monkeypatch):
    chat_id = "chat-kimi"
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    frame.chat_store.upsert_chat(frame._current_chat_state)
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    native = {"display_kind": "thinking", "provider": "kimi", "turn_idx": 0, "turn_id": "turn-1",
              "session_id": "session-1", "thread_id": "session-1", "agent_id": "main", "item_id": "native-stream",
              "kimi_step_id": "native", "kimi_step_number": 1, "detail_text": "PRIVATE native", "list_text": "PRIVATE native"}
    assert frame._append_execution_entry_to_chat(chat_id, native, save_state=False)
    assert frame._flush_execution_step_persists_sync()
    native = frame.chat_store.load_execution_steps(chat_id)[0]
    frame._current_chat_state["execution_steps"].extend({"turn_idx": 0} for _ in range(500))
    frame._prune_cached_execution_steps_for_turn(frame._current_chat_state, {"turn_idx": 0})
    assert all(step.get("item_id") != "native-stream" for step in frame._current_chat_state["execution_steps"])
    fact = frame.chat_store.commit_durable_fact(pair_id="test", domain="events", execution=True, envelope={
        "event_id": "native-fact", "chat_id": chat_id, "kind": "execution_entry",
        "body": {"kind": "thinking", "canonical_item_id": native["canonical_item_id"], "detail": "PRIVATE native"},
    })
    prompt = {"id": "prompt-1", "role": "user", "content": []}
    first = {"id": "native-rest", "role": "assistant", "hidden": True, "content": []}
    sibling = {"id": "sibling", "role": "assistant", "content": [{"type": "thinking", "text": "public sibling"}]}

    def deliver(messages):
        frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
        wx_app.Yield()
        assert _yield_until(wx_app, lambda: not frame._kimi_thinking_sync_counts and not frame._kimi_execution_write_counts)

    deliver([prompt, first, sibling])
    sibling["content"].append({"type": "thinking", "text": "PRIVATE sibling", "hidden": True})
    deliver([prompt, first, sibling])
    reopened = ChatStore(frame.chat_store.db_path)
    stored = reopened.load_execution_steps(chat_id)
    assert "PRIVATE" not in str(stored)
    assert any(step.get("item_id") == "native-stream" and step.get("private_reasoning") for step in stored)
    assert any(step.get("rest_message_id") == "sibling" and step.get("kimi_rest_block_index") == 1
               and step.get("private_reasoning") for step in stored)
    assert not reopened.load_execution_page(chat_id=chat_id, pair_id="test", domain="events", secret="secret")["entries"]
    with pytest.raises(ValueError, match="SNAPSHOT_REQUIRED"):
        reopened.replay_after(pair_id="test", domain="events", sync_sequence=fact["sync_sequence"] - 1)
    monkeypatch.setattr(frame, "chat_store", reopened)
    frame._current_chat_state["execution_steps"] = []
    frame._kimi_thinking_synced.clear()
    frame._kimi_rest_withdrawals.clear()
    sibling["content"][1].pop("hidden")
    deliver([prompt, first, sibling])
    assert "PRIVATE" not in str(reopened.load_execution_steps(chat_id))


def test_rest_single_large_message_keeps_cap_and_coalesces_inflight_snapshot(frame, wx_app, monkeypatch):
    chat_id = "chat-kimi"
    steps = [{"turn_idx": 0, "detail_text": f"prior {index}"} for index in range(500)]
    _setup_active_kimi_chat(frame, monkeypatch, execution_steps=steps)
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    frame._chat_store_enabled = False
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    messages = [{"id": "prompt-1", "role": "user", "content": []}, {"id": "large", "role": "assistant",
        "content": [{"type": "thinking", "text": f"block {index}"} for index in range(600)]}]
    pending = []
    count = [0]
    iterations = []
    original = frame._call_after_if_alive
    append = frame._append_execution_entry_to_chat

    def track(callback, *args, **kwargs):
        if callback == frame._continue_kimi_thinking_sync:
            pending.append((callback, args, kwargs))
            return True
        return original(callback, *args, **kwargs)

    def append_row(*args, **kwargs):
        count[0] += 1
        return append(*args, **kwargs)

    monkeypatch.setattr(frame, "_call_after_if_alive", track)
    monkeypatch.setattr(frame, "_append_execution_entry_to_chat", append_row)
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    assert _yield_until(wx_app, lambda: bool(pending))
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    wx_app.Yield()
    assert frame._kimi_thinking_sync_counts == {chat_id: 1} and len(pending) == 1
    while pending and len(iterations) < 100:
        callback, args, kwargs = pending.pop(0)
        before = count[0]
        callback(*args, **kwargs)
        iterations.append(count[0] - before)
    assert frame._kimi_thinking_sync_counts == {} and not pending
    assert max(iterations) <= 16 and count[0] == 600
    assert len(frame._current_chat_state["execution_steps"]) == 500
    assert frame._current_chat_state["execution_steps"][-1]["detail_text"] == "block 599"

    # A privacy change in the same transcript must get its own job while an
    # identical repeat is already running.
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    assert _yield_until(wx_app, lambda: bool(pending))
    messages[1]["content"][-1]["hidden"] = True
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    wx_app.Yield()
    assert frame._kimi_thinking_sync_counts == {chat_id: 2}
    for _ in range(120):
        if not pending:
            break
        callback, args, kwargs = pending.pop(0)
        callback(*args, **kwargs)
    assert not pending and not frame._kimi_thinking_sync_counts
    assert all(step.get("detail_text") != "block 599" for step in frame._current_chat_state["execution_steps"])


def test_rest_withdrawal_groups_visible_changes_in_a_batch(frame, wx_app, monkeypatch):
    chat_id = "chat-kimi"
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    messages = [{"id": "prompt-1", "role": "user"}, {"id": "response", "role": "assistant", "content": [
        {"type": "thinking", "text": "first public"}, {"type": "thinking", "text": "second public"},
    ]}]
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert frame._flush_execution_step_persists_sync()
    removed = []
    remove = frame._remove_visible_kimi_withdrawals

    def track(chat, rows):
        removed.append(set(rows))
        return remove(chat, rows)

    monkeypatch.setattr(frame, "_remove_visible_kimi_withdrawals", track)
    for block in messages[1]["content"]:
        block["hidden"] = True
    frame._schedule_kimi_thinking_sync(chat_id, owner, messages)
    wx_app.Yield()
    assert _yield_until(wx_app, lambda: not frame._kimi_thinking_sync_counts and not frame._kimi_execution_write_counts)
    assert len(removed) == 1 and len(removed[0]) == 2


def test_rest_withdrawal_between_blocks_stops_older_public_snapshot(frame, monkeypatch):
    chat_id = "chat-kimi"
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    prompt = {"id": "prompt-1", "role": "user"}
    public = {"id": "response", "role": "assistant", "content": [
        {"type": "thinking", "text": f"withdrawn block {index}"} for index in range(32)
    ]}

    updates = frame._iter_kimi_thinking_updates(chat_id, owner, frame._prepare_kimi_thinking_rows([prompt, public]))
    assert any(updates)
    assert [step["detail_text"] for step in frame._current_chat_state["execution_steps"]] == ["withdrawn block 0"]
    assert frame._kimi_rest_withdrawals == {}

    assert frame._kimi_sync_thinking_rows(chat_id, owner, [prompt, dict(public, hidden=True)])
    assert not any(updates)
    assert frame._flush_execution_step_persists_sync()
    reopened = ChatStore(frame.chat_store.db_path)
    assert "withdrawn block" not in str(frame._current_chat_state["execution_steps"])
    assert "withdrawn block" not in str(reopened.load_execution_steps(chat_id))


def test_withdrawal_keeps_other_prompt_buffer_with_reused_item(frame, monkeypatch):
    _setup_active_kimi_chat(frame, monkeypatch)
    first = ("chat-kimi", "session-1", "prompt-A", "main")
    second = ("chat-kimi", "session-1", "prompt-B", "main")
    frame._kimi_turn_answer_segments.update({(*first, "same"): {0: "withdraw me"}, (*second, "same"): {0: "keep me"}})
    frame._kimi_turn_answer_parts.update({first: ["withdraw me"], second: ["keep me"]})
    frame._redact_kimi_narrative("chat-kimi", {"item_id": "same", "session_id": "session-1", "turn_id": "turn-1"},
                                owner_key="prompt-A", store_only=True)
    assert frame._kimi_turn_answer_segments[(*first, "same")] == {}
    assert frame._kimi_turn_answer_parts[first] == []
    assert frame._kimi_turn_answer_segments[(*second, "same")] == {0: "keep me"}
    assert frame._kimi_turn_answer_parts[second] == ["keep me"]


def test_rejected_rest_projection_allows_identical_redelivery(frame, monkeypatch):
    chat_id = "chat-kimi"
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0]["kimi_prompt_id"] = "prompt-1"
    owner = dict(frame._kimi_active_turns[chat_id], chat_id=chat_id, prompt_id="prompt-1", revision=1)
    messages = [{"id": "prompt-1", "role": "user"}, {"id": "response", "role": "assistant",
                 "content": [{"type": "thinking", "text": "retry REST publication"}]}]
    upsert = frame.chat_store.upsert_execution_step

    def reject(*args, **kwargs):
        raise ValueError("test rejection")

    monkeypatch.setattr(frame.chat_store, "upsert_execution_step", reject)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert frame._flush_execution_step_persists_sync()
    assert not frame.chat_store.load_execution_steps(chat_id)
    assert not any(frame._kimi_thinking_synced.values())

    monkeypatch.setattr(frame.chat_store, "upsert_execution_step", upsert)
    frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert frame._flush_execution_step_persists_sync()
    assert not frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    reopened = ChatStore(frame.chat_store.db_path)
    assert [step["detail_text"] for step in reopened.load_execution_steps(chat_id)] == ["retry REST publication"]


def test_sdk_public_thinking_updates_execution_list_and_detail_without_moving_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution")
    monkeypatch.setattr(frame, "_maybe_trigger_kimi_thinking_sync", lambda *args: None)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    transport = KimiServerClient()
    _sdk_push(frame, transport, "session-1", "turn.step.started", 1, chat_id="chat-kimi", turnId="turn-1", step=1, stepId="native-step")
    _sdk_push(frame, transport, "session-1", "thinking.delta", 1, chat_id="chat-kimi", turnId="turn-1", offset=0, delta="先检查输入。\n完整公开细节。")
    assert _yield_until(wx_app, lambda: any(meta[0] == "execution" and meta[2] == "先检查输入。" for meta in frame.execution_meta))
    assert frame.input_edit.HasFocus()
    selected_id = frame.execution_list_model.selected_id()
    _sdk_push(frame, transport, "session-1", "thinking.delta", 1, chat_id="chat-kimi", turnId="turn-1", offset=len("先检查输入。\n完整公开细节。"), delta="\n然后核对结果。")
    assert _yield_until(wx_app, lambda: any(meta[0] == "execution" and meta[3] == "先检查输入。\n完整公开细节。\n然后核对结果。" for meta in frame.execution_meta))
    assert frame.input_edit.HasFocus()
    assert frame.execution_list_model.selected_id() == selected_id
    counts = frame.execution_list.GetCount()
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda _control: pytest.fail("replay must not repaint"))
    _sdk_push(frame, transport, "session-1", "thinking.delta", 1, chat_id="chat-kimi", turnId="turn-1", offset=0, delta="先检查输入。\n完整公开细节。")
    wx_app.Yield()
    assert frame.execution_list.GetCount() == counts
    assert frame.input_edit.HasFocus()
    # Exercise the real caller and dialog construction, using the existing
    # modal harness so this isolated test never waits for desktop interaction.
    opened = []
    def show_modal(dialog):
        dialog.Show()
        wx_app.Yield()
        opened.append((dialog, dialog.canonical_text, dialog.text_ctrl.GetValue()))
        return main.wx.ID_CLOSE
    monkeypatch.setattr(main.AnswerTextViewerDialog, "ShowModal", show_modal)
    row = next(index for index, meta in enumerate(frame.execution_meta)
               if meta[0] == "execution" and meta[2] == "先检查输入。")
    frame.execution_list.SetSelection(row)
    assert frame._open_selected_execution_text_viewer()
    full_text = "先检查输入。\n完整公开细节。\n然后核对结果。"
    assert [(canonical, text) for _dialog, canonical, text in opened] == [(full_text, "\n" + full_text)]
    wx_app.Yield()
    assert not opened[0][0]
    assert not frame._answer_viewer_open


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
    parts = frame._kimi_turn_answer_parts.get(("chat-kimi", "session-1", "turn-1", "main")) or []
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
    content_rows_before = sum(1 for meta in frame.execution_meta if meta[0] != "time")
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
    content_rows = [row for row, meta in zip(rows, frame.execution_meta) if meta[0] != "time"]
    assert len(content_rows) - content_rows_before == 40
    assert all(any(f"计划步骤 {idx}" in row for row in rows) for idx in range(40))
    # 最后一次 drain 的事件按序落在尾部
    tail = content_rows[-last_drain:]
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
    frame.input_edit.SetFocus()
    wx_app.Yield()
    focused_before = main.wx.Window.FindFocus()
    assert focused_before is frame.input_edit

    repaint_calls = []

    def _spy(name):
        return lambda *args, **kwargs: repaint_calls.append(name)

    for control in (frame.answer_list, frame.execution_list, frame.history_list):
        monkeypatch.setattr(control, "Refresh", _spy(f"{'history' if control is frame.history_list else 'detail'}.Refresh"))
        monkeypatch.setattr(control, "SetSelection", _spy(f"{'history' if control is frame.history_list else 'detail'}.SetSelection"))

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

    # Successful background completion is a history activity fact; only its
    # history row may repaint, with foreground detail/input untouched.
    assert all("history" in label for label in repaint_calls)
    assert main.wx.Window.FindFocus() is focused_before
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

    rows_before = [
        row for row, meta in zip(frame.execution_list.GetStrings(), frame.execution_meta)
        if meta[0] != "time"
    ]
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

    rows_after = [
        row for row, meta in zip(frame.execution_list.GetStrings(), frame.execution_meta)
        if meta[0] != "time"
    ]
    assert len(rows_after) == 6
    assert "新步骤一" in rows_after[3]
    assert "新步骤二" in rows_after[4]
    assert "新步骤三" in rows_after[5]
    assert not (frame._pending_execution_tail_appends.get("chat-kimi") or [])
    steps = frame._current_chat_state["execution_steps"]
    tail_texts = [str(step["list_text"]) for step in steps[-3:]]
    assert all(text.endswith(expected) for text, expected in zip(tail_texts, ("新步骤一", "新步骤二", "新步骤三")))


def test_kimi_status_batch_preserves_focus_selection_and_skips_noop_repaint(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(
        frame,
        monkeypatch,
        detail_panel_mode="execution",
        execution_steps=[
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "已有步骤", "detail_text": "已有步骤", "turn_idx": 0},
        ],
    )
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetSelection(1)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    repaint_count = {"n": 0}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda _control: repaint_count.__setitem__("n", repaint_count["n"] + 1))

    frame._dispatch_kimi_event_to_ui("chat-kimi", main.CodexEvent(type="thread_status_changed", thread_id="session-1", turn_id="turn-1", status="streaming"))
    _drain_all_kimi_events(frame)
    assert repaint_count["n"] == 0
    assert frame.input_edit.HasFocus()
    assert frame.execution_list.GetSelection() == 1

    source = {"adapter": "kimi_server", "source_kind": "thinking.delta", "offset": 0}
    for event in (
            main.CodexEvent(type="agent_message_delta", provider="kimi", event_id="thinking-1", fragment_id="f0", offset=0, thread_id="session-1", turn_id="turn-1", text="The", raw_text="The", display_kind="thinking", data=source),
        main.CodexEvent(type="thread_status_changed", thread_id="session-1", turn_id="turn-1", status="streaming"),
            main.CodexEvent(type="agent_message_delta", provider="kimi", event_id="thinking-1", fragment_id="f3", offset=3, thread_id="session-1", turn_id="turn-1", text=" user", raw_text=" user", display_kind="thinking", data={**source, "offset": 3}),
        main.CodexEvent(type="agent_message_delta", thread_id="session-1", turn_id="turn-1", text="答案", raw_text="答案", display_kind="assistant", data={"source_kind": "assistant.delta", "offset": 0}),
    ):
        frame._dispatch_kimi_event_to_ui("chat-kimi", event)
    _drain_all_kimi_events(frame)

    assert _yield_until(wx_app, lambda: not frame._kimi_execution_write_counts)
    rows = [frame.execution_list.GetString(idx) for idx in range(frame.execution_list.GetCount())]
    assert rows.count("The user") == 1
    assert frame.input_edit.HasFocus()
    assert frame.execution_list.GetSelection() == 1
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    frame.execution_list.SetSelection(rows.index("The user"))

    class _CopyEvent:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def StopPropagation(self):
            pass

        def Skip(self):
            raise AssertionError("Ctrl+C should be handled")

    frame._on_execution_key_down(_CopyEvent())
    assert copied == ["The user"]


def test_background_structured_kimi_batch_persists_owner_without_foreground_repaint(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _setup_active_kimi_chat(frame, monkeypatch, detail_panel_mode="execution",
        execution_steps=[{"event_type": "plan_updated", "display_kind": "plan", "list_text": "前台", "detail_text": "前台", "turn_idx": 0}])
    frame.archived_chats = [{
        "id": "chat-bg", "title": "bg", "turns": [{"question": "bg", "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main", "request_status": "pending", "kimi_session_id": "session-bg", "kimi_turn_id": "turn-bg"}],
        "kimi_session_id": "session-bg", "execution_steps": [],
    }]
    frame._kimi_active_turns["chat-bg"] = {"turn_idx": 0, "turn_id": "turn-bg", "session_id": "session-bg", "model": "kimi/main"}
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetSelection(1)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    repaint = []
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_args: repaint.append(True))
    event = KimiEvent(type="item_started", thread_id="session-bg", turn_id="turn-bg", item_id="bg-tool",
        title="C:/private/file.txt", display_kind="file",
        data={"source_kind": "tool.call.started", "operation_kind": "file"})
    frame._dispatch_kimi_event_to_ui("chat-bg", main.CodexEvent(**event_to_payload(event)))
    _drain_all_kimi_events(frame)
    bg_steps = frame.archived_chats[0]["execution_steps"]
    assert bg_steps == []  # tool events never become F1 list rows
    transport = KimiServerClient()
    _sdk_push(frame, transport, "session-bg", "turn.step.started", 1, chat_id="chat-bg", turnId="turn-bg", step=1, stepId="bg-step")
    _sdk_push(frame, transport, "session-bg", "thinking.delta", 1, chat_id="chat-bg", turnId="turn-bg", offset=0, delta="后台真实思考。")
    assert _yield_until(wx_app, lambda: not frame._kimi_execution_write_counts)
    assert [step["detail_text"] for step in frame.archived_chats[0]["execution_steps"]] == ["后台真实思考。"]
    assert repaint == []
    assert frame.execution_list.GetSelection() == 1
    assert frame.input_edit.HasFocus()


# D5 — 活跃 turn 中关闭 frame：close 在超时内完成且 client.close() 恰好调用一次
def test_frame_close_with_active_kimi_turn_does_not_hang(frame, wx_app, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame.input_edit.SetValue("长任务")
    frame._on_send_clicked(None)
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="1"))
    assert frame.active_kimi_turn_active is True
    assert frame.is_running is True

    class _CloseEvent:
        vetoed = False

        def Veto(self):
            self.vetoed = True

    event = _CloseEvent()
    started = time.perf_counter()
    frame._on_close(event)
    elapsed = time.perf_counter() - started

    assert elapsed < .5 and not frame.IsShown() and event.vetoed
    assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False))
    assert fake.closed == 1


@pytest.mark.parametrize("phase", ["producer", "database"])
def test_close_hides_in_first_dispatch_and_pumps_until_accepted_writes_finish(frame, wx_app, monkeypatch, phase, caplog):
    _setup_active_kimi_chat(frame, monkeypatch)
    monkeypatch.setattr(frame, "_stop_remote_servers", lambda: None)
    monkeypatch.setattr(frame.file_service, "stop", lambda: None)
    frame._global_ctrl_hook.stop()
    frame.Show()
    frame.active_session_turns[0].update(answer_md="accepted before close", request_status="done")
    frame._mark_chat_turns_dirty()
    database = frame.chat_store.db_path
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(4)

    if phase == "producer":
        monkeypatch.setattr(frame.notes_sync, "close", block)
    else:
        write = frame.chat_store.upsert_chat

        def blocked_write(chat):
            block()
            return write(chat)

        monkeypatch.setattr(frame.chat_store, "upsert_chat", blocked_write)
    ticked = []
    with caplog.at_level("INFO"):
        try:
            frame.Close()
            assert not frame.IsShown()
            assert _yield_until(wx_app, entered.is_set, timeout=5)
            main.wx.CallAfter(ticked.append, True)
            assert _yield_until(wx_app, lambda: bool(ticked))
            assert not getattr(frame, "_close_complete", False)
        finally:
            release.set()
        assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False), timeout=5)
    assert "MC close timing: hide" in caplog.text
    print(next(record.message for record in caplog.records if record.message.startswith("MC close timing")))
    assert ChatStore(database).load_chat("chat-kimi")["turns"][0]["answer_md"] == "accepted before close"


def test_close_drains_remote_final_and_fences_producer_after_optional_budget(frame, wx_app, monkeypatch):
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0].update(answer_md="canonical close answer", request_status="done")
    frame._mark_chat_turns_dirty()
    frame._queue_remote_final("chat-kimi", 0)
    database = frame.chat_store.db_path
    release_producer, release_storage, writing, producer_finished = (threading.Event() for _ in range(4))
    late = []
    write = frame._write_close_batch

    def slow_producer():
        assert release_producer.wait(6)
        late.append(frame._call_after_if_alive(frame._on_delta, 0, "late answer", "chat-kimi"))
        producer_finished.set()

    def gated_write(batch):
        writing.set()
        assert release_storage.wait(3)
        write(batch)

    monkeypatch.setattr(frame, "_stop_remote_servers", slow_producer)
    monkeypatch.setattr(frame, "_write_close_batch", gated_write)
    frame.Close()
    try:
        assert _yield_until(wx_app, writing.is_set, timeout=5)
        frame.active_session_turns[0]["answer_md"] = "mutation after capture"
        release_producer.set()
        assert producer_finished.wait(1)
        assert late == [False]
    finally:
        release_producer.set()
        release_storage.set()
    assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False))
    reopened = ChatStore(database)
    assert reopened.load_turns("chat-kimi")[0]["answer_md"] == "canonical close answer"
    kinds = [json.loads(bytes(row["payload"]).decode())["kind"]
             for row in reopened.pending_outbox(pair_id=frame._close_pair_id, domain="events")]
    assert kinds.count("assistant_final") == 1


def test_close_persists_chat_while_notes_http_is_still_pending(frame, monkeypatch, wait_for_wx):
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0].update(answer_md="accepted chat answer", request_status="done")
    frame._mark_chat_turns_dirty()
    entered, release = threading.Event(), threading.Event()

    class SlowNotesClient:
        def write_documents(self, docs):
            return []

        def fetch_changes(self, checkpoint):
            entered.set()
            assert release.wait(6)
            return {"results": [], "last_seq": "late-sequence"}

        def close(self):
            pass

    notes = frame.notes_sync
    checkpoint = notes.get_checkpoint()
    notes.configure("", "", client=SlowNotesClient())
    worker = threading.Thread(target=notes.sync_once, daemon=True)
    worker.start()
    database = frame.chat_store.db_path
    try:
        assert entered.wait(2)
        frame.Close()
        wait_for_wx(lambda: getattr(frame, "_close_complete", False), "close while HTTP remains blocked")
        assert worker.is_alive()
        assert ChatStore(database).load_turns("chat-kimi")[0]["answer_md"] == "accepted chat answer"
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert notes.get_checkpoint() == checkpoint


@pytest.mark.parametrize("failure, attempts", [(OSError, 3), (ValueError, 1)])
def test_close_force_persists_after_accessible_storage_retry(frame, wx_app, monkeypatch, failure, attempts):
    _setup_active_kimi_chat(frame, monkeypatch)
    frame.active_session_turns[0].update(answer_md="retained answer", request_status="done")
    frame._mark_chat_turns_dirty()
    database = frame.chat_store.db_path
    write = frame._write_close_batch
    tries, spoken = [], []

    def store_error(batch):
        tries.append(batch)
        raise failure("controlled storage failure")

    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", spoken.append)
    monkeypatch.setattr(frame, "_write_close_batch", store_error)
    frame.Close(force=True)
    assert _yield_until(wx_app, lambda: bool(spoken))
    assert len(tries) == attempts
    assert frame.IsShown() and "Alt+F4" in frame.GetTitle()
    assert not getattr(frame, "_close_complete", False)
    monkeypatch.setattr(frame, "_write_close_batch", write)
    frame.Close(force=True)
    assert _yield_until(wx_app, lambda: getattr(frame, "_close_complete", False))
    assert ChatStore(database).load_turns("chat-kimi")[0]["answer_md"] == "retained answer"


def test_kimi_candidate_new_chat_reopen_codex_and_dispatch_by_owner(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = frame.current_chat_id = "chat-codex-a"
    frame.active_session_turns = [
        {"question": "codex question", "answer_md": "codex answer", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}
    ]
    frame._current_chat_state = {
        "id": "chat-codex-a",
        "title": "Codex A",
        "model": main.DEFAULT_CODEX_MODEL,
        "turns": frame.active_session_turns,
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    dispatches = []
    monkeypatch.setattr(
        frame, "_start_codex_worker_for_turn",
        lambda chat_id, turn_idx, question, model: dispatches.append(("codex", chat_id, turn_idx, question, model)),
    )
    monkeypatch.setattr(
        frame, "_start_kimi_worker_for_turn",
        lambda chat_id, turn_idx, question, model: dispatches.append(("kimi", chat_id, turn_idx, question, model)),
    )

    frame.model_combo.SetValue(main.model_display_name("kimi/main"))
    frame._on_model_changed(None)
    assert frame._current_chat_state["model"] == main.DEFAULT_CODEX_MODEL
    frame._on_new_chat_clicked(None)
    kimi_b = frame.active_chat_id
    wx_app.Yield()

    assert kimi_b != "chat-codex-a"
    assert frame._current_chat_state["model"] == "kimi/main"
    assert frame.model_combo.GetValue() == main.model_display_name("kimi/main")
    assert frame.input_edit.HasFocus()

    assert frame._switch_current_chat("chat-codex-a") is True
    wx_app.Yield()
    assert frame.selected_model == main.DEFAULT_CODEX_MODEL
    assert frame.model_combo.GetValue() == main.model_display_name(main.DEFAULT_CODEX_MODEL)
    assert frame.input_edit.HasFocus()
    assert frame._submit_question("codex follow-up", model=frame.selected_model) == (True, "")

    assert frame._switch_current_chat(kimi_b) is True
    wx_app.Yield()
    assert frame.selected_model == "kimi/main"
    assert frame.model_combo.GetValue() == main.model_display_name("kimi/main")
    assert frame.input_edit.HasFocus()
    assert frame._submit_question("kimi follow-up", model=frame.selected_model) == (True, "")

    assert dispatches == [
        ("codex", "chat-codex-a", 1, "codex follow-up", main.DEFAULT_CODEX_MODEL),
        ("kimi", kimi_b, 0, "kimi follow-up", "kimi/main"),
    ]
