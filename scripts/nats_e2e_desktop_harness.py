from __future__ import annotations

import os
import signal
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


def main() -> None:
    token = os.environ.get("NATS_E2E_TOKEN", "test-token")
    port = int(os.environ.get("NATS_E2E_PORT", "4222"))
    pair_id = os.environ.get("NATS_E2E_PAIR_ID", "default")
    ready_file = os.environ.get("NATS_E2E_READY_FILE", "")
    app_data = Path(os.environ.get("NATS_E2E_APP_DATA", tempfile.mkdtemp(prefix="zgwd-nats-e2e-")))
    server = NatsServerProcess(
        NatsRuntimeConfig(
            app_data_dir=app_data,
            token=token,
            host="0.0.0.0",
            port=port,
            websocket_host="127.0.0.1",
            websocket_port=int(os.environ.get("NATS_E2E_WS_PORT", "8081")),
        )
    )
    transport: RemoteNatsTransport | None = None
    stop = threading.Event()

    def _on_signal(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    last_question = ""

    def on_state(_payload: dict) -> tuple[int, dict]:
        return 200, _state_body(last_question, f"desktop received: {last_question}" if last_question else "")

    def on_message(payload: dict) -> tuple[int, dict]:
        nonlocal last_question
        last_question = str(payload.get("text") or "")
        return 200, _state_body(last_question, f"desktop received: {last_question}")

    try:
        server.start(timeout=15)
        transport = RemoteNatsTransport(
            pair_id=pair_id,
            token=token,
            on_state=on_state,
            on_message=on_message,
        )
        transport.start_threaded(f"nats://127.0.0.1:{port}", timeout=15)
        if ready_file:
            Path(ready_file).write_text("ready", encoding="utf-8")
        while not stop.wait(0.2):
            pass
    finally:
        if transport is not None:
            transport.stop()
        server.stop()


if __name__ == "__main__":
    main()
