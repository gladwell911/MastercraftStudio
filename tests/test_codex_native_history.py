"""Codex inventory and exact continuation through desktop and remote entry points."""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from remote_nats import RemoteNatsTransport


real_codex = pytest.mark.skipif(
    not os.getenv("MC_TEST_CODEX_NATIVE"), reason="requires the isolated Codex runtime",
)


@pytest.fixture
def codex_homes(tmp_path, monkeypatch, request):
    import codex_history
    import main
    from codex_worker_client import CodexWorkerClient

    calls = []
    hold = request.getfixturevalue("codex_model_request_gate") if (
        request is not None and "codex_model_request_gate" in request.fixturenames) else None

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if hold and "MC_SETTINGS_HOLD" in str(payload["input"][-1]):
                started, release = hold
                started.set()
                if not release.wait(20):
                    return
            item = {"id": "msg_fixture", "type": "message", "status": "completed", "role": "assistant",
                    "phase": "final_answer", "content": [{"type": "output_text", "text": "CODX_08_FULL_TAIL",
                                                           "annotations": []}]}
            response = {"id": "resp_fixture", "object": "response", "status": "completed", "output": [item],
                        "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for kind, payload in (
                ("response.created", {"response": dict(response, output=[], status="in_progress")}),
                ("response.output_item.added", {"item": dict(item, content=[], status="in_progress"),
                                                "output_index": 0}),
                ("response.output_text.delta", {"item_id": item["id"], "output_index": 0, "content_index": 0,
                                                "delta": "CODX_08_FULL_TAIL"}),
                ("response.output_item.done", {"item": item, "output_index": 0}),
                ("response.completed", {"response": response}),
            ):
                event = json.dumps(dict(payload, type=kind))
                self.wfile.write(f"event: {kind}\ndata: {event}\n\n".encode())
                self.wfile.flush()

    ordinary = tmp_path / "profile with apostrophe's" / ".codex"
    private = tmp_path / ".codex-home"
    work = tmp_path / "original project"
    other_work = tmp_path / "other project"
    for folder in (ordinary, private, work, other_work):
        folder.mkdir(parents=True)
    executable = os.environ["MC_TEST_CODEX_NATIVE"]
    monkeypatch.setenv("CODEX_BIN", executable)
    monkeypatch.setenv("USERPROFILE", str(ordinary.parent))
    monkeypatch.setenv("CODEX_HOME", str(ordinary))
    monkeypatch.setenv("MC_LOCAL_FAKE_MODEL_KEY", "loopback-fixture")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(codex_history, "codex_worker_workspace", lambda: tmp_path)

    def worker_factory(**options):
        def start(argv):
            return subprocess.Popen(argv, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        return CodexWorkerClient(**options, process_factory=start)

    monkeypatch.setattr(main, "CodexWorkerClient", worker_factory)
    with ThreadingHTTPServer(("127.0.0.1", 0), Model) as http:
        threading.Thread(target=http.serve_forever, daemon=True).start()
        config = ('approval_policy="never"\nsandbox_mode="read-only"\n'
                  'model="gpt-5.4"\nmodel_provider="fixture"\n'
                  '[model_providers.fixture]\nname="Fixture"\n'
                  f'base_url="http://127.0.0.1:{http.server_port}/v1"\n'
                  'env_key="MC_LOCAL_FAKE_MODEL_KEY"\nwire_api="responses"\n')
        for cwd in (work, other_work):
            config += f"\n[projects.{json.dumps(str(cwd))}]\ntrust_level='trusted'\n"
        for home in (ordinary, private):
            (home / "config.toml").write_text(config, encoding="utf-8")

        def native(home, cwd, *args):
            result = subprocess.run([executable, "exec", "--json", "--skip-git-repo-check", *args],
                                    cwd=cwd, env=dict(os.environ, CODEX_HOME=str(home)),
                                    timeout=20, capture_output=True, encoding="utf-8")
            assert result.returncode == 0, result.stderr
            return next(event["thread_id"] for event in map(json.loads, result.stdout.splitlines())
                        if event["type"] == "thread.started")

        yield ordinary, private, work, other_work, calls, native
        http.shutdown()


@real_codex
def test_native_codex_discovers_home_qualified_history(codex_homes, frame, wait_for_wx):
    ordinary, private, work, other_work, calls, native = codex_homes
    question = "Same terminal question"
    from winpty import PtyProcess
    from codex_client import CodexAppServerClient, DEFAULT_CODEX_MODEL

    tui = PtyProcess.spawn([os.environ["MC_TEST_CODEX_NATIVE"], "--no-alt-screen", question], cwd=str(work),
                           env=dict(os.environ, CODEX_HOME=str(ordinary), TERM="xterm-256color"))
    answer_drawn = threading.Event()

    def read_tui():
        rendered = ""
        try:
            while tui.isalive():
                rendered = (rendered + tui.read(4096))[-8192:]
                if "CODX_08_FULL_TAIL" in rendered:
                    answer_drawn.set()
        except (EOFError, OSError):
            pass

    threading.Thread(target=read_tui, daemon=True).start()
    try:
        assert answer_drawn.wait(20), "the standalone native terminal did not render its final answer"
    finally:
        tui.close(force=True)
    lookup = CodexAppServerClient(native_home=ordinary, use_native_config=True)
    try:
        terminal, = lookup.request("thread/list", {"sourceKinds": ["cli"], "modelProviders": []})["data"]
        ordinary_id = terminal["id"]
    finally:
        lookup.close()
    private_id = native(private, other_work, question)
    for owner, sid, home in (("mc-ordinary", ordinary_id, ordinary), ("mc-private", private_id, private)):
        frame.chat_store.upsert_chat({"id": owner, "model": DEFAULT_CODEX_MODEL, "codex_thread_id": sid,
                                      "codex_storage_id": os.path.normcase(str(home))})
    seed_calls = len(calls)
    remote = RemoteNatsTransport(pair_id="default", token="synthetic",
                                on_history_list=frame._remote_api_history_list_ui,
                                on_history_read=frame._remote_api_history_read_ui)

    def listed():
        status, result = remote._route_command({"type": "history_list"})
        assert status == 200
        return [row for row in result["chats"] if row["turn_count"] == 1]

    wait_for_wx(lambda: len(listed()) == 2, "both Codex stores in the remote history", timeout=15)
    rows = {row["native_workspace"]: row for row in listed()}
    assert len({row["chat_id"] for row in rows.values()}) == 2
    for cwd, thread_id, home in ((work, ordinary_id, ordinary), (other_work, private_id, private)):
        row = rows[str(cwd)]
        assert thread_id in row["native_open_command"]
        assert str(home).replace("'", "''") in row["native_open_command"]
        status, page = remote._route_command({"type": "history_read", "chat_id": row["chat_id"], "limit": 1})
        assert status == 200
        turn, = page["chat"]["turns"]
        assert (turn["question"], turn["answer"]) == (question, "CODX_08_FULL_TAIL")
    assert len(calls) == seed_calls

    desktop_id = rows[str(work)]["chat_id"]
    phone_id = rows[str(other_work)]["chat_id"]
    assert frame._show_history_chat(desktop_id)
    frame.input_edit.SetValue("Desktop continuation")
    frame._on_send_clicked(None)
    wait_for_wx(lambda: len(calls) > seed_calls, "native continuation from MC", timeout=15)
    assert question in str(calls[seed_calls]["input"])
    assert calls[seed_calls]["client_metadata"]["session_id"] == ordinary_id
    status, reply = frame._remote_api_message_ui({
        "chat_id": phone_id, "model": rows[str(other_work)]["model"], "text": "Phone continuation",
    })
    assert status == 200, reply
    wait_for_wx(lambda: len(calls) > seed_calls + 1, "native continuation from RC", timeout=15)
    assert question in str(calls[seed_calls + 1]["input"])
    assert calls[seed_calls + 1]["client_metadata"]["session_id"] == private_id

    def done(chat_id):
        return remote._route_command({"type": "history_read", "chat_id": chat_id, "limit": 1})[1]["chat"]["turns"][-1]

    wait_for_wx(lambda: all(done(i)["answer"] == "CODX_08_FULL_TAIL" for i in (desktop_id, phone_id)),
                "both completed replies")
    for home, cwd, original, followup in (
        (ordinary, work, ordinary_id, "Desktop continuation"),
        (private, other_work, private_id, "Phone continuation"),
    ):
        assert native(home, cwd, "resume", original, "Original terminal continuation") == original
        assert followup in str(calls[-1]["input"])



@real_codex
def test_app_server_and_legacy_codex_history_keep_their_owner(codex_homes, frame, monkeypatch, wait_for_wx):
    from codex_client import CodexAppServerClient, DEFAULT_CODEX_MODEL, _create_directory_link
    from main import model_display_name

    ordinary, private, work, other_work, calls, native = codex_homes
    ordinary_skills = ordinary / "skills"
    ordinary_plugins = ordinary / "plugins"
    ordinary_skills.mkdir()
    ordinary_plugins.mkdir()
    _create_directory_link(ordinary_skills, private / "skills")
    _create_directory_link(ordinary_plugins, private / "plugins")
    config_before = (private / "config.toml").read_text(encoding="utf-8")
    private_id = native(private, work, "Legacy private question")
    frame.chat_store.upsert_chat({
        "id": "legacy-codex-owner", "model": "codex/gpt-5.4-medium", "title": "Old saved chat",
        "codex_thread_id": private_id,
    })
    ended = threading.Event()
    server = CodexAppServerClient(native_home=ordinary, use_native_config=True,
                                  on_event=lambda event: ended.set() if event.type == "turn_completed" else None)
    try:
        app_server_id = server.start_thread(str(other_work), approval_policy="never", sandbox="read-only")["thread"]["id"]
        server.request("thread/name/set", {"threadId": app_server_id, "name": "App Server fixture"})
        server.start_turn(app_server_id, "App Server history question")
        assert ended.wait(12)
    finally:
        server.close()
    frame.chat_store.upsert_chat({"id": "mc-app-server", "model": DEFAULT_CODEX_MODEL,
                                  "codex_thread_id": app_server_id,
                                  "codex_storage_id": os.path.normcase(str(ordinary))})

    def listing():
        return frame._remote_api_history_list_ui()[1]["chats"]

    wait_for_wx(lambda: len([row for row in listing() if row.get("native_open_command")]) == 2,
                "legacy private and explicit App Server records", timeout=15)
    rows = [row for row in listing() if row.get("native_open_command")]
    assert len([row for row in rows if private_id in row["native_open_command"]]) == 1
    legacy = next(row for row in rows if private_id in row["native_open_command"])
    assert (legacy["chat_id"], legacy["model"], legacy["title"], legacy["turn_count"]) == (
        "legacy-codex-owner", "codex/gpt-5.4-medium", "Old saved chat", 1,
    )
    assert any(app_server_id in row["native_open_command"] for row in rows)
    assert len(calls) == 2
    assert (private / "config.toml").read_text(encoding="utf-8") == config_before
    assert (private / "skills").resolve() == ordinary_skills
    assert (private / "plugins").resolve() == ordinary_plugins

    wait_for_wx(lambda: not frame._native_history_worker.is_alive(), "inventory finished")
    missing_home = private.with_name("temporarily unavailable")
    private.rename(missing_home)
    try:
        wait_for_wx(lambda: any(row["chat_id"] == legacy["chat_id"] and row.get("native_history_status")
                                for row in listing()), "unavailable private store", timeout=15)
        history = frame._remote_api_history_read_ui({"chat_id": legacy["chat_id"], "limit": 1})[1]
        assert history["chat"]["turns"][-1]["answer"] == "CODX_08_FULL_TAIL"
    finally:
        missing_home.rename(private)
    wait_for_wx(lambda: any(row["chat_id"] == legacy["chat_id"] and row.get("native_history_status") == ""
                            for row in listing()), "recovered private store", timeout=15)

    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(work))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args, **_kwargs: None)
    frame.model_combo.SetValue(model_display_name(DEFAULT_CODEX_MODEL))
    frame._on_new_chat_clicked(None, title="MC new Codex chat", title_manual=True)
    desktop_id = frame.active_chat_id
    frame.input_edit.SetValue("MC first question")
    frame._on_send_clicked(None)
    wait_for_wx(lambda: any("MC first question" in str(call["input"]) for call in calls), "MC new Codex send")
    status, created = frame._remote_api_new_chat_ui({"title": "RC new Codex chat", "model": DEFAULT_CODEX_MODEL})
    assert status == 200
    phone_id = created["chat_id"]
    status, reply = frame._remote_api_message_ui({
        "chat_id": phone_id, "model": DEFAULT_CODEX_MODEL, "text": "RC first question",
    })
    assert status == 200, reply

    def completed(owner):
        response = frame._remote_api_history_read_ui({"chat_id": owner, "limit": 1})[1]
        turns = response.get("chat", {}).get("turns", [])
        return turns and turns[-1]["answer"] == "CODX_08_FULL_TAIL"

    wait_for_wx(lambda: completed(desktop_id) and completed(phone_id), "both newly created Codex chats", timeout=20)
    for owner, seed in ((desktop_id, "MC first question"), (phone_id, "RC first question")):
        chat = frame._history_chat_for_id(owner)
        thread = chat["codex_thread_id"]
        assert thread in chat["native_open_command"]
        assert str(private) in chat["native_open_command"]
        assert native(private, work, "resume", thread, "Native followup") == thread
        assert seed in str(calls[-1]["input"])



def test_cleared_codex_history_cannot_be_rediscovered_on_the_same_chat(tmp_path):
    from chat_store import ChatStore
    from native_history import NativeSessionIdentity, NativeSessionSnapshot

    path = tmp_path / "retired.sqlite"
    store = ChatStore(path)
    store.initialize()

    def snapshot(thread):
        return NativeSessionSnapshot(
            NativeSessionIdentity("default", "codex", "private", thread),
            "account", "codex/main", str(tmp_path), "Same title", 1,
            chat_fields={"codex_thread_id": thread, "codex_storage_id": "private"},
            turns=[{"question": thread, "answer_md": "Saved answer", "request_status": "done"}],
            legacy_fields={"codex_thread_id": thread},
        )

    original = snapshot("old-thread")
    chat_id, _ = store.native_history_target(original)
    assert store.merge_native_history(original, expected_chat_id=chat_id)["turn_count"] == 1
    store.begin_clear_operation(chat_id, idempotency_key="clear native chat")
    store = ChatStore(path)
    store.initialize()
    assert not store.load_turns(chat_id)
    assert store.native_history_target(original) == ("", 0)
    assert not store.load_chat(chat_id).get("native_identity")

    current = snapshot("new-thread")
    stored = store.load_chat(chat_id)
    store.upsert_chat(dict(stored, codex_thread_id=current.identity.session_id, codex_storage_id="private"))
    assert store.native_history_target(current)[0] == chat_id
    revision = store.get_clear_reconciliation_authority(chat_id)["revision"]
    assert store.merge_native_history(current, expected_chat_id=chat_id, expected_revision=revision)["turn_count"] == 1
    assert store.native_history_target(original) == ("", 0)
