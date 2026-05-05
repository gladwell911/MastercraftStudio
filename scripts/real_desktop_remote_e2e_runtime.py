from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import wx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from nats_runtime import NatsRuntimeConfig, NatsServerProcess  # noqa: E402
from remote_nats import RemoteNatsTransport  # noqa: E402


NATS_PORT_FALLBACKS = (4223, 4224, 4522)
NATS_WS_PORT_FALLBACKS = (18080, 18081, 18082, 8082)


def _require_env(name: str) -> str:
    value = str(os.environ.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"missing required env {name}")
    return value


def _read_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid integer env {name}: {raw}") from exc
    return value if value > 0 else int(default)


def _wait_until(predicate, *, timeout: float, step: float = 0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


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
    seen: set[int] = set()
    for candidate in (preferred_port, *fallbacks):
        if candidate in seen or candidate <= 0:
            continue
        seen.add(candidate)
        if _can_bind_loopback_tcp_port(candidate):
            return candidate
    raise RuntimeError(f"no available loopback port found near {preferred_port}")


def _notes_contains(frame: main.ChatFrame, title: str, content: str) -> bool:
    for notebook in frame.notes_store.list_notebooks():
        if notebook.title != title:
            continue
        for entry in frame.notes_store.list_entries(notebook.id):
            if content in entry.content:
                return True
    return False


def _history_contains(chats: list[dict], title: str) -> bool:
    for chat in list(chats or []):
        if str(chat.get("title") or "").strip() == title:
            return True
    return False


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _pump_wx_events() -> None:
    app = wx.GetApp()
    if app is None:
        return
    try:
        while app.Pending():
            app.Dispatch()
        app.ProcessIdle()
    except Exception:
        try:
            wx.YieldIfNeeded()
        except Exception:
            pass


def main_entry() -> None:
    state_dir = Path(_require_env("DESKTOP_E2E_STATE_DIR"))
    ready_file = Path(_require_env("DESKTOP_E2E_READY_FILE"))
    result_file = Path(_require_env("DESKTOP_E2E_RESULT_FILE"))
    stop_file = Path(_require_env("DESKTOP_E2E_STOP_FILE"))
    token = _require_env("DESKTOP_E2E_TOKEN")
    desktop_chat_title = _require_env("DESKTOP_E2E_DESKTOP_CHAT_TITLE")
    desktop_note_title = _require_env("DESKTOP_E2E_DESKTOP_NOTE_TITLE")
    desktop_note_body = _require_env("DESKTOP_E2E_DESKTOP_NOTE_BODY")
    mobile_chat_title = _require_env("DESKTOP_E2E_MOBILE_CHAT_TITLE")
    mobile_chat_message = _require_env("DESKTOP_E2E_MOBILE_CHAT_MESSAGE")
    desktop_chat_reply = _require_env("DESKTOP_E2E_DESKTOP_CHAT_REPLY")
    mobile_note_title = _require_env("DESKTOP_E2E_MOBILE_NOTE_TITLE")
    mobile_note_body = _require_env("DESKTOP_E2E_MOBILE_NOTE_BODY")
    observe_timeout_seconds = _read_int_env("DESKTOP_E2E_OBSERVE_TIMEOUT_SECONDS", 600)

    main.resolve_app_data_dir = lambda: state_dir

    stop_requested = False

    def _handle_signal(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    main.ChatFrame._schedule_remote_nats_autostart = lambda self: None
    main.ChatFrame._start_claudecode_remote_nats_runtime_if_configured = lambda self: None

    app = wx.App(False)
    frame = main.ChatFrame()
    transport: RemoteNatsTransport | None = None
    try:
        chat_records: list[dict] = []

        def _snapshot_chat(chat: dict) -> dict:
            created_at = float(chat.get("created_at") or time.time())
            updated_at = float(chat.get("updated_at") or created_at)
            turns = list(chat.get("turns") or [])
            return {
                "chat_id": str(chat.get("chat_id") or ""),
                "title": str(chat.get("title") or "新聊天"),
                "model": str(chat.get("model") or main.DEFAULT_MODEL_ID),
                "created_at": created_at,
                "updated_at": updated_at,
                "title_source": str(chat.get("title_source") or "default"),
                "title_updated_at": float(chat.get("title_updated_at") or updated_at),
                "title_revision": int(chat.get("title_revision") or 1),
                "turn_count": int(chat.get("turn_count") or len(turns)),
                "running": False,
                "request_kind": "",
                "current": False,
                "active": False,
                "pinned": False,
                "detail_panel_mode": "answers",
                "execution_steps": [],
                "turns": turns,
            }

        def _upsert_chat(chat: dict) -> None:
            chat_id = str(chat.get("chat_id") or "").strip()
            for idx, existing in enumerate(chat_records):
                if str(existing.get("chat_id") or "").strip() == chat_id:
                    chat_records[idx] = dict(chat)
                    return
            chat_records.append(dict(chat))

        def _find_chat(chat_id: str) -> dict | None:
            normalized = str(chat_id or "").strip()
            for chat in chat_records:
                if str(chat.get("chat_id") or "").strip() == normalized:
                    return chat
            return None

        def _remote_history_list() -> tuple[int, dict]:
            return 200, {"accepted": True, "chats": [_snapshot_chat(chat) for chat in chat_records]}

        def _remote_history_read(payload: dict) -> tuple[int, dict]:
            chat = _find_chat(str(payload.get("chat_id") or "").strip())
            return 200, {"accepted": True, "chat": _snapshot_chat(chat or {})}

        def _remote_new_chat(payload: dict) -> tuple[int, dict]:
            now = time.time()
            chat = {
                "chat_id": str(uuid.uuid4()),
                "title": str((payload or {}).get("title") or "").strip() or "新聊天",
                "model": str((payload or {}).get("model") or main.DEFAULT_MODEL_ID).strip() or main.DEFAULT_MODEL_ID,
                "created_at": now,
                "updated_at": now,
                "title_source": "default",
                "title_updated_at": now,
                "title_revision": 1,
            }
            _upsert_chat(chat)
            return 200, {"accepted": True, **_snapshot_chat(chat)}

        def _remote_message(payload: dict) -> tuple[int, dict]:
            chat_id = str(payload.get("chat_id") or "").strip()
            text = str(payload.get("text") or "").strip()
            chat = _find_chat(chat_id)
            if chat is None:
                return 404, {"accepted": False, "error": "not_found"}
            turn = {
                "question": text,
                "answer": desktop_chat_reply,
                "model": str(payload.get("model") or chat.get("model") or main.DEFAULT_MODEL_ID),
                "created_at": time.time(),
                "pending": False,
            }
            chat.setdefault("turns", []).append(turn)
            chat["turn_count"] = len(chat.get("turns") or [])
            chat["updated_at"] = time.time()
            _upsert_chat(chat)
            if transport is not None:
                transport.publish_event_threadsafe(
                    {
                        "type": "state_changed",
                        "chat_id": chat_id,
                        "body": {
                            "accepted": True,
                            "chat_id": chat_id,
                            "status": "idle",
                            "request_kind": "",
                            "turns": list(chat.get("turns") or []),
                        },
                    }
                )
                transport.publish_event_threadsafe(
                    {
                        "type": "final_answer",
                        "chat_id": chat_id,
                        "text": desktop_chat_reply,
                    }
                )
            return 200, {
                "accepted": True,
                "chat_id": chat_id,
                "status": "idle",
                "request_kind": "",
                "turns": list(chat.get("turns") or []),
            }

        def _remote_rename_chat(payload: dict) -> tuple[int, dict]:
            chat = _find_chat(str(payload.get("chat_id") or "").strip())
            if chat is None:
                return 404, {"accepted": False, "error": "not_found"}
            title = str(payload.get("title") or "").strip()
            if not title:
                return 400, {"accepted": False, "error": "invalid_payload"}
            chat["title"] = title
            chat["updated_at"] = float(payload.get("title_updated_at") or time.time())
            chat["title_source"] = str(payload.get("title_source") or "manual").strip() or "manual"
            chat["title_updated_at"] = float(payload.get("title_updated_at") or chat["updated_at"])
            chat["title_revision"] = int(payload.get("title_revision") or (int(chat.get("title_revision") or 0) + 1))
            _upsert_chat(chat)
            return 200, {"accepted": True, **_snapshot_chat(chat)}

        tcp_port = _choose_available_port(4222, NATS_PORT_FALLBACKS)
        websocket_port = _choose_available_port(18080, NATS_WS_PORT_FALLBACKS)
        process = NatsServerProcess(
            NatsRuntimeConfig(
                app_data_dir=state_dir,
                token=token,
                host="127.0.0.1",
                port=tcp_port,
                websocket_host="127.0.0.1",
                websocket_port=websocket_port,
            )
        )
        process.start(timeout=20)
        transport = RemoteNatsTransport(
            pair_id="default",
            token=token,
            on_message=_remote_message,
            on_new_chat=_remote_new_chat,
            on_reply_request=lambda _payload: (200, {"accepted": True}),
            on_state=lambda payload=None: (
                200,
                {
                    "accepted": True,
                    "chat_id": str((payload or {}).get("chat_id") or ""),
                    "status": "idle",
                    "request_kind": "",
                    "turns": [],
                },
            ),
            on_rename_chat=_remote_rename_chat,
            on_update_settings=lambda _payload: (200, {"accepted": True, "settings": {}}),
            on_history_list=_remote_history_list,
            on_history_read=_remote_history_read,
            on_notes_changes=frame._remote_api_notes_changes,
            on_notes_bulk_docs=frame._remote_api_notes_bulk_docs,
        )
        transport.start_threaded(f"nats://127.0.0.1:{tcp_port}", timeout=20)
        frame._remote_nats_process = process
        frame._remote_nats_transport = transport
        frame._remote_nats_websocket_port = websocket_port
        frame._set_remote_nats_runtime_status(
            enabled=True,
            tcp_url=f"nats://127.0.0.1:{tcp_port}",
            websocket_url=f"ws://127.0.0.1:{websocket_port}/nats",
            last_error="",
        )

        desktop_chat = {
            "chat_id": str(uuid.uuid4()),
            "title": desktop_chat_title,
            "model": main.DEFAULT_MODEL_ID,
            "created_at": time.time(),
            "updated_at": time.time(),
            "title_source": "manual",
            "title_updated_at": time.time(),
            "title_revision": 1,
        }
        _upsert_chat(desktop_chat)
        transport.publish_event_threadsafe(
            {
                "type": "history_changed",
                "chat_id": desktop_chat["chat_id"],
            }
        )

        notebook = frame.notes_store.create_notebook(desktop_note_title)
        frame.notes_store.create_entry(notebook.id, desktop_note_body, source="manual")

        ready_payload = {
            "endpoint": f"ws://127.0.0.1:{websocket_port}/nats",
            "token": token,
            "desktop_chat_title": desktop_chat_title,
            "desktop_note_title": desktop_note_title,
            "desktop_note_body": desktop_note_body,
            "mobile_chat_title": mobile_chat_title,
            "mobile_chat_message": mobile_chat_message,
            "desktop_chat_reply": desktop_chat_reply,
            "mobile_note_title": mobile_note_title,
            "mobile_note_body": mobile_note_body,
            "app_data_dir": str(state_dir),
        }
        _write_json(ready_file, ready_payload)

        deadline = time.time() + observe_timeout_seconds
        observed_mobile_chat = False
        observed_mobile_note = False
        while time.time() < deadline and not stop_requested:
            _pump_wx_events()
            if stop_file.exists():
                break
            observed_mobile_chat = observed_mobile_chat or _history_contains(chat_records, mobile_chat_title)
            observed_mobile_note = observed_mobile_note or _notes_contains(
                frame,
                mobile_note_title,
                mobile_note_body,
            )
            _write_json(
                result_file,
                {
                    "mobile_chat_visible_on_desktop": observed_mobile_chat,
                    "mobile_note_visible_on_desktop": observed_mobile_note,
                    "desktop_chat_title": desktop_chat_title,
                    "desktop_note_title": desktop_note_title,
                    "mobile_chat_title": mobile_chat_title,
                    "mobile_note_title": mobile_note_title,
                },
            )
            time.sleep(0.5)
    finally:
        try:
            frame._stop_remote_servers()
        except Exception:
            pass
        if transport is not None:
            try:
                transport.stop()
            except Exception:
                pass
        frame.Destroy()
        app.Destroy()


if __name__ == "__main__":
    main_entry()
