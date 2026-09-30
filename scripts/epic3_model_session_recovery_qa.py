"""Private protocol fixtures and repeatable desktop recovery QA; no model login.

The default command runs the native wx tests. Child modes provide loopback
Kimi HTTP/WS and Codex stdio app-server fixtures behind production clients.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]


def record(folder, **data):
    with (Path(folder) / "protocol.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")


def kimi_fixture(folder, port):
    from aiohttp import web
    folder = Path(folder)
    if (folder / "fail-start").exists():
        record(folder, kind="startup_exit", code=23)
        print("controlled Kimi startup exit 23", flush=True)
        return 23
    sessions, sockets = {}, set()
    seq = 0

    async def emit(sid, payload):
        nonlocal seq
        seq += 1
        message = {"type": "session_event", "session_id": sid, "seq": seq, "epoch": "private-qa", "payload": payload}
        for ws in list(sockets):
            if not ws.closed:
                await ws.send_json(message)

    async def finish(sid, prompt, question):
        # Wait for the test's explicit gate so ownership is established before
        # events are released. No fixed sleep controls acceptance.
        while not (folder / "release-kimi").exists():
            await asyncio.sleep(.02)
        answer = "ANSWER:" + question.rsplit("\n", 1)[-1]
        tid = prompt
        sessions[sid]["messages"] = [
            {"id": prompt, "role": "user", "content": [{"type":"text", "text":question}], "turn_id":tid},
            {"id": "assistant-"+prompt, "role":"assistant", "content":[{"type":"text", "text":answer}], "turn_id":tid},
        ]
        await emit(sid, {"type":"turn.started", "turnId":tid, "promptId":prompt, "prompt":question})
        await emit(sid, {"type":"assistant.delta", "turnId":tid, "promptId":prompt, "messageId":"assistant-"+prompt, "delta":answer})
        await emit(sid, {"type":"turn.ended", "turnId":tid, "promptId":prompt, "reason":"completed"})
        record(folder, kind="kimi_terminal", session=sid, turn=tid, answer=answer)

    async def handle(request):
        path = request.path
        if path.endswith("healthz"):
            return web.json_response({"code":0,"data":{}})
        if request.headers.get("Authorization") != "Bearer private-epic3":
            return web.json_response({}, status=401)
        if path == "/api/v1/ws":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            sockets.add(ws)
            try:
                async for item in ws:
                    if item.type == web.WSMsgType.TEXT:
                        message = json.loads(item.data)
                        record(folder, kind="kimi_control", type=message.get("type"))
                        await ws.send_json({"type":"ack", "id":message.get("id"), "payload":{"ok":True}})
            finally:
                sockets.discard(ws)
            return ws
        data = {}
        if path == "/api/v1/sessions" and request.method == "POST":
            sid = "session-" + uuid.uuid4().hex
            sessions[sid] = {"messages":[]}
            data = {"id":sid}
        elif "/sessions/" in path:
            sid = path.split("/sessions/", 1)[1].split("/", 1)[0]
            if sid not in sessions:
                return web.json_response({}, status=404)
            if path.endswith("/prompts"):
                body = await request.json()
                question = "".join(x.get("text", "") for x in body["content"])
                prompt = "prompt-" + uuid.uuid4().hex
                data = {"prompt_id":prompt}
                record(folder, kind="kimi_submit", session=sid, prompt=prompt, question=question)
                asyncio.create_task(finish(sid, prompt, question))
            elif path.endswith("/messages"):
                data = {"items":sessions[sid]["messages"]}
            elif path.endswith("/status"):
                data = {"phase":{"kind":"idle"}, "context_tokens":32, "max_context_tokens":2048}
            elif path.endswith("/snapshot"):
                data = {"session":{"usage":{"input_tokens":32,"output_tokens":16}}}
            else:
                data = {"id":sid}
        return web.json_response({"code":0,"data":data})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    print("Token: private-epic3", flush=True)
    web.run_app(app, host="127.0.0.1", port=port, print=None)
    return 0


def codex_fixture(folder):
    folder = Path(folder)
    output_lock = threading.Lock()
    def send(message):
        with output_lock:
            print(json.dumps(message), flush=True)
    def notify(method, params):
        send({"method":method,"params":params})
    def finish(thread, turn, question):
        if question == "HOLD":
            gate = folder / "release-old-codex"
            while not gate.exists():
                threading.Event().wait(.02)
        answer = "ANSWER:" + question
        notify("item/agentMessage/delta", {"threadId":thread,"turnId":turn,"itemId":"item-"+turn,"delta":answer})
        notify("item/completed", {"threadId":thread,"turnId":turn,"item":{"id":"item-"+turn,"type":"agentMessage","phase":"final_answer","text":answer}})
        notify("turn/completed", {"threadId":thread,"turn":{"id":turn,"status":"completed"}})
        record(folder, kind="codex_terminal", thread=thread, turn=turn, answer=answer)
    for line in sys.stdin:
        message = json.loads(line)
        method, params = message.get("method"), message.get("params") or {}
        if "id" not in message:
            continue
        result = {}
        if method == "thread/start":
            tid = "thread-"+uuid.uuid4().hex
            result = {"thread":{"id":tid}}
            record(folder, kind="codex_thread", thread=tid)
        elif method == "thread/resume":
            result = {"thread":{"id":params["threadId"]}}
        elif method in {"turn/start", "turn/steer"}:
            tid = params["threadId"]
            turn = "turn-"+uuid.uuid4().hex
            question = "".join(x.get("text", "") for x in params.get("input", []))
            result = {"turn":{"id":turn,"status":"inProgress"}}
            send({"id":message["id"],"result":result})
            notify("turn/started", {"threadId":tid,"turn":{"id":turn,"status":"inProgress"}})
            record(folder, kind="codex_submit", thread=tid, turn=turn, question=question)
            threading.Thread(target=finish,args=(tid,turn,question),daemon=True).start()
            continue
        elif method == "thread/read":
            result = {"thread":{"id":params.get("threadId"),"turns":[]}}
        send({"id":message["id"],"result":result})
    return 0


def worker_fixture(folder):
    sys.path.insert(0, str(ROOT))
    import codex_client
    import codex_worker_process
    codex_client.build_codex_app_server_command = lambda *a, **kw: [sys.executable, str(Path(__file__).resolve()), "--codex-fixture", folder]
    # Prevent the controlled fixture from copying any personal authentication.
    codex_client.build_codex_app_server_env = lambda *a, **kw: (dict(os.environ, CODEX_HOME=str(Path(folder)/"codex-home")), Path(folder)/"codex-home")
    return codex_worker_process.main()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--kimi-fixture":
        return kimi_fixture(sys.argv[2], int(sys.argv[sys.argv.index("--port")+1]))
    if len(sys.argv) > 1 and sys.argv[1] == "--codex-fixture":
        return codex_fixture(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == "--worker-fixture":
        return worker_fixture(sys.argv[2])
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "-s", "tests/test_model_session_recovery_ui_automation.py"], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
