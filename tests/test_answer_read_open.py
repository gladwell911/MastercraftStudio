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
                                            {"generation": "g", "answer_seq": 7} if index == 1 else None),
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
