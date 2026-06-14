from urllib import request

from file_transfer import DesktopFileHttpService, DesktopFileLibrary


def test_http_service_downloads_library_record(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        with request.urlopen(f"{service.base_url}/files/{record.id}", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"payload"
            assert response.headers["Content-Disposition"] == 'attachment; filename="report.txt"'
    finally:
        service.stop()


def test_http_service_uploads_without_overwriting(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "upload.txt").write_text("old", encoding="utf-8")
    library = DesktopFileLibrary(storage)
    service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    service.start()
    try:
        req = request.Request(
            f"{service.base_url}/uploads/upload.txt",
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
