import json
import main
import pytest
import wx
from main_test_support import _Story13KeyEvent
from pathlib import Path


def test_chat_frame_initializes_common_commands_store(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "resolve_notes_data_dir", lambda: tmp_path / "notes")
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_merge_legacy_archived_chats", lambda self: None)

    frame = main.ChatFrame()
    frame.Hide()
    try:
        assert frame.common_commands_store is not None
        assert frame.common_commands_store.path == tmp_path / "common_commands.json"
        assert frame.common_commands_store.path.exists()
    finally:
        frame.Destroy()


def test_common_commands_path_uses_personal_onedrive_only_when_packaged(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path / "personal"))
    monkeypatch.setenv("OneDrive", str(tmp_path / "other"))
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    expected = tmp_path / "personal" / "OneDrive" / "code" / "data" / "sj" / "common_commands.json"
    expected.parent.mkdir(parents=True)
    expected.write_text('{}', encoding="utf-8")
    assert main.resolve_common_commands_path() == expected
    monkeypatch.delattr(main.sys, "frozen")
    assert main.resolve_common_commands_path() == tmp_path / "common_commands.json"


def test_packaged_common_commands_path_rejects_missing_onedrive(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    with pytest.raises(RuntimeError, match="未检测到个人版 OneDrive 路径"):
        main.resolve_common_commands_path()


def test_packaged_common_commands_path_rejects_unsynced_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path / "personal"))
    with pytest.raises(RuntimeError, match="未找到 OneDrive 常用命令文件"):
        main.resolve_common_commands_path()


def test_remote_common_commands_list_returns_fixture_contract(frame, monkeypatch):
    fixture = json.loads((Path("tests") / "fixtures" / "common_commands_snapshot.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(
        frame.common_commands_store,
        "read_snapshot",
        lambda: main.CommonCommandsSnapshot.from_dict(fixture),
    )

    status, body = frame._remote_api_common_commands_list_ui()

    assert status == 200
    assert body == fixture


def test_remote_common_commands_list_read_error_is_deterministic(frame, monkeypatch):
    monkeypatch.setattr(
        frame.common_commands_store,
        "read_snapshot",
        lambda: (_ for _ in ()).throw(main.CommonCommandsReadError(frame.common_commands_store.path, "broken")),
    )

    status, body = frame._remote_api_common_commands_list_ui()

    assert status == 500
    assert body == {"accepted": False, "error": "common_commands_unavailable"}


def test_remote_common_commands_payload_matches_frozen_shape(frame):
    fixture = json.loads((Path("tests") / "fixtures" / "common_commands_snapshot.json").read_text(encoding="utf-8"))

    payload = frame._remote_common_commands_snapshot_payload(
        main.CommonCommandsSnapshot.from_dict(fixture)
    )

    assert payload == fixture


def test_remote_common_commands_create_is_idempotent_for_same_device_request(frame):
    payload = {
        "device_id": "desktop-a",
        "request_id": "req-a",
        "title": "List Files",
        "content": "dir",
    }

    first_status, first_body = frame._remote_api_common_commands_create_ui(payload)
    second_status, second_body = frame._remote_api_common_commands_create_ui(payload)

    assert first_status == 200
    assert second_status == 200
    assert first_body["accepted"] is True
    assert first_body["result"] == "created"
    assert second_body["result"] == "replayed"
    assert second_body["revision"] == first_body["revision"]
    assert second_body["commands"] == first_body["commands"]
    assert first_body["revision"] == 1
    assert len(first_body["commands"]) == 1
    assert first_body["commands"][0]["title"] == "List Files"


def test_remote_common_commands_create_returns_snapshot_body(frame):
    status, body = frame._remote_api_common_commands_create_ui(
        {"title": "Run Tests", "content": "pytest -q", "observed_revision": 0}
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["result"] == "created"
    assert body["revision"] == 1
    assert body["commands"][0]["title"] == "Run Tests"
    assert body["commands"][0]["content"] == "pytest -q"


def test_remote_common_commands_create_rejects_stale_revision_with_409(frame):
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Existing", content="dir")
    )

    status, body = frame._remote_api_common_commands_create_ui(
        {
            "title": "Run Tests",
            "content": "pytest -q",
            "observed_revision": 0,
        }
    )

    assert status == 409
    assert body == {
        "accepted": False,
        "error": "stale_state",
        "current_revision": 1,
        "observed_revision": 0,
    }


def test_remote_common_commands_create_replays_duplicate_even_with_stale_observed_revision(frame):
    first_status, first_body = frame._remote_api_common_commands_create_ui(
        {
            "device_id": "desktop-a",
            "request_id": "req-a",
            "title": "List Files",
            "content": "dir",
            "observed_revision": 0,
        }
    )

    second_status, second_body = frame._remote_api_common_commands_create_ui(
        {
            "device_id": "desktop-a",
            "request_id": "req-a",
            "title": "List Files Changed",
            "content": "dir /b",
            "observed_revision": 0,
        }
    )

    assert first_status == 200
    assert second_status == 200
    assert first_body["result"] == "created"
    assert second_body["result"] == "replayed"
    assert second_body["revision"] == 1
    assert second_body["commands"] == first_body["commands"]


def test_remote_common_commands_update_rejects_stale_revision_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )

    status, body = frame._remote_api_common_commands_update_ui(
        {
            "id": created.id,
            "title": "List Files Updated",
            "content": "dir /b",
            "observed_revision": 0,
            "observed_version": created.version,
        }
    )

    assert status == 409
    assert body == {
        "accepted": False,
        "error": "stale_state",
        "current_revision": 1,
        "observed_revision": 0,
    }


def test_remote_common_commands_update_rejects_stale_version_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )
    updated = frame.common_commands_store.update_command(
        created.id,
        main.CommonCommandUpdate(expected_version=created.version, content="dir /b"),
    )

    status, body = frame._remote_api_common_commands_update_ui(
        {
            "id": created.id,
            "title": "List Files Updated",
            "content": "dir /a",
            "observed_revision": 2,
            "observed_version": created.version,
        }
    )

    assert updated.version == 2
    assert status == 409
    assert body == {
        "accepted": False,
        "error": "stale_state",
        "current_revision": 2,
        "observed_revision": 2,
        "current_version": 2,
        "observed_version": 1,
    }


def test_remote_common_commands_update_returns_snapshot_body(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )

    status, body = frame._remote_api_common_commands_update_ui(
        {
            "id": created.id,
            "title": "Run Tests",
            "content": "pytest -q",
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["revision"] == 2
    assert body["commands"][0]["title"] == "Run Tests"
    assert body["commands"][0]["content"] == "pytest -q"
    assert body["commands"][0]["version"] == 2


def test_remote_common_commands_delete_rejects_stale_revision_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )

    status, body = frame._remote_api_common_commands_delete_ui(
        {
            "id": created.id,
            "observed_revision": 0,
            "observed_version": created.version,
        }
    )

    assert status == 409
    assert body == {
        "accepted": False,
        "error": "stale_state",
        "current_revision": 1,
        "observed_revision": 0,
    }


def test_remote_common_commands_delete_rejects_stale_version_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )
    updated = frame.common_commands_store.update_command(
        created.id,
        main.CommonCommandUpdate(expected_version=created.version, content="dir /b"),
    )

    status, body = frame._remote_api_common_commands_delete_ui(
        {
            "id": created.id,
            "observed_revision": 2,
            "observed_version": created.version,
        }
    )

    assert updated.version == 2
    assert status == 409
    assert body == {
        "accepted": False,
        "error": "stale_state",
        "current_revision": 2,
        "observed_revision": 2,
        "current_version": 2,
        "observed_version": 1,
    }


def test_remote_common_commands_delete_returns_snapshot_body(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )

    status, body = frame._remote_api_common_commands_delete_ui(
        {
            "id": created.id,
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body == {"accepted": True, "revision": 2, "commands": []}


def test_remote_common_commands_pin_returns_snapshot_body(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="List Files", content="dir")
    )

    status, body = frame._remote_api_common_commands_pin_ui(
        {
            "id": created.id,
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["revision"] == 2
    assert body["commands"][0]["pinned"] is True


def test_remote_common_commands_move_up_returns_section_local_snapshot(frame):
    first = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    second = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )
    third = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Third", content="echo third")
    )
    frame.common_commands_store.pin_command(third.id, expected_version=third.version)

    status, body = frame._remote_api_common_commands_move_up_ui(
        {
            "id": second.id,
            "observed_revision": 4,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert [item["title"] for item in body["commands"]] == ["Third", "Second", "First"]
    assert [item["pinned"] for item in body["commands"]] == [True, False, False]


def test_remote_common_commands_move_down_returns_section_local_snapshot(frame):
    first = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    second = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )
    third = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Third", content="echo third")
    )
    frame.common_commands_store.pin_command(first.id, expected_version=first.version)

    status, body = frame._remote_api_common_commands_move_down_ui(
        {
            "id": second.id,
            "observed_revision": 4,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["result"] == "updated"
    assert [item["title"] for item in body["commands"]] == ["First", "Third", "Second"]
    assert [item["pinned"] for item in body["commands"]] == [True, False, False]


def test_remote_common_commands_move_up_noop_at_section_top_returns_unchanged_snapshot(frame):
    first = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )

    status, body = frame._remote_api_common_commands_move_up_ui(
        {
            "id": first.id,
            "observed_revision": 2,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["result"] == "updated"
    assert body["revision"] == 2
    assert [item["title"] for item in body["commands"]] == ["First", "Second"]


def test_remote_common_commands_move_down_noop_at_section_bottom_returns_unchanged_snapshot(frame):
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    second = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )

    status, body = frame._remote_api_common_commands_move_down_ui(
        {
            "id": second.id,
            "observed_revision": 2,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["result"] == "updated"
    assert body["revision"] == 2
    assert [item["title"] for item in body["commands"]] == ["First", "Second"]


def test_remote_common_commands_move_down_rejects_stale_revision_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )

    status, body = frame._remote_api_common_commands_move_down_ui(
        {
            "id": created.id,
            "observed_revision": 0,
            "observed_version": 1,
        }
    )

    assert status == 409
    assert body["accepted"] is False
    assert body["error"] == "stale_state"
    assert body["current_revision"] == 1
    assert body["observed_revision"] == 0


def test_remote_common_commands_move_down_rejects_stale_version_with_409(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )
    updated = frame.common_commands_store.update_command(
        created.id,
        main.CommonCommandUpdate(
            expected_version=created.version,
            title="Second updated",
            content="echo second updated",
        ),
    )

    status, body = frame._remote_api_common_commands_move_down_ui(
        {
            "id": updated.id,
            "observed_revision": 2,
            "observed_version": 1,
        }
    )

    assert status == 409
    assert body["accepted"] is False
    assert body["error"] == "stale_state"
    assert body["current_revision"] == 2
    assert body["current_version"] == 2
    assert body["observed_version"] == 1


def test_remote_common_commands_move_down_accepts_command_id_payload(frame):
    first = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    second = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )

    status, body = frame._remote_api_common_commands_move_down_ui(
        {
            "command_id": first.id,
            "observed_revision": 2,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert [item["title"] for item in body["commands"]] == ["Second", "First"]


def test_remote_common_commands_update_accepts_command_id_payload(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )

    status, body = frame._remote_api_common_commands_update_ui(
        {
            "command_id": created.id,
            "title": "First edited",
            "content": "echo first edited",
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["commands"][0]["title"] == "First edited"


def test_remote_common_commands_delete_accepts_command_id_payload(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )

    status, body = frame._remote_api_common_commands_delete_ui(
        {
            "command_id": created.id,
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body == {"accepted": True, "revision": 2, "commands": []}


def test_remote_common_commands_pin_accepts_command_id_payload(frame):
    created = frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )

    status, body = frame._remote_api_common_commands_pin_ui(
        {
            "command_id": created.id,
            "observed_revision": 1,
            "observed_version": 1,
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["commands"][0]["pinned"] is True


def test_remote_common_commands_create_read_error_is_deterministic(frame, monkeypatch):
    monkeypatch.setattr(
        frame.common_commands_store,
        "create_command",
        lambda _data: (_ for _ in ()).throw(main.CommonCommandsReadError(frame.common_commands_store.path, "broken")),
    )

    status, body = frame._remote_api_common_commands_create_ui(
        {"title": "List Files", "content": "dir"}
    )

    assert status == 500
    assert body == {"accepted": False, "error": "common_commands_unavailable"}


def test_common_command_edit_dialog_constructs_without_parent_assertion(frame):
    dlg = main.CommonCommandEditDialog(
        frame,
        dialog_title="添加常用命令",
        initial_title="标题",
        initial_content="echo test",
    )
    try:
        assert dlg.title_edit.GetValue() == "标题"
        assert dlg.content_edit.GetValue() == "echo test"
        assert dlg.FindWindowById(wx.ID_OK) is not None
        assert dlg.FindWindowById(wx.ID_CANCEL) is not None
    finally:
        if dlg:
            dlg.Destroy()


def test_common_command_edit_dialog_save_and_cancel_buttons_end_modal(frame, monkeypatch):
    dlg = main.CommonCommandEditDialog(
        frame,
        dialog_title="添加常用命令",
        initial_content="echo test",
    )
    try:
        closed = []
        monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda self, code: closed.append(code))

        ok_button = dlg.FindWindowById(wx.ID_OK)
        cancel_button = dlg.FindWindowById(wx.ID_CANCEL)
        assert ok_button is not None
        assert cancel_button is not None

        ok_event = wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_OK)
        ok_event.SetEventObject(ok_button)
        assert ok_button.ProcessEvent(ok_event)
        assert closed == [wx.ID_OK]

        cancel_event = wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_CANCEL)
        cancel_event.SetEventObject(cancel_button)
        assert cancel_button.ProcessEvent(cancel_event)
        assert closed == [wx.ID_OK]
    finally:
        if dlg:
            dlg.Destroy()


@pytest.mark.parametrize("shift", [False, True])
def test_common_command_edit_dialog_content_enter_is_native(frame, monkeypatch, shift):
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add")
    try:
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.content_edit)
        dlg._has_ime_composition = lambda: False
        event = _Story13KeyEvent(shift=shift)
        dlg._on_char_hook(event)
        assert event.skipped == 1
    finally: dlg.Destroy()


@pytest.mark.parametrize("kind", ["ctrl_enter", "alt_s", "button"])
def test_common_command_edit_dialog_save_paths_share_once_latch(frame, monkeypatch, kind):
    calls, closed = [], []
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="echo", on_save=lambda *v: calls.append(v) or True)
    try:
        monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda self, code: closed.append(code))
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.content_edit)
        dlg._has_ime_composition = lambda: False
        if kind == "ctrl_enter": dlg._on_char_hook(_Story13KeyEvent(ctrl=True))
        elif kind == "alt_s": dlg._on_char_hook(_Story13KeyEvent(ord("S"), alt=True))
        else: dlg._on_save()
        dlg._on_save()
        assert len(calls) == 1 and closed == [wx.ID_OK]
    finally: dlg.Destroy()


def test_common_command_edit_dialog_validation_and_exception_are_retryable(frame, monkeypatch):
    outcomes = [RuntimeError("write failed"), True]
    calls = []
    def save(*values):
        calls.append(values); result = outcomes.pop(0)
        if isinstance(result, Exception): raise result
        return result
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add", on_save=save)
    try:
        monkeypatch.setattr(main.wx, "MessageBox", lambda *a, **k: wx.ID_OK)
        closed = []; monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda self, code: closed.append(code))
        dlg.content_edit.SetValue("   "); dlg._on_save(); assert calls == [] and closed == []
        dlg.content_edit.SetValue(" exact "); dlg.content_edit.SetSelection(1, 4); dlg._on_save()
        assert dlg.content_edit.GetValue() == " exact " and dlg.content_edit.GetSelection() == (1, 4) and closed == []
        dlg._on_save(); assert len(calls) == 2 and closed == [wx.ID_OK]
    finally: dlg.Destroy()


@pytest.mark.parametrize("action", ["escape", "cancel", "close"])
@pytest.mark.parametrize("dirty,answer,closes", [(False, wx.NO, True), (True, wx.NO, False), (True, wx.YES, True)])
def test_common_command_edit_dialog_cancel_matrix(frame, monkeypatch, action, dirty, answer, closes):
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="initial")
    try:
        if dirty: dlg.content_edit.SetValue("changed")
        monkeypatch.setattr(main.wx, "MessageBox", lambda *a, **k: answer)
        closed = []; monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda self, code: closed.append(code))
        if action == "escape": dlg._has_ime_composition=lambda: False; dlg._on_char_hook(_Story13KeyEvent(wx.WXK_ESCAPE))
        elif action == "cancel": dlg._on_cancel()
        else:
            class Close:
                def Veto(self): pass
            dlg._on_close(Close())
        assert bool(closed) is closes
    finally: dlg.Destroy()


@pytest.mark.parametrize("cancel", [False, True])
def test_common_command_edit_dialog_endmodal_exception_recovers_latches(frame, monkeypatch, cancel):
    dlg=main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="x", on_save=lambda *v: True)
    try:
        monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda *_a: (_ for _ in ()).throw(RuntimeError("close failed")))
        monkeypatch.setattr(main.wx, "MessageBox", lambda *a, **k: wx.YES)
        if cancel: assert dlg._request_cancel() is False
        else: dlg._on_save()
        assert dlg._closing is False
        assert dlg._save_in_progress is False
    finally: dlg.Destroy()


def test_common_command_edit_dialog_callback_false_then_success_retains_state(frame, monkeypatch):
    outcomes=[False, True]; calls=[]
    dlg=main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="original", on_save=lambda *v: calls.append(v) or outcomes.pop(0))
    try:
        dlg.content_edit.SetValue(" exact "); dlg.content_edit.SetSelection(1, 4); dlg.content_edit.SetFocus()
        closed=[]; monkeypatch.setattr(main.wx.Dialog, "EndModal", lambda self, code: closed.append(code))
        dlg._on_save()
        assert dlg.content_edit.GetValue()==" exact " and dlg.content_edit.GetSelection()==(1,4)
        assert not dlg._save_in_progress and not dlg._closing and closed==[]
        dlg._on_save(); assert len(calls)==2 and closed==[wx.ID_OK]
    finally: dlg.Destroy()


def test_common_command_edit_dialog_declined_close_confirms_once_and_vetoes(frame, monkeypatch):
    dlg=main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="original")
    try:
        dlg.content_edit.SetValue("dirty"); confirmations=[]
        monkeypatch.setattr(main.wx, "MessageBox", lambda *a, **k: confirmations.append(True) or wx.NO)
        class Close:
            def __init__(self): self.vetoes=0
            def Veto(self): self.vetoes += 1
        event=Close(); dlg._on_close(event)
        assert confirmations==[True] and event.vetoes==1 and not dlg._closing
    finally: dlg.Destroy()


def test_alt_z_menu_shortcut_opens_common_commands_surface(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "_show_common_commands_surface", lambda: calls.append("open") or True)

    event = main.wx.CommandEvent(main.wx.wxEVT_MENU, int(frame._common_commands_menu_id))
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert calls == ["open"]


def test_app_menu_common_commands_item_uses_alt_z_shortcut(frame):
    menu_bar = frame.GetMenuBar()
    app_menu = menu_bar.GetMenu(0)
    menu_item = next(item for item in app_menu.GetMenuItems() if item.GetId() == int(frame._common_commands_menu_id))

    assert menu_item.GetItemLabelText() == "常用命令"
    assert "\tAlt+Z" in menu_item.GetItemLabel()


def test_common_command_send_returns_false_and_preserves_input_when_submit_fails(frame, monkeypatch):
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Send", content="echo send")
    )
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    frame.input_edit.SetValue("keep me")
    frame.send_button.Enable(False)
    seen = {}
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: (False, "broken"))
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, title, flags: seen.update({"message": message, "title": title, "flags": flags}))

    assert frame._send_selected_common_command() is False
    assert frame.input_edit.GetValue() == "keep me"
    assert seen["message"] == "broken"
