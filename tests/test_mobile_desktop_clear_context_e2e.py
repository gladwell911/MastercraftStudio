import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import main
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport


def _choose_free_port(start: int) -> int:
    for port in range(start, start + 40):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"no free port starting at {start}")


def _run_with_wx_yield(wx_app, command: list[str], cwd: Path, timeout: float = 300.0) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output: list[str] = []
    lines: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            lines.put(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            wx_app.Yield()
            while True:
                try:
                    output.append(lines.get_nowait())
                except queue.Empty:
                    break
            if time.monotonic() > deadline:
                process.terminate()
                raise TimeoutError("flutter integration test timed out")
            time.sleep(0.05)
        reader.join(timeout=1)
        while True:
            try:
                output.append(lines.get_nowait())
            except queue.Empty:
                break
    finally:
        if process.poll() is None:
            process.kill()
    return subprocess.CompletedProcess(command, process.returncode, "".join(output), "")


def test_mobile_emulator_clear_context_clears_desktop_chat_frame(frame, wx_app, tmp_path):
    chat_id = "chat-clear-context-e2e"
    question = "desktop clear context question from real frame"
    answer = "desktop clear context answer from real frame"
    pair_id = "clear-context-e2e"
    token = "clear-context-token"
    device_id = os.environ.get("NATS_CLEAR_CONTEXT_E2E_DEVICE", "emulator-5556")
    tcp_port = _choose_free_port(4622)
    websocket_port = _choose_free_port(19080)
    endpoint_host = os.environ.get(
        "NATS_CLEAR_CONTEXT_E2E_ENDPOINT_HOST",
        "10.0.2.2" if device_id.startswith("emulator-") else "127.0.0.1",
    )

    frame.Show()
    frame.active_chat_id = chat_id
    frame.current_chat_id = chat_id
    frame.active_session_turns = [
        {
            "question": question,
            "answer_md": answer,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
        }
    ]
    frame._current_chat_state = {
        "id": chat_id,
        "title": "clear context e2e",
        "turns": frame.active_session_turns,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    frame._render_answer_list()
    wx_app.Yield()
    assert any(question in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))

    server = NatsServerProcess(
        NatsRuntimeConfig(
            app_data_dir=tmp_path / "nats",
            token=token,
            host="127.0.0.1",
            port=tcp_port,
            websocket_host="0.0.0.0",
            websocket_port=websocket_port,
        ),
        bundled_dir=Path(__file__).resolve().parents[1] / "tools" / "nats-server",
    )
    transport = None

    def invoke_on_ui(callback):
        done = threading.Event()
        result = {}

        def run_callback():
            try:
                result["value"] = callback()
            except Exception as exc:  # pragma: no cover - propagated below
                result["error"] = exc
            finally:
                done.set()

        main.wx.CallAfter(run_callback)
        if not done.wait(20):
            return 503, {"accepted": False, "error": "ui_timeout"}
        if "error" in result:
            raise result["error"]
        return result["value"]

    try:
        server.start(timeout=20)
        transport = RemoteNatsTransport(
            pair_id=pair_id,
            token=token,
            on_state=frame._remote_api_state_ui,
            on_clear_context=frame._remote_api_clear_context_ui,
            invoke_callback=invoke_on_ui,
        )
        transport.start_threaded(f"nats://127.0.0.1:{tcp_port}", timeout=20)

        rc_dir = Path(__file__).resolve().parents[2] / "rc"
        flutter_bin = shutil.which("flutter.bat") or shutil.which("flutter")
        assert flutter_bin, "flutter executable was not found on PATH"
        adb_bin = shutil.which("adb.exe") or shutil.which("adb")
        reversed_websocket = False
        if endpoint_host == "127.0.0.1" and not device_id.startswith("emulator-"):
            assert adb_bin, "adb executable was not found on PATH"
            subprocess.run(
                [
                    adb_bin,
                    "-s",
                    device_id,
                    "reverse",
                    f"tcp:{websocket_port}",
                    f"tcp:{websocket_port}",
                ],
                check=True,
                cwd=str(rc_dir),
            )
            reversed_websocket = True
        command = [
            flutter_bin,
            "test",
            "integration_test/nats_clear_context_e2e_test.dart",
            "--plain-name",
            "mobile menu clears desktop chat context over NATS",
            "-d",
            device_id,
            "--dart-define",
            f"NATS_E2E_ENDPOINT=ws://{endpoint_host}:{websocket_port}/nats",
            "--dart-define",
            f"NATS_E2E_TOKEN={token}",
            "--dart-define",
            f"NATS_E2E_PAIR_ID={pair_id}",
            "--dart-define",
            f"NATS_E2E_CHAT_ID={chat_id}",
            "--dart-define",
            f"NATS_E2E_CONTEXT_QUESTION={question}",
        ]
        completed = _run_with_wx_yield(wx_app, command, rc_dir)
        assert completed.returncode == 0, completed.stdout
        wx_app.Yield()

        assert frame.active_session_turns == []
        assert frame._current_chat_state["turns"] == []
        assert not any(question in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))
    finally:
        if "reversed_websocket" in locals() and reversed_websocket and "adb_bin" in locals() and adb_bin:
            subprocess.run(
                [adb_bin, "-s", device_id, "reverse", "--remove", f"tcp:{websocket_port}"],
                cwd=str(Path(__file__).resolve().parents[2] / "rc"),
                check=False,
            )
        if transport is not None:
            transport.stop()
        server.stop()
