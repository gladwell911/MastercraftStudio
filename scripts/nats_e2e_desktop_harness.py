from __future__ import annotations

import json
import os
import signal
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from chat_store import ChatStore


CHAT_ID = "chat-e2e"
MODEL = "codex/main"
KIMI_MODEL = "kimi/main"
NATS_E2E_PORT_FALLBACKS = (4223, 4224, 4522)
NATS_E2E_WS_PORT_FALLBACKS = (18080, 18081, 18082, 8082)


def _state_body(chat_id: str = CHAT_ID, turns: list[dict] | None = None) -> dict:
    return {
        "accepted": True,
        "status": "idle",
        "request_kind": "",
        "active_turn": False,
        "chat_id": chat_id,
        "last_event_id": f"desktop-{int(time.time() * 1000)}",
        "turns": list(turns or []),
        "settings": {"codex_answer_english_filter_enabled": False},
    }


class CrossClientHarnessState:
    def __init__(
        self,
        *,
        durable_store: ChatStore,
        pair_id: str,
        seed_title: str,
        codex_reply: str,
        kimi_reply: str,
        result_file: Path | None = None,
    ) -> None:
        self.durable_store = durable_store
        self.pair_id = pair_id
        self.codex_reply = codex_reply
        self.kimi_reply = kimi_reply
        self.result_file = result_file
        self.transport: RemoteNatsTransport | None = None
        self._lock = threading.RLock()
        now = time.time()
        self.chats: dict[str, dict] = {
            CHAT_ID: {
                "chat_id": CHAT_ID,
                "title": seed_title,
                "model": MODEL,
                "created_at": now,
                "updated_at": now,
                "turns": [],
            }
        }
        self.observed_messages: list[dict[str, str]] = []
        self._persist_chat(self.chats[CHAT_ID])

    def attach_transport(self, transport: RemoteNatsTransport) -> None:
        self.transport = transport

    def _persist_chat(self, chat: dict) -> None:
        self.durable_store.upsert_chat(
            {
                "id": chat["chat_id"],
                "title": chat["title"],
                "model": chat["model"],
                "created_at": chat["created_at"],
                "updated_at": chat["updated_at"],
                "title_manual": True,
                "title_source": "manual",
                "title_updated_at": chat["updated_at"],
                "title_revision": 1,
            }
        )
        self.durable_store.replace_turns(chat["chat_id"], chat["turns"])

    @staticmethod
    def _summary(chat: dict) -> dict:
        return {
            "chat_id": chat["chat_id"],
            "title": chat["title"],
            "model": chat["model"],
            "created_at": chat["created_at"],
            "updated_at": chat["updated_at"],
            "turn_count": len(chat["turns"]),
            "running": False,
            "request_kind": "",
            "current": False,
            "active": False,
            "pinned": False,
            "title_source": "manual",
            "title_updated_at": chat["updated_at"],
            "title_revision": 1,
        }

    def model_list(self) -> tuple[int, dict]:
        return 200, {
            "accepted": True,
            "models": [
                {"id": MODEL, "label": "Codex"},
                {"id": KIMI_MODEL, "label": "Kimi Code"},
            ],
        }

    def history_list(self) -> tuple[int, dict]:
        with self._lock:
            chats = sorted(
                self.chats.values(), key=lambda item: item["updated_at"], reverse=True
            )
            return 200, {
                "accepted": True,
                "chats": [self._summary(chat) for chat in chats],
            }

    def history_read(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        with self._lock:
            chat = self.chats.get(chat_id)
            if chat is None:
                return 404, {"accepted": False, "error": "chat_not_found"}
            return 200, {
                "accepted": True,
                "chat": {**self._summary(chat), "turns": list(chat["turns"])},
                "has_more": False,
                "oldest_cursor": "",
            }

    def new_chat(self, payload: dict) -> tuple[int, dict]:
        model = str(payload.get("model") or MODEL).strip() or MODEL
        if model not in {MODEL, KIMI_MODEL}:
            return 400, {"accepted": False, "error": "unsupported_model"}
        now = time.time()
        chat_id = str(payload.get("chat_id") or f"local-{uuid.uuid4().hex}").strip()
        chat = {
            "chat_id": chat_id,
            "title": "Local Kimi chat" if model == KIMI_MODEL else "Local Codex chat",
            "model": model,
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }
        with self._lock:
            self.chats[chat_id] = chat
            self._persist_chat(chat)
        return 200, {"accepted": True, **self._summary(chat)}

    def state(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or CHAT_ID).strip() or CHAT_ID
        with self._lock:
            chat = self.chats.get(chat_id)
            if chat is None:
                return 404, {"accepted": False, "error": "chat_not_found"}
            return 200, _state_body(chat_id, chat["turns"])

    def message(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        requested_model = str(payload.get("model") or "").strip()
        if not chat_id or not text:
            return 400, {"accepted": False, "error": "missing_chat_or_text"}
        with self._lock:
            chat = self.chats.get(chat_id)
            if chat is None:
                return 404, {"accepted": False, "error": "chat_not_found"}
            model = requested_model or str(chat["model"])
            if model not in {MODEL, KIMI_MODEL}:
                return 400, {"accepted": False, "error": "unsupported_model"}
            reply = self.kimi_reply if model == KIMI_MODEL else self.codex_reply
            now = time.time()
            chat["model"] = model
            chat["updated_at"] = now
            chat["turns"].append(
                {
                    "question": text,
                    "answer_md": reply,
                    "answer": reply,
                    "model": model,
                    "created_at": now,
                    "pending": False,
                }
            )
            self._persist_chat(chat)
            turn_index = len(chat["turns"]) - 1
            message_id = self.durable_store.resolve_canonical_message_by_turn(
                chat_id, role="assistant", turn_index=turn_index
            )
            self.durable_store.commit_message_notification_fact(
                pair_id=self.pair_id,
                domain="events",
                chat_id=chat_id,
                message_id=message_id,
                notification_kind="assistant_final",
                text="ignored non-authoritative fixture text",
                chat_title="ignored non-authoritative fixture title",
            )
            self.observed_messages.append(
                {"chat_id": chat_id, "model": model, "text": text, "reply": reply}
            )
            self._write_result()
            self._schedule_outbox_drain()
            return 200, {"accepted": True, "chat_id": chat_id, "model": model}

    def _schedule_outbox_drain(self) -> None:
        transport = self.transport
        loop = getattr(transport, "_loop", None) if transport is not None else None
        if loop is not None and loop.is_running():
            import asyncio

            asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)

    def _write_result(self) -> None:
        if self.result_file is None:
            return
        payload = {"messages": list(self.observed_messages)}
        self.result_file.parent.mkdir(parents=True, exist_ok=True)
        self.result_file.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


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
    return _allocate_ephemeral_loopback_port()


def _allocate_ephemeral_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_runtime_ports(
    *,
    preferred_port: int,
    preferred_ws_port: int,
) -> tuple[int, int]:
    tcp_port = _choose_available_port(preferred_port, NATS_E2E_PORT_FALLBACKS)
    websocket_port = _choose_available_port(
        preferred_ws_port, NATS_E2E_WS_PORT_FALLBACKS
    )
    while websocket_port == tcp_port:
        websocket_port = _allocate_ephemeral_loopback_port()
    return tcp_port, websocket_port


def write_ready_file(
    ready_file: Path,
    *,
    tcp_port: int,
    websocket_port: int,
    token: str,
    pair_id: str,
) -> None:
    payload = {
        "tcp_port": int(tcp_port),
        "websocket_port": int(websocket_port),
        "endpoint": f"ws://127.0.0.1:{int(websocket_port)}/nats",
        "token": str(token),
        "pair_id": str(pair_id),
    }
    ready_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class InMemoryNotesHarnessStore:
    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}
        self._seq = 0
        self._changes: list[dict] = []

    def bulk_docs(self, payload: dict | None = None) -> tuple[int, dict]:
        results = []
        for raw_doc in list((payload or {}).get("docs") or []):
            doc = dict(raw_doc or {})
            doc_id = str(doc.get("_id") or "").strip()
            if not doc_id:
                results.append({"id": "", "ok": False, "error": "missing_id"})
                continue
            current = self._docs.get(doc_id)
            rev_num = 1
            if current:
                rev_num = int(str(current.get("_rev") or "0-0").split("-", 1)[0] or "0") + 1
            doc["_rev"] = f"{rev_num}-{self._seq + 1:08d}"
            self._docs[doc_id] = doc
            self._seq += 1
            self._changes.append(
                {
                    "seq": self._seq,
                    "id": doc_id,
                    "deleted": bool(doc.get("_deleted")),
                    "doc": dict(doc),
                }
            )
            results.append({"id": doc_id, "_id": doc_id, "ok": True, "rev": doc["_rev"]})
        return 201, {"ok": True, "results": results}

    def changes(self, payload: dict | None = None) -> tuple[int, dict]:
        since_text = str((payload or {}).get("since") or "0").strip() or "0"
        try:
            since = int(float(since_text))
        except Exception:
            since = 0
        include_docs = bool((payload or {}).get("include_docs"))
        results = []
        for item in self._changes:
            if int(item["seq"]) <= since:
                continue
            row = {
                "seq": item["seq"],
                "id": item["id"],
                "changes": [{"rev": item["doc"].get("_rev", "")}],
            }
            if item["deleted"]:
                row["deleted"] = True
            if include_docs:
                row["doc"] = dict(item["doc"])
            results.append(row)
        return 200, {"results": results, "last_seq": str(self._seq)}


def main() -> None:
    token = os.environ.get("NATS_E2E_TOKEN", "test-token")
    preferred_port = int(os.environ.get("NATS_E2E_PORT", "4222"))
    pair_id = os.environ.get("NATS_E2E_PAIR_ID", "default")
    ready_file = os.environ.get("NATS_E2E_READY_FILE", "")
    result_file = os.environ.get("NATS_E2E_RESULT_FILE", "")
    stop_file = os.environ.get("NATS_E2E_STOP_FILE", "")
    seed_title = os.environ.get("E2E_DESKTOP_CHAT_TITLE", "Local desktop seed")
    codex_reply = os.environ.get("E2E_CODEX_REPLY_MARKER", "LOCAL_CODEX_OK")
    kimi_reply = os.environ.get("E2E_KIMI_REPLY_MARKER", "LOCAL_KIMI_OK")
    app_data = Path(os.environ.get("NATS_E2E_APP_DATA", tempfile.mkdtemp(prefix="zgwd-nats-e2e-")))
    preferred_ws_port = int(os.environ.get("NATS_E2E_WS_PORT", "18080"))
    port, websocket_port = resolve_runtime_ports(
        preferred_port=preferred_port,
        preferred_ws_port=preferred_ws_port,
    )
    server = NatsServerProcess(
        NatsRuntimeConfig(
            app_data_dir=app_data,
            token=token,
            host="0.0.0.0",
            port=port,
            websocket_host="127.0.0.1",
            websocket_port=websocket_port,
        )
    )
    transport: RemoteNatsTransport | None = None
    stop = threading.Event()
    notes_store = InMemoryNotesHarnessStore()
    durable_store = ChatStore(app_data / "cross-client-harness.db")
    durable_store.initialize()
    state = CrossClientHarnessState(
        durable_store=durable_store,
        pair_id=pair_id,
        seed_title=seed_title,
        codex_reply=codex_reply,
        kimi_reply=kimi_reply,
        result_file=Path(result_file) if result_file else None,
    )

    def _on_signal(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    def on_notes_changes(payload: dict) -> tuple[int, dict]:
        return notes_store.changes(payload)

    def on_notes_bulk_docs(payload: dict) -> tuple[int, dict]:
        return notes_store.bulk_docs(payload)

    try:
        server.start(timeout=15)
        transport = RemoteNatsTransport(
            pair_id=pair_id,
            token=token,
            on_state=state.state,
            on_message=state.message,
            on_new_chat=state.new_chat,
            on_model_list=state.model_list,
            on_history_list=state.history_list,
            on_history_read=state.history_read,
            on_notes_changes=on_notes_changes,
            on_notes_bulk_docs=on_notes_bulk_docs,
            durable_store=durable_store,
        )
        state.attach_transport(transport)
        transport.start_threaded(f"nats://127.0.0.1:{port}", timeout=15)
        if ready_file:
            write_ready_file(
                Path(ready_file),
                tcp_port=port,
                websocket_port=websocket_port,
                token=token,
                pair_id=pair_id,
            )
        while not stop.wait(0.2):
            if stop_file and Path(stop_file).exists():
                break
    finally:
        if transport is not None:
            transport.stop()
        server.stop()


if __name__ == "__main__":
    main()
