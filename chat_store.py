from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


CHAT_PAYLOAD_FIELDS = {"turns", "execution_steps"}


class ChatStore:
    def __init__(self, db_path: Path, *, max_execution_steps_per_turn: int = 500) -> None:
        self.db_path = Path(db_path)
        self.max_execution_steps_per_turn = int(max_execution_steps_per_turn)

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
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chats_order
                    ON chats(pinned, updated_at DESC, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_turns_chat
                    ON turns(chat_id, turn_index);
                CREATE INDEX IF NOT EXISTS idx_execution_chat_turn
                    ON execution_steps(chat_id, turn_idx, step_index);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_chat(self, chat: dict[str, Any]) -> None:
        if not isinstance(chat, dict):
            return
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            return
        created = self._float_or(chat.get("created_at"), 0.0)
        updated = self._float_or(chat.get("updated_at"), created)
        title_manual = self._bool_value(chat.get("title_manual"))
        title_source = str(chat.get("title_source") or ("manual" if title_manual else "default"))
        title_updated = self._float_or(chat.get("title_updated_at"), updated)
        detail_panel_mode = str(chat.get("detail_panel_mode") or "").strip()
        if detail_panel_mode != "execution":
            detail_panel_mode = "answers"
        metadata = {k: v for k, v in chat.items() if k not in CHAT_PAYLOAD_FIELDS}
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
                    1 if self._bool_value(chat.get("pinned")) else 0,
                    1 if title_manual else 0,
                    title_source,
                    title_updated,
                    self._int_or(chat.get("title_revision"), 1),
                    detail_panel_mode,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

    def list_chat_summaries(self) -> list[dict[str, Any]]:
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

    def load_chat(self, chat_id: str) -> dict[str, Any] | None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.*, COUNT(t.turn_index) AS turn_count
                FROM chats c
                LEFT JOIN turns t ON t.chat_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        chat = self._metadata_from_row(row)
        chat.update(self._summary_from_row(row))
        chat["turns"] = self.load_turns(normalized)
        chat["execution_steps"] = self.load_execution_steps(normalized)
        return chat

    def replace_turns(self, chat_id: str, turns: list[dict[str, Any]]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM turns WHERE chat_id = ?", (normalized,))
            conn.executemany(
                "INSERT INTO turns(chat_id, turn_index, payload_json) VALUES (?, ?, ?)",
                [
                    (normalized, idx, json.dumps(turn, ensure_ascii=False))
                    for idx, turn in enumerate(turns or [])
                    if isinstance(turn, dict)
                ],
            )

    def load_turns(self, chat_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id = ? ORDER BY turn_index",
                (str(chat_id or "").strip(),),
            ).fetchall()
        return [payload for payload in (self._json_dict(row["payload_json"]) for row in rows) if payload]

    def append_execution_step(self, chat_id: str, step: dict[str, Any]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized or not isinstance(step, dict):
            return
        turn_value = self._optional_int(step.get("turn_idx"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(step_index), -1) + 1 AS next_idx FROM execution_steps WHERE chat_id = ?",
                (normalized,),
            ).fetchone()
            next_idx = int(row["next_idx"] or 0)
            conn.execute(
                """
                INSERT INTO execution_steps(
                    chat_id, step_index, turn_idx, event_type, display_kind, list_text, detail_text, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized,
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
                    (
                        normalized,
                        turn_value,
                        normalized,
                        turn_value,
                        int(self.max_execution_steps_per_turn),
                    ),
                )

    def replace_execution_steps(self, chat_id: str, steps: list[dict[str, Any]]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM execution_steps WHERE chat_id = ?", (normalized,))
        for step in steps or []:
            if isinstance(step, dict):
                self.append_execution_step(normalized, step)

    def load_execution_steps(self, chat_id: str, turn_idx: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [str(chat_id or "").strip()]
        where = "chat_id = ?"
        if turn_idx is not None:
            where += " AND turn_idx = ?"
            params.append(int(turn_idx))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json, turn_idx, event_type, display_kind, list_text, detail_text
                FROM execution_steps
                WHERE {where}
                ORDER BY step_index
                """,
                tuple(params),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = self._json_dict(row["payload_json"])
            payload.setdefault("turn_idx", row["turn_idx"])
            payload.setdefault("event_type", str(row["event_type"] or ""))
            payload.setdefault("display_kind", str(row["display_kind"] or ""))
            payload.setdefault("list_text", str(row["list_text"] or ""))
            payload.setdefault("detail_text", str(row["detail_text"] or ""))
            out.append(payload)
        return out

    def get_meta(self, key: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (str(key or ""),)).fetchone()
        return "" if row is None else str(row["value"] or "")

    def set_meta(self, key: str, value: str) -> None:
        normalized = str(key or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (normalized, str(value or "")),
            )

    def _summary_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"] or ""),
            "title": str(row["title"] or "新聊天"),
            "model": str(row["model"] or ""),
            "created_at": self._float_or(row["created_at"], 0.0),
            "updated_at": self._float_or(row["updated_at"], 0.0),
            "pinned": bool(row["pinned"]),
            "title_manual": bool(row["title_manual"]),
            "title_source": str(row["title_source"] or "default"),
            "title_updated_at": self._float_or(row["title_updated_at"], row["updated_at"] or 0.0),
            "title_revision": self._int_or(row["title_revision"], 1),
            "detail_panel_mode": str(row["detail_panel_mode"] or "answers"),
            "turn_count": self._int_or(row["turn_count"], 0),
        }

    def _metadata_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return self._json_dict(row["metadata_json"])

    def _json_dict(self, raw: Any) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw or "{}"))
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _float_or(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _int_or(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _bool_value(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
