from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import wx

from file_transfer import FileDirection, FileTransferStatus


def _menu_labels(menu):
    return [item.GetItemLabelText() for item in menu.GetMenuItems() if not item.IsSeparator()]


def _assert_url_path_and_token(url: str, expected_base_path: str) -> None:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == expected_base_path
    assert query.get("t")


def test_alt_app_menu_contains_file_manager(frame):
    menu_bar = frame.GetMenuBar()
    app_menu = menu_bar.GetMenu(0)

    assert "文件管理" in _menu_labels(app_menu)


def test_alt_app_menu_contains_previous_tools_actions(frame):
    menu_bar = frame.GetMenuBar()
    app_menu = menu_bar.GetMenu(0)

    labels = _menu_labels(app_menu)

    assert "语音通话设置" in labels
    assert "载入图片或文件" in labels
    assert "过滤英文内容" in labels
    filter_items = [
        item
        for item in app_menu.GetMenuItems()
        if not item.IsSeparator() and item.GetItemLabelText() == "过滤英文内容"
    ]
    assert len(filter_items) == 1
    assert filter_items[0].IsCheckable()


def test_show_file_manager_uses_answer_list_style_listbox(frame):
    frame._show_file_manager()

    assert isinstance(frame.file_manager_list, wx.ListBox)
    assert frame.file_manager_list.GetName() == "文件管理"
    assert frame.file_manager_list_model.control is frame.file_manager_list
    assert frame.file_manager_list.IsShown()


def test_file_manager_lists_records_newest_first(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    first_source = tmp_path / "first.txt"
    second_source = tmp_path / "second.txt"
    first_source.write_text("first", encoding="utf-8")
    second_source.write_text("second", encoding="utf-8")
    first = frame.file_library.add_local_file(first_source, now=10.0)
    second = frame.file_library.add_local_file(second_source, now=20.0, direction=FileDirection.PHONE_TO_DESKTOP)

    frame._show_file_manager()

    assert frame.file_manager_ids == [second.id, first.id]
    assert frame.file_manager_list.GetString(0).startswith("second.txt")
    assert frame.file_manager_list.GetString(1).startswith("first.txt")


def test_file_manager_context_menu_contains_required_actions(frame, monkeypatch):
    captured = {"items": []}
    monkeypatch.setattr(
        frame,
        "PopupMenu",
        lambda menu: captured.__setitem__(
            "items",
            [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
        ),
    )

    frame._show_file_manager_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert labels == ["添加文件", "发送到手机", "删除"]


def test_file_manager_enter_opens_selected_file(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "open.txt"
    source.write_text("open", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    opened = []
    monkeypatch.setattr(frame, "_open_path_with_default_app", lambda path: opened.append(Path(path)) or True)

    frame._show_file_manager()
    frame.file_manager_list.SetSelection(frame.file_manager_ids.index(record.id))

    assert frame._open_selected_file_manager_record() is True
    assert opened == [record.stored_path]


def test_file_manager_delete_removes_record_and_file(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "delete.txt"
    source.write_text("delete", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._show_file_manager()
    frame.file_manager_list.SetSelection(frame.file_manager_ids.index(record.id))

    assert frame._delete_selected_file_manager_record() is True
    assert frame.file_manager_ids == []
    assert not record.stored_path.exists()


def test_file_offer_for_selected_file_manager_record(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "offer.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    frame.file_service = type(
        "FakeFileService",
        (),
        {"download_url_for": lambda _self, item: f"http://127.0.0.1:18080/{item.name}"},
    )()

    offer = frame._file_offer_for_record(record)

    assert offer["type"] == "file_offer"
    assert offer["body"]["file_id"] == record.id
    assert offer["body"]["name"] == "offer.txt"
    assert offer["body"]["size_bytes"] == 7
    assert offer["body"]["download_url"] == "http://127.0.0.1:18080/offer.txt"


def test_send_selected_file_to_phone_publishes_file_offer(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "send.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    frame._show_file_manager()
    frame.file_manager_list.SetSelection(frame.file_manager_ids.index(record.id))

    assert frame._send_selected_file_to_phone() is True
    assert len(published) == 1
    assert published[0]["type"] == "file_offer"
    assert published[0]["body"]["file_id"] == record.id
    assert frame.file_library.get_record(record.id).status == FileTransferStatus.WAITING_CONFIRMATION


def test_send_selected_file_to_phone_repairs_public_file_route(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    calls = []
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: calls.append("proxy") or True)
    monkeypatch.setattr(
        frame,
        "_ensure_cloudflared_service_url",
        lambda port: calls.append(("service", port)) or (True, False),
    )
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: calls.append("start") or True)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda _payload: None)

    frame._show_file_manager()
    frame.file_manager_list.SetSelection(frame.file_manager_ids.index(record.id))

    assert frame._send_selected_file_to_phone() is True
    assert "proxy" in calls
    assert ("service", 18080) in calls


def test_file_offer_does_not_fall_back_to_local_file_url_when_public_route_unavailable(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: False)

    offer = frame._file_offer_for_record(record)

    assert offer["body"]["download_url"] == ""
    assert "公网文件通道不可用" in offer["body"]["error"]


def test_send_selected_file_to_phone_fails_when_public_route_unavailable(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: False)
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    frame._show_file_manager()
    frame.file_manager_list.SetSelection(frame.file_manager_ids.index(record.id))

    assert frame._send_selected_file_to_phone() is False
    assert published == []
    updated = frame.file_library.get_record(record.id)
    assert updated.status == FileTransferStatus.FAILED
    assert "公网文件通道不可用" in updated.error_message


def test_send_selected_extracted_file_to_phone_fails_when_public_route_unavailable(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: False)
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    frame._show_file_manager()
    frame.extracted_file_list.Show()
    frame.extracted_file_list_model.replace_visible_page([(str(source), source.name)], selected_id=str(source))
    frame.extracted_file_ids = list(frame.extracted_file_list_model.visible_ids)

    assert frame._send_selected_extracted_file_to_phone() is False
    assert published == []
    records = frame.file_library.list_records()
    assert len(records) == 1
    assert records[0].status == FileTransferStatus.FAILED
    assert "公网文件通道不可用" in records[0].error_message


def test_windows_application_key_on_file_manager_shows_menu(frame, monkeypatch):
    seen = {"shown": 0}
    monkeypatch.setattr(frame, "_show_file_manager_menu", lambda: seen.__setitem__("shown", seen["shown"] + 1))

    event = type(
        "Event",
        (),
        {
            "GetKeyCode": lambda self: wx.WXK_WINDOWS_MENU,
            "Skip": lambda self: None,
        },
    )()
    frame._on_file_manager_key_down(event)

    assert seen["shown"] == 1


def test_remote_file_accept_updates_desktop_record(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "accept.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)

    status, body = frame._remote_api_file_command_ui({
        "type": "file_accept",
        "body": {"file_id": record.id},
    })

    assert status == 200
    assert body["accepted"] is True
    assert frame.file_library.get_record(record.id).status == FileTransferStatus.ACCEPTED


def test_remote_file_progress_updates_desktop_record(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "progress.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)

    status, body = frame._remote_api_file_command_ui({
        "type": "file_progress",
        "body": {
            "file_id": record.id,
            "transferred_bytes": 3,
            "speed_bytes_per_second": 42,
        },
    })

    updated = frame.file_library.get_record(record.id)
    assert status == 200
    assert body["accepted"] is True
    assert updated.status == FileTransferStatus.TRANSFERRING
    assert updated.transferred_bytes == 3
    assert updated.speed_bytes_per_second == 42


def test_remote_file_probe_existing_path_publishes_offer(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "probe.txt"
    source.write_text("payload", encoding="utf-8")
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    status, body = frame._remote_api_file_command_ui({
        "type": "file_probe",
        "body": {"path": str(source)},
    })

    assert status == 200
    assert body["accepted"] is True
    assert body["exists"] is True
    assert len(published) == 1
    assert published[0]["type"] == "file_offer"
    assert published[0]["body"]["name"] == "probe.txt"


def test_remote_file_probe_uses_cloudflare_download_url(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "cloud-probe.txt"
    source.write_text("payload", encoding="utf-8")
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: True)

    status, body = frame._remote_api_file_command_ui({
        "type": "file_probe",
        "body": {"path": str(source)},
    })

    assert status == 200
    _assert_url_path_and_token(body["download_url"], "https://rc.tingyou.cc/cloud-probe.txt")
    _assert_url_path_and_token(published[0]["body"]["download_url"], "https://rc.tingyou.cc/cloud-probe.txt")


def test_remote_file_probe_fails_when_public_route_unavailable(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "123.txt"
    source.write_text("payload", encoding="utf-8")
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: False)

    status, body = frame._remote_api_file_command_ui({
        "type": "file_probe",
        "body": {"path": str(source)},
    })

    assert status == 503
    assert body["accepted"] is False
    assert body["exists"] is True
    assert body["download_url"] == ""
    assert "公网文件通道不可用" in body["error"]
    assert published == []


def test_remote_file_upload_request_prepares_upload_url(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    frame.file_service = type(
        "FakeFileService",
        (),
        {"upload_url_for": lambda _self, name: f"http://127.0.0.1:18080/{name}"},
    )()

    status, body = frame._remote_api_file_command_ui({
        "type": "file_upload_request",
        "body": {"name": "phone.txt", "size_bytes": 12},
    })

    records = frame.file_library.list_records()
    assert status == 200
    assert body["accepted"] is True
    assert body["upload_url"] == "http://127.0.0.1:18080/phone.txt"
    assert body["file_id"] == records[0].id
    assert records[0].name == "phone.txt"
    assert records[0].direction == FileDirection.PHONE_TO_DESKTOP


def test_remote_file_upload_request_uses_cloudflare_upload_url(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")

    status, body = frame._remote_api_file_command_ui({
        "type": "file_upload_request",
        "body": {"name": "phone.txt", "size_bytes": 12},
    })

    assert status == 200
    assert body["accepted"] is True
    _assert_url_path_and_token(body["upload_url"], "https://rc.tingyou.cc/phone.txt")


def test_remote_file_list_returns_records(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "listed.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)

    status, body = frame._remote_api_file_command_ui({"type": "file_list", "body": {}})

    assert status == 200
    assert body["accepted"] is True
    assert body["files"][0]["file_id"] == record.id
    assert body["files"][0]["name"] == "listed.txt"


def test_remote_file_add_adds_existing_path(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "added.txt"
    source.write_text("payload", encoding="utf-8")

    status, body = frame._remote_api_file_command_ui({"type": "file_add", "body": {"path": str(source)}})

    records = frame.file_library.list_records()
    assert status == 200
    assert body["accepted"] is True
    assert body["file_id"] == records[0].id
    assert records[0].name == "added.txt"


def test_remote_file_download_request_aliases_probe(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "download-request.txt"
    source.write_text("payload", encoding="utf-8")
    published = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    status, body = frame._remote_api_file_command_ui({"type": "file_download_request", "body": {"path": str(source)}})

    assert status == 200
    assert body["accepted"] is True
    assert body["download_url"] == published[0]["body"]["download_url"]


def test_remote_file_delete_removes_record_and_file(frame, tmp_path, monkeypatch):
    monkeypatch.setattr(frame.file_library, "storage_dir", tmp_path / "storage")
    source = tmp_path / "delete-remote.txt"
    source.write_text("payload", encoding="utf-8")
    record = frame.file_library.add_local_file(source)

    status, body = frame._remote_api_file_command_ui({"type": "file_delete", "body": {"file_id": record.id}})

    assert status == 200
    assert body["accepted"] is True
    assert frame.file_library.get_record(record.id) is None
    assert not record.stored_path.exists()
