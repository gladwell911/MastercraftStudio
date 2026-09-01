from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


DEFAULT_DESKTOP_FILE_STORAGE_DIR = Path("D:/code/file")
FILE_TRANSFER_TOKEN_QUERY_PARAM = "t"
DEFAULT_MAX_FILE_TRANSFER_BYTES = 512 * 1024 * 1024
FILE_TRANSFER_CHUNK_SIZE = 1024 * 1024
FILE_SERVICE_FALLBACK_PORTS = tuple(range(49300, 49321))


def detect_lan_ip() -> str:
    override = str(os.environ.get("DESKTOP_FILE_SERVICE_PUBLIC_HOST") or "").strip()
    if override:
        return override
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = str(sock.getsockname()[0] or "").strip()
            if host and not host.startswith("127."):
                return host
    except OSError:
        pass
    return socket.gethostbyname(socket.gethostname())


class FileTransferStatus(StrEnum):
    WAITING_CONFIRMATION = "waiting_confirmation"
    ACCEPTED = "accepted"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def label(self) -> str:
        return {
            FileTransferStatus.WAITING_CONFIRMATION: "等待确认",
            FileTransferStatus.ACCEPTED: "已确认",
            FileTransferStatus.TRANSFERRING: "正在传输",
            FileTransferStatus.COMPLETED: "已完成",
            FileTransferStatus.REJECTED: "已拒绝",
            FileTransferStatus.FAILED: "失败",
            FileTransferStatus.CANCELED: "已取消",
        }[self]


class FileDirection(StrEnum):
    DESKTOP_TO_PHONE = "desktop_to_phone"
    PHONE_TO_DESKTOP = "phone_to_desktop"
    LOCAL = "local"


@dataclass(frozen=True)
class FileTransferRecord:
    id: str
    name: str
    stored_path: Path
    size_bytes: int
    direction: FileDirection
    status: FileTransferStatus
    created_at: float
    transferred_bytes: int = 0
    speed_bytes_per_second: int = 0
    error_message: str = ""
    access_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))

    @property
    def percent(self) -> int:
        if self.size_bytes <= 0:
            return 0
        return max(0, min(100, int((self.transferred_bytes / self.size_bytes) * 100)))

    def label(self) -> str:
        if self.status == FileTransferStatus.TRANSFERRING:
            return f"{self.name} - {self.status.label} {self.percent}% {_format_speed(self.speed_bytes_per_second)}"
        if self.status == FileTransferStatus.FAILED and self.error_message:
            return f"{self.name} - {self.status.label}：{self.error_message}"
        return f"{self.name} - {self.status.label}"

    def with_status(self, status: FileTransferStatus, **changes) -> "FileTransferRecord":
        return replace(self, status=status, **changes)


def _format_speed(bytes_per_second: int) -> str:
    value = max(0, int(bytes_per_second or 0))
    if value < 1024:
        return f"{value} B/s"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB/s"
    return f"{value / (1024 * 1024):.1f} MB/s"


_QUOTED_WINDOWS_PATH_RE = re.compile(r"[`\"']([A-Za-z]:[\\/][^`\"'\r\n<>|?*]+)[`\"']")
_PLAIN_WINDOWS_PATH_RE = re.compile(r"(?<![\w])([A-Za-z]:[\\/][^\s`\"'<>|?*]+)")


def extract_windows_file_paths(text: str) -> list[str]:
    source = str(text or "")
    results: list[str] = []
    seen: set[str] = set()
    matches: list[tuple[int, str]] = []

    def _append(raw_value: str) -> None:
        raw = raw_value.strip().rstrip("。；，,;")
        if not raw:
            return
        key = raw.lower().replace("/", "\\")
        if key in seen:
            return
        seen.add(key)
        results.append(raw)

    for match in _QUOTED_WINDOWS_PATH_RE.finditer(source):
        matches.append((match.start(1), match.group(1)))
    masked = _QUOTED_WINDOWS_PATH_RE.sub(" ", source)
    for match in _PLAIN_WINDOWS_PATH_RE.finditer(masked):
        matches.append((match.start(1), match.group(1)))
    for _pos, raw in sorted(matches, key=lambda item: item[0]):
        _append(raw)
    return results


def unique_destination_path(storage_dir: Path | str, filename: str) -> Path:
    base_dir = Path(storage_dir)
    name = Path(str(filename or "file")).name or "file"
    candidate = base_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    idx = 1
    while True:
        next_candidate = base_dir / f"{stem} ({idx}){suffix}"
        if not next_candidate.exists():
            return next_candidate
        idx += 1


def file_transfer_token_for(record: FileTransferRecord) -> str:
    return str(record.access_token or "")


def append_file_transfer_token(url: str, record: FileTransferRecord) -> str:
    parsed = urlsplit(str(url or ""))
    query = [
        item
        for item in parse_qsl(parsed.query, keep_blank_values=True)
        if item[0] != FILE_TRANSFER_TOKEN_QUERY_PARAM
    ]
    query.append((FILE_TRANSFER_TOKEN_QUERY_PARAM, file_transfer_token_for(record)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def is_valid_file_transfer_token(record: FileTransferRecord, token: str | None) -> bool:
    expected = file_transfer_token_for(record)
    provided = str(token or "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def file_transfer_token_from_url(path: str) -> str:
    parsed = urlsplit(str(path or ""))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return str(query.get(FILE_TRANSFER_TOKEN_QUERY_PARAM) or "")


def _content_length_from_headers(handler: BaseHTTPRequestHandler) -> int | None:
    raw_value = handler.headers.get("Content-Length")
    if raw_value is None:
        return None
    try:
        return max(0, int(raw_value))
    except ValueError:
        return None


def _stream_file_to_handler(source: Path, handler: BaseHTTPRequestHandler) -> None:
    with source.open("rb") as reader:
        while True:
            chunk = reader.read(FILE_TRANSFER_CHUNK_SIZE)
            if not chunk:
                break
            handler.wfile.write(chunk)


def _write_request_body_to_file(
    handler: BaseHTTPRequestHandler,
    target: Path,
    content_length: int,
) -> int:
    remaining = max(0, int(content_length or 0))
    written = 0
    with target.open("wb") as writer:
        while remaining > 0:
            chunk = handler.rfile.read(min(FILE_TRANSFER_CHUNK_SIZE, remaining))
            if not chunk:
                break
            writer.write(chunk)
            written += len(chunk)
            remaining -= len(chunk)
    return written


class DesktopFileLibrary:
    def __init__(self, storage_dir: Path | str = DEFAULT_DESKTOP_FILE_STORAGE_DIR):
        self.storage_dir = Path(storage_dir)
        self._records: list[FileTransferRecord] = []

    def list_records(self) -> list[FileTransferRecord]:
        return sorted(self._records, key=lambda record: record.created_at, reverse=True)

    def sync_storage_dir(self, *, direction: FileDirection = FileDirection.PHONE_TO_DESKTOP) -> list[FileTransferRecord]:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        known_paths = {record.stored_path.resolve() for record in self._records if record.stored_path.exists()}
        added: list[FileTransferRecord] = []
        for path in sorted(self.storage_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in known_paths:
                continue
            size = path.stat().st_size
            record = FileTransferRecord(
                id=f"file-{uuid.uuid4().hex}",
                name=path.name,
                stored_path=path,
                size_bytes=size,
                direction=direction,
                status=FileTransferStatus.COMPLETED,
                created_at=path.stat().st_mtime,
                transferred_bytes=size,
            )
            self._records.append(record)
            known_paths.add(resolved)
            added.append(record)
        return added

    def add_local_file(
        self,
        source_path: Path | str,
        *,
        now: float | None = None,
        direction: FileDirection = FileDirection.LOCAL,
        status: FileTransferStatus = FileTransferStatus.COMPLETED,
    ) -> FileTransferRecord:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        target = unique_destination_path(self.storage_dir, source.name)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        created_at = time.time() if now is None else float(now)
        record = FileTransferRecord(
            id=f"file-{uuid.uuid4().hex}",
            name=target.name,
            stored_path=target,
            size_bytes=target.stat().st_size,
            direction=direction,
            status=status,
            created_at=created_at,
            transferred_bytes=target.stat().st_size if status == FileTransferStatus.COMPLETED else 0,
        )
        self._records.append(record)
        return record

    def add_record(self, record: FileTransferRecord) -> FileTransferRecord:
        self._records = [item for item in self._records if item.id != record.id]
        self._records.append(record)
        return record

    def prepare_incoming_upload(
        self,
        filename: str,
        *,
        size_bytes: int = 0,
        now: float | None = None,
    ) -> FileTransferRecord:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        target = unique_destination_path(self.storage_dir, filename)
        record = FileTransferRecord(
            id=f"file-{uuid.uuid4().hex}",
            name=target.name,
            stored_path=target,
            size_bytes=max(0, int(size_bytes or 0)),
            direction=FileDirection.PHONE_TO_DESKTOP,
            status=FileTransferStatus.ACCEPTED,
            created_at=time.time() if now is None else float(now),
            transferred_bytes=0,
        )
        return self.add_record(record)

    def get_record(self, record_id: str) -> FileTransferRecord | None:
        target = str(record_id or "").strip()
        for record in self._records:
            if record.id == target:
                return record
        return None

    def update_record_status(
        self,
        record_id: str,
        status: FileTransferStatus,
        **changes,
    ) -> FileTransferRecord | None:
        record = self.get_record(record_id)
        if record is None:
            return None
        allowed = {
            "name",
            "stored_path",
            "size_bytes",
            "direction",
            "created_at",
            "transferred_bytes",
            "speed_bytes_per_second",
            "error_message",
        }
        normalized_changes = {key: value for key, value in changes.items() if key in allowed}
        updated = record.with_status(status, **normalized_changes)
        self.add_record(updated)
        return updated

    def delete_record(self, record_id: str) -> bool:
        record = self.get_record(record_id)
        if record is None:
            return False
        self._records = [item for item in self._records if item.id != record.id]
        try:
            record.stored_path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def probe_path(self, path: Path | str) -> dict:
        target = Path(path)
        if target.is_file():
            resolved = target.resolve()
            return {
                "exists": True,
                "path": str(resolved),
                "name": resolved.name,
                "size_bytes": resolved.stat().st_size,
            }
        return {
            "exists": False,
            "path": str(target),
            "name": target.name,
            "size_bytes": 0,
        }


class CopypartyFileService:
    def __init__(
        self,
        library: DesktopFileLibrary,
        *,
        host: str | None = None,
        advertised_host: str | None = None,
        port: int = 3923,
        process_factory=None,
        python_executable: str | None = None,
        public_base_url: str | None = None,
        wait_for_ready: bool = False,
        use_copyparty_process: bool | None = None,
    ):
        self.library = library
        self.host = host or "0.0.0.0"
        self.advertised_host = advertised_host or (detect_lan_ip() if host is None else self.host)
        self.port = int(port)
        self.process_factory = process_factory or subprocess.Popen
        self.python_executable = python_executable or sys.executable
        self.public_base_url = str(public_base_url or "").strip().rstrip("/")
        self.wait_for_ready = bool(wait_for_ready)
        if use_copyparty_process is None:
            use_copyparty_process = str(os.environ.get("DESKTOP_FILE_SERVICE_USE_COPYPARTY") or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
        self.use_copyparty_process = bool(use_copyparty_process)
        self._process = None
        self._embedded_server: ThreadingHTTPServer | None = None
        self._embedded_thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url
        return f"http://{self.advertised_host}:{self.port}"

    @property
    def local_base_url(self) -> str:
        connect_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{connect_host}:{self.port}"

    def set_public_base_url(self, public_base_url: str | None) -> None:
        self.public_base_url = str(public_base_url or "").strip().rstrip("/")

    def command(self) -> list[str]:
        storage = self.library.storage_dir.resolve()
        return [
            self.python_executable,
            "-m",
            "copyparty",
            "-i",
            self.host,
            "-p",
            str(self.port),
            "-v",
            f"{storage}:/:rw",
            "-q",
            "--no-robots",
            "--force-js",
            "--no-thumb",
            "--no-logues",
            "--no-readme",
            "--dotpart",
            "--unpost",
            "0",
            "--no-del",
            "--no-mv",
        ]

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if self._embedded_server is not None:
            return
        if not self.use_copyparty_process:
            self._start_embedded_http_server()
            return
        self._select_bindable_port()
        self.library.storage_dir.mkdir(parents=True, exist_ok=True)
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = self.process_factory(self.command(), **kwargs)
        if self.wait_for_ready:
            try:
                self._wait_until_ready()
            except (RuntimeError, TimeoutError):
                process = self._process
                self._process = None
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3.0)
                self._start_embedded_http_server()

    def _can_bind_port(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((self.host, int(port)))
            return True
        except OSError:
            return False

    def _select_bindable_port(self) -> None:
        if self._can_bind_port(self.port):
            return
        for candidate in FILE_SERVICE_FALLBACK_PORTS:
            if self._can_bind_port(candidate):
                self.port = candidate
                return
        raise RuntimeError(
            f"文件服务端口 {self.port} 不可绑定，且未找到可用回退端口。"
        )

    def stop(self) -> None:
        self._stop_embedded_http_server()
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3.0)

    def download_url_for(self, record: FileTransferRecord) -> str:
        return append_file_transfer_token(f"{self.base_url}/{quote(Path(record.name).name)}", record)

    def upload_url_for(self, filename: str) -> str:
        target = unique_destination_path(self.library.storage_dir, filename)
        record = self._upload_record_for_name(target.name)
        if record is None:
            record = self.library.prepare_incoming_upload(target.name)
        return append_file_transfer_token(f"{self.base_url}/{quote(record.name)}", record)

    def _record_for_storage_name(self, filename: str) -> FileTransferRecord | None:
        target_name = Path(str(filename or "")).name
        for record in self.library.list_records():
            if Path(record.name).name == target_name:
                return record
        return None

    def _upload_record_for_name(self, filename: str) -> FileTransferRecord | None:
        target_name = Path(str(filename or "")).name
        for record in self.library.list_records():
            if (
                Path(record.name).name == target_name
                and record.direction == FileDirection.PHONE_TO_DESKTOP
                and record.status == FileTransferStatus.ACCEPTED
            ):
                return record
        return None

    def _start_embedded_http_server(self) -> None:
        if self._embedded_server is not None:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                service._handle_embedded_get(self)

            def do_HEAD(self):  # noqa: N802
                service._handle_embedded_get(self, include_body=False)

            def do_PUT(self):  # noqa: N802
                service._handle_embedded_put(self)

            def log_message(self, _format, *args):
                return

        self.library.storage_dir.mkdir(parents=True, exist_ok=True)
        last_error: OSError | None = None
        candidates = (self.port, *FILE_SERVICE_FALLBACK_PORTS)
        for candidate in dict.fromkeys(candidates):
            try:
                server = ThreadingHTTPServer((self.host, int(candidate)), Handler)
            except OSError as exc:
                last_error = exc
                continue
            self.port = int(candidate)
            self._embedded_server = server
            break
        if self._embedded_server is None:
            raise RuntimeError("文件服务没有可绑定端口。") from last_error
        self._embedded_thread = threading.Thread(target=self._embedded_server.serve_forever, daemon=True)
        self._embedded_thread.start()

    def _stop_embedded_http_server(self) -> None:
        server = self._embedded_server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._embedded_thread is not None:
            self._embedded_thread.join(timeout=2.0)
        self._embedded_server = None
        self._embedded_thread = None

    def _handle_embedded_get(self, handler: BaseHTTPRequestHandler, *, include_body: bool = True) -> None:
        filename = Path(unquote(urlsplit(handler.path).path.lstrip("/")).strip()).name
        target = self.library.storage_dir / filename
        if not filename or not target.is_file():
            self._send_embedded_text(handler, HTTPStatus.NOT_FOUND, "not_found")
            return
        record = self._record_for_storage_name(filename)
        if record is None or not is_valid_file_transfer_token(record, file_transfer_token_from_url(handler.path)):
            self._send_embedded_text(handler, HTTPStatus.FORBIDDEN, "forbidden")
            return
        size = target.stat().st_size
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(size))
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        handler.end_headers()
        if include_body:
            _stream_file_to_handler(target, handler)

    def _handle_embedded_put(self, handler: BaseHTTPRequestHandler) -> None:
        filename = Path(unquote(urlsplit(handler.path).path.lstrip("/")).strip()).name or "upload.bin"
        content_length = _content_length_from_headers(handler)
        if content_length is None:
            self._send_embedded_text(handler, HTTPStatus.LENGTH_REQUIRED, "length_required")
            return
        if content_length > DEFAULT_MAX_FILE_TRANSFER_BYTES:
            self._send_embedded_text(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large")
            return
        record = self._upload_record_for_name(filename)
        if record is None or not is_valid_file_transfer_token(record, file_transfer_token_from_url(handler.path)):
            self._send_embedded_text(handler, HTTPStatus.FORBIDDEN, "forbidden")
            return
        self.library.storage_dir.mkdir(parents=True, exist_ok=True)
        written = _write_request_body_to_file(handler, record.stored_path, content_length)
        if written != content_length:
            record.stored_path.unlink(missing_ok=True)
            self._send_embedded_text(handler, HTTPStatus.BAD_REQUEST, "incomplete_upload")
            return
        updated = self.library.update_record_status(
            record.id,
            FileTransferStatus.COMPLETED,
            size_bytes=written,
            transferred_bytes=written,
        )
        self._send_embedded_text(handler, HTTPStatus.CREATED, (updated or record).name)

    def _send_embedded_text(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, text: str) -> None:
        data = str(text or "").encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _wait_until_ready(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError("copyparty exited before accepting connections")
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise TimeoutError(f"copyparty did not start on {self.host}:{self.port}")


class DesktopFileHttpService:
    def __init__(self, library: DesktopFileLibrary, *, host: str = "127.0.0.1", port: int = 0):
        self.library = library
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            return f"http://{self.host}:{self.port}"
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                service._handle_get(self)

            def do_PUT(self):  # noqa: N802
                service._handle_put(self)

            def log_message(self, _format, *args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    def download_url_for(self, record: FileTransferRecord) -> str:
        return append_file_transfer_token(f"{self.base_url}/files/{quote(record.id)}", record)

    def upload_url_for(self, record: FileTransferRecord | str) -> str:
        if isinstance(record, FileTransferRecord):
            upload_record = record
        else:
            upload_record = self.library.prepare_incoming_upload(str(record or "upload.bin"))
        return append_file_transfer_token(f"{self.base_url}/uploads/{quote(upload_record.id)}", upload_record)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlsplit(handler.path).path
        prefix = "/files/"
        if not path.startswith(prefix):
            self._send_text(handler, HTTPStatus.NOT_FOUND, "not_found")
            return
        record_id = unquote(path[len(prefix):]).strip()
        record = self.library.get_record(record_id)
        if record is None or not record.stored_path.is_file():
            self._send_text(handler, HTTPStatus.NOT_FOUND, "not_found")
            return
        if not is_valid_file_transfer_token(record, file_transfer_token_from_url(handler.path)):
            self._send_text(handler, HTTPStatus.FORBIDDEN, "forbidden")
            return
        size = record.stored_path.stat().st_size
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(size))
        handler.send_header("Content-Disposition", f'attachment; filename="{record.name}"')
        handler.end_headers()
        _stream_file_to_handler(record.stored_path, handler)

    def _handle_put(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlsplit(handler.path).path
        prefix = "/uploads/"
        if not path.startswith(prefix):
            self._send_text(handler, HTTPStatus.NOT_FOUND, "not_found")
            return
        record_id = unquote(path[len(prefix):]).strip()
        record = self.library.get_record(record_id)
        if record is None or record.direction != FileDirection.PHONE_TO_DESKTOP or record.status != FileTransferStatus.ACCEPTED:
            self._send_text(handler, HTTPStatus.NOT_FOUND, "not_found")
            return
        if not is_valid_file_transfer_token(record, file_transfer_token_from_url(handler.path)):
            self._send_text(handler, HTTPStatus.FORBIDDEN, "forbidden")
            return
        content_length = _content_length_from_headers(handler)
        if content_length is None:
            self._send_text(handler, HTTPStatus.LENGTH_REQUIRED, "length_required")
            return
        if content_length > DEFAULT_MAX_FILE_TRANSFER_BYTES:
            self._send_text(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large")
            return
        self.library.storage_dir.mkdir(parents=True, exist_ok=True)
        written = _write_request_body_to_file(handler, record.stored_path, content_length)
        if written != content_length:
            record.stored_path.unlink(missing_ok=True)
            self._send_text(handler, HTTPStatus.BAD_REQUEST, "incomplete_upload")
            return
        updated = self.library.update_record_status(
            record.id,
            FileTransferStatus.COMPLETED,
            size_bytes=written,
            transferred_bytes=written,
        )
        self._send_text(handler, HTTPStatus.CREATED, (updated or record).name)

    def _send_text(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, text: str) -> None:
        data = str(text or "").encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
