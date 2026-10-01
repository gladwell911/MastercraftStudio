"""Native Alt+C and quiet history activity with isolated controlled providers."""
import ctypes
import time

import pytest
import wx

import main
from codex_client import CodexAppServerClient, CodexEvent
from kimi_server_client import KimiEvent
from test_kimi_integration import _make_fake_client
from test_model_session_recovery_ui_automation import (
    native_gui_loop, native_key_input_batch, native_modifier_state, native_user32, pump, run_native_for, wait_for,
)


CONTINUE = "好的，继续"


def seed_history(frame, *, pinned=False, active_pinned=False):
    frame._on_new_chat_clicked(None)
    owner = frame._ensure_active_chat_id()
    assert owner and frame.current_chat_id == owner and frame._current_chat_state["id"] == owner
    frame._current_chat_state.update(
        id=owner, title="activity owner", created_at=1.0, updated_at=1.0,
        pinned=active_pinned, turns=frame.active_session_turns,
    )
    frame.archived_chats = [
        {"id": "recent", "title": "recent", "created_at": 20.0, "updated_at": 20.0,
         "pinned": False, "model": main.DEFAULT_CODEX_MODEL, "turns": []},
    ]
    if pinned:
        frame.archived_chats.insert(0, {
            "id": "pinned", "title": "pinned", "created_at": 30.0,
            "updated_at": 30.0, "pinned": True, "model": main.DEFAULT_CODEX_MODEL, "turns": [],
        })
    frame._refresh_history(owner)
    frame._save_state()
    assert frame.chat_store.load_chat(owner) is not None
    assert frame.chat_store.load_chat(owner)["pinned"] == frame._current_chat_state["pinned"]
    assert frame.active_chat_id == frame.current_chat_id == frame._current_chat_state["id"] == owner
    assert frame.history_list_model.selected_id() == owner
    return owner


def activate(frame, control):
    user32 = native_user32()
    for attempt in range(3):
        pump()
        frame.Show()
        frame.Raise()
        user32.SetForegroundWindow(ctypes.c_void_p(int(frame.GetHandle())))
        control.SetFocus()
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            pump()
            focus = wx.Window.FindFocus()
            if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                    and focus is not None and int(focus.GetHandle()) == int(control.GetHandle())
                    and control.HasFocus()):
                pump()
                focus = wx.Window.FindFocus()
                if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                        and focus is not None and int(focus.GetHandle()) == int(control.GetHandle())
                        and control.HasFocus()):
                    return
            time.sleep(.01)
    focus = wx.Window.FindFocus()
    raise AssertionError({"phase": "native-activation", "frame": int(frame.GetHandle()),
                          "control": int(control.GetHandle()), "has_focus": control.HasFocus(),
                          "actual_focus": int(focus.GetHandle()) if focus else None,
                          "foreground": int(user32.GetForegroundWindow() or 0)})


def alt_c():
    inserted = []
    timer = wx.CallLater(30, lambda: inserted.append(native_key_input_batch(
        [(0x12, 0), (0x43, 0), (0x43, 2), (0x12, 2)], "alt-c")))
    try:
        run_native_for(200)
        assert inserted == [4]
    finally:
        timer.Stop()
        native_key_input_batch([(0x43, 2), (0x12, 2)], "alt-c-release")


@pytest.mark.parametrize("control_name", ["history_list", "answer_list", "input_edit", "model_combo"])
@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_native_alt_c_sends_exactly_once_and_displays_owned_answer(
    frame, monkeypatch, control_name, provider,
):
    owner = seed_history(frame)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    model = main.DEFAULT_CODEX_MODEL if provider == "codex" else main.DEFAULT_KIMI_MODEL
    frame.selected_model = model
    frame.model_combo.SetValue(main.model_display_name(model))
    starts = []
    fake = _make_fake_client(monkeypatch) if provider == "kimi" else None
    if provider == "codex":
        monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *a: starts.append(a))
    activate(frame, getattr(frame, control_name))
    alt_c()
    wait_for(lambda: len(frame.active_session_turns) == 1, "native-alt-c-one-turn")
    turn = frame.active_session_turns[0]
    assert turn["question"] == CONTINUE
    assert frame.active_chat_id == owner
    sent_at = frame._current_chat_state["updated_at"]
    assert sent_at == turn["created_at"]
    answer = provider + " controlled continued answer"
    if fake is not None:
        wait_for(lambda: len(fake.submitted) == 1, "kimi-one-submission")
        assert fake.submitted[0]["blocks"] == [{"type": "text", "text": CONTINUE}]
        session = fake.submitted[0]["session_id"]
        fake.push_event(KimiEvent(type="turn_started", thread_id=session, turn_id="1"))
        fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session,
                                 turn_id="1", text=answer, display_kind="assistant"))
        completion = KimiEvent(type="turn_completed", thread_id=session, turn_id="1", status="completed")
        fake.push_event(completion)
    else:
        assert len(starts) == 1 and starts[0][0] == owner and starts[0][2] == CONTINUE
        turn["codex_turn_id"] = "native-continue"
        frame._on_codex_event_for_chat(owner, CodexEvent(
            type="item_completed", turn_id="native-continue", phase="final_answer", text=answer,
            data={"turn_idx": 0},
        ))
        assert frame._current_chat_state["updated_at"] == sent_at
        completion = CodexEvent(type="turn_completed", turn_id="native-continue", status="completed",
                                data={"turn_idx": 0})
        frame._on_codex_event_for_chat(owner, completion)
    wait_for(lambda: turn.get("request_status") == "done" and any(
        answer in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())
    ), "native-alt-c-visible-answer")
    completed_at = frame._current_chat_state["updated_at"]
    assert completed_at >= sent_at
    if fake is not None:
        fake.push_event(completion)
    else:
        frame._on_codex_event_for_chat(owner, completion)
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == completed_at
    frame._save_state()
    persisted = frame.chat_store.load_chat(owner)
    assert [t["question"] for t in persisted["turns"]] == [CONTINUE]
    assert persisted["turns"][0]["answer_md"] == answer


@pytest.mark.parametrize("pinned,active_pinned", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("fact", ["send", "completion"])
def test_history_activity_keeps_selection_focus_and_pinned_partition(
    frame, monkeypatch, pinned, active_pinned, fact,
):
    owner = seed_history(frame, pinned=pinned, active_pinned=active_pinned)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *a: None)
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._navigation_quiet_until = time.monotonic() + 2.0
    if fact == "send":
        assert frame._submit_question("activity")[0]
    else:
        turn = {"question": "activity", "answer_md": main.REQUESTING_TEXT,
                "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "created_at": 1.0}
        frame.active_session_turns.append(turn)
        frame._on_done(0, "authoritative answer", "", main.DEFAULT_CODEX_MODEL, "", owner)
    # Select a different identity after the activity. The delayed refresh must
    # preserve that user's selection rather than selecting the activity owner.
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    frame._navigation_quiet_until = time.monotonic() + .4
    before = list(frame.history_ids)
    frame._flush_idle_ui_refreshes()
    assert frame.history_ids == before
    frame._navigation_quiet_until = 0
    frame._last_primary_interaction_at = 0
    frame._flush_idle_ui_refreshes()
    pump()
    expected = ["pinned", owner, "recent"] if pinned and not active_pinned else (
        [owner, "pinned", "recent"] if pinned else [owner, "recent"])
    assert frame.history_ids == expected
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    frame._save_state()
    stored = [c["id"] for c in frame.chat_store.list_chat_summaries()]
    assert stored == expected


def test_late_worker_completion_only_moves_its_owner_once(frame, monkeypatch):
    owner = seed_history(frame)
    background = frame.archived_chats[0]
    background["updated_at"] = .5
    background["turns"] = [{"question": "old request", "answer_md": main.REQUESTING_TEXT,
                             "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending"}]
    frame._refresh_history(owner)
    activate(frame, frame.input_edit)
    foreground_time = frame._current_chat_state["updated_at"]
    frame._on_done(0, "late answer", "", main.DEFAULT_CODEX_MODEL, "", "recent")
    accepted_at = background["updated_at"]
    assert frame._current_chat_state["updated_at"] == foreground_time
    frame._on_done(0, "late answer", "", main.DEFAULT_CODEX_MODEL, "", "recent")
    assert background["updated_at"] == accepted_at
    assert frame.input_edit.HasFocus()
    redraws = []
    original = frame._request_listbox_repaint
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *a, **k: (redraws.append(1), original(*a, **k))[1])
    frame._navigation_quiet_until = 0
    frame._last_primary_interaction_at = 0
    frame._flush_idle_ui_refreshes()
    redraws.clear()
    frame._mark_history_list_dirty()
    frame._flush_idle_ui_refreshes()
    assert redraws == []


class ControlledCodexClient:
    """Push controlled worker messages through the real source-client callback."""
    def __init__(self, frame, owner):
        self.frame = frame
        self.owner = owner

    def close(self):
        pass

    def push(self, kind, *, generation=0, text="late A authoritative answer"):
        event = CodexEvent(type=kind, turn_id="turn-" + self.owner,
                           thread_id="thread-" + self.owner, phase="final_answer", text=text,
                           status="completed", data={"turn_idx": 0, "context_generation": generation})
        self.frame._on_codex_worker_message(self.owner, {
            "type": "event", "payload": {"chat_id": self.owner, "turn_idx": 0,
            "context_generation": generation, "event": main.codex_worker_protocol.event_to_payload(event)},
        }, self)


def controlled_codex_starts(frame, monkeypatch):
    clients = {}
    def start(owner, index, question, model):
        chat = frame._current_chat_state if owner == frame.active_chat_id else frame._find_archived_chat(owner)
        turn = chat["turns"][index]
        turn.update(codex_turn_id="turn-" + owner, codex_thread_id="thread-" + owner,
                    codex_start_generation=0, codex_context_generation=0)
        chat.update(codex_thread_id="thread-" + owner, codex_context_generation=0)
        client = ControlledCodexClient(frame, owner)
        clients[owner] = client
        frame._codex_clients[owner] = client
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", start)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *a: None)
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    return clients


def test_switch_a_to_b_keeps_surface_owner_when_a_client_completes(frame, monkeypatch):
    owner_a = seed_history(frame, pinned=True)
    frame._save_state()
    clients = controlled_codex_starts(frame, monkeypatch)
    activate(frame, frame.input_edit)
    alt_c()
    wait_for(lambda: owner_a in clients, "A-real-shortcut-submitted")
    # Select B in the actual history control and activate it with Enter.
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    assert native_key_input_batch([(0x0D, 0), (0x0D, 2)], "history-B-enter") == 2
    run_native_for(100)
    wait_for(lambda: frame.view_history_id == "recent", "B-history-visible")
    activate(frame, frame.input_edit)
    alt_c()
    wait_for(lambda: frame.active_chat_id == "recent" and "recent" in clients, "B-current-owner")
    before_b = frame._current_chat_state["updated_at"]
    before_answers = list(frame.answer_list.GetStrings())
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    clients[owner_a].push("item_completed")
    clients[owner_a].push("turn_completed")
    wait_for(lambda: frame._find_archived_chat(owner_a)["turns"][0].get("request_status") == "done",
             "A-client-authoritative-completion")
    wait_for(lambda: frame.history_ids == ["pinned", owner_a, "recent"], "A-visible-recency")
    accepted_at = frame._find_archived_chat(owner_a)["updated_at"]
    assert frame.active_chat_id == "recent"
    assert frame._current_chat_state["updated_at"] == before_b
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert list(frame.answer_list.GetStrings()) == before_answers
    clients[owner_a].push("turn_completed")
    # A retired source and an obsolete generation must be rejected before UI application.
    retired = clients[owner_a]
    replacement = ControlledCodexClient(frame, owner_a)
    frame._codex_clients[owner_a] = replacement
    retired.push("item_completed", text="retired source")
    replacement.push("item_completed", generation=-1, text="obsolete generation")
    run_native_for(100)
    assert frame._find_archived_chat(owner_a)["updated_at"] == accepted_at
    assert frame.history_ids == ["pinned", owner_a, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert frame._current_chat_state["updated_at"] == before_b
    assert list(frame.answer_list.GetStrings()) == before_answers


def test_native_history_navigation_defers_current_completion_reorder(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    clients = controlled_codex_starts(frame, monkeypatch)
    # Submit, then put the newer chat ahead again so completion has a visible move.
    assert frame._submit_question("navigation request")[0]
    frame.archived_chats[1]["updated_at"] = frame._current_chat_state["updated_at"] + .01
    frame._refresh_history(owner)
    activate(frame, frame.history_list)
    before = list(frame.history_ids)
    repaints = []
    original = frame._request_listbox_repaint
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda control: (
        repaints.append(control.GetHandle()), original(control))[1])
    received = []
    touch = frame._touch_navigation_quiet_window
    def observe(event):
        touch(event)
        if event.GetKeyCode() in (wx.WXK_UP, wx.WXK_DOWN):
            received.append((event.GetKeyCode(), event.ControlDown(), event.AltDown(),
                             event.ShiftDown(), frame.history_list.GetSelection(),
                             frame._navigation_quiet_active()))
    monkeypatch.setattr(frame, "_touch_navigation_quiet_window", observe)
    releases = wx.CallLater(20, lambda: native_key_input_batch(
        [(0x11, 2), (0x12, 2), (0x10, 2)], "navigation-modifier-release"))
    try:
        run_native_for(60)
    finally:
        releases.Stop()
    assert not any(native_modifier_state(native_user32()).values())
    def arrow(vk):
        inserted = []
        selected_before = frame.history_list.GetSelection()
        timer = wx.CallLater(20, lambda: inserted.append(native_key_input_batch(
            [(vk, 1), (vk, 3)], "history-navigation-extended")))
        try:
            run_native_for(100)
            assert inserted == [2]
            assert received and received[-1][0] == (wx.WXK_UP if vk == 0x26 else wx.WXK_DOWN)
            assert received[-1][1:4] == (False, False, False)
            assert received[-1][-1]
            print("NATIVE_HISTORY_ARROW", received[-1], "selection", selected_before,
                  "->", frame.history_list.GetSelection(), flush=True)
        finally:
            timer.Stop()
    arrow(0x26)
    wait_for(frame._navigation_quiet_active, "real-arrow-quiet-gate")
    clients[owner].push("item_completed")
    clients[owner].push("turn_completed")
    wait_for(lambda: frame.active_session_turns[0].get("request_status") == "done",
             "completion-during-real-navigation")
    for vk in (0x28, 0x26, 0x28, 0x26):
        arrow(vk)
        assert frame._navigation_quiet_active()
        assert frame.history_ids == before
        assert int(frame.history_list.GetHandle()) not in repaints
    selected = frame.history_list_model.selected_id()
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "navigation-quiet-final-order")
    assert frame.history_list_model.selected_id() == selected
    assert frame.history_list.HasFocus()


def test_active_pinned_state_roundtrips_and_unpin_is_persisted(frame):
    owner = seed_history(frame, pinned=True, active_pinned=True)
    frame._record_chat_activity(frame._current_chat_state, time.time())
    frame._save_state()
    assert frame.chat_store.load_chat(owner)["pinned"] is True
    assert [chat["id"] for chat in frame.chat_store.list_chat_summaries()] == [owner, "pinned", "recent"]
    frame._current_chat_state["pinned"] = False
    frame._save_state()
    assert frame.chat_store.load_chat(owner)["pinned"] is False
    assert [chat["id"] for chat in frame.chat_store.list_chat_summaries()] == ["pinned", owner, "recent"]


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("status", ["failed", "interrupted", "completed", ""])
def test_protocol_mapped_codex_terminal_activity_is_success_only(frame, monkeypatch, archived, status):
    owner = seed_history(frame, pinned=True)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *a: None)
    target = frame.archived_chats[1] if archived else frame._current_chat_state
    target_id = target["id"]
    target["updated_at"] = .5
    turn = {"question": "terminal request", "answer_md": main.REQUESTING_TEXT,
            "request_status": "pending", "model": main.DEFAULT_CODEX_MODEL,
            "codex_turn_id": "terminal-turn", "codex_thread_id": "terminal-thread"}
    target["turns"] = [turn]
    target["codex_thread_id"] = "terminal-thread"
    if not archived:
        frame.active_session_turns = target["turns"]
    frame._refresh_history(owner)
    activate(frame, frame.input_edit)
    before_order = list(frame.history_ids)
    before_foreground = frame._current_chat_state["updated_at"]
    client = CodexAppServerClient(on_event=lambda event: frame._on_codex_event_for_chat(target_id, event))
    message = {"method": "turn/completed", "params": {
        "threadId": "terminal-thread", "turn": {"id": "terminal-turn", "status": status},
    }}
    client._handle_message(message)
    assert turn["request_status"] == "done"  # Retain the existing terminal handling.
    if status in {"failed", "interrupted"}:
        run_native_for(100)
        assert target["updated_at"] == .5
        assert frame.history_ids == before_order
    else:
        assert target["updated_at"] > .5
        expected = ["pinned", "recent", owner] if archived else ["pinned", owner, "recent"]
        wait_for(lambda: frame.history_ids == expected, "mapped-success-history-order")
    accepted_at = target["updated_at"]
    client._handle_message(message)
    run_native_for(100)
    assert target["updated_at"] == accepted_at
    if archived:
        assert frame._current_chat_state["updated_at"] == before_foreground


def test_openclaw_visible_sync_reorders_pinned_history_once(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    frame._stop_openclaw_sync()
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame.active_openclaw_session_id = "openclaw-activity"
    frame._current_chat_state["openclaw_session_id"] = "openclaw-activity"
    # The sync consumer, rather than the submission ACK, owns reply activity.
    state = {"chat_id": owner, "session_id": "openclaw-activity", "offset": 100,
             "session_file": "", "file_changed": False, "session_changed": False}
    event = main.OpenClawSyncEvent(event_id="incoming-activity", role="assistant",
                                  text="OpenClaw authoritative visible answer", timestamp=time.time())
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    before = frame._current_chat_state["updated_at"]
    frame._apply_openclaw_sync_batch(state, [event])
    assert frame._current_chat_state["updated_at"] > before
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "openclaw-visible-history")
    assert any(event.text in text for text in frame.answer_list.GetStrings())
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    accepted_at = frame._current_chat_state["updated_at"]
    repaints = []
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda control: repaints.append(control.GetHandle()))
    frame._apply_openclaw_sync_batch(state, [event])
    frame._apply_openclaw_sync_batch(state, [])
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == accepted_at
    assert frame.history_ids == ["pinned", owner, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert repaints == []


def test_kimi_client_completion_moves_owner_after_competing_activity(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    fake = _make_fake_client(monkeypatch)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    frame.selected_model = main.DEFAULT_KIMI_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_KIMI_MODEL))
    assert frame._submit_question("Kimi receive activity")[0]
    wait_for(lambda: len(fake.submitted) == 1, "kimi-receive-provider-submitted")
    sent_at = frame._current_chat_state["updated_at"]
    session = fake.submitted[0]["session_id"]
    frame.archived_chats[1]["updated_at"] = time.time()
    frame._refresh_history("recent")
    assert frame.history_ids == ["pinned", "recent", owner]
    activate(frame, frame.history_list)
    fake.push_event(KimiEvent(type="turn_started", thread_id=session, turn_id="receipt-turn"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session, turn_id="receipt-turn",
                             text="Kimi authoritative receipt", display_kind="assistant"))
    assert frame._current_chat_state["updated_at"] == sent_at
    completion = KimiEvent(type="turn_completed", thread_id=session, turn_id="receipt-turn", status="completed")
    fake.push_event(completion)
    wait_for(lambda: frame.active_session_turns[0].get("request_status") == "done", "kimi-receive-done")
    completed_at = frame._current_chat_state["updated_at"]
    assert completed_at > sent_at
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "kimi-receive-partition-order")
    wait_for(lambda: any("Kimi authoritative receipt" in text for text in frame.answer_list.GetStrings()),
             "kimi-receive-visible-answer")
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    fake.push_event(completion)
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == completed_at
    assert frame.history_ids == ["pinned", owner, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
