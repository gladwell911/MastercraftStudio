from pathlib import Path
from types import SimpleNamespace
import sqlite3

import main
from chat_store import ChatStore


def test_web_answer_read_requires_successful_open(monkeypatch, tmp_path):
    target = {"chat_id": "a", "answer_seq": 7}
    confirmations = []
    frame = SimpleNamespace(
        answer_list=SimpleNamespace(GetSelection=lambda: 0),
        answer_meta=[("answer", 0, None, None)],
        _selected_read_target=lambda: target,
        _get_view_turns=lambda: [{"answer_md": "completed answer"}],
        _ensure_answer_detail_page=lambda *_: Path(tmp_path / "answer.html"),
        _open_local_webpage=lambda _: False,
        SetStatusText=lambda _: None,
        _save_state=lambda: None,
        _confirm_answer_read=confirmations.append,
    )
    monkeypatch.setattr(main.wx, "MessageBox", lambda *_: None)
    assert main.ChatFrame._try_open_selected_answer_detail(frame) is False
    assert confirmations == []
    frame._open_local_webpage = lambda _: True
    assert main.ChatFrame._try_open_selected_answer_detail(frame) is True
    assert confirmations == [target]


def test_hotkey_empty_or_failed_navigation_never_confirms(monkeypatch):
    monkeypatch.setattr(main, "os", SimpleNamespace(name="posix"))
    for count, opened in [(0, True), (2, False)]:
        confirmations = []
        frame = SimpleNamespace(
            history_ids=["a"], _restore_or_raise=lambda: None,
            _visible_answer_owner_chat_id=lambda: "a",
            _chat_read_state=lambda _: {"generation": "g", "latest_readable_seq": 7, "read_seq": 0},
            _show_history_chat=lambda *_, **__: opened,
            _apply_detail_panel_mode=lambda _: None,
            answer_list=SimpleNamespace(GetCount=lambda: count),
            SetStatusText=lambda _: None,
            _confirm_answer_read=confirmations.append,
            _speak_text_via_screen_reader=lambda _: None,
        )
        main.ChatFrame._jump_to_unread_chat(frame)
        assert confirmations == []


def test_hotkey_real_tail_focus_keeps_frozen_cap_when_new_answer_arrives(monkeypatch):
    monkeypatch.setattr(main, "os", SimpleNamespace(name="posix"))
    selection, confirmations = [], []
    state = {"generation": "g", "latest_readable_seq": 7, "read_seq": 0}
    def hydrate(*_, **__):
        state["latest_readable_seq"] = 9
        return True
    frame = SimpleNamespace(
        history_ids=["a"], _restore_or_raise=lambda: None,
        _visible_answer_owner_chat_id=lambda: "a",
        _chat_read_state=lambda _: dict(state), _show_history_chat=hydrate,
        _apply_detail_panel_mode=lambda _: None,
        answer_list=SimpleNamespace(GetCount=lambda: 4, SetSelection=selection.append, SetFocus=lambda: None),
        _selected_read_target=lambda index: ({"generation": "g", "answer_seq": 9} if index == 2 else
                                            {"generation": "g", "answer_seq": 7} if index == 1 else
                                            {"generation": "g", "frozen_turn": {}} if index == 3 else None),
        _confirm_answer_read=confirmations.append,
        _speak_text_via_screen_reader=lambda _: None,
    )
    main.ChatFrame._jump_to_unread_chat(frame)
    assert selection == [3]  # Actual last item is a question, still keep its focus.
    assert confirmations == [{"generation": "g", "answer_seq": 7}]


def test_unread_hotkey_native_registration_keeps_norepeat_flag_and_is_idempotent(monkeypatch):
    registered = []
    def register(*arguments):
        registered.append(arguments)
        return 1
    monkeypatch.setattr(main.ctypes, "WinDLL", lambda *_, **__: SimpleNamespace(RegisterHotKey=register))
    frame = SimpleNamespace(
        GetHandle=lambda: 1234, _show_hotkey_registered=False,
        RegisterHotKey=lambda *_: False, SetStatusText=lambda _: None,
        _resolve_backslash_hotkey_vk=lambda: main.VK_OEM_5,
    )
    main.ChatFrame._register_global_hotkey(frame)
    main.ChatFrame._register_global_hotkey(frame)
    assert registered == [(1234, main.HOTKEY_ID_UNREAD, 0x4006, ord("X"))]
    assert frame._unread_hotkey_registered is True


def test_hotkey_reverse_completion_uses_max_visible_canonical_seq_after_real_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "os", SimpleNamespace(name="posix"))
    store = ChatStore(tmp_path / "read.db")
    store.initialize()
    store.upsert_chat({"id": "a", "title": "A"})
    def turn(question, status="done"):
        return {"question": question, "answer_md": question if status == "done" else "", "request_status": status}
    store.replace_turns("a", [turn("baseline"), turn("earlier", "pending"), turn("tail", "pending")])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM meta WHERE key='read_state_initialized'")
    store.initialize()
    baseline = store.get_chat_read_state("a")["read_seq"]
    store.replace_turns("a", [turn("baseline"), turn("earlier", "pending"), turn("tail")])
    store.replace_turns("a", [turn("baseline"), turn("earlier"), turn("tail")])
    early = store.readable_answer("a", turn_index=1)
    tail = store.readable_answer("a", turn_index=2)
    assert early["answer_seq"] > tail["answer_seq"] > baseline
    selected = []
    def confirm(target):
        store.mark_chat_read(pair_id="default", chat_id="a", operation_id="test", **target)
    frame = SimpleNamespace(
        history_ids=["a"], _restore_or_raise=lambda: None,
        _visible_answer_owner_chat_id=lambda: "a",
        _chat_read_state=lambda _: store.get_chat_read_state("a"),
        _show_history_chat=lambda *_, **__: True, _apply_detail_panel_mode=lambda _: None,
        answer_list=SimpleNamespace(GetCount=lambda: 3, SetSelection=selected.append, SetFocus=lambda: None),
        _selected_read_target=lambda index: early if index == 0 else tail if index == 1 else None,
        _confirm_answer_read=confirm, _speak_text_via_screen_reader=lambda _: None,
    )
    main.ChatFrame._jump_to_unread_chat(frame)
    assert selected == [2]
    assert store.get_chat_read_state("a")["read_seq"] == early["answer_seq"]
    assert store.get_chat_read_state("a")["read_seq"] == store.get_chat_read_state("a")["latest_readable_seq"]
    store.replace_turns("a", [turn("baseline"), turn("earlier"), turn("tail"), turn("hidden")])
    main.ChatFrame._jump_to_unread_chat(frame)
    assert store.get_chat_read_state("a")["read_seq"] == early["answer_seq"]
    assert store.get_chat_read_state("a")["latest_readable_seq"] > early["answer_seq"]


def test_keyboard_entry_confirms_selected_answer_without_return(monkeypatch):
    class AnswerControl:
        def SetFocus(self): pass
    answer = AnswerControl()
    source = object()
    target = {"message_id": "frozen", "answer_seq": 7}
    confirmations = []
    frame = SimpleNamespace(answer_list=answer, execution_list=None, input_edit=source,
        history_list=object(), _current_detail_tab_target=lambda: answer,
        _selected_read_target=lambda: target, _confirm_answer_read=confirmations.append)
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: source)
    event = SimpleNamespace(GetKeyCode=lambda: main.wx.WXK_TAB, ControlDown=lambda: False,
                            AltDown=lambda: False, ShiftDown=lambda: True)
    assert main.ChatFrame._handle_primary_tab_navigation(frame, event)
    assert confirmations == [target]


def test_displayed_completion_before_save_freezes_only_that_turn(tmp_path):
    store = ChatStore(tmp_path / "read.db")
    store.initialize()
    store.upsert_chat({"id": "a", "title": "A"})
    original = {"question": "q", "answer_md": "visible completion", "request_status": "done"}
    turns = [original]
    frame = SimpleNamespace(chat_store=store, answer_list=SimpleNamespace(GetSelection=lambda: 0),
        answer_meta=[("answer", 0, None, None)], _visible_answer_owner_chat_id=lambda: "a",
        _get_view_turns=lambda: turns, _chat_read_state=lambda owner: store.get_chat_read_state(owner),
        _read_pair_id=lambda: "p", _on_chat_read_changed=lambda _: None,
        SetStatusText=lambda _: None, _defer_chat_state_save=lambda: None)
    frame._confirm_answer_read = lambda target: main.ChatFrame._confirm_answer_read(frame, target)
    target = main.ChatFrame._selected_read_target(frame)
    assert target["frozen_turn"] == original
    turns.append({"question": "future", "answer_md": "future completion", "request_status": "done"})
    main.ChatFrame._confirm_answer_read(frame, target)
    assert store.count_turns("a") == 0
    store.replace_turns("a", turns)
    main.ChatFrame._flush_pending_answer_reads(frame, "a")
    state = store.get_chat_read_state("a", pair_id="p")
    assert state["read_seq"] == store.readable_answer("a", turn_index=0)["answer_seq"]
    assert state["latest_readable_seq"] > state["read_seq"]


def test_read_second_unsaved_turn_keeps_pagination_contiguous(tmp_path):
    store = ChatStore(tmp_path / "read.db")
    store.initialize()
    store.upsert_chat({"id": "a", "title": "A"})
    def turn(text):
        return {"question": text, "answer_md": text, "request_status": "done"}
    baseline = turn("baseline")
    store.replace_turns("a", [baseline])
    turns = [baseline, turn("first"), turn("second")]
    frame = SimpleNamespace(chat_store=store, answer_list=SimpleNamespace(GetSelection=lambda: 0),
        answer_meta=[("answer", 2, None, None)], _visible_answer_owner_chat_id=lambda: "a",
        _get_view_turns=lambda: turns, _chat_read_state=lambda owner: store.get_chat_read_state(owner),
        _read_pair_id=lambda: "p", _on_chat_read_changed=lambda _: None,
        SetStatusText=lambda _: None, _defer_chat_state_save=lambda: None,
        _chat_turn_dirty_from={"a": 1}, _call_after_if_alive=lambda *_: None,
        _refresh_chat_read_row=lambda *_: None)
    frame._confirm_answer_read = lambda target: main.ChatFrame._confirm_answer_read(frame, target)
    frame._flush_pending_answer_reads = lambda owner: main.ChatFrame._flush_pending_answer_reads(frame, owner)
    frame._confirm_answer_read(main.ChatFrame._selected_read_target(frame))
    assert store.load_turns("a") == [baseline]
    assert store.load_turns_page("a") == (1, [baseline])
    main.ChatFrame._persist_dirty_chat_turns(frame, store, "a", turns)
    assert store.load_turns("a") == turns
    assert store.load_turns_page("a") == (3, turns)
    target = store.readable_answer("a", turn_index=2)
    assert store.get_chat_read_state("a", pair_id="p")["read_seq"] == target["answer_seq"]
    store.replace_turns("a", turns + [turn("later")])
    assert store.get_chat_read_state("a", pair_id="p")["read_seq"] < store.get_chat_read_state("a", pair_id="p")["latest_readable_seq"]
    assert frame._pending_answer_reads == {}


def test_pending_read_drops_replaced_or_cleared_target(tmp_path):
    for cleared in (False, True):
        store = ChatStore(tmp_path / ("clear.db" if cleared else "replace.db"))
        store.initialize()
        store.upsert_chat({"id": "a", "title": "A"})
        original = {"question": "q", "answer_md": "original", "request_status": "done"}
        frame = SimpleNamespace(chat_store=store, _read_pair_id=lambda: "p",
            _on_chat_read_changed=lambda _: None, SetStatusText=lambda _: None,
            _defer_chat_state_save=lambda: None)
        frame._confirm_answer_read = lambda target: main.ChatFrame._confirm_answer_read(frame, target)
        generation = store.get_chat_read_state("a")["generation"]
        frame._confirm_answer_read({"chat_id": "a", "generation": generation,
            "turn_index": 0, "frozen_turn": original})
        if cleared:
            store.begin_clear_operation("a", idempotency_key="clear", pair_id="p")
        store.replace_turns("a", [{**original, "answer_md": "replacement"}])
        main.ChatFrame._flush_pending_answer_reads(frame, "a")
        state = store.get_chat_read_state("a", pair_id="p")
        assert state["read_seq"] < state["latest_readable_seq"]
        assert frame._pending_answer_reads == {}


def test_frozen_read_callback_after_normal_save_confirms_immediately(tmp_path):
    store = ChatStore(tmp_path / "read.db")
    store.initialize()
    store.upsert_chat({"id": "a", "title": "A"})
    turn = {"question": "q", "answer_md": "completed", "request_status": "done"}
    generation = store.get_chat_read_state("a")["generation"]
    target = {"chat_id": "a", "generation": generation, "turn_index": 0, "frozen_turn": turn}
    store.replace_turns("a", [turn])
    frame = SimpleNamespace(chat_store=store, _read_pair_id=lambda: "p",
        _on_chat_read_changed=lambda _: None, SetStatusText=lambda _: None)
    main.ChatFrame._confirm_answer_read(frame, target)
    state = store.get_chat_read_state("a", pair_id="p")
    assert state["read_seq"] == state["latest_readable_seq"] > 0
    assert not getattr(frame, "_pending_answer_reads", None)
