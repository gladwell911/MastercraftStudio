"""Probe v3: figure out how to set model + permission_mode on a session."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import websocket

PORT = 58934
BASE = f"http://127.0.0.1:{PORT}"
WS_URL = f"ws://127.0.0.1:{PORT}/api/v1/ws"
OUT = Path(r"D:\code\sj\mc\tests\fixtures\kimi_server_events.jsonl")
NOTES = Path(r"D:\code\sj\mc\tests\fixtures\kimi_server_probe_notes.json")
KIMI = r"C:\Users\Lenovo\.kimi-code\bin\kimi.exe"
CWD = r"D:\code\sj\mc"
MODEL = "kimi-code/kimi-for-coding"

notes = {}
events_log = []


def collect(ws, phase, stop_types=(), timeout=90, idle=8):
    ws.settimeout(idle)
    deadline = time.time() + timeout
    got = []
    while time.time() < deadline:
        try:
            msg = json.loads(ws.recv())
        except Exception:
            break
        events_log.append({"phase": phase, "msg": msg})
        got.append(msg)
        if msg.get("type") in stop_types:
            break
    return got


def main():
    token = Path(r"C:\Users\Lenovo\.kimi-code\server.token").read_text(encoding="utf-8").strip()
    h = {"Authorization": f"Bearer {token}"}
    proc = subprocess.Popen(
        [KIMI, "web", "--no-open", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=CWD,
    )
    try:
        for _ in range(60):
            try:
                if requests.get(f"{BASE}/api/v1/healthz", headers=h, timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)

        # GET /api/v1/models — available models
        r = requests.get(f"{BASE}/api/v1/models", headers=h, timeout=10)
        notes["models_endpoint"] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:500]}

        # create session WITHOUT agent_config, then set via profile
        r = requests.post(f"{BASE}/api/v1/sessions",
                          json={"title": "probe v3", "metadata": {"cwd": CWD}},
                          headers=h, timeout=15)
        data = r.json().get("data") or {}
        sid = data.get("id")
        notes["create_no_config"] = {"status": r.status_code, "agent_config": data.get("agent_config")}

        r = requests.post(f"{BASE}/api/v1/sessions/{sid}/profile",
                          json={"agent_config": {"model": MODEL, "permission_mode": "auto"}},
                          headers=h, timeout=15)
        notes["profile_update"] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:400]}

        r = requests.get(f"{BASE}/api/v1/sessions/{sid}/status", headers=h, timeout=10)
        notes["status_after_profile"] = r.json().get("data")

        # create a second session WITH agent_config at create time, for comparison
        r = requests.post(f"{BASE}/api/v1/sessions",
                          json={"title": "probe v3b", "metadata": {"cwd": CWD},
                                "agent_config": {"model": MODEL, "permission_mode": "auto"}},
                          headers=h, timeout=15)
        d2 = r.json().get("data") or {}
        notes["create_with_config_full"] = d2

        ws = websocket.create_connection(WS_URL, header=[f"Authorization: Bearer {token}"], timeout=10)
        ws.send(json.dumps({"type": "client_hello", "id": "h1", "payload": {"client_id": "probe", "subscriptions": [], "cursors": {}}}))
        ws.send(json.dumps({"type": "subscribe", "id": "s1", "payload": {"session_ids": [sid]}}))
        collect(ws, "handshake", timeout=3, idle=2)

        # simple turn
        requests.post(f"{BASE}/api/v1/sessions/{sid}/prompts",
                      json={"content": [{"type": "text", "text": "Reply with exactly the single word: pong. Do not use any tools."}]},
                      headers=h, timeout=15)
        got = collect(ws, "success_turn", stop_types=("prompt.completed",), timeout=150)
        notes["success_turn_types"] = [m.get("type") for m in got]
        notes["success_turn_ok"] = any(m.get("type") == "assistant.delta" for m in got)

        if notes["success_turn_ok"]:
            # tool turn
            requests.post(f"{BASE}/api/v1/sessions/{sid}/prompts",
                          json={"content": [{"type": "text", "text": "Use the Glob tool to find files matching *.md in the current directory, then answer with just the count."}]},
                          headers=h, timeout=15)
            got = collect(ws, "tool_turn", stop_types=("prompt.completed",), timeout=240)
            notes["tool_turn_types"] = sorted({m.get("type") for m in got})

            # abort during long turn
            r = requests.post(f"{BASE}/api/v1/sessions/{sid}/prompts",
                              json={"content": [{"type": "text", "text": "Count from 1 to 500, one number per line, no tools."}]},
                              headers=h, timeout=15)
            pid = (r.json().get("data") or {}).get("prompt_id")
            collect(ws, "long_turn_start", timeout=8, idle=5)
            ws.send(json.dumps({"type": "abort", "id": "a1", "payload": {"session_id": sid, "prompt_id": pid}}))
            got = collect(ws, "abort_turn", stop_types=("prompt.completed", "prompt.aborted"), timeout=60)
            notes["abort_turn_tail_types"] = [m.get("type") for m in got][-8:]

            # steer during long turn
            r = requests.post(f"{BASE}/api/v1/sessions/{sid}/prompts",
                              json={"content": [{"type": "text", "text": "Count from 100 to 900, one number per line, no tools."}]},
                              headers=h, timeout=15)
            pid = (r.json().get("data") or {}).get("prompt_id")
            collect(ws, "steer_turn_start", timeout=8, idle=5)
            r2 = requests.post(f"{BASE}/api/v1/sessions/{sid}/prompts:steer", json={"prompt_ids": [pid]}, headers=h, timeout=10)
            notes["steer"] = {"status": r2.status_code, "body": r2.text[:200]}
            collect(ws, "steer_events", timeout=8, idle=5)
            ws.send(json.dumps({"type": "abort", "id": "a2", "payload": {"session_id": sid, "prompt_id": pid}}))
            collect(ws, "steer_abort", stop_types=("prompt.completed", "prompt.aborted"), timeout=60)

            r = requests.get(f"{BASE}/api/v1/sessions/{sid}/messages", headers=h, timeout=10)
            items = (r.json().get("data") or {}).get("items") or []
            notes["messages_roles"] = [i.get("role") for i in items][:15]
            for it in items:
                if it.get("role") == "assistant":
                    notes["assistant_message_sample"] = it
                    break
            r = requests.get(f"{BASE}/api/v1/sessions/{sid}/status", headers=h, timeout=10)
            notes["status_final"] = r.json().get("data")

        ws.close()
        return 0
    finally:
        try:
            requests.post(f"{BASE}/api/v1/shutdown", headers=h, timeout=10)
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    rc = main()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events_log), encoding="utf-8")
    NOTES.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    print("events:", len(events_log), "rc:", rc)
    sys.exit(rc)
