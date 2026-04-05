import json
import subprocess
import time

import main
import openclaw_client


def test_stream_chat_uses_node_entrypoint_for_multiline_message(monkeypatch, tmp_path):
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    cmd_path = npm_dir / "openclaw.cmd"
    cmd_path.write_text("@echo off\n", encoding="utf-8")
    script_path = npm_dir / "node_modules" / "openclaw" / "openclaw.mjs"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("console.log('ok')\n", encoding="utf-8")
    node_path = tmp_path / "node.exe"
    node_path.write_text("", encoding="utf-8")

    seen = {}

    def fake_run(*args, **kwargs):
        seen["command"] = args[0]
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"payloads":[{"text":"ok"}]}',
            stderr="",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    monkeypatch.setattr(openclaw_client.shutil, "which", lambda name: str(node_path) if name == "node" else None)
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)
    monkeypatch.setattr(client, "_resolve_openclaw_command", lambda: str(cmd_path))

    multiline = "第一行\n第二行\n第三行"
    out = client.stream_chat(multiline, session_id="zgwd-1")

    assert out == "ok"
    assert seen["command"][:2] == [str(node_path), str(script_path)]
    assert seen["command"][9] == multiline


def test_read_session_events_ignores_assistant_commentary_and_keeps_final_answer(tmp_path):
    session_path = tmp_path / "main.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "id": "a1",
                        "timestamp": "2026-03-18T00:35:32.129Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "我先看看配置。",
                                    "textSignature": json.dumps({"phase": "commentary"}),
                                },
                                {
                                    "type": "text",
                                    "text": "[[reply_to_current]] 最终答复",
                                    "textSignature": json.dumps({"phase": "final_answer"}),
                                },
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "a2",
                        "timestamp": "2026-03-18T00:36:00.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "这是一条 commentary",
                                    "textSignature": json.dumps({"phase": "commentary"}),
                                }
                            ],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    _offset, events = openclaw_client.read_session_events(session_path, 0)

    assert [(event.event_id, event.text) for event in events] == [("a1", "最终答复")]


def test_apply_openclaw_sync_batch_does_not_play_sound_for_duplicate_assistant_text(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_openclaw_session_file = r"C:\tmp\main.jsonl"
    frame.active_openclaw_last_synced_at = time.time()
    frame.active_session_turns = [
        {
            "question": "hello",
            "answer_md": "done",
            "model": "openclaw/main",
            "created_at": 100.0,
        }
    ]
    rendered = {"n": 0}
    played = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))

    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-chat-001",
            "session_file": r"C:\tmp\main.jsonl",
            "offset": 120,
            "updated_at": time.time(),
            "file_changed": False,
            "session_changed": False,
        },
        [main.OpenClawSyncEvent(event_id="a-dup", role="assistant", text="done", timestamp=101.0)],
    )

    assert rendered["n"] == 0
    assert played["n"] == 0
    assert frame.active_session_turns[0]["answer_external_event_id"] == "a-dup"
