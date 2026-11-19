from pathlib import Path
import main
import wx


def _execution_content_labels(frame):
    """Return execution content rows while ignoring separate time nodes."""
    return [
        frame.execution_list.GetString(index)
        for index, meta in enumerate(frame.execution_meta)
        if meta[0] != "time"
    ]


def _execution_content_ids(frame):
    return [
        row_id
        for row_id, meta in zip(frame.execution_list_model.visible_ids, frame.execution_meta)
        if meta[0] != "time"
    ]


def _execution_row_index(frame, label, occurrence=0):
    indexes = [
        index
        for index, row_label in enumerate(frame.execution_list.GetStrings())
        if row_label == label
    ]
    return indexes[occurrence]


REQUEST_METADATA_FIELDS = {
    "request_status",
    "request_model",
    "request_question",
    "request_started_at",
    "request_last_attempt_at",
    "request_attempt_count",
    "request_recoverable",
    "request_recovery_mode",
    "request_resume_token",
    "request_error",
    "request_recovered_after_restart",
}


def assert_request_metadata(turn, *, status, model, question, recoverable, mode, resume_token, attempt_count):
    assert REQUEST_METADATA_FIELDS.issubset(turn.keys())
    assert turn["request_status"] == status
    assert turn["request_model"] == model
    assert turn["request_question"] == question
    assert turn["request_recoverable"] is recoverable
    assert turn["request_recovery_mode"] == mode
    assert turn["request_resume_token"] == resume_token
    assert turn["request_attempt_count"] == attempt_count


class _QuietKeyEvent:
    def __init__(self, key, *, alt=False, shift=False, ctrl=False):
        self._key = key
        self._alt = alt
        self._shift = shift
        self._ctrl = ctrl

    def GetKeyCode(self):
        return self._key

    def AltDown(self):
        return self._alt

    def ShiftDown(self):
        return self._shift

    def ControlDown(self):
        return self._ctrl


class _Story13KeyEvent:
    def __init__(self, key=wx.WXK_RETURN, *, ctrl=False, alt=False, shift=False, repeat=False):
        self.key, self.ctrl, self.alt, self.shift, self.repeat = key, ctrl, alt, shift, repeat
        self.skipped = 0

    def GetKeyCode(self): return self.key
    def ControlDown(self): return self.ctrl
    def AltDown(self): return self.alt
    def ShiftDown(self): return self.shift
    def IsAutoRepeat(self): return self.repeat
    def Skip(self): self.skipped += 1


class _UnmodifiedSpaceEvent:
    def __init__(self, *, ctrl=False, alt=False, shift=False):
        self.ctrl = ctrl
        self.alt = alt
        self.shift = shift
        self.skipped = 0

    def GetKeyCode(self):
        return wx.WXK_SPACE

    def ControlDown(self):
        return self.ctrl

    def AltDown(self):
        return self.alt

    def ShiftDown(self):
        return self.shift

    def Skip(self):
        self.skipped += 1


def _prepare_execution_async_history(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "async-followup.db", max_execution_steps_per_turn=2000)
    store.initialize()
    store.upsert_chat({"id": "async", "detail_panel_mode": "answers"})
    store.replace_turns("async", [{"question": "B question", "answer_md": "B answer"}])
    for i in range(650):
        store.append_execution_step("async", {"step": f"B step {i}", "turn_idx": 0,
            "created_at": i + 1, "display_kind": "command" if i >= 120 else ""})
    frame.chat_store, frame._chat_store_enabled = store, True
    frame.archived_chats = [{"id": "async"}]
    frame.view_mode, frame.view_history_id = "history", "async"
    frame._current_execution_steps()
    monkeypatch.setattr(frame, "_primary_navigation_control_is_recently_active", lambda: False)
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    monkeypatch.setattr(frame, "_save_state", lambda *a, **kw: None)
    return store


def _prepare_switched_chat_final(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "switch-owner"
    frame.active_session_turns = [{
        "question": "second request", "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending",
        "codex_context_generation": 1,
    }]
    frame._current_chat_state = {
        "id": "switch-owner", "title": "owner", "model": main.DEFAULT_CODEX_MODEL,
        "turns": frame.active_session_turns, "codex_context_generation": 1,
    }
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    frame._apply_codex_worker_thread_state("switch-owner", {
        "chat_id": "switch-owner", "turn_idx": 0, "thread_id": "switch-thread",
        "turn_id": "switch-turn", "context_generation": 1, "active": True,
    })
    assert frame._current_chat_state["codex_context_generation"] == 2
    assert frame.active_session_turns[0]["codex_start_generation"] == 1


def _switched_chat_final_event(kind, generation=1, **kwargs):
    return main.CodexEvent(
        type=kind, thread_id="switch-thread", turn_id="switch-turn",
        data={"turn_idx": 0, "context_generation": generation}, **kwargs,
    )


def desktop_source() -> str:
    entry = Path(main.__file__)
    paths = [entry, *sorted(entry.parent.glob("mc_frame_*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)
