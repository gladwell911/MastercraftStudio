import wx


def _set_answer_with_file(frame, text):
    frame.active_session_turns = [{"question": "问题", "answer_md": text, "model": ""}]
    frame.answer_list.Clear()
    frame.answer_list.Append(text)
    frame.answer_meta = [("answer", 0, text, text)]
    frame.answer_list.SetSelection(0)


def test_answer_context_menu_contains_extract_file(frame, monkeypatch):
    _set_answer_with_file(frame, "见 d:\\123.txt")
    captured = {"items": []}
    monkeypatch.setattr(
        frame,
        "PopupMenu",
        lambda menu: captured.__setitem__(
            "items",
            [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
        ),
    )

    frame._show_answer_menu()

    assert "提取文件" in [label for label, _item_id in captured["items"]]


def test_extract_selected_answer_files_renders_file_names(frame):
    _set_answer_with_file(frame, "见 d:\\123.txt 和 `D:/code/file/report final.pdf`")

    assert frame._extract_files_from_selected_answer() is True

    assert frame.extracted_file_ids == ["d:\\123.txt", "D:/code/file/report final.pdf"]
    assert frame.extracted_file_list.GetString(0) == "123.txt"
    assert frame.extracted_file_list.GetString(1) == "report final.pdf"


def test_extracted_file_menu_contains_add_and_send(frame, monkeypatch):
    _set_answer_with_file(frame, "见 d:\\123.txt")
    frame._extract_files_from_selected_answer()
    captured = {"items": []}
    monkeypatch.setattr(
        frame,
        "PopupMenu",
        lambda menu: captured.__setitem__(
            "items",
            [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
        ),
    )

    frame._show_extracted_file_menu()

    assert [label for label, _item_id in captured["items"]] == ["添加到文件管理", "发送到手机"]


def test_add_selected_extracted_file_to_manager(frame, tmp_path, monkeypatch):
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    _set_answer_with_file(frame, str(source))
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    frame._extract_files_from_selected_answer()
    frame.extracted_file_list.SetSelection(0)

    assert frame._add_selected_extracted_file_to_manager() is True

    records = frame.file_library.list_records()
    assert len(records) == 1
    assert records[0].name == "123.txt"


def test_send_selected_extracted_file_to_phone_publishes_offer(frame, tmp_path, monkeypatch):
    source = tmp_path / "send-extracted.txt"
    source.write_text("payload", encoding="utf-8")
    _set_answer_with_file(frame, str(source))
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))
    frame._extract_files_from_selected_answer()
    frame.extracted_file_list.SetSelection(0)

    assert frame._send_selected_extracted_file_to_phone() is True
    assert len(published) == 1
    assert published[0]["type"] == "file_offer"
    assert published[0]["body"]["name"] == "send-extracted.txt"


def test_application_key_on_answer_list_shows_answer_menu(frame, monkeypatch):
    _set_answer_with_file(frame, "见 d:\\123.txt")
    seen = {"shown": 0}
    monkeypatch.setattr(frame, "_show_answer_menu", lambda: seen.__setitem__("shown", seen["shown"] + 1))

    event = type(
        "Event",
        (),
        {
            "GetKeyCode": lambda self: wx.WXK_MENU,
            "ControlDown": lambda self: False,
            "AltDown": lambda self: False,
            "ShiftDown": lambda self: False,
            "Skip": lambda self: None,
        },
    )()
    frame._on_answer_key_down(event)

    assert seen["shown"] == 1


def test_windows_application_key_on_answer_list_shows_answer_menu(frame, monkeypatch):
    _set_answer_with_file(frame, "见 d:\\123.txt")
    seen = {"shown": 0}
    monkeypatch.setattr(frame, "_show_answer_menu", lambda: seen.__setitem__("shown", seen["shown"] + 1))

    event = type(
        "Event",
        (),
        {
            "GetKeyCode": lambda self: wx.WXK_WINDOWS_MENU,
            "ControlDown": lambda self: False,
            "AltDown": lambda self: False,
            "ShiftDown": lambda self: False,
            "Skip": lambda self: None,
        },
    )()
    frame._on_answer_key_down(event)

    assert seen["shown"] == 1


def test_windows_application_key_on_extracted_file_list_shows_menu(frame, monkeypatch):
    _set_answer_with_file(frame, "见 d:\\123.txt")
    frame._extract_files_from_selected_answer()
    seen = {"shown": 0}
    monkeypatch.setattr(frame, "_show_extracted_file_menu", lambda: seen.__setitem__("shown", seen["shown"] + 1))

    event = type(
        "Event",
        (),
        {
            "GetKeyCode": lambda self: wx.WXK_WINDOWS_MENU,
            "Skip": lambda self: None,
        },
    )()
    frame._on_extracted_file_key_down(event)

    assert seen["shown"] == 1


def test_send_selected_extracted_file_to_phone_marks_waiting_confirmation(frame, tmp_path, monkeypatch):
    source = tmp_path / "waiting-extracted.txt"
    source.write_text("payload", encoding="utf-8")
    _set_answer_with_file(frame, str(source))
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: None)
    frame._extract_files_from_selected_answer()
    frame.extracted_file_list.SetSelection(0)

    assert frame._send_selected_extracted_file_to_phone() is True

    records = frame.file_library.list_records()
    assert len(records) == 1
    assert records[0].status.value == "waiting_confirmation"
