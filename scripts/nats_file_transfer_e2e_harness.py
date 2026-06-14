from __future__ import annotations

import json
import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from file_transfer import DesktopFileHttpService, DesktopFileLibrary
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from remote_nats_protocol import build_file_command_event


NATS_E2E_PORT_FALLBACKS = (4223, 4224, 4522)
NATS_E2E_WS_PORT_FALLBACKS = (18080, 18081, 18082, 8082)
CHAT_ID = "file-e2e"


def _can_bind_loopback_tcp_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return False
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", int(port)))
            return True
    except Exception:
        return False


def _choose_available_port(preferred_port: int, fallbacks: tuple[int, ...]) -> int:
    seen = set()
    for candidate in (preferred_port, *fallbacks):
        if candidate in seen or candidate <= 0:
            continue
        seen.add(candidate)
        if _can_bind_loopback_tcp_port(candidate):
            return candidate
    raise RuntimeError(f"no available loopback port found near {preferred_port}")


def main() -> None:
    token = os.environ.get("FILE_E2E_TOKEN", "file-e2e-token")
    pair_id = os.environ.get("FILE_E2E_PAIR_ID", "file-e2e")
    preferred_port = int(os.environ.get("FILE_E2E_PORT", "4222"))
    preferred_ws_port = int(os.environ.get("FILE_E2E_WS_PORT", "18080"))
    ready_file = Path(os.environ["FILE_E2E_READY_FILE"])
    result_file = Path(os.environ["FILE_E2E_RESULT_FILE"])
    desktop_content = os.environ.get("FILE_E2E_DESKTOP_CONTENT", "desktop-to-phone")
    desktop_name = os.environ.get("FILE_E2E_DESKTOP_NAME", "desktop-offer.txt")
    phone_upload_name = os.environ.get("FILE_E2E_PHONE_UPLOAD_NAME", "phone-upload.txt")
    phone_upload_content = os.environ.get("FILE_E2E_PHONE_UPLOAD_CONTENT", "phone-to-desktop")

    work_dir = Path(os.environ.get("FILE_E2E_APP_DATA", tempfile.mkdtemp(prefix="zgwd-file-e2e-")))
    storage_dir = work_dir / "storage"
    source_dir = work_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    desktop_source = source_dir / desktop_name
    desktop_source.write_text(desktop_content, encoding="utf-8")

    tcp_port = _choose_available_port(preferred_port, NATS_E2E_PORT_FALLBACKS)
    websocket_port = _choose_available_port(preferred_ws_port, NATS_E2E_WS_PORT_FALLBACKS)

    library = DesktopFileLibrary(storage_dir)
    desktop_record = library.add_local_file(desktop_source)
    http_service = DesktopFileHttpService(library, host="127.0.0.1", port=0)
    server = NatsServerProcess(
        NatsRuntimeConfig(
            app_data_dir=work_dir,
            token=token,
            host="0.0.0.0",
            port=tcp_port,
            websocket_host="127.0.0.1",
            websocket_port=websocket_port,
        )
    )
    transport: RemoteNatsTransport | None = None
    stop = threading.Event()
    desktop_download_completed = threading.Event()
    phone_upload_received = threading.Event()
    offer_accepted = threading.Event()
    seen: dict[str, object] = {
        "desktop_file_id": desktop_record.id,
        "desktop_download_completed": False,
        "desktop_download_progress_events": 0,
        "phone_upload_received": False,
        "phone_upload_name": "",
        "phone_upload_content": "",
        "errors": [],
    }

    def _on_signal(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    def on_file_command(payload: dict) -> tuple[int, dict]:
        command_type = str(payload.get("type") or "")
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        if command_type == "file_accept":
            if str(body.get("file_id") or "") == desktop_record.id:
                offer_accepted.set()
            return 200, {"ok": True}
        if command_type == "file_progress":
            if str(body.get("file_id") or "") == desktop_record.id:
                seen["desktop_download_progress_events"] = int(seen["desktop_download_progress_events"]) + 1
            return 200, {"ok": True}
        if command_type == "file_complete":
            if str(body.get("file_id") or "") == desktop_record.id:
                seen["desktop_download_completed"] = True
                desktop_download_completed.set()
            return 200, {"ok": True}
        if command_type == "file_error":
            seen["errors"].append(dict(body))
            return 200, {"ok": True}
        if command_type == "file_upload_request":
            name = Path(str(body.get("name") or phone_upload_name)).name or phone_upload_name
            return 200, {
                "ok": True,
                "upload_url": f"{http_service.base_url}/uploads/{quote(name)}",
            }
        return 404, {"error": "unsupported_file_command", "type": command_type}

    try:
        http_service.start()
        server.start(timeout=15)
        transport = RemoteNatsTransport(
            pair_id=pair_id,
            token=token,
            on_file_command=on_file_command,
        )
        transport.start_threaded(f"nats://127.0.0.1:{tcp_port}", timeout=15)

        ready_payload = {
            "tcp_port": tcp_port,
            "websocket_port": websocket_port,
            "http_port": int(http_service.base_url.rsplit(":", 1)[1]),
            "endpoint": f"ws://127.0.0.1:{websocket_port}/nats",
            "token": token,
            "pair_id": pair_id,
            "desktop_file_id": desktop_record.id,
            "desktop_file_name": desktop_record.name,
            "desktop_content": desktop_content,
            "phone_upload_name": phone_upload_name,
            "phone_upload_content": phone_upload_content,
            "storage_dir": str(storage_dir),
        }
        ready_file.write_text(json.dumps(ready_payload, ensure_ascii=False), encoding="utf-8")

        def _publish_offer_until_seen() -> None:
            while not stop.is_set() and not offer_accepted.is_set() and not desktop_download_completed.is_set():
                offer = build_file_command_event(
                    request_id="",
                    event_type="file_offer",
                    device_id="desktop-harness",
                    chat_id=CHAT_ID,
                    body={
                        "file_id": desktop_record.id,
                        "name": desktop_record.name,
                        "size_bytes": desktop_record.size_bytes,
                        "direction": "desktop_to_phone",
                        "status": "pending",
                        "download_url": f"{http_service.base_url}/files/{desktop_record.id}",
                    },
                )
                transport.publish_event_threadsafe(offer)
                stop.wait(1.0)

        threading.Thread(target=_publish_offer_until_seen, name="file-offer-publisher", daemon=True).start()

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not stop.wait(0.2):
            matches = sorted(storage_dir.glob(phone_upload_name))
            matches.extend(sorted(storage_dir.glob(f"{Path(phone_upload_name).stem} (*{Path(phone_upload_name).suffix}")))
            for candidate in matches:
                if candidate.is_file() and candidate.read_text(encoding="utf-8") == phone_upload_content:
                    seen["phone_upload_received"] = True
                    seen["phone_upload_name"] = candidate.name
                    seen["phone_upload_content"] = phone_upload_content
                    phone_upload_received.set()
                    break
            if desktop_download_completed.is_set() and phone_upload_received.is_set():
                break
        seen["timed_out"] = not (desktop_download_completed.is_set() and phone_upload_received.is_set())
        result_file.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    finally:
        if transport is not None:
            transport.stop()
        server.stop()
        http_service.stop()


if __name__ == "__main__":
    main()
