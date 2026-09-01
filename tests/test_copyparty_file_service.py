from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib import error, request
import importlib.util
import socket

import file_transfer
from file_transfer import CopypartyFileService, DesktopFileLibrary, file_transfer_token_for


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.wait_timeout = None
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        return 0

    def kill(self):
        self.killed = True


class ExitedProcess:
    def poll(self):
        return 1


def test_copyparty_service_starts_storage_only_volume(tmp_path):
    storage = tmp_path / "storage"
    library = DesktopFileLibrary(storage)
    captured = {}
    fake_process = FakeProcess()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return fake_process

    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=49231,
        process_factory=fake_popen,
        python_executable="python-test",
        wait_for_ready=False,
        use_copyparty_process=True,
    )

    service.start()

    command = captured["command"]
    assert command[:3] == ["python-test", "-m", "copyparty"]
    assert "-i" in command
    assert command[command.index("-i") + 1] == "127.0.0.1"
    assert "-p" in command
    assert command[command.index("-p") + 1] == "49231"
    assert "-v" in command
    assert command[command.index("-v") + 1] == f"{storage.resolve()}:/:rw"
    assert "D:\\" not in " ".join(command).replace(str(storage.resolve()), "")
    assert service.base_url == "http://127.0.0.1:49231"

    service.stop()

    assert fake_process.terminated is True
    assert fake_process.wait_timeout == 3.0


def test_copyparty_default_binds_all_interfaces_and_advertises_lan_host(tmp_path, monkeypatch):
    monkeypatch.setattr(file_transfer, "detect_lan_ip", lambda: "192.168.50.23")
    storage = tmp_path / "storage"
    library = DesktopFileLibrary(storage)
    captured = {}
    fake_process = FakeProcess()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return fake_process

    service = CopypartyFileService(
        library,
        port=49233,
        process_factory=fake_popen,
        python_executable="python-test",
        wait_for_ready=False,
        use_copyparty_process=True,
    )

    service.start()

    command = captured["command"]
    assert command[command.index("-i") + 1] == "0.0.0.0"
    assert service.base_url == "http://192.168.50.23:49233"
    assert service.local_base_url == "http://127.0.0.1:49233"


def test_copyparty_uses_safe_fallback_when_default_port_cannot_bind(tmp_path, monkeypatch):
    library = DesktopFileLibrary(tmp_path / "storage")
    service = CopypartyFileService(library, port=3923, wait_for_ready=False)
    attempted = []

    def can_bind(port):
        attempted.append(port)
        return port == 49300

    monkeypatch.setattr(service, "_can_bind_port", can_bind)

    service._select_bindable_port()

    assert attempted[:2] == [3923, 49300]
    assert service.port == 49300
    assert service.local_base_url.endswith(":49300")


def test_copyparty_bind_probe_rejects_a_real_listening_port(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        occupied_port = int(listener.getsockname()[1])
        service = CopypartyFileService(
            DesktopFileLibrary(tmp_path / "storage"),
            host="127.0.0.1",
            port=occupied_port,
        )

        assert service._can_bind_port(occupied_port) is False


def test_embedded_file_service_retries_after_actual_bind_race(tmp_path, monkeypatch):
    attempted = []

    class _FakeServer:
        def serve_forever(self):
            return None

        def shutdown(self):
            return None

        def server_close(self):
            return None

    def server_factory(address, _handler):
        attempted.append(address)
        if address[1] == 3923:
            raise OSError("port taken after probe")
        return _FakeServer()

    monkeypatch.setattr(file_transfer, "FILE_SERVICE_FALLBACK_PORTS", (49300,))
    monkeypatch.setattr(file_transfer, "ThreadingHTTPServer", server_factory)
    service = CopypartyFileService(DesktopFileLibrary(tmp_path / "storage"), port=3923)

    service.start()
    try:
        assert attempted == [("0.0.0.0", 3923), ("0.0.0.0", 49300)]
        assert service.port == 49300
    finally:
        service.stop()


def test_copyparty_default_starts_embedded_token_enforcing_service(tmp_path):
    source = tmp_path / "served.txt"
    source.write_text("served-payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    fake_process = FakeProcess()
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=_free_port(),
        process_factory=lambda _command, **_kwargs: fake_process,
        wait_for_ready=False,
    )

    service.start()
    try:
        assert fake_process.terminated is False
        with request.urlopen(service.download_url_for(record), timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"served-payload"
        try:
            request.urlopen(f"{service.base_url}/{quote(record.name)}", timeout=5)
        except error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("expected 403")
    finally:
        service.stop()


def test_copyparty_process_timeout_falls_back_to_embedded_and_stops_process(tmp_path, monkeypatch):
    source = tmp_path / "served.txt"
    source.write_text("served-payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    fake_process = FakeProcess()
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=_free_port(),
        process_factory=lambda _command, **_kwargs: fake_process,
        wait_for_ready=True,
        use_copyparty_process=True,
    )
    monkeypatch.setattr(service, "_wait_until_ready", lambda: (_ for _ in ()).throw(TimeoutError("not ready")))

    service.start()
    try:
        assert fake_process.terminated is True
        with request.urlopen(service.download_url_for(record), timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"served-payload"
    finally:
        service.stop()


def test_copyparty_urls_are_filename_based_and_escaped(tmp_path):
    source = tmp_path / "hello report.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = CopypartyFileService(library, host="127.0.0.1", port=49232, wait_for_ready=False)

    download_url = service.download_url_for(record)
    parsed_download = urlsplit(download_url)
    assert f"{parsed_download.scheme}://{parsed_download.netloc}{parsed_download.path}" == "http://127.0.0.1:49232/hello%20report.txt"
    assert parse_qs(parsed_download.query)["t"] == [file_transfer_token_for(record)]
    upload_url = service.upload_url_for("hello report.txt")
    assert upload_url.startswith("http://127.0.0.1:49232/")
    parsed_upload = urlsplit(upload_url)
    assert unquote(parsed_upload.path) == "/hello report (1).txt"
    upload_record = next(item for item in library.list_records() if item.name == "hello report (1).txt")
    assert parse_qs(parsed_upload.query)["t"] == [file_transfer_token_for(upload_record)]


def test_copyparty_publishes_cloudflare_public_urls_when_configured(tmp_path):
    source = tmp_path / "cloud file.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=49234,
        public_base_url="https://rc.tingyou.cc",
        wait_for_ready=False,
    )

    assert service.base_url == "https://rc.tingyou.cc"
    download_url = service.download_url_for(record)
    parsed_download = urlsplit(download_url)
    assert f"{parsed_download.scheme}://{parsed_download.netloc}{parsed_download.path}" == "https://rc.tingyou.cc/cloud%20file.txt"
    assert parse_qs(parsed_download.query)["t"] == [file_transfer_token_for(record)]
    upload_url = service.upload_url_for("cloud file.txt")
    assert upload_url.startswith("https://rc.tingyou.cc/")
    assert unquote(urlsplit(upload_url).path) == "/cloud file (1).txt"


def test_copyparty_service_serves_storage_files(tmp_path):
    if importlib.util.find_spec("copyparty") is None:
        return
    source = tmp_path / "served.txt"
    source.write_text("served-payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=_free_port(),
        wait_for_ready=True,
    )
    service.start()
    try:
        with request.urlopen(service.download_url_for(record), timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"served-payload"
    finally:
        service.stop()


def test_copyparty_service_falls_back_to_embedded_http_when_process_exits(tmp_path):
    source = tmp_path / "app-release (3).apk"
    source.write_bytes(b"apk-payload")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=_free_port(),
        process_factory=lambda _command, **_kwargs: ExitedProcess(),
        wait_for_ready=True,
    )
    service.start()
    try:
        with request.urlopen(service.download_url_for(record), timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"apk-payload"
        try:
            request.urlopen(f"{service.base_url}/{quote(record.name)}", timeout=5)
        except error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("expected 403")
    finally:
        service.stop()


def test_copyparty_service_accepts_put_uploads(tmp_path):
    if importlib.util.find_spec("copyparty") is None:
        return
    library = DesktopFileLibrary(tmp_path / "storage")
    service = CopypartyFileService(
        library,
        host="127.0.0.1",
        port=_free_port(),
        wait_for_ready=True,
    )
    service.start()
    try:
        req = request.Request(
            service.upload_url_for("phone.txt"),
            data=b"from-phone",
            method="PUT",
        )
        with request.urlopen(req, timeout=5) as response:
            assert 200 <= response.status < 300
    finally:
        service.stop()

    assert (library.storage_dir / "phone.txt").read_bytes() == b"from-phone"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
