# Chat History SQLite Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent packaged-app browsing stalls by moving large chat history and execution data out of `app_state.json` into incremental SQLite storage.

**Architecture:** Add a wx-independent `ChatStore` backed by `chat_history.db`. Keep `app_state.json` for small preferences and active identifiers only, then load chat summaries, turns, and execution steps from SQLite on demand.

**Tech Stack:** Python 3.11, sqlite3, wxPython, pytest, existing PyInstaller packaging.

---

## File Structure

- Create `chat_store.py`: SQLite schema, migration helpers, summary/turn/execution CRUD.
- Modify `main.py`: initialize `ChatStore`, slim `_save_state/_load_state`, read summaries for history UI, load turns and execution steps on demand.
- Add `tests/test_chat_store_unit.py`: store schema, CRUD, migration, ordering, retention.
- Extend `tests/test_main_unit.py`: app-state slimming, migration, packaged data path, history loading behavior.
- Extend `tests/test_history_ui_automation.py` or `tests/test_codex_ui_responsiveness_automation.py`: large-history keyboard responsiveness.

## Task 1: Add ChatStore Schema And Basic CRUD

**Files:**
- Create: `chat_store.py`
- Test: `tests/test_chat_store_unit.py`

- [ ] **Step 1: Write failing store tests**

```python
from pathlib import Path

from chat_store import ChatStore


def test_chat_store_initializes_schema_and_lists_summaries(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat(
        {
            "id": "chat-1",
            "title": "First",
            "model": "codex/main",
            "created_at": 1.0,
            "updated_at": 2.0,
            "pinned": False,
            "detail_panel_mode": "answers",
        }
    )

    summaries = store.list_chat_summaries()

    assert summaries == [
        {
            "id": "chat-1",
            "title": "First",
            "model": "codex/main",
            "created_at": 1.0,
            "updated_at": 2.0,
            "pinned": False,
            "title_manual": False,
            "title_source": "default",
            "title_updated_at": 2.0,
            "title_revision": 1,
            "detail_panel_mode": "answers",
            "turn_count": 0,
        }
    ]


def test_chat_store_replaces_and_loads_turns(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    turns = [
        {"question": "q1", "answer_md": "a1", "model": "codex/main", "created_at": 1.0},
        {"question": "q2", "answer_md": "a2", "model": "codex/main", "created_at": 2.0},
    ]

    store.replace_turns("chat-1", turns)

    assert store.load_turns("chat-1") == turns
    assert store.list_chat_summaries()[0]["turn_count"] == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_store_unit.py -q
```

Expected: fails with `ModuleNotFoundError: No module named 'chat_store'`.

- [ ] **Step 3: Implement minimal `ChatStore`**

Create `chat_store.py` with:

```python
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ChatStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新聊天',
                    model TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    title_manual INTEGER NOT NULL DEFAULT 0,
                    title_source TEXT NOT NULL DEFAULT 'default',
                    title_updated_at REAL NOT NULL DEFAULT 0,
                    title_revision INTEGER NOT NULL DEFAULT 1,
                    detail_panel_mode TEXT NOT NULL DEFAULT 'answers',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS turns (
                    chat_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (chat_id, turn_index)
                );
                CREATE INDEX IF NOT EXISTS idx_chats_order
                    ON chats(pinned, updated_at DESC, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_turns_chat
                    ON turns(chat_id, turn_index);
                """
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_chat(self, chat: dict) -> None:
        chat_id = str((chat or {}).get("id") or "").strip()
        if not chat_id:
            return
        created = float((chat or {}).get("created_at") or 0.0)
        updated = float((chat or {}).get("updated_at") or created or 0.0)
        title_updated = float((chat or {}).get("title_updated_at") or updated)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chats (
                    id, title, model, created_at, updated_at, pinned, title_manual,
                    title_source, title_updated_at, title_revision, detail_panel_mode, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    model=excluded.model,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    pinned=excluded.pinned,
                    title_manual=excluded.title_manual,
                    title_source=excluded.title_source,
                    title_updated_at=excluded.title_updated_at,
                    title_revision=excluded.title_revision,
                    detail_panel_mode=excluded.detail_panel_mode,
                    metadata_json=excluded.metadata_json
                """,
                (
                    chat_id,
                    str(chat.get("title") or "新聊天"),
                    str(chat.get("model") or ""),
                    created,
                    updated,
                    1 if chat.get("pinned") else 0,
                    1 if chat.get("title_manual") else 0,
                    str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")),
                    title_updated,
                    int(chat.get("title_revision") or 1),
                    "execution" if str(chat.get("detail_panel_mode") or "") == "execution" else "answers",
                    json.dumps({k: v for k, v in chat.items() if k not in {"turns", "execution_steps"}}, ensure_ascii=False),
                ),
            )

    def list_chat_summaries(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, COUNT(t.turn_index) AS turn_count
                FROM chats c
                LEFT JOIN turns t ON t.chat_id = c.id
                GROUP BY c.id
                ORDER BY c.pinned DESC, c.updated_at DESC, c.created_at DESC
                """
            ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def replace_turns(self, chat_id: str, turns: list[dict]) -> None:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM turns WHERE chat_id = ?", (chat_id,))
            conn.executemany(
                "INSERT INTO turns(chat_id, turn_index, payload_json) VALUES (?, ?, ?)",
                [
                    (chat_id, idx, json.dumps(turn, ensure_ascii=False))
                    for idx, turn in enumerate(turns or [])
                    if isinstance(turn, dict)
                ],
            )

    def load_turns(self, chat_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id = ? ORDER BY turn_index",
                (str(chat_id or "").strip(),),
            ).fetchall()
        out = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                out.append(payload)
        return out

    def _summary_from_row(self, row) -> dict:
        return {
            "id": str(row["id"] or ""),
            "title": str(row["title"] or "新聊天"),
            "model": str(row["model"] or ""),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
            "pinned": bool(row["pinned"]),
            "title_manual": bool(row["title_manual"]),
            "title_source": str(row["title_source"] or "default"),
            "title_updated_at": float(row["title_updated_at"] or row["updated_at"] or 0.0),
            "title_revision": int(row["title_revision"] or 1),
            "detail_panel_mode": str(row["detail_panel_mode"] or "answers"),
            "turn_count": int(row["turn_count"] or 0),
        }
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_store_unit.py -q
```

Expected: all tests in `tests/test_chat_store_unit.py` pass.

## Task 2: Add Execution Step Storage And Retention

**Files:**
- Modify: `chat_store.py`
- Test: `tests/test_chat_store_unit.py`

- [ ] **Step 1: Write failing execution tests**

```python
def test_chat_store_appends_and_loads_execution_steps(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db")
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    store.append_execution_step(
        "chat-1",
        {
            "turn_idx": 0,
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：检查",
            "detail_text": "检查 main.py",
        },
    )

    assert store.load_execution_steps("chat-1", turn_idx=0) == [
        {
            "turn_idx": 0,
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：检查",
            "detail_text": "检查 main.py",
        }
    ]


def test_chat_store_prunes_execution_steps_per_turn(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db", max_execution_steps_per_turn=3)
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})

    for idx in range(5):
        store.append_execution_step("chat-1", {"turn_idx": 0, "list_text": f"step {idx}"})

    assert [step["list_text"] for step in store.load_execution_steps("chat-1", turn_idx=0)] == [
        "step 2",
        "step 3",
        "step 4",
    ]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_store_unit.py -k execution -q
```

Expected: fails because `append_execution_step` and `load_execution_steps` do not exist.

- [ ] **Step 3: Implement execution table methods**

Extend `initialize()` with:

```python
CREATE TABLE IF NOT EXISTS execution_steps (
    chat_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    turn_idx INTEGER,
    event_type TEXT NOT NULL DEFAULT '',
    display_kind TEXT NOT NULL DEFAULT '',
    list_text TEXT NOT NULL DEFAULT '',
    detail_text TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY (chat_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_execution_chat_turn
    ON execution_steps(chat_id, turn_idx, step_index);
```

Add `max_execution_steps_per_turn` to `__init__`, default `500`, and implement:

```python
def append_execution_step(self, chat_id: str, step: dict) -> None:
    chat_id = str(chat_id or "").strip()
    if not chat_id or not isinstance(step, dict):
        return
    turn_idx = step.get("turn_idx")
    try:
        turn_value = int(turn_idx) if turn_idx is not None else None
    except Exception:
        turn_value = None
    with self._connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 AS next_idx FROM execution_steps WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        next_idx = int(row["next_idx"] or 0)
        conn.execute(
            """
            INSERT INTO execution_steps(
                chat_id, step_index, turn_idx, event_type, display_kind, list_text, detail_text, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                next_idx,
                turn_value,
                str(step.get("event_type") or ""),
                str(step.get("display_kind") or ""),
                str(step.get("list_text") or step.get("step") or ""),
                str(step.get("detail_text") or step.get("message") or step.get("step") or ""),
                json.dumps(step, ensure_ascii=False),
            ),
        )
        if turn_value is not None and self.max_execution_steps_per_turn > 0:
            conn.execute(
                """
                DELETE FROM execution_steps
                WHERE chat_id = ? AND turn_idx = ? AND step_index NOT IN (
                    SELECT step_index FROM execution_steps
                    WHERE chat_id = ? AND turn_idx = ?
                    ORDER BY step_index DESC
                    LIMIT ?
                )
                """,
                (chat_id, turn_value, chat_id, turn_value, int(self.max_execution_steps_per_turn)),
            )

def load_execution_steps(self, chat_id: str, turn_idx: int | None = None) -> list[dict]:
    params = [str(chat_id or "").strip()]
    where = "chat_id = ?"
    if turn_idx is not None:
        where += " AND turn_idx = ?"
        params.append(int(turn_idx))
    with self._connect() as conn:
        rows = conn.execute(
            f"SELECT payload_json, turn_idx, event_type, display_kind, list_text, detail_text FROM execution_steps WHERE {where} ORDER BY step_index",
            tuple(params),
        ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("turn_idx", row["turn_idx"])
        payload.setdefault("event_type", str(row["event_type"] or ""))
        payload.setdefault("display_kind", str(row["display_kind"] or ""))
        payload.setdefault("list_text", str(row["list_text"] or ""))
        payload.setdefault("detail_text", str(row["detail_text"] or ""))
        out.append(payload)
    return out
```

- [ ] **Step 4: Run execution tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_store_unit.py -k execution -q
```

Expected: execution tests pass.

## Task 3: Slim App State Save And Legacy Migration

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing app-state slimming tests**

Add tests near existing `_save_state` tests:

```python
def test_save_state_excludes_large_chat_history_after_store_migration(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "old",
            "turns": [{"question": "q", "answer_md": "a" * 10000, "model": "codex/main"}],
            "execution_steps": [{"detail_text": "x" * 10000}],
        }
    ]
    frame.active_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active", "answer_md": "answer", "model": "codex/main"}]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "execution_steps": [{"detail_text": "active step"}],
    }
    frame._chat_store_enabled = True

    frame._save_state()

    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert "archived_chats" not in data
    assert "chats" not in data
    assert "active_session_turns" not in data
    assert "turns" not in data.get("active_chat", {})
    assert "execution_steps" not in data.get("active_chat", {})
    assert frame.state_path.stat().st_size < 200_000
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "save_state_excludes_large_chat_history" -q
```

Expected: fails because `_save_state()` still writes full chat history.

- [ ] **Step 3: Initialize chat store in `ChatFrame.__init__`**

Add after `self.notes_db_path` setup:

```python
from chat_store import ChatStore

self.chat_db_path = self.app_data_dir / "chat_history.db"
self.chat_store = ChatStore(self.chat_db_path)
self.chat_store.initialize()
self._chat_store_enabled = True
```

Import `ChatStore` at the top of `main.py`.

- [ ] **Step 4: Add a slim active chat helper**

Add:

```python
def _slim_active_chat_state(self) -> dict:
    state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
    return {
        "id": str(state.get("id") or self.active_chat_id or self.current_chat_id or ""),
        "title": str(state.get("title") or EMPTY_CURRENT_CHAT_TITLE),
        "title_manual": bool(state.get("title_manual", False)),
        "title_source": str(state.get("title_source") or ("manual" if state.get("title_manual") else "default")),
        "title_updated_at": float(state.get("title_updated_at") or time.time()),
        "title_revision": int(state.get("title_revision") or 1),
        "model": str(state.get("model") or self.selected_model or DEFAULT_MODEL_ID),
        "created_at": float(state.get("created_at") or self.active_session_started_at or time.time()),
        "updated_at": float(state.get("updated_at") or time.time()),
        "detail_panel_mode": self._detail_panel_mode(),
    }
```

- [ ] **Step 5: Modify `_save_state()` to omit large history when store is enabled**

In `_save_state()`, replace large chat fields with:

```python
use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
data = {
    "selected_model_id": self.selected_model,
    "active_chat": self._slim_active_chat_state(),
    "active_chat_id": self.active_chat_id,
    ...
}
if not use_chat_store:
    data["archived_chats"] = self.archived_chats
    data["active_session_turns"] = self.active_session_turns
```

- [ ] **Step 6: Run slimming test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "save_state_excludes_large_chat_history" -q
```

Expected: passes.

## Task 4: Persist Active And Archived Chats To SQLite

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing persistence test**

```python
def test_save_state_persists_chats_to_chat_store(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active q", "answer_md": "active a", "model": "codex/main"}]
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [{"question": "old q", "answer_md": "old a"}]},
    ]

    frame._save_state()

    summaries = frame.chat_store.list_chat_summaries()
    assert {item["id"] for item in summaries} == {"chat-active", "chat-old"}
    assert frame.chat_store.load_turns("chat-active") == frame.active_session_turns
    assert frame.chat_store.load_turns("chat-old") == frame.archived_chats[0]["turns"]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "persists_chats_to_chat_store" -q
```

Expected: fails because `_save_state()` does not write to `chat_store`.

- [ ] **Step 3: Add `_persist_chat_history_to_store()`**

```python
def _persist_chat_history_to_store(self) -> None:
    store = getattr(self, "chat_store", None)
    if store is None:
        return
    if self.active_chat_id or self.active_session_turns:
        active = self._slim_active_chat_state()
        active["id"] = str(active.get("id") or self.active_chat_id or self.current_chat_id or "")
        active["turns"] = self.active_session_turns
        active["execution_steps"] = (
            list(self._current_chat_state.get("execution_steps") or [])
            if isinstance(getattr(self, "_current_chat_state", None), dict)
            else []
        )
        store.upsert_chat(active)
        store.replace_turns(active["id"], self.active_session_turns)
        store.replace_execution_steps(active["id"], active["execution_steps"])
    for chat in self.archived_chats:
        if not isinstance(chat, dict):
            continue
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        store.upsert_chat(chat)
        store.replace_turns(chat_id, chat.get("turns") if isinstance(chat.get("turns"), list) else [])
        store.replace_execution_steps(chat_id, chat.get("execution_steps") if isinstance(chat.get("execution_steps"), list) else [])
```

Also add `replace_execution_steps()` to `ChatStore` as a bulk delete/insert wrapper around `append_execution_step()`.

- [ ] **Step 4: Call persistence from `_save_state()` before writing config**

```python
if use_chat_store:
    self._persist_chat_history_to_store()
```

- [ ] **Step 5: Run persistence test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "persists_chats_to_chat_store" -q
```

Expected: passes.

## Task 5: Load Summaries And Turns On Demand

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing summary-load test**

```python
def test_load_state_uses_chat_store_summaries_without_full_turns(frame, tmp_path, monkeypatch):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-old", "title": "old", "updated_at": 10.0})
    frame.chat_store.replace_turns("chat-old", [{"question": "q", "answer_md": "a" * 10000}])
    frame.state_path.write_text(
        json.dumps({"selected_model_id": "codex/main", "active_chat_id": "chat-old", "active_chat": {"id": "chat-old"}}),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame.archived_chats == [frame.chat_store.list_chat_summaries()[0]]
    assert "turns" not in frame.archived_chats[0]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "load_state_uses_chat_store_summaries" -q
```

Expected: fails until `_load_state()` reads summaries from `ChatStore`.

- [ ] **Step 3: Modify `_load_state()`**

After reading small JSON config:

```python
if getattr(self, "_chat_store_enabled", False):
    summaries = self.chat_store.list_chat_summaries()
    self.archived_chats = summaries
    if self.active_chat_id:
        self.active_session_turns = self.chat_store.load_turns(self.active_chat_id)
        if not isinstance(self._current_chat_state, dict):
            self._current_chat_state = {}
        self._current_chat_state.update(self._chat_summary_by_id(self.active_chat_id) or {})
        self._current_chat_state["turns"] = self.active_session_turns
```

Add `_chat_summary_by_id(chat_id)` that searches `self.archived_chats` summaries.

- [ ] **Step 4: Modify `_switch_current_chat()` and `_show_history_chat()`**

Before rendering a selected chat, if the chat summary has no `turns`, load:

```python
if getattr(self, "_chat_store_enabled", False) and "turns" not in chat:
    chat = dict(chat)
    chat["turns"] = self.chat_store.load_turns(chat_id)
    chat["execution_steps"] = self.chat_store.load_execution_steps(chat_id)
```

- [ ] **Step 5: Run summary and history tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "load_state_uses_chat_store_summaries or history or switch_current_chat" -q
```

Expected: passes existing and new history behavior.

## Task 6: Migrate Legacy Large JSON

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing migration test**

```python
def test_large_legacy_app_state_migrates_to_chat_store_and_slims_json(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    legacy = {
        "selected_model_id": "codex/main",
        "active_chat_id": "chat-active",
        "active_session_turns": [{"question": "active", "answer_md": "answer"}],
        "archived_chats": [
            {"id": "chat-old", "title": "old", "turns": [{"question": "q", "answer_md": "a" * 10000}]}
        ],
    }
    (tmp_path / "app_state.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    f = main.ChatFrame()
    try:
        assert f.chat_store.load_turns("chat-old")[0]["question"] == "q"
        data = json.loads((tmp_path / "app_state.json").read_text(encoding="utf-8"))
        assert "archived_chats" not in data
        assert (tmp_path / "app_state.json").stat().st_size < 200_000
        assert list(tmp_path.glob("app_state.json.bak.*"))
    finally:
        f.Destroy()
```

- [ ] **Step 2: Run migration test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "large_legacy_app_state_migrates" -q
```

Expected: fails because migration is not implemented.

- [ ] **Step 3: Add migration marker methods**

In `ChatStore`, add `get_meta(key)` and `set_meta(key, value)` using a `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)` table.

- [ ] **Step 4: Add `_migrate_legacy_chat_json_if_needed()`**

Run during `ChatFrame.__init__` after `chat_store.initialize()` and before `_load_state()`:

```python
def _migrate_legacy_chat_json_if_needed(self) -> None:
    store = getattr(self, "chat_store", None)
    if store is None or store.get_meta("legacy_json_migration_complete") == "1":
        return
    if not self.state_path.exists():
        store.set_meta("legacy_json_migration_complete", "1")
        return
    try:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    chats = []
    active_chat = data.get("active_chat") if isinstance(data.get("active_chat"), dict) else {}
    active_turns = data.get("active_session_turns") if isinstance(data.get("active_session_turns"), list) else active_chat.get("turns")
    active_id = str(data.get("active_chat_id") or active_chat.get("id") or "").strip()
    if active_id and isinstance(active_turns, list):
        active = dict(active_chat)
        active["id"] = active_id
        active["turns"] = active_turns
        chats.append(active)
    archived = data.get("archived_chats") if isinstance(data.get("archived_chats"), list) else data.get("chats")
    if isinstance(archived, list):
        chats.extend(chat for chat in archived if isinstance(chat, dict))
    if not chats:
        store.set_meta("legacy_json_migration_complete", "1")
        return
    for chat in chats:
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        store.upsert_chat(chat)
        store.replace_turns(chat_id, chat.get("turns") if isinstance(chat.get("turns"), list) else [])
        store.replace_execution_steps(chat_id, chat.get("execution_steps") if isinstance(chat.get("execution_steps"), list) else [])
    backup = self.state_path.with_name(f"{self.state_path.name}.bak.{int(time.time())}")
    shutil.copy2(self.state_path, backup)
    for key in ("archived_chats", "chats", "active_session_turns"):
        data.pop(key, None)
    if isinstance(data.get("active_chat"), dict):
        data["active_chat"].pop("turns", None)
        data["active_chat"].pop("execution_steps", None)
    self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    store.set_meta("legacy_json_migration_complete", "1")
```

- [ ] **Step 5: Run migration test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "large_legacy_app_state_migrates" -q
```

Expected: passes.

## Task 7: Performance And Accessibility Regression Coverage

**Files:**
- Modify: `tests/test_codex_ui_responsiveness_automation.py`
- Modify: `tests/test_history_ui_automation.py`
- Test: existing UI automation suite

- [ ] **Step 1: Add large-history UI automation test**

```python
def test_ui_automation_large_sqlite_history_keeps_primary_controls_responsive(frame, wx_app, tmp_path):
    frame.Show()
    frame.archived_chats = []
    for chat_idx in range(1000):
        chat_id = f"chat-{chat_idx}"
        frame.chat_store.upsert_chat(
            {
                "id": chat_id,
                "title": f"chat {chat_idx}",
                "created_at": float(chat_idx),
                "updated_at": float(chat_idx),
            }
        )
        frame.chat_store.replace_turns(
            chat_id,
            [{"question": "q", "answer_md": "long answer " * 200, "model": "codex/main", "created_at": float(chat_idx)}],
        )
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()

    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
    wx_app.Yield()

    assert time.perf_counter() - started < 0.5
    assert frame.history_list.GetSelection() == 1
```

Use the existing `_send_listbox_key` helper from `tests/test_codex_ui_responsiveness_automation.py`; move it to a small shared helper only if import cycles stay clean.

- [ ] **Step 2: Run UI automation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_codex_ui_responsiveness_automation.py tests\test_history_ui_automation.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run targeted model workflow tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_codex_integration.py tests\test_openclaw_integration.py tests\test_claudecode_manager_integration.py -q
```

Expected: all tests pass or documented external dependency skips only.

## Task 8: Package And Real Desktop Verification

**Files:**
- No source changes unless tests reveal a bug.

- [ ] **Step 1: Run packaging precheck**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_packaging_specs.py tests\test_main_unit.py -k "packaging_spec or package_script or resolve_app_data_dir or chat_store or save_state_excludes" -q
```

Expected: pass.

- [ ] **Step 2: Repackage to a clean directory**

Run from non-admin PowerShell:

```powershell
.\package_mc.ps1 -DistPath C:\code\cv
```

Expected: `C:\code\cv\mc\mc.exe` exists.

- [ ] **Step 3: Real packaged keyboard responsiveness check**

Use a temporary `C:\code\cv\history` backup, seed `chat_history.db` with 1000 summaries and long turns, launch `C:\code\cv\mc\mc.exe`, and send real Win32 key messages to:

- execution list
- answer list
- history list
- input box
- model combo

Expected: each 30-key burst completes under 500 ms and no target control loses focus.

## Self-Review Notes

- Spec coverage: the plan covers SQLite store, small JSON config, migration, on-demand loading, execution retention, UI automation, and packaged verification.
- Placeholder scan: no task contains unresolved placeholder markers.
- Type consistency: `ChatStore`, `chat_history.db`, `list_chat_summaries`, `replace_turns`, `append_execution_step`, `load_execution_steps`, and `replace_execution_steps` are consistently named.
