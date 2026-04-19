from __future__ import annotations

import asyncio
import threading
import uuid

from aiohttp import ClientSession

import main


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if 200 <= self.status_code < 300:
            return
        raise RuntimeError(f"http {self.status_code}: {self.text}")


class _FakeCouchDbSession:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.changes: list[dict] = []
        self.last_seq = 0
        self.writes: list[list[dict]] = []

    def seed_notebook(self, notebook_id: str, title: str, entries: list[dict] | None = None) -> None:
        self._store_doc(
            {
                "_id": f"notebook:{notebook_id}",
                "type": "notebook",
                "title": title,
                "created_at": "2026-04-19T10:00:00+00:00",
                "updated_at": "2026-04-19T10:00:00+00:00",
            }
        )
        for index, entry in enumerate(list(entries or []), start=1):
            self._store_doc(
                {
                    "_id": f"entry:{entry['id']}",
                    "type": "entry",
                    "notebook_id": f"notebook:{notebook_id}",
                    "content": entry["content"],
                    "sort_order": int(entry.get("sort_order") or index),
                    "source": str(entry.get("source") or "manual"),
                    "created_at": str(entry.get("created_at") or "2026-04-19T10:00:00+00:00"),
                    "updated_at": str(entry.get("updated_at") or "2026-04-19T10:00:00+00:00"),
                }
            )

    def upsert_entry(self, *, notebook_id: str, entry_id: str, content: str, sort_order: int) -> None:
        self._store_doc(
            {
                "_id": f"entry:{entry_id}",
                "type": "entry",
                "notebook_id": f"notebook:{notebook_id}",
                "content": content,
                "sort_order": sort_order,
                "source": "manual",
                "created_at": "2026-04-19T11:00:00+00:00",
                "updated_at": "2026-04-19T11:00:00+00:00",
            }
        )

    def get(self, url: str, *, params: dict | None = None, timeout: int | None = None):
        assert url.endswith("/zhuge_notes/_changes")
        assert timeout == 10
        params = dict(params or {})
        since = self._normalize_since(params.get("since"))
        results = []
        for change in self.changes:
            if change["seq"] <= since:
                continue
            result = {"seq": str(change["seq"]), "id": change["id"]}
            if change.get("deleted"):
                result["deleted"] = True
            doc = self.docs.get(change["id"])
            if doc is not None:
                result["doc"] = dict(doc)
            results.append(result)
        return _FakeResponse({"results": results, "last_seq": str(self.last_seq)})

    def post(self, url: str, *, json: dict | None = None, timeout: int | None = None):
        assert url.endswith("/zhuge_notes/_bulk_docs")
        assert timeout == 10
        docs = [dict(item) for item in list((json or {}).get("docs") or [])]
        self.writes.append(docs)
        payload = []
        for doc in docs:
            doc_id = str(doc.get("_id") or "")
            existing = self.docs.get(doc_id)
            expected_rev = str(existing.get("_rev") or "") if existing else ""
            supplied_rev = str(doc.get("_rev") or "")
            if existing is not None and expected_rev and supplied_rev != expected_rev:
                payload.append({"id": doc_id, "error": "conflict", "reason": "document update conflict"})
                continue
            next_generation = self._next_generation(expected_rev)
            next_rev = f"{next_generation}-{uuid.uuid4().hex[:8]}"
            stored = dict(doc)
            stored["_rev"] = next_rev
            self._store_doc(stored, record_write=False)
            payload.append({"id": doc_id, "ok": True, "rev": next_rev})
        return _FakeResponse(payload)

    def _store_doc(self, doc: dict, *, record_write: bool = True) -> None:
        stored = dict(doc)
        stored["_rev"] = str(stored.get("_rev") or f"1-{uuid.uuid4().hex[:8]}")
        self.docs[stored["_id"]] = stored
        if record_write:
            self.last_seq += 1
            self.changes.append(
                {
                    "seq": self.last_seq,
                    "id": stored["_id"],
                    "deleted": bool(stored.get("_deleted")),
                }
            )

    @staticmethod
    def _next_generation(rev: str) -> int:
        try:
            return int(str(rev or "0").split("-", 1)[0]) + 1
        except Exception:
            return 1

    @staticmethod
    def _normalize_since(value) -> int:
        try:
            return int(str(value or "0").split("-", 1)[0])
        except Exception:
            return 0


async def _connect_notes_client(port: int, token: str = "secret"):
    session = ClientSession()
    ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?token={token}")
    connected = (await ws.receive()).json()
    assert connected["type"] == "connected"
    return session, ws


async def _request(ws, payload: dict):
    await ws.send_json(payload)
    while True:
        message = (await ws.receive()).json()
        if message.get("type") == "response" and message.get("id") == payload.get("id"):
            return message


async def _wait_for_event(ws, event_type: str, *, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = (await ws.receive(timeout=timeout)).json()
        if message.get("type") == event_type:
            return message
    raise AssertionError(f"did not receive event {event_type}")


def test_background_couchdb_sync_routes_callbacks_via_callafter(frame, monkeypatch):
    session = _FakeCouchDbSession()
    session.seed_notebook(
        "shared-notebook",
        "Shared notebook",
        entries=[{"id": "seed-entry", "content": "existing body", "sort_order": 1}],
    )
    frame.notes_sync.configure("http://couchdb.test", "zhuge_notes", session=session)

    main_thread = threading.get_ident()
    direct_calls: list[str] = []
    scheduled: list[tuple[object, tuple, dict]] = []
    refreshed_threads: list[int] = []
    status_threads: list[int] = []

    def fake_call_after(func, *args, **kwargs):
        scheduled.append((func, args, kwargs))
        return True

    def fake_set_status_text(_text):
        current = threading.get_ident()
        status_threads.append(current)
        if current != main_thread:
            direct_calls.append("status")

    def fake_refresh_ui():
        current = threading.get_ident()
        refreshed_threads.append(current)
        if current != main_thread:
            direct_calls.append("refresh")

    monkeypatch.setattr(frame, "_call_after_if_alive", fake_call_after)
    monkeypatch.setattr(frame, "SetStatusText", fake_set_status_text)
    monkeypatch.setattr(frame, "_notes_refresh_ui", fake_refresh_ui)

    worker = threading.Thread(target=frame.notes_sync.sync_once)
    worker.start()
    worker.join(timeout=5)

    assert worker.is_alive() is False
    assert direct_calls == []
    assert any(getattr(item[0], "__name__", "") == "_on_notes_sync_status_changed" for item in scheduled)
    assert any(getattr(item[0], "__name__", "") == "_on_notes_remote_ops_applied" for item in scheduled)

    for func, args, kwargs in scheduled:
        func(*args, **kwargs)

    assert frame.notes_sync_hint == "笔记已同步"
    assert refreshed_threads
    assert all(thread_id == main_thread for thread_id in refreshed_threads)
    assert status_threads
    assert all(thread_id == main_thread for thread_id in status_threads)


def test_couchdb_sync_close_clears_configuration_and_prevents_background_schedule(frame, monkeypatch):
    frame.notes_sync.configure("http://couchdb.test", "zhuge_notes", session=_FakeCouchDbSession())
    started: list[bool] = []

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(True)

    monkeypatch.setattr(main.threading, "Thread", _FakeThread)

    frame.notes_sync.close()

    assert frame.notes_sync.is_configured() is False
    frame._schedule_notes_couchdb_sync()
    assert started == []


def test_legacy_push_ops_does_not_synthesize_acked_ids(frame):
    result = frame.notes_sync.push_ops(
        [
            {
                "op_id": "op-1",
                "entity_type": "notebook",
                "entity_id": "nb-1",
                "action": "create",
                "payload": {"title": "push notebook"},
            }
        ]
    )

    assert result["acked"] == []


def test_legacy_notes_websocket_path_remains_active(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    seen_pushes: list[list[dict]] = []

    class _FakeNotesSync:
        def snapshot(self):
            return {"cursor": "10", "notebooks": [{"id": "nb-1"}], "entries": []}

        def pull_since(self, cursor):
            return {"cursor": f"{cursor}-next", "ops": [{"entity_type": "entry", "entity_id": "entry-1"}]}

        def push_ops(self, ops):
            seen_pushes.append(list(ops))
            result = {"cursor": "11", "applied": [], "conflicts": [], "acked": []}
            frame._on_notes_sync_push_result(result)
            return result

        def subscribe(self, payload=None):
            return {"cursor": "12", "subscribed": True, "request": dict(payload or {})}

        def ack(self, payload=None):
            return {"cursor": "13", "acked": [], "request": dict(payload or {})}

        def ping(self, payload=None):
            return {"cursor": "14", "pong": True, "request": dict(payload or {})}

    frame.notes_sync = _FakeNotesSync()
    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session_a, ws_a = await _connect_notes_client(frame._remote_ws_server.bound_port)
            session_b, ws_b = await _connect_notes_client(frame._remote_ws_server.bound_port)
            try:
                subscribe = await _request(ws_b, {"id": "sub-1", "type": "notes_subscribe", "cursor": "0"})
                assert subscribe["ok"] is True
                assert subscribe["body"]["cursor"] == "12"

                pull = await _request(ws_b, {"id": "pull-1", "type": "notes_pull_since", "cursor": "7"})
                assert pull["ok"] is True
                assert pull["body"]["cursor"] == "7-next"

                push = await _request(
                    ws_a,
                    {
                        "id": "push-1",
                        "type": "notes_push_ops",
                        "ops": [{"op_id": "op-1", "entity_type": "entry", "entity_id": "entry-1", "action": "create"}],
                    },
                )
                assert push["ok"] is True
                assert push["body"]["cursor"] == "11"
                assert push["body"]["acked"] == []

                changed = await _wait_for_event(ws_b, "notes_changed")
                assert changed["cursor"] == "11"
            finally:
                await ws_a.close()
                await ws_b.close()
                await session_a.close()
                await session_b.close()

        asyncio.run(_run())
        assert seen_pushes == [[{"op_id": "op-1", "entity_type": "entry", "entity_id": "entry-1", "action": "create"}]]
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_mobile_offline_entry_syncs_to_desktop_after_reconnect(frame):
    session = _FakeCouchDbSession()
    session.seed_notebook(
        "shared-notebook",
        "Shared notebook",
        entries=[{"id": "seed-entry", "content": "existing body", "sort_order": 1}],
    )
    frame.notes_sync.configure("http://couchdb.test", "zhuge_notes", session=session)

    frame.notes_sync.sync_once()
    initial_checkpoint = frame.notes_sync.get_checkpoint()

    offline_body = "offline mobile body " * 400
    session.upsert_entry(
        notebook_id="shared-notebook",
        entry_id="offline-entry",
        content=offline_body,
        sort_order=2,
    )

    frame.notes_sync.sync_once()

    notebook = frame.notes_store.get_notebook("shared-notebook")
    entries = frame.notes_store.list_entries("shared-notebook")
    assert notebook is not None
    assert any(item.id == "offline-entry" and item.content == offline_body for item in entries)
    assert frame.notes_sync.get_checkpoint() != initial_checkpoint


def test_desktop_sync_pushes_dirty_documents_to_couchdb_and_clears_dirty_state(frame):
    notebook = frame.notes_store.create_notebook("Desktop draft")
    entry = frame.notes_store.create_entry(notebook.id, "desktop body", source="manual")
    session = _FakeCouchDbSession()
    frame.notes_sync.configure("http://couchdb.test", "zhuge_notes", session=session)

    frame.notes_sync.sync_once()

    notebook_doc = session.docs.get(f"notebook:{notebook.id}")
    entry_doc = session.docs.get(f"entry:{entry.id}")
    snapshot = frame.notes_store.load_documents()
    notebook_row = next(item for item in snapshot.notebooks if item.id == notebook.id)
    entry_row = next(item for item in snapshot.entries if item.id == entry.id)

    assert notebook_doc is not None
    assert notebook_doc["title"] == "Desktop draft"
    assert entry_doc is not None
    assert entry_doc["content"] == "desktop body"
    assert entry_doc["notebook_id"] == f"notebook:{notebook.id}"
    assert notebook_row.dirty is False
    assert entry_row.dirty is False
    assert notebook_row.rev
    assert entry_row.rev
