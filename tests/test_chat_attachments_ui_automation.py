import threading
import time

import wx

import main
import mc_frame_requests


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


def test_claude_attachment_preflight_reads_in_background_and_rejects_edited_draft(frame, monkeypatch, tmp_path):
    path = tmp_path / "attachment.txt"
    path.write_text("payload", encoding="utf-8")
    read_started, release, queued = threading.Event(), threading.Event(), threading.Event()
    callbacks = []
    reader = mc_frame_requests.read_openrouter_claude_attachments

    def read(model, files):
        assert threading.current_thread() is not threading.main_thread()
        read_started.set()
        assert release.wait(3)
        return reader(model, files)

    def dispatch(fn, *args):
        callbacks.append((fn, args))
        queued.set()

    monkeypatch.setattr(mc_frame_requests, "read_openrouter_claude_attachments", read)
    monkeypatch.setattr(frame, "_call_after_if_alive", dispatch)
    frame.input_edit.SetValue("original draft")
    frame._pending_input_attachments = [{"path": str(path), "name": path.name}]
    turns_before = list(frame.active_session_turns)
    try:
        assert frame._submit_question("original draft", model="anthropic/claude-sonnet-4.6") == (True, "")
        assert read_started.wait(2)
        assert frame.input_edit.GetValue() == "original draft"
        assert frame.active_session_turns == turns_before
        frame.input_edit.SetValue("edited while checking")
        release.set()
        assert queued.wait(2)
        fn, args = callbacks.pop()
        fn(*args)
        assert frame.input_edit.GetValue() == "edited while checking"
        assert frame.active_session_turns == turns_before
        assert frame._pending_input_attachments[0]["path"] == str(path)
    finally:
        release.set()


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
    assert "\u8f7d\u5165\u56fe\u7247\u6216\u6587\u4ef6" in labels


def test_ui_automation_answer_list_attachment_opens_on_enter(frame, monkeypatch, tmp_path):
    frame.Show()
    attachment = tmp_path / "opened-by-enter.txt"
    attachment.write_text("hello", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "opened-by-enter.txt \u6587\u4ef6\u5df2\u6210\u529f\u4e0a\u4f20",
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
    assert len(rows) == 4
    assert rows[0] == "累计暂无"
    assert [meta[0] for meta in frame.answer_meta] == ["context_usage", "time", "user", "attachment"]


def test_ui_automation_attachment_only_turn_keeps_my_and_assistant_rows(frame, tmp_path):
    frame.Show()
    attachment = tmp_path / "attachment-only.png"
    attachment.write_text("img", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "\u5df2\u6536\u5230\u56fe\u7247",
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
    assert len(rows) == 6
    assert rows[0] == "累计暂无"
    assert [meta[0] for meta in frame.answer_meta] == ["context_usage", "time", "user", "attachment", "ai", "answer"]
