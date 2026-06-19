from urllib import error, request

import file_transfer
from file_transfer import DesktopFileHttpService, DesktopFileLibrary


def test_http_service_downloads_library_record(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        with request.urlopen(service.download_url_for(record), timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"payload"
            assert response.headers["Content-Disposition"] == 'attachment; filename="report.txt"'
    finally:
        service.stop()


def test_http_service_rejects_download_without_valid_token(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        for url in (
            f"{service.base_url}/files/{record.id}",
            f"{service.base_url}/files/{record.id}?t=wrong",
        ):
            try:
                request.urlopen(url, timeout=5)
            except error.HTTPError as exc:
                assert exc.code == 403
            else:
                raise AssertionError(f"expected 403 for {url}")
    finally:
        service.stop()


def test_http_service_uploads_without_overwriting(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "upload.txt").write_text("old", encoding="utf-8")
    library = DesktopFileLibrary(storage)
    record = library.prepare_incoming_upload("upload.txt", size_bytes=3)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        req = request.Request(
            service.upload_url_for(record),
            data=b"new",
            method="PUT",
        )
        with request.urlopen(req, timeout=5) as response:
            assert response.status == 201
            assert b"upload (1).txt" in response.read()
    finally:
        service.stop()

    assert (storage / "upload.txt").read_text(encoding="utf-8") == "old"
    assert (storage / "upload (1).txt").read_text(encoding="utf-8") == "new"
    completed = library.get_record(record.id)
    assert completed is not None
    assert completed.status.value == "completed"


def test_http_service_rejects_upload_without_valid_token(tmp_path):
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.prepare_incoming_upload("upload.txt", size_bytes=3)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        for url in (
            f"{service.base_url}/uploads/{record.id}",
            f"{service.base_url}/uploads/{record.id}?t=wrong",
        ):
            req = request.Request(url, data=b"new", method="PUT")
            try:
                request.urlopen(req, timeout=5)
            except error.HTTPError as exc:
                assert exc.code == 403
            else:
                raise AssertionError(f"expected 403 for {url}")
    finally:
        service.stop()

    assert not record.stored_path.exists()


def test_http_service_rejects_upload_over_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(file_transfer, "DEFAULT_MAX_FILE_TRANSFER_BYTES", 2)
    library = DesktopFileLibrary(tmp_path / "storage")
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        record = library.prepare_incoming_upload("upload.txt", size_bytes=7)
        req = request.Request(
            service.upload_url_for(record),
            data=b"toolarge",
            method="PUT",
        )
        try:
            request.urlopen(req, timeout=5)
        except error.HTTPError as exc:
            assert exc.code == 413
        else:
            raise AssertionError("expected 413")
    finally:
        service.stop()

    assert not (library.storage_dir / "upload.txt").exists()


def test_http_service_rejects_truncated_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(file_transfer, "_write_request_body_to_file", lambda _handler, _target, _length: 1)
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.prepare_incoming_upload("upload.txt", size_bytes=3)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        req = request.Request(
            service.upload_url_for(record),
            data=b"new",
            method="PUT",
        )
        try:
            request.urlopen(req, timeout=5)
        except error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
    finally:
        service.stop()

    assert not record.stored_path.exists()
    assert library.get_record(record.id).status.value == "accepted"
