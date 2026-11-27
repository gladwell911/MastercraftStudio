"""Native CLI history through the existing desktop and remote chat routes."""
import json
import os
import subprocess

import pytest

from kimi_runtime import child_environment
from kimi_history import kimi_history_storage
from remote_nats import RemoteNatsTransport
from test_kimi_runtime_usage_e2e import loopback_kimi_home


pytestmark = pytest.mark.skipif(
    os.getenv("MC_KIMI_RUNTIME_TEST") != "1",
    reason="set MC_KIMI_RUNTIME_TEST=1 to spawn the local runtime",
)


def test_native_history_is_discovered_and_resumed(loopback_kimi_home, frame, monkeypatch, wait_for_wx,
                                                wait_for_wx_main_loop):
    workspace, requests = loopback_kimi_home

    def native(*args):
        return subprocess.run(
            [os.environ["KIMI_BIN"], *args], cwd=workspace, env=child_environment(),
            timeout=20, capture_output=True, check=True, encoding="utf-8",
        ).stdout

    (workspace / "qa-native-tool-evidence.txt").write_text("fixture", encoding="utf-8")
    seed = "QA_NATIVE_TOOL_CALL"
    remote = RemoteNatsTransport(pair_id="default", token="synthetic",
        on_history_read=frame._remote_api_history_read_ui,
        on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret="synthetic"),
    )
    assert "QA_RUNTIME_OK" in native("-p", seed)
    terminal, = json.loads(native("session", "list", "--json"))
    assert "QA_RUNTIME_OK" in native("--session", terminal["id"], "-p", seed)
    frame.chat_store.upsert_chat({"id": "mc-kimi-native", "model": "kimi/main",
                                 "kimi_session_id": terminal["id"], "kimi_storage_id": kimi_history_storage()})
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args, **_kwargs: None)

    def listed(count):
        chats = frame._remote_api_history_list_ui()[1]["chats"]
        return [row for row in chats if row.get("model") == "kimi/main" and row["turn_count"] == count]

    try:
        wait_for_wx(lambda: listed(2), "Kimi native history", timeout=20)
        listed_chat, = listed(2)
        chat_id = listed_chat["chat_id"]
        assert len(requests) == 4
        code, history = remote._route_command({"type": "history_read", "chat_id": chat_id, "limit": 1})
        assert code == 200
        assert history["has_more"]
        turn, = history["chat"]["turns"]
        assert (turn["question"], turn["answer"]) == (seed, "QA_RUNTIME_OK")
        assert frame._find_archived_chat(chat_id)["native_cwd"] == str(workspace)
        assert frame._find_archived_chat(chat_id)["kimi_session_id"] == terminal["id"]
        assert listed_chat["native_workspace"] == str(workspace)
        code, execution = remote._route_command({"type": "execution_page_v3", "chat_id": chat_id})
        assert code == 200
        assert any(row["list_text"] == "Glob" for row in execution["rows"])
        assert any("qa-native-tool-evidence.txt" in row["detail_text"]
                   for row in frame.chat_store.load_execution_steps(chat_id))
        assert frame._show_history_chat(chat_id)
        status, response = frame._remote_api_message_ui({
            "chat_id": chat_id, "text": "Continue using the recorded context", "model": "kimi/main",
        })
        assert status == 200, response
        wait_for_wx(lambda: len(requests) >= 5 and listed(3)
                    and frame._remote_api_state_ui({"chat_id": chat_id})[1]["turns"][-1]["answer"] == "QA_RUNTIME_OK",
                    "Kimi continuation", timeout=25)
        assert sum(seed in str(message["content"]) for message in requests[4]["messages"]) == 2
        assert requests[4]["prompt_cache_key"] == terminal["id"]
        frame._kimi_client.close()
        frame._kimi_client = None
        assert "QA_RUNTIME_OK" in native("--session", terminal["id"], "-p", "Advanced from the CLI")
        assert any("Continue using the recorded context" in str(message["content"])
                   for message in requests[5]["messages"])
        wait_for_wx_main_loop(lambda: listed(4), "updated native history after external indexing", timeout=15)
        assert listed(4)[0]["chat_id"] == chat_id
        history = frame._remote_api_history_read_ui({"chat_id": chat_id, "limit": 1})[1]
        assert history["chat"]["turns"][0]["question"] == "Advanced from the CLI"
    finally:
        if frame._kimi_client is not None:
            frame._kimi_client.close()
