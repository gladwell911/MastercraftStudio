from pathlib import Path

from file_transfer import (
    DesktopFileLibrary,
    FileDirection,
    FileTransferRecord,
    FileTransferStatus,
    extract_windows_file_paths,
    unique_destination_path,
)


def test_extract_windows_file_paths_from_plain_markdown_and_quotes():
    text = (
        "请查看 d:\\123.txt 和 `D:/code/file/report final.pdf`。\n"
        "另一个路径是 \"c:\\Users\\gladwell\\Desktop\\截图.png\"。"
    )

    paths = extract_windows_file_paths(text)

    assert paths == [
        "d:\\123.txt",
        "D:/code/file/report final.pdf",
        "c:\\Users\\gladwell\\Desktop\\截图.png",
    ]


def test_unique_destination_path_keeps_existing_files(tmp_path):
    (tmp_path / "report.txt").write_text("old", encoding="utf-8")
    (tmp_path / "report (1).txt").write_text("old 1", encoding="utf-8")

    target = unique_destination_path(tmp_path, "report.txt")

    assert target == tmp_path / "report (2).txt"


def test_desktop_file_library_adds_files_newest_first_without_overwrite(tmp_path):
    source_a = tmp_path / "source-a.txt"
    source_b = tmp_path / "source-b.txt"
    source_a.write_text("a", encoding="utf-8")
    source_b.write_text("b", encoding="utf-8")
    storage = tmp_path / "storage"
    library = DesktopFileLibrary(storage)

    first = library.add_local_file(source_a, now=10.0)
    second = library.add_local_file(source_b, now=20.0)

    assert [record.id for record in library.list_records()] == [second.id, first.id]
    assert first.stored_path == storage / "source-a.txt"
    assert second.stored_path == storage / "source-b.txt"
    assert first.stored_path.read_text(encoding="utf-8") == "a"
    assert second.stored_path.read_text(encoding="utf-8") == "b"


def test_desktop_file_library_syncs_external_storage_files(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    uploaded = storage / "phone.txt"
    uploaded.write_text("from-phone", encoding="utf-8")
    library = DesktopFileLibrary(storage)

    records = library.sync_storage_dir(direction=FileDirection.PHONE_TO_DESKTOP)

    assert [record.name for record in records] == ["phone.txt"]
    assert records[0].direction == FileDirection.PHONE_TO_DESKTOP
    assert records[0].status == FileTransferStatus.COMPLETED
    assert records[0].transferred_bytes == len("from-phone")


def test_transfer_record_label_includes_progress_and_speed_for_active_transfer():
    record = FileTransferRecord(
        id="file-1",
        name="video.mp4",
        stored_path=Path("D:/code/file/video.mp4"),
        size_bytes=1000,
        direction=FileDirection.PHONE_TO_DESKTOP,
        status=FileTransferStatus.TRANSFERRING,
        created_at=1.0,
        transferred_bytes=250,
        speed_bytes_per_second=500,
    )

    assert record.label() == "video.mp4 - 正在传输 25% 500 B/s"


def test_delete_record_removes_metadata_and_stored_file(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)

    assert record.stored_path.exists()
    assert library.delete_record(record.id) is True

    assert library.list_records() == []
    assert not record.stored_path.exists()


def test_update_record_status_replaces_existing_record_without_reordering_unrelated_items(tmp_path):
    source_a = tmp_path / "a.txt"
    source_b = tmp_path / "b.txt"
    source_a.write_text("a", encoding="utf-8")
    source_b.write_text("bb", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    first = library.add_local_file(source_a, now=10.0)
    second = library.add_local_file(source_b, now=20.0)

    updated = library.update_record_status(
        first.id,
        FileTransferStatus.TRANSFERRING,
        transferred_bytes=1,
        speed_bytes_per_second=128,
    )

    assert updated is not None
    assert updated.id == first.id
    assert updated.status == FileTransferStatus.TRANSFERRING
    assert updated.transferred_bytes == 1
    assert updated.speed_bytes_per_second == 128
    assert [record.id for record in library.list_records()] == [second.id, first.id]


def test_update_record_status_returns_none_for_missing_record(tmp_path):
    library = DesktopFileLibrary(tmp_path / "storage")

    assert library.update_record_status("missing", FileTransferStatus.FAILED) is None


def test_prepare_incoming_upload_record_uses_non_conflicting_storage_path(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "phone.txt").write_text("old", encoding="utf-8")
    library = DesktopFileLibrary(storage)

    record = library.prepare_incoming_upload("phone.txt", size_bytes=20, now=30.0)

    assert record.name == "phone (1).txt"
    assert record.stored_path == storage / "phone (1).txt"
    assert record.direction == FileDirection.PHONE_TO_DESKTOP
    assert record.status == FileTransferStatus.ACCEPTED
    assert record.size_bytes == 20
    assert record.transferred_bytes == 0
    assert library.get_record(record.id) == record


def test_probe_path_returns_existing_file_metadata(tmp_path):
    target = tmp_path / "probe.txt"
    target.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")

    info = library.probe_path(target)

    assert info == {
        "exists": True,
        "path": str(target.resolve()),
        "name": "probe.txt",
        "size_bytes": 7,
    }


def test_probe_path_reports_missing_file(tmp_path):
    missing = tmp_path / "missing.txt"
    library = DesktopFileLibrary(tmp_path / "storage")

    info = library.probe_path(missing)

    assert info == {
        "exists": False,
        "path": str(missing),
        "name": "missing.txt",
        "size_bytes": 0,
    }
