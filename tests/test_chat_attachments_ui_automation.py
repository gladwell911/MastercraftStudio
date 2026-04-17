import time

import wx

import main


def _key_event(key_code, *, ctrl=False, alt=False):
    class _Event:
        def GetKeyCode(self):
            return key_code

        def ControlDown(self):
            return ctrl

        def AltDown(self):
            return alt

        def Skip(self):
            return None

        def StopPropagation(self):
            return None

    return _Event()


def test_ui_automation_alt_menu_exposes_load_image_or_file(frame, monkeypatch):
    frame.Show()
    captured = {"items": []}
    monkeypatch.setattr(
        frame,
        "PopupMenu",
        lambda menu, *_args: captured.__setitem__(
            "items",
            [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
        ),
    )

    frame._show_tools_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "载入图片或文件" in labels


def test_ui_automation_answer_list_attachment_opens_on_enter(frame, monkeypatch, tmp_path):
    frame.Show()
    attachment = tmp_path / "opened-by-enter.txt"
    attachment.write_text("hello", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "opened-by-enter.txt 文件已成功上传",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {
                    "name": attachment.name,
                    "path": str(attachment),
                    "kind": "file",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(attachment),
                }
            ],
        }
    ]
    frame._render_answer_list()
    attachment_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "attachment")
    frame.answer_list.SetSelection(attachment_row)
    frame.answer_list.SetFocus()
    opened = {}
    monkeypatch.setattr(main.os, "startfile", lambda path: opened.__setitem__("path", path), raising=False)

    frame._on_answer_key_down(_key_event(wx.WXK_RETURN))

    assert opened["path"] == str(attachment)


def test_ui_automation_uploaded_image_uses_single_success_row(frame, tmp_path):
    frame.Show()
    attachment = tmp_path / "clipboard_image.png"
    attachment.write_text("img", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {
                    "name": attachment.name,
                    "path": str(attachment),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(attachment),
                }
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows == ["我", "图片上传成功"]


def test_ui_automation_attachment_only_turn_keeps_my_and_assistant_rows(frame, tmp_path):
    frame.Show()
    attachment = tmp_path / "attachment-only.png"
    attachment.write_text("img", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "已收到图片",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "attachments": [
                {
                    "name": attachment.name,
                    "path": str(attachment),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(attachment),
                }
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows == ["我", "图片上传成功", "小诸葛", "已收到图片"]
