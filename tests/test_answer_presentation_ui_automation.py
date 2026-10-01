"""Native controls and non-postponable accepted-answer refresh contract."""
import time
from collections import Counter

import pytest
import wx

import main


@pytest.fixture(autouse=True)
def native_loop(wx_app, monkeypatch):
    # Autouse setup precedes frame construction, so overwritten CallLater
    # handles from this test remain reachable throughout its native lifetime.
    registry = []
    actual_call_later = main.wx_call_later_if_alive

    def registered_call_later(delay_ms, func, *args, **kwargs):
        handle = actual_call_later(delay_ms, func, *args, **kwargs)
        if isinstance(handle, wx.CallLater):
            registry.append((handle, getattr(func, "__self__", None), getattr(func, "__name__", type(func).__name__)))
        return handle

    monkeypatch.setattr(main, "wx_call_later_if_alive", registered_call_later)
    loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(loop)
    wx_app._answer_presentation_loop = loop
    try:
        yield registry
    finally:
        for handle, _, _ in registry:
            handle.Stop()
        remaining = Counter(name for handle, _, name in registry if handle.IsRunning())
        print(f"TIMER_REGISTRY final total={len(registry)} running={dict(remaining)}", flush=True)
        assert not remaining
        del activator


@pytest.fixture(autouse=True)
def complete_native_timer_cleanup(native_loop, frame):
    # This dependency makes cleanup run before conftest destroys the frame.
    yield
    frame._closing = True
    frame._flush_execution_step_persists_sync()
    frames = {id(frame): frame}
    for handle, owner, _ in native_loop:
        handle.Stop()
        if isinstance(owner, main.ChatFrame):
            frames[id(owner)] = owner
    for owner in frames.values():
        if bool(owner):
            for value in vars(owner).values():
                if isinstance(value, wx.Timer):
                    value.Stop()
    run_native_for(50)
    # Draining can schedule a new callback. Include every new registry entry,
    # stop it, and check the complete registry rather than only frame fields.
    names = Counter(name for _, _, name in native_loop)
    pending = Counter(name for handle, _, name in native_loop if handle.IsRunning())
    for handle, _, _ in native_loop:
        handle.Stop()
    for owner in frames.values():
        if bool(owner):
            for value in vars(owner).values():
                if isinstance(value, wx.Timer):
                    value.Stop()
    remaining = Counter(name for handle, _, name in native_loop if handle.IsRunning())
    print(f"TIMER_REGISTRY cleanup_before_frame_destroy total={len(native_loop)} callbacks={dict(names)} running_after_drain={dict(pending)} final_running={dict(remaining)}", flush=True)
    assert not remaining


def pump():
    app = wx.GetApp()
    loop = app._answer_presentation_loop
    while loop.Pending():
        loop.Dispatch()
    app.ProcessPendingEvents()
    loop.ProcessIdle()


def run_native_for(milliseconds):
    loop = wx.GUIEventLoop()

    def leave():
        if loop.IsRunning():
            loop.Exit(0)

    exit_timer = wx.CallLater(milliseconds, leave)
    try:
        return loop.Run()
    finally:
        exit_timer.Stop()


def stop_timers(frame, drain=pump):
    frame._stop_answer_refresh_deadline()
    for value in vars(frame).values():
        if isinstance(value, wx.Timer):
            value.Stop()
        elif isinstance(value, wx.CallLater):
            value.Stop()
    frame.Hide()
    drain()


def test_answer_text_viewer_plain_scratch_reopens_authoritative_markdown(frame):
    try:
        canonical = "# Heading\n\n**bold** and \\*literal\\* and C# \U0001f600\n\n**alpha** **beta** [link](https://example.test) `code`\n\n![diagram description](image.png)\n\n- first\n- second\n\n1. one\n2. two\n\n```\na*b # code\n\n\nlast\n```"
        turn = {"question": "q", "answer_md": canonical, "model": main.DEFAULT_CODEX_MODEL,
                "codex_thread_id": "thread", "codex_turn_id": "turn"}
        frame.active_chat_id = frame.current_chat_id = "plain-detail-owner"
        frame.active_session_turns = [turn]
        frame._current_chat_state.update(id="plain-detail-owner", turns=frame.active_session_turns)
        frame._render_answer_list()
        wx.GetApp().SetTopWindow(frame)
        row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
        frame.answer_list.SetSelection(row)
        payload = frame._selected_answer_viewer_payload()
        assert payload is not None
        assert payload.answer_md == canonical
        for attempt in range(2):
            dialog = main.AnswerTextViewerDialog(frame, "Details", payload=payload)
            dialog._set_answer_display_text()
            try:
                print(f"DETAIL attempt={attempt} show_before", flush=True)
                dialog.Show()
                print(f"DETAIL attempt={attempt} show_run_before", flush=True)
                run_native_for(50)
                print(f"DETAIL attempt={attempt} show_run_after", flush=True)
                visible = dialog.text_ctrl.GetValue()
                print(f"DETAIL attempt={attempt} get_value_complete", flush=True)
                assert "Heading\n\nbold and *literal* and C# \U0001f600" in visible
                assert "alpha beta link (https://example.test) code" in visible
                assert "diagram description" in visible
                assert "- first\n- second" in visible
                assert "1. one\n2. two" in visible
                assert "a*b # code\n\n\nlast" in visible
                assert "**bold**" not in visible
                assert dialog.canonical_text == canonical
                print(f"DETAIL attempt={attempt} set_value_before", flush=True)
                dialog.text_ctrl.SetValue("scratch mutation")
                print(f"DETAIL attempt={attempt} set_value_returned run_before", flush=True)
                run_native_for(50)
                print(f"DETAIL attempt={attempt} set_value_run_after", flush=True)
                assert turn["answer_md"] == canonical
                assert dialog.payload.answer_md == canonical
            finally:
                print(f"DETAIL attempt={attempt} destroy_before", flush=True)
                dialog.Destroy()
                run_native_for(50)
                try:
                    native_dead = not bool(dialog) or not dialog.GetHandle()
                except RuntimeError:
                    native_dead = True
                print(f"DETAIL attempt={attempt} destroy_run_after native_dead={native_dead}", flush=True)
                assert native_dead
    finally:
        stop_timers(frame, drain=lambda: run_native_for(50))


def test_final_visible_with_continuous_native_navigation_preserves_identity(frame, monkeypatch):
    try:
        for name in ("_play_finish_sound", "_push_remote_state", "_push_remote_history_changed",
                     "_queue_remote_final", "_defer_chat_state_save", "_upsert_history_row",
                     "_request_execution_list_sync", "_refresh_context_usage_after_done"):
            monkeypatch.setattr(frame, name, lambda *args, **kwargs: None)
        chat_id = str(frame.active_chat_id or frame.current_chat_id or "deadline-chat")
        frame.active_chat_id = frame.current_chat_id = chat_id
        turn = {"question": "new question", "answer_md": main.REQUESTING_TEXT,
                "created_at": time.time(), "model": "openai/gpt-5.2", "request_status": "pending"}
        frame.active_session_turns = [
            {"question": "old question", "answer_md": "old answer", "created_at": time.time() - 600,
             "model": "openai/gpt-5.2"}, turn]
        frame._current_chat_state.update(id=chat_id, turns=frame.active_session_turns)
        frame.view_mode = "active"
        frame._render_answer_list()
        wx.GetApp().SetTopWindow(frame)
        frame.Show()
        frame.Raise()
        pump()
        frame.answer_list.SetFocus()
        pump()
        assert wx.Window.FindFocus() is frame.answer_list
        started = time.monotonic()
        frame._navigation_quiet_until = started + 30
        frame._on_done(1, "accepted final body", "", "openai/gpt-5.2", "", chat_id)
        assert not any(meta[0] == "answer" and meta[1] == 1 and meta[3] == "accepted final body" for meta in frame.answer_meta)
        timer = frame._answer_refresh_deadline_timer
        refresh = frame._refresh_answer_list_preserving_selection
        checks = []
        def preserving_refresh(*args, **kwargs):
            selected = frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()])
            focus = wx.Window.FindFocus()
            assert focus is frame.answer_list
            refresh(*args, **kwargs)
            assert frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()]) == selected
            assert wx.Window.FindFocus() is focus
            checks.append(selected)
        monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", preserving_refresh)
        simulator = wx.UIActionSimulator()
        saw = False
        while time.monotonic() - started < 1.6:
            simulator.Char(wx.WXK_UP)
            pump()
            assert wx.Window.FindFocus() is frame.answer_list
            frame._navigation_quiet_until = time.monotonic() + 30
            index = frame.answer_list.GetSelection()
            selected_id = frame._answer_row_id(frame.answer_meta[index])
            if any(meta[0] == "answer" and meta[1] == 1 and meta[3] == "accepted final body" for meta in frame.answer_meta):
                saw = True
                assert frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()]) == selected_id
                assert wx.Window.FindFocus() is frame.answer_list
                break
            assert frame._answer_refresh_deadline_timer is timer
            time.sleep(.025)
        assert saw
        assert checks
        elapsed = time.monotonic() - started
        print(f"ANSWER_VISIBLE elapsed_ms={elapsed * 1000:.1f} selected_id={checks[-1]} focus=answer_list", flush=True)
        assert elapsed < 1.6
        frame._on_done(1, "accepted final body", "", "openai/gpt-5.2", "", chat_id)
        until = time.monotonic() + 1.15
        while time.monotonic() < until:
            pump()
            time.sleep(.01)
        ids = [frame._answer_row_id(meta) for meta in frame.answer_meta]
        assert ids.count("answer:1:answer") == 1
        assert len(ids) == len(set(ids))
        before = list(frame.answer_meta)
        frame._render_answer_list()
        assert frame.answer_meta == before
    finally:
        stop_timers(frame)


def test_answer_deadline_discards_replaced_owner_and_explicit_helper_stops_timer(frame):
    wx.GetApp().SetTopWindow(frame)
    turn = {"question": "q", "answer_md": "a"}
    frame.active_session_turns = [turn]
    frame._schedule_accepted_answer_refresh(str(frame.active_chat_id or frame.current_chat_id or ""), turn)
    owner = frame._answer_refresh_deadline_owner
    frame.active_session_turns = []
    frame._flush_accepted_answer_refresh(owner)
    assert frame._answer_refresh_deadline_owner is None
    frame.active_session_turns = [turn]
    frame._schedule_accepted_answer_refresh(str(frame.active_chat_id or frame.current_chat_id or ""), turn)
    timer = frame._answer_refresh_deadline_timer
    frame._stop_answer_refresh_deadline()
    assert timer is None or not timer.IsRunning()
    stop_timers(frame)


def test_accepted_answer_switch_chat_late_done_and_destroy_real_deadline(frame, monkeypatch):
    for name in ("_play_finish_sound", "_push_remote_state", "_push_remote_history_changed",
                 "_queue_remote_final", "_defer_chat_state_save", "_upsert_history_row",
                 "_request_execution_list_sync", "_refresh_context_usage_after_done"):
        monkeypatch.setattr(frame, name, lambda *args, **kwargs: None)
    wx.GetApp().SetTopWindow(frame)
    old_turn = {"question": "old q", "answer_md": main.REQUESTING_TEXT,
                "model": "openai/gpt-5.2", "created_at": time.time()}
    old_chat = {"id": "old-owner", "turns": [old_turn]}
    frame.active_chat_id = frame.current_chat_id = "old-owner"
    frame.active_session_turns = old_chat["turns"]
    frame._current_chat_state = old_chat
    frame._render_answer_list()
    frame.Show()
    pump()
    frame._navigation_quiet_until = time.monotonic() + 30
    try:
        frame._on_done(0, "old accepted body", "", "openai/gpt-5.2", "", "old-owner")
        new_turn = {"question": "new owner q", "answer_md": "new owner a", "created_at": time.time()}
        frame.active_chat_id = frame.current_chat_id = "new-owner"
        frame.active_session_turns = [new_turn]
        frame._current_chat_state = {"id": "new-owner", "turns": frame.active_session_turns}
        monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: old_chat if chat_id == "old-owner" else None)
        frame._render_answer_list()
        frame.answer_list.SetSelection(next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer"))
        selected = frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()])
        frame._on_done(0, "late old body", "", "openai/gpt-5.2", "", "old-owner")
        until = time.monotonic() + 1.15
        while time.monotonic() < until:
            pump()
            time.sleep(.01)
        assert all("old accepted body" not in label and "late old body" not in label for label in frame.answer_list.GetStrings())
        assert frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()]) == selected
        # A separate native frame keeps fixture teardown safe after real Destroy.
        doomed = main.ChatFrame()
        timer = None
        destroy_verified = False
        destroy_events = []
        callback_calls = []
        quiet_callback_calls = []
        refresh_calls = []

        def native_dead():
            try:
                return not bool(doomed) or not doomed.GetHandle()
            except RuntimeError:
                return True

        captured_frame_id = doomed.GetId()

        def observe_destroy(event):
            event_object = event.GetEventObject()
            destroy_events.append({
                "at": time.monotonic(),
                "id": event.GetId(),
                "type": type(event_object).__name__,
                "is_frame": event_object is doomed,
                "equals_frame": event_object == doomed,
            })
            event.Skip()

        try:
            wx.GetApp().SetTopWindow(doomed)
            pump()
            doomed.active_chat_id = doomed.current_chat_id = "destroy-owner"
            doomed.active_session_turns = [{"question": "q", "answer_md": "body"}]
            actual_callback = doomed._flush_accepted_answer_refresh
            actual_refresh = doomed._refresh_answer_list_preserving_selection
            actual_quiet_callback = doomed._flush_pending_background_ui_updates

            def observe_callback(*args, **kwargs):
                callback_calls.append(time.monotonic())
                return actual_callback(*args, **kwargs)

            def observe_quiet_callback(*args, **kwargs):
                quiet_callback_calls.append(time.monotonic())
                return actual_quiet_callback(*args, **kwargs)

            def observe_refresh(*args, **kwargs):
                refresh_calls.append(time.monotonic())
                return actual_refresh(*args, **kwargs)

            monkeypatch.setattr(doomed, "_flush_accepted_answer_refresh", observe_callback)
            monkeypatch.setattr(doomed, "_refresh_answer_list_preserving_selection", observe_refresh)
            monkeypatch.setattr(doomed, "_flush_pending_background_ui_updates", observe_quiet_callback)
            doomed.Bind(wx.EVT_WINDOW_DESTROY, observe_destroy)
            for value in vars(doomed).values():
                if isinstance(value, wx.Timer):
                    value.Stop()
                elif isinstance(value, wx.CallLater):
                    value.Stop()
            doomed._flush_execution_step_persists_sync()
            wx.GetApp().SetTopWindow(frame)
            child = wx.Panel(doomed)
            child_id = child.GetId()
            assert child_id != captured_frame_id
            scheduled_at = time.monotonic()
            doomed._schedule_accepted_answer_refresh("destroy-owner", doomed.active_session_turns[0])
            timer = doomed._answer_refresh_deadline_timer
            owner = doomed._answer_refresh_deadline_owner
            assert timer is not None and timer.IsRunning()
            assert owner is not None
            doomed._navigation_quiet_until = time.monotonic() + .2
            doomed._schedule_navigation_quiet_flush()
            first_quiet = doomed._navigation_quiet_flush_timer
            assert first_quiet is not None and first_quiet.IsRunning()
            doomed._schedule_navigation_quiet_flush()
            second_quiet = doomed._navigation_quiet_flush_timer
            assert second_quiet is not None and second_quiet.IsRunning()
            assert not first_quiet.IsRunning()
            assert not first_quiet.HasRun()
            print("QUIET_RESCHEDULE first_stopped=True second_running=True", flush=True)
            child.Destroy()
            run_native_for(50)
            print(
                f"CHILD_DESTROY child_id={child_id} frame_id={captured_frame_id}",
                f"running={timer.IsRunning()} has_run={timer.HasRun()}",
                f"owner_unchanged={doomed._answer_refresh_deadline_owner == owner}",
                f"handle_unchanged={doomed._answer_refresh_deadline_timer is timer}",
                f"callback_calls={len(callback_calls)}",
                flush=True,
            )
            assert any(event["id"] == child_id for event in destroy_events)
            assert not bool(child)
            assert timer.IsRunning()
            assert not timer.HasRun()
            assert doomed._answer_refresh_deadline_owner == owner
            assert doomed._answer_refresh_deadline_timer is timer
            assert callback_calls == []
            assert second_quiet.IsRunning()
            assert not second_quiet.HasRun()
            assert doomed._navigation_quiet_flush_timer is second_quiet
            assert quiet_callback_calls == []
            doomed._closing = True
            requested_at = time.monotonic()
            destroy_result = doomed.Destroy()
            # Exercise normal native Run with a separate bounded loop, which
            # restores the existing active test loop when it returns.
            cancellation_bound = scheduled_at + .3
            while time.monotonic() + .05 < cancellation_bound:
                run_native_for(50)
                self_events = [event for event in destroy_events if event["id"] == captured_frame_id]
                if self_events and native_dead() and not timer.IsRunning():
                    break
            self_events = [event for event in destroy_events if event["id"] == captured_frame_id]
            print(f"DESTROY_NATIVE_EVENTS frame_id={captured_frame_id} events={destroy_events}", flush=True)
            elapsed = time.monotonic() - requested_at
            event_elapsed_ms = (self_events[0]["at"] - requested_at) * 1000 if self_events else None
            print(
                "DESTROY_EVIDENCE",
                f"result={destroy_result} request_to_event_ms={event_elapsed_ms} request_to_check_ms={elapsed * 1000:.1f}",
                f"scheduled_to_check_ms={(time.monotonic() - scheduled_at) * 1000:.1f}",
                f"self_events={len(self_events)} native_dead={native_dead()}",
                f"running={timer.IsRunning()} has_run={timer.HasRun()}",
                f"owner={doomed._answer_refresh_deadline_owner}",
                f"handle={doomed._answer_refresh_deadline_timer}",
                f"quiet_running={second_quiet.IsRunning()} quiet_has_run={second_quiet.HasRun()} quiet_handle={doomed._navigation_quiet_flush_timer}",
                flush=True,
            )
            assert self_events
            assert native_dead()
            assert time.monotonic() - scheduled_at < .3
            assert not timer.IsRunning()
            assert not timer.HasRun()
            assert doomed._answer_refresh_deadline_owner is None
            assert doomed._answer_refresh_deadline_timer is None
            assert not second_quiet.IsRunning()
            assert not second_quiet.HasRun()
            assert doomed._navigation_quiet_flush_timer is None
            destroy_verified = True
            run_native_for(1100)
            print(f"DESTROY_AFTER_DEADLINE callback_calls={len(callback_calls)} quiet_callback_calls={len(quiet_callback_calls)} refresh_calls={len(refresh_calls)}", flush=True)
            assert callback_calls == []
            assert refresh_calls == []
            assert quiet_callback_calls == []
            assert not second_quiet.IsRunning()
            assert not second_quiet.HasRun()
            assert not timer.IsRunning()
            assert not timer.HasRun()
        finally:
            wx.GetApp().SetTopWindow(frame)
            if not native_dead():
                doomed._closing = True
                doomed._flush_execution_step_persists_sync()
                for value in vars(doomed).values():
                    if isinstance(value, wx.Timer):
                        value.Stop()
                    elif isinstance(value, wx.CallLater) and value is not timer:
                        value.Stop()
                doomed.Destroy()
                cleanup_bound = time.monotonic() + .3
                while not native_dead() and time.monotonic() + .05 < cleanup_bound:
                    run_native_for(50)
            if not destroy_verified and timer is not None and timer.IsRunning():
                print("DESTROY_FAILED emergency timer cleanup after failed AC; excluded from evidence", flush=True)
                timer.Stop()
    finally:
        wx.GetApp().SetTopWindow(frame)
        stop_timers(frame)


def test_real_show_more_recomputes_visible_answer_time_anchor(frame):
    wx.GetApp().SetTopWindow(frame)
    base = time.time() - 3600
    seconds = [index * 600 + offset for index in range(5) for offset in [0, 200, 299, 300, 301, 600]]
    frame.active_session_turns = [{"question": f"q{i}", "answer_md": f"a{i}",
                                  "created_at": base + offset} for i, offset in enumerate(seconds)]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame.view_mode = "active"
    try:
        frame._render_answer_list()
        assert any(meta[0] == "more" for meta in frame.answer_meta)
        assert min(meta[1] for meta in frame.answer_meta if meta[1] >= 0) > 0
        frame._show_more_answer_rows()
        pump()
        indices = list(dict.fromkeys(meta[1] for meta in frame.answer_meta if meta[1] >= 0))
        assert indices[0] == 0
        timestamps = [base + seconds[index] for index in indices]
        expected = [index for index, show in zip(indices, main.answer_time_projection(timestamps)) if show]
        actual = [meta[1] for meta in frame.answer_meta if meta[0] == "time"]
        assert actual == expected
        assert [frame.answer_list.GetString(i) for i, meta in enumerate(frame.answer_meta) if meta[0] == "time"] == [main.wechat_time_label(base + seconds[index], time.time()) for index in expected]
        assert 2 not in actual and 3 in actual
    finally:
        stop_timers(frame)


@pytest.mark.parametrize("kind", ["question", "execution", "non_cli_answer", "cli_answer"])
def test_answer_text_viewer_real_callers_qualify_plain_display(frame, monkeypatch, kind):
    wx.GetApp().SetTopWindow(frame)
    raw = "# heading\n\n*item* and **alpha** **beta**"
    turn = {"question": raw, "answer_md": raw, "model": "openai/gpt-5.2"}
    if kind == "cli_answer":
        turn.update(model=main.DEFAULT_CODEX_MODEL, codex_thread_id="thread", codex_turn_id="turn")
    frame.active_chat_id = frame.current_chat_id = "caller-owner"
    frame.active_session_turns = [turn]
    frame._current_chat_state.update(id="caller-owner", model=turn["model"], turns=frame.active_session_turns)
    frame.view_mode = "active"
    frame._render_answer_list()
    observations = []
    is_answer = kind.endswith("answer")
    expected = "heading\n\nitem and alpha beta" if is_answer else raw
    if kind == "execution":
        # Existing execution conversion produces literal asterisks from escapes.
        # The shared dialog must preserve its caller's final literal string.
        raw_execution = "\\*item\\* and C#"
        frame.execution_meta = [("execution", 0, raw_execution, raw_execution)]
        frame.execution_list.Set([raw_execution])
        frame.execution_list.SetSelection(0)
        monkeypatch.setattr(frame, "_detail_panel_mode", lambda: "execution")
        expected = "*item* and C#"
    else:
        row_kind = "answer" if is_answer else "question"
        frame.answer_list.SetSelection(next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == row_kind))

    def show_modal(dialog):
        dialog.Show()
        run_native_for(50)
        observations.append(dialog.text_ctrl.GetValue())
        assert observations[-1] == "\n" + expected
        assert dialog.canonical_text == (expected if kind == "execution" else raw)
        if kind == "cli_answer":
            assert dialog.payload.answer_md == raw
        if kind == "non_cli_answer":
            assert dialog.payload is None
        dialog.text_ctrl.SetValue("temporary caller scratch")
        run_native_for(50)
        assert turn["answer_md"] == raw
        return wx.ID_CLOSE

    monkeypatch.setattr(main.AnswerTextViewerDialog, "ShowModal", show_modal)
    try:
        caller = frame._open_selected_execution_text_viewer if kind == "execution" else frame._open_selected_answer_text_viewer
        assert caller()
        run_native_for(50)
        assert caller()
        run_native_for(50)
        assert len(observations) == 2
        assert turn["question"] == raw and turn["answer_md"] == raw
    finally:
        stop_timers(frame, drain=lambda: run_native_for(50))


def test_answer_text_viewer_already_visible_and_duplicate_deadlines_have_zero_native_writes(frame, monkeypatch):
    for name in ("_play_finish_sound", "_push_remote_state", "_push_remote_history_changed",
                 "_queue_remote_final", "_defer_chat_state_save", "_upsert_history_row",
                 "_request_execution_list_sync", "_refresh_context_usage_after_done", "_mark_chat_turns_dirty"):
        monkeypatch.setattr(frame, name, lambda *args, **kwargs: None)
    wx.GetApp().SetTopWindow(frame)
    frame.active_chat_id = frame.current_chat_id = "no-change-owner"
    turn = {"question": "q", "answer_md": "visible final body", "model": "openai/gpt-5.2",
            "created_at": time.time(), "request_status": "done"}
    frame.active_session_turns = [turn]
    frame._current_chat_state.update(id="no-change-owner", model=turn["model"], turns=frame.active_session_turns)
    frame.view_mode = "active"
    frame._render_answer_list()
    frame.Show()
    run_native_for(50)
    row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(row)
    frame.answer_list.SetFocus()
    operations = []
    renders = []
    for method in ("Refresh", "SetSelection"):
        original = getattr(frame.answer_list, method)
        def observe(*args, _method=method, _original=original, **kwargs):
            operations.append(_method)
            return _original(*args, **kwargs)
        monkeypatch.setattr(frame.answer_list, method, observe)
    render = frame._render_answer_list
    def observe_render(*args, **kwargs):
        renders.append(True)
        return render(*args, **kwargs)
    monkeypatch.setattr(frame, "_render_answer_list", observe_render)
    try:
        for duplicate in (False, True):
            frame._navigation_quiet_until = time.monotonic() + 30 if duplicate else 0
            frame._on_done(0, "visible final body", "", turn["model"], "", "no-change-owner")
            timer = frame._answer_refresh_deadline_timer
            assert timer is not None and timer.IsRunning()
            before_deadline_renders = len(renders)
            run_native_for(1100)
            assert timer.HasRun()
            assert len(renders) == before_deadline_renders
            assert operations == []
            assert frame.answer_list.GetSelection() == row
            assert wx.Window.FindFocus() is frame.answer_list
        frame._refresh_answer_list_preserving_selection(refresh_execution=False)
        assert operations == []
    finally:
        stop_timers(frame)


def test_review_detail_projection_native_and_cleanup(frame, monkeypatch):
    stop_timers(frame)
    canonical = ('<ol start="bad"><li>fallback</li></ol>\n\n'
                 '[docs](https://example.com/docs)\n\nfirst<br><br>second\n\n'
                 '<ul><li>outer<ul><li>inner</li></ul></li><li>next</li></ul>\n\n'
                 '<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>')
    for _ in range(2):
        dialog = main.AnswerTextViewerDialog(frame, 'Details', canonical)
        try:
            dialog._set_answer_display_text()
            dialog.Show()
            run_native_for(25)
            visible = dialog.text_ctrl.GetValue()
            assert '1. fallback' in visible
            assert 'docs (https://example.com/docs)' in visible
            assert 'first\n\nsecond' in visible
            assert '- outer\n  - inner\n- next' in visible
            assert 'a\tb\nc\td' in visible
            dialog.text_ctrl.SetValue('scratch')
            assert dialog.canonical_text == canonical
        finally:
            dialog.Destroy()
            run_native_for(25)
    created = []
    original = main.AnswerTextViewerDialog.__init__
    def record(dialog, *args, **kwargs):
        original(dialog, *args, **kwargs)
        created.append(dialog)
    monkeypatch.setattr(main.AnswerTextViewerDialog, '__init__', record)
    def fail(_dialog):
        raise ValueError('conversion failure')
    monkeypatch.setattr(main.AnswerTextViewerDialog, '_set_answer_display_text', fail)
    assert frame._open_answer_text_viewer('Details', canonical, _answer_markdown=True) is False
    run_native_for(25)
    assert not bool(created[0])
    assert frame._answer_viewer_open is False
