from __future__ import annotations

import json
import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport


CHAT_ID = "chat-e2e"
MODEL = "codex/main"
NATS_E2E_PORT_FALLBACKS = (4223, 4224, 4522)
NATS_E2E_WS_PORT_FALLBACKS = (18080, 18081, 18082, 8082)


def _state_body(text: str = "", answer: str = "") -> dict:
    turns = []
    if text or answer:
        turns.append(
            {
                "question": text,
                "answer": answer,
                "model": MODEL,
                "created_at": time.time(),
                "pending": False,
            }
        )
    return {
        "accepted": True,
        "status": "idle",
        "request_kind": "",
        "active_turn": False,
        "chat_id": CHAT_ID,
        "last_event_id": f"desktop-{int(time.time() * 1000)}",
        "turns": turns,
        "settings": {"codex_answer_english_filter_enabled": False},
    }


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


def resolve_runtime_ports(
    *,
    preferred_port: int,
    preferred_ws_port: int,
) -> tuple[int, int]:
    return (
        _choose_available_port(preferred_port, NATS_E2E_PORT_FALLBACKS),
        _choose_available_port(preferred_ws_port, NATS_E2E_WS_PORT_FALLBACKS),
    )


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

    def _on_signal(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    last_question = ""

    def _push_state_and_answer() -> None:
        if transport is None or not last_question:
            return
        body = _state_body(
            last_question,
            f"desktop received: {last_question}",
        )
        transport.publish_event_threadsafe(
            {
                "type": "state",
                "chat_id": CHAT_ID,
                "body": body,
            }
        )
        transport.publish_event_threadsafe(
            {
                "type": "final_answer",
                "chat_id": CHAT_ID,
                "text": f"desktop received: {last_question}",
            }
        )

    def on_state(_payload: dict) -> tuple[int, dict]:
        return 200, _state_body(last_question, f"desktop received: {last_question}" if last_question else "")

    def on_message(payload: dict) -> tuple[int, dict]:
        nonlocal last_question
        last_question = str(payload.get("text") or "")
        _push_state_and_answer()
        return 200, _state_body(last_question, f"desktop received: {last_question}")

    def on_notes_changes(payload: dict) -> tuple[int, dict]:
        return notes_store.changes(payload)

    def on_notes_bulk_docs(payload: dict) -> tuple[int, dict]:
        return notes_store.bulk_docs(payload)

    try:
        server.start(timeout=15)
        transport = RemoteNatsTransport(
            pair_id=pair_id,
            token=token,
            on_state=on_state,
            on_message=on_message,
            on_notes_changes=on_notes_changes,
            on_notes_bulk_docs=on_notes_bulk_docs,
        )
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
            pass
    finally:
        if transport is not None:
            transport.stop()
        server.stop()


if __name__ == "__main__":
    main()
