import main
import time


def test_story_1_3_attachment_only_keyboard_and_button_admission(frame, monkeypatch):
    frame.Show(); frame.input_edit.SetValue(""); frame._pending_input_attachments=[{"path":"x"}]
    calls=[]; frame._submit_question=lambda value, **kw: calls.append(value) or (True, "")
    frame.input_edit.SetFocus(); assert frame._request_question_submit() is True
    frame._pending_input_attachments=[{"path":"x"}]
    assert frame._request_question_submit(require_focus=False) is True
    assert calls == ["", ""]


def test_on_done_keeps_attachment_only_turn_answer_at_bottom(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "done-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CLAUDECODE_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)

    frame._on_done(0, "最终回答", "", main.DEFAULT_CLAUDECODE_MODEL, "", "chat-1")

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert meta_types[1] == "time"
    assert rows[2:] == ["我", "图片上传成功", "小诸葛", "最终回答"]
    assert meta_types[2:] == ["user", "attachment", "ai", "answer"]


def test_on_done_extracts_received_file_attachment_from_cli_text(frame, monkeypatch, tmp_path):
    file_path = tmp_path / "from-claudecode.txt"
    file_path.write_text("received", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_session_turns = [
        {
            "question": "请生成文件",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CLAUDECODE_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.active_turn_idx = 0
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)

    frame._on_done(0, f"已生成文件：{file_path}", "", main.DEFAULT_CLAUDECODE_MODEL, "", "chat-1")

    attachments = frame.active_session_turns[0].get("received_attachments") or []
    assert attachments == [
        {
            "name": "from-claudecode.txt",
            "path": str(file_path),
            "kind": "file",
            "direction": "incoming",
            "status": "success",
            "open_path": str(file_path),
            "source": main.DEFAULT_CLAUDECODE_MODEL,
        }
    ]


def test_input_key_down_ctrl_v_pastes_clipboard_attachments_when_input_has_focus(frame):
    seen = {"pasted": 0}
    frame.input_edit.SetFocus()
    frame._try_paste_clipboard_attachments_to_input = lambda: seen.__setitem__("pasted", seen["pasted"] + 1) or True

    class E:
        def GetKeyCode(self):
            return ord("V")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] = True

    frame._on_input_key_down(E())

    assert seen["pasted"] == 1


def test_clear_recovery_is_cancel_only_and_text_snapshot_ignores_attachments(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "ui-recovery.db")
    store.initialize()
    frame.chat_store = store
    frame._chat_store_enabled = True
    operations = {}
    for choice in ("retry", "text-only", "cancel"):
        owner = f"choice-{choice}"
        store.upsert_chat({"id": owner})
        store.replace_turns(owner, [{"question": "payload"}])
        op = store.begin_clear_operation(owner, idempotency_key=choice)
        store.transition_clear_operation(op["operation_id"], "resend_blocked", failure_code="TIMEOUT")
        operations[choice] = op
        assert frame.recover_clear_operation(op["operation_id"], choice) is (choice == "cancel")
    assert store.get_clear_operation(operations["retry"]["operation_id"])["state"] == "resend_blocked"
    assert store.get_clear_operation(operations["text-only"]["operation_id"])["state"] == "resend_blocked"
    assert store.get_clear_operation(operations["cancel"]["operation_id"])["state"] == "completed_clear_only"

    missing = tmp_path / "missing.txt"
    store.upsert_chat({"id": "missing"})
    store.replace_turns("missing", [{"question": "with file", "attachments": [{"path": str(missing)}]}])
    op = store.begin_clear_operation("missing", idempotency_key="missing")
    assert "attachments" not in op["snapshot"]
