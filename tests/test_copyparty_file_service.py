from urllib.parse import unquote, urlsplit
from urllib import request
import importlib.util
import socket

import file_transfer
from file_transfer import CopypartyFileService, DesktopFileLibrary


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
    )

    service.start()

    command = captured["command"]
    assert command[command.index("-i") + 1] == "0.0.0.0"
    assert service.base_url == "http://192.168.50.23:49233"


def test_copyparty_urls_are_filename_based_and_escaped(tmp_path):
    source = tmp_path / "hello report.txt"
    source.write_text("payload", encoding="utf-8")
    library = DesktopFileLibrary(tmp_path / "storage")
    record = library.add_local_file(source)
    service = CopypartyFileService(library, host="127.0.0.1", port=49232, wait_for_ready=False)

    assert service.download_url_for(record) == "http://127.0.0.1:49232/hello%20report.txt"
    upload_url = service.upload_url_for("hello report.txt")
    assert upload_url.startswith("http://127.0.0.1:49232/")
    assert unquote(urlsplit(upload_url).path) == "/hello report (1).txt"


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
    assert service.download_url_for(record) == "https://rc.tingyou.cc/cloud%20file.txt"
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
