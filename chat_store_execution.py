"""Execution step persistence and cursor-based execution projection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from chat_store_schema import MAX_INT64
from typing import Any


_DISCLOSABLE_EXECUTION_FACT = (
    "NOT EXISTS (SELECT 1 FROM execution_steps AS step WHERE step.chat_id=fact.chat_id "
    "AND step.logical_key=substr(json_extract(fact.envelope_json,'$.body.canonical_item_id'),11) "
    "AND json_extract(step.payload_json,'$.private_reasoning')=1) "
    "AND NOT EXISTS (SELECT 1 FROM v2_chat_state AS state WHERE state.chat_id=fact.chat_id "
    "AND state.revision>fact.revision AND fact.kind='execution_entry' "
    "AND json_extract(fact.envelope_json,'$.body.canonical_item_id') LIKE 'execution-%')"
)


class ChatStoreExecutionMixin:
    @staticmethod
    def _execution_cursor_token(payload: dict[str, Any], secret: str | bytes) -> str:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if not key:
            raise ValueError("CURSOR_SECRET_UNAVAILABLE")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(key, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_execution_cursor(token: str, secret: str | bytes) -> dict[str, Any]:
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if not key:
            raise ValueError("CURSOR_SECRET_UNAVAILABLE")
        try:
            encoded = str(token or "").strip()
            packed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            raw, supplied = packed[:-32], packed[-32:]
            expected = hmac.new(key, raw, hashlib.sha256).digest()
            if len(raw) == 0 or not hmac.compare_digest(supplied, expected):
                raise ValueError
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception as exc:
            raise ValueError("INVALID_EXECUTION_CURSOR") from exc

    def load_execution_page(self, *, pair_id: str, domain: str, chat_id: str,
                            secret: str | bytes, limit: int = 100, cursor: str = "",
                            now: float | None = None, cursor_ttl: int = 900) -> dict[str, Any]:
        """Return a revision-bound frozen page of authoritative execution facts."""
        owner = self.normalize_chat_id(chat_id)
        pair, sequence_domain = str(pair_id or "").strip(), str(domain or "").strip()
        if not pair or not sequence_domain:
            raise ValueError("INVALID_FEED_SCOPE")
        page_limit = min(100, max(1, int(limit)))
        current_time = int(self._clock.time() if now is None else now)
        expires_at = current_time + max(1, int(cursor_ttl))
        with self._connect() as conn:
            state = conn.execute("SELECT revision,execution_sequence FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            revision = int(state["revision"]) if state else 0
            snapshot_high = int(state["execution_sequence"]) if state else 0
            exclusive_before = snapshot_high + 1
            if cursor:
                decoded = self._decode_execution_cursor(cursor, secret)
                required = {"pair": pair, "domain": sequence_domain, "chat": owner, "revision": revision}
                if any(decoded.get(key) != value for key, value in required.items()):
                    raise ValueError("EXECUTION_CURSOR_SCOPE_MISMATCH")
                if int(decoded.get("expires_at") or 0) < current_time:
                    raise ValueError("EXECUTION_CURSOR_EXPIRED")
                snapshot_high = int(decoded.get("snapshot_high") or 0)
                exclusive_before = int(decoded.get("exclusive_before") or 0)
                expires_at = int(decoded.get("expires_at") or 0)
                if snapshot_high < 0 or exclusive_before < 1 or exclusive_before > snapshot_high + 1:
                    raise ValueError("INVALID_EXECUTION_CURSOR")
            rows = conn.execute(
                "SELECT envelope_json,execution_sequence FROM durable_facts AS fact "
                "WHERE pair_id=? AND domain=? AND chat_id=? AND revision=? "
                "AND execution_sequence IS NOT NULL AND execution_sequence<=? AND execution_sequence<? "
                f"AND {_DISCLOSABLE_EXECUTION_FACT} "
                "ORDER BY execution_sequence DESC LIMIT ?",
                (pair, sequence_domain, owner, revision, snapshot_high, exclusive_before, page_limit + 1),
            ).fetchall()
        has_more = len(rows) > page_limit
        selected = rows[:page_limit]
        entries = [json.loads(str(row["envelope_json"])) for row in reversed(selected)]
        next_cursor = ""
        if has_more and selected:
            next_cursor = self._execution_cursor_token({
                "v": 1, "pair": pair, "domain": sequence_domain, "chat": owner,
                "revision": revision, "snapshot_high": snapshot_high,
                "exclusive_before": int(selected[-1]["execution_sequence"]),
                "expires_at": expires_at,
            }, secret)
        return {"entries": entries, "has_more": has_more, "cursor": next_cursor,
                "oldest_cursor": next_cursor, "revision": revision,
                "sequence_domain": sequence_domain, "snapshot_high": snapshot_high}

    def create_execution_projection_snapshot(
        self, *, chat_id: str, revision: int, source_hash: str,
        rows: list[dict[str, Any]], ttl: int = 900,
    ) -> str:
        """Freeze projected rows only if their canonical source still matches."""
        owner = self.normalize_chat_id(chat_id)
        snapshot_id = uuid.uuid4().hex
        now = self._clock.time()
        rows_json = json.dumps(rows, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            expired = [row["snapshot_id"] for row in conn.execute(
                "SELECT snapshot_id FROM execution_projection_snapshots WHERE expires_at<?", (now,)
            ).fetchall()]
            if expired:
                conn.executemany("DELETE FROM execution_projection_rows WHERE snapshot_id=?",
                                 [(value,) for value in expired])
                conn.executemany("DELETE FROM execution_projection_snapshots WHERE snapshot_id=?",
                                 [(value,) for value in expired])
            state = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            current_revision = int(state["revision"]) if state else 0
            turn_rows = conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,)
            ).fetchall()
            step_rows = conn.execute(
                "SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? ORDER BY step_index", (owner,)
            ).fetchall()
            current_hash = self._execution_projection_source_hash(current_revision, turn_rows, step_rows)
            if current_revision != int(revision) or current_hash != source_hash:
                raise ValueError("EXECUTION_SOURCE_CHANGED")
            previous = conn.execute(
                """SELECT snapshot_id,rows_json FROM execution_projection_snapshots
                   WHERE chat_id=? AND revision=? AND source_hash=? AND expires_at>=?
                   ORDER BY created_at DESC LIMIT 1""",
                (owner, current_revision, source_hash, now),
            ).fetchone()
            if previous is not None and str(previous["rows_json"]) == rows_json:
                return str(previous["snapshot_id"])
            conn.execute(
                """INSERT INTO execution_projection_snapshots
                   (snapshot_id,chat_id,revision,source_hash,rows_json,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (snapshot_id, owner, current_revision, source_hash,
                 rows_json, now, now + max(1, int(ttl))),
            )
            conn.executemany(
                "INSERT INTO execution_projection_rows(snapshot_id,row_index,row_json) VALUES(?,?,?)",
                [(snapshot_id, index, json.dumps(row, ensure_ascii=False))
                 for index, row in enumerate(rows)],
            )
        return snapshot_id

    def load_execution_projection_page(
        self, *, pair_id: str, domain: str, chat_id: str,
        secret: str | bytes, snapshot_id: str, limit: int = 50,
        cursor: str = "", now: float | None = None,
    ) -> dict[str, Any]:
        """Page an immutable projection with an owner/revision-bound cursor."""
        owner = self.normalize_chat_id(chat_id)
        current_time = self._clock.time() if now is None else float(now)
        page_limit = min(50, max(1, int(limit)))
        with self._connect() as conn:
            state = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            current_revision = int(state["revision"]) if state else 0
            snapshot = conn.execute(
                """SELECT revision,expires_at FROM execution_projection_snapshots
                   WHERE snapshot_id=? AND chat_id=?""", (snapshot_id, owner),
            ).fetchone()
        if snapshot is None:
            raise ValueError("EXECUTION_SNAPSHOT_MISSING")
        revision = int(snapshot["revision"])
        if revision != current_revision:
            raise ValueError("EXECUTION_CURSOR_SCOPE_MISMATCH")
        expires_at = float(snapshot["expires_at"])
        if expires_at < current_time:
            raise ValueError("EXECUTION_CURSOR_EXPIRED")
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS n FROM execution_projection_rows WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        total = int(total_row["n"] if total_row else 0)
        before = total
        if cursor:
            decoded = self._decode_execution_cursor(cursor, secret)
            required = {"v": 3, "pair": pair_id, "domain": domain, "chat": owner,
                        "revision": revision, "snapshot": snapshot_id}
            if any(decoded.get(key) != value for key, value in required.items()):
                raise ValueError("EXECUTION_CURSOR_SCOPE_MISMATCH")
            if float(decoded.get("expires_at") or 0) < current_time:
                raise ValueError("EXECUTION_CURSOR_EXPIRED")
            before = int(decoded.get("before", -1))
            if before < 0 or before > total:
                raise ValueError("INVALID_EXECUTION_CURSOR")
        start = max(0, before - page_limit)
        with self._connect() as conn:
            selected = [json.loads(row["row_json"]) for row in conn.execute(
                """SELECT row_json FROM execution_projection_rows
                   WHERE snapshot_id=? AND row_index>=? AND row_index<?
                   ORDER BY row_index LIMIT ?""",
                (snapshot_id, start, before, page_limit),
            ).fetchall()]
        has_more = start > 0
        for projected in selected:
            for field in ("list_text", "detail_text"):
                prefix, continuation = self.content_continuation(chat_id=owner,
                    text=str(projected.get(field) or ""), secret=secret, prefix_bytes=1024)
                if continuation:
                    projected[field] = prefix
                    projected[field + "_continuation"] = continuation
        next_cursor = self._execution_cursor_token({
            "v": 3, "pair": pair_id, "domain": domain, "chat": owner,
            "revision": revision, "snapshot": snapshot_id,
            "before": start, "expires_at": expires_at,
        }, secret) if has_more else ""
        return {"version": 3, "snapshot_id": snapshot_id, "chat_id": owner,
                "revision": revision, "rows": selected, "has_more": has_more,
                "cursor": next_cursor, "oldest_cursor": next_cursor}

    def load_execution_range(self, *, pair_id: str, domain: str, chat_id: str,
                             revision: int, start_sequence: int, end_sequence: int) -> dict[str, Any]:
        owner = self.normalize_chat_id(chat_id)
        start, end = int(start_sequence), int(end_sequence)
        if start < 1 or end < start or end > MAX_INT64 or end - start + 1 > 100:
            raise ValueError("INVALID_EXECUTION_RANGE")
        with self._connect() as conn:
            state = conn.execute("SELECT revision,execution_sequence FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            current_revision = int(state["revision"]) if state else 0
            if int(revision) != current_revision:
                raise ValueError("SNAPSHOT_REQUIRED")
            rows = conn.execute(
                "SELECT envelope_json FROM durable_facts AS fact WHERE pair_id=? AND domain=? AND chat_id=? "
                f"AND revision=? AND execution_sequence BETWEEN ? AND ? AND {_DISCLOSABLE_EXECUTION_FACT} "
                "ORDER BY execution_sequence",
                (str(pair_id), str(domain), owner, current_revision, start, end),
            ).fetchall()
        if len(rows) != end - start + 1:
            raise ValueError("SNAPSHOT_REQUIRED")
        return {"entries": [json.loads(str(row["envelope_json"])) for row in rows],
                "revision": current_revision, "sequence_domain": str(domain),
                "snapshot_high": int(state["execution_sequence"]) if state else 0,
                "has_more": False, "from_sequence": start, "to_sequence": end}

    def commit_message_execution_projection(self, *, pair_id: str, domain: str,
                                            chat_id: str, message_id: str,
                                            projection_kind: str) -> dict[str, Any]:
        """Commit a content-free deterministic question/final timeline projection."""
        owner = self.normalize_chat_id(chat_id)
        canonical_message = str(message_id or "").strip()
        kind = str(projection_kind or "").strip().lower()
        expected_role = {"question": "user", "final": "assistant"}.get(kind)
        if not canonical_message or expected_role is None:
            raise ValueError("INVALID_MESSAGE_PROJECTION")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cm.chat_id,cm.role,cm.turn_id,cm.legacy_turn_index FROM canonical_messages cm WHERE cm.message_id=?",
                (canonical_message,),
            ).fetchone()
        if row is None or str(row["chat_id"]) != owner or str(row["role"]).lower() != expected_role:
            raise ValueError("AMBIGUOUS_CANONICAL_MESSAGE")
        projection_name = "user-question" if kind == "question" else "assistant-final"
        return self.commit_durable_fact(
            pair_id=pair_id,
            domain=domain,
            execution=True,
            envelope={
                "event_id": f"projection:{canonical_message}:{projection_name}",
                "kind": "execution_entry",
                "chat_id": owner,
                "turn_id": str(row["turn_id"]),
                "body": {
                    "kind": kind,
                    "message_id": canonical_message,
                    "turn_index": int(row["legacy_turn_index"]),
                    "title": "我：" if kind == "question" else "小助理：",
                    "detail": "",
                },
            },
        )

    def append_execution_step(self, chat_id: str, step: dict[str, Any]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized or not isinstance(step, dict):
            return
        if str(step.get("logical_key") or "").strip():
            self.upsert_execution_step(normalized, step)
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._append_execution_step_on_conn(conn, normalized, step)

    def _append_execution_step_on_conn(self, conn, chat_id: str, step: dict[str, Any]) -> None:
        """Append one trusted canonical step inside the caller's transaction."""
        logical_key = str(step.get("logical_key") or "").strip()
        if logical_key:
            scope = step.get("logical_scope")
            if (not isinstance(scope, dict)
                    or self.execution_logical_key(scope) != logical_key
                    or str(scope.get("chat_id") or "").strip() != chat_id):
                raise ValueError("EXECUTION_LOGICAL_KEY_MISMATCH")
            for field in ("provider", "thread_id", "turn_id", "revision", "agent_id"):
                incoming = step.get(field)
                expected = scope.get(field)
                if str(incoming if incoming is not None else "") != str(expected if expected is not None else ""):
                    raise ValueError("EXECUTION_SCOPE_CONFLICT")
            existing = conn.execute(
                "SELECT step_index FROM execution_steps WHERE chat_id=? AND logical_key=?",
                (chat_id, logical_key),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """UPDATE execution_steps SET turn_idx=?,event_type=?,display_kind=?,
                       list_text=?,detail_text=?,payload_json=?,visible=? WHERE chat_id=? AND logical_key=?""",
                    (self._optional_int(step.get("turn_idx")), str(step.get("event_type") or ""),
                     str(step.get("display_kind") or ""), str(step.get("list_text") or step.get("step") or ""),
                     str(step.get("detail_text") or step.get("message") or step.get("step") or ""),
                     json.dumps(step, ensure_ascii=False), self._execution_visible(step), chat_id, logical_key),
                )
                return
        turn_value = self._optional_int(step.get("turn_idx"))
        row = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 AS next_idx FROM execution_steps WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        next_idx = int(row["next_idx"] or 0)
        conn.execute(
            """
            INSERT INTO execution_steps(
                chat_id, step_index, turn_idx, event_type, display_kind, list_text, detail_text, payload_json, visible, logical_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                next_idx,
                turn_value,
                str(step.get("event_type") or ""),
                str(step.get("display_kind") or ""),
                str(step.get("list_text") or step.get("step") or ""),
                str(step.get("detail_text") or step.get("message") or step.get("step") or ""),
                json.dumps(step, ensure_ascii=False), self._execution_visible(step),
                str(step.get("logical_key") or ""),
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
                    chat_id,
                    turn_value,
                    chat_id,
                    turn_value,
                    int(self.max_execution_steps_per_turn),
                ),
            )

    def replace_execution_lifecycle_step(self, chat_id: str, step: dict[str, Any]) -> bool:
        normalized = str(chat_id or "").strip()
        item_id = str((step or {}).get("item_id") or "").strip()
        if not normalized or not item_id or str((step or {}).get("event_type") or "") != "item_completed":
            return False
        turn_value = self._optional_int(step.get("turn_idx"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT step_index, payload_json FROM execution_steps WHERE chat_id = ? ORDER BY step_index",
                (normalized,),
            ).fetchall()
            matches = []
            for row in rows:
                previous = self._json_dict(row["payload_json"])
                if (
                    str(previous.get("event_type") or "") in {"item_started", "agent_message_delta"}
                    and str(previous.get("source_kind") or "").startswith(("tool.", "shell.", "subagent."))
                    and str(previous.get("item_id") or "").strip() == item_id
                    and self._optional_int(previous.get("turn_idx")) == turn_value
                    and str(previous.get("thread_id") or "") == str(step.get("thread_id") or "")
                    and str(previous.get("turn_id") or "") == str(step.get("turn_id") or "")
                ):
                    matches.append(int(row["step_index"]))
            if not matches:
                return False
            first_index = matches[0]
            conn.executemany(
                "DELETE FROM execution_steps WHERE chat_id = ? AND step_index = ?",
                [(normalized, index) for index in matches[1:]],
            )
            conn.execute(
                """
                UPDATE execution_steps
                SET turn_idx=?, event_type=?, display_kind=?, list_text=?, detail_text=?, payload_json=?,visible=?
                WHERE chat_id=? AND step_index=?
                """,
                (
                    turn_value,
                    str(step.get("event_type") or ""),
                    str(step.get("display_kind") or ""),
                    str(step.get("list_text") or step.get("step") or ""),
                    str(step.get("detail_text") or step.get("message") or step.get("step") or ""),
                    json.dumps(step, ensure_ascii=False), self._execution_visible(step),
                    normalized,
                    first_index,
                ),
            )
        return True

    def update_execution_step_by_identity(self, chat_id: str, step: dict[str, Any]) -> bool:
        normalized = str(chat_id or "").strip()
        logical_key = str((step or {}).get("logical_key") or "").strip()
        item_id = str((step or {}).get("item_id") or "").strip()
        if not normalized or (not logical_key and not item_id):
            return False
        turn_value = self._optional_int(step.get("turn_idx"))
        with self._connect() as conn:
            if logical_key:
                row = conn.execute(
                    "SELECT step_index, payload_json FROM execution_steps WHERE chat_id=? AND logical_key=?",
                    (normalized, logical_key),
                ).fetchone()
                if row is None:
                    return False
                previous = self._json_dict(row["payload_json"])
                merged = dict(step)
                for key in ("event_id", "_execution_uid", "created_at", "ts", "execution_sequence", "revision"):
                    if previous.get(key) not in (None, ""):
                        merged[key] = previous[key]
                conn.execute(
                    """UPDATE execution_steps SET turn_idx=?, event_type=?, display_kind=?, list_text=?,
                       detail_text=?, payload_json=?,visible=? WHERE chat_id=? AND logical_key=?""",
                    (self._optional_int(merged.get("turn_idx")), str(merged.get("event_type") or ""),
                     str(merged.get("display_kind") or ""), str(merged.get("list_text") or merged.get("step") or ""),
                     str(merged.get("detail_text") or merged.get("message") or merged.get("step") or ""),
                     json.dumps(merged, ensure_ascii=False), self._execution_visible(merged), normalized, logical_key),
                )
                return True
            rows = conn.execute(
                "SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? ORDER BY step_index DESC",
                (normalized,),
            ).fetchall()
            for row in rows:
                previous = self._json_dict(row["payload_json"])
                if (str(previous.get("item_id") or "").strip() == item_id
                        and self._optional_int(previous.get("turn_idx")) == turn_value
                        and str(previous.get("thread_id") or "") == str(step.get("thread_id") or "")
                        and str(previous.get("turn_id") or "") == str(step.get("turn_id") or "")):
                    conn.execute(
                        """UPDATE execution_steps SET turn_idx=?,event_type=?,display_kind=?,list_text=?,detail_text=?,payload_json=?,visible=?
                           WHERE chat_id=? AND step_index=?""",
                        (turn_value, str(step.get("event_type") or ""), str(step.get("display_kind") or ""),
                         str(step.get("list_text") or step.get("step") or ""),
                         str(step.get("detail_text") or step.get("message") or step.get("step") or ""),
                         json.dumps(step, ensure_ascii=False), self._execution_visible(step), normalized, int(row["step_index"])),
                    )
                    return True
        return False

    def upsert_execution_step(self, chat_id: str, step: dict[str, Any]) -> dict[str, Any]:
        """Atomically insert/update one fully-scoped execution projection."""
        normalized = str(chat_id or "").strip()
        logical_key = str((step or {}).get("logical_key") or "").strip()
        if not normalized or not logical_key:
            raise ValueError("MISSING_EXECUTION_LOGICAL_KEY")
        incoming = dict(step)
        scope = incoming.get("logical_scope") if isinstance(incoming.get("logical_scope"), dict) else None
        if scope is None or self.execution_logical_key(scope) != logical_key or str(scope.get("chat_id") or "").strip() != normalized:
            raise ValueError("EXECUTION_LOGICAL_KEY_MISMATCH")
        expected = {
            "provider": scope.get("provider"), "thread_id": scope.get("thread_id"),
            "turn_id": scope.get("turn_id"), "revision": scope.get("revision"), "agent_id": scope.get("agent_id"),
        }
        for field, value in expected.items():
            if str(incoming.get(field) if incoming.get(field) is not None else "") != str(value if value is not None else ""):
                raise ValueError("EXECUTION_SCOPE_CONFLICT")
        immutable = ("logical_key", "canonical_item_id", "provider", "thread_id", "turn_id", "revision", "agent_id")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._execution_scope_retired(conn, normalized, scope):
                return {}
            assembler = conn.execute("SELECT scope_json FROM execution_assemblers WHERE chat_id=? AND logical_key=?",
                                     (normalized, logical_key)).fetchone()
            if assembler is not None and self._json_dict(assembler["scope_json"]) != scope:
                self._record_execution_conflict(conn, normalized, logical_key, str(incoming.get("fragment_id") or ""), "PROJECTION_ASSEMBLER_SCOPE_CONFLICT", incoming)
                raise ValueError("EXECUTION_SCOPE_CONFLICT")
            row = conn.execute(
                "SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? AND logical_key=?",
                (normalized, logical_key),
            ).fetchone()
            if row is not None:
                previous = self._json_dict(row["payload_json"])
                for key in immutable:
                    if str(previous.get(key) if previous.get(key) is not None else "") != str(incoming.get(key) if incoming.get(key) is not None else ""):
                        self._record_execution_conflict(conn, normalized, logical_key, str(incoming.get("fragment_id") or ""), "IMMUTABLE_SCOPE_CONFLICT", incoming)
                        raise ValueError("EXECUTION_SCOPE_CONFLICT")
                for key in ("event_id", "canonical_item_id", "_execution_uid", "created_at", "ts", "execution_sequence", "revision"):
                    if previous.get(key) not in (None, ""):
                        incoming[key] = previous[key]
                if previous.get("private_reasoning"):
                    incoming.update(private_reasoning=True, list_text="", kimi_summary="", detail_text="", raw_text="",
                                    text="", source_detail={}, source_detail_history=[], diagnostic_history=[])
                elif incoming.get("private_reasoning"):
                    self._invalidate_execution_snapshots(conn, normalized)
                step_index = int(row["step_index"])
                conn.execute(
                    """UPDATE execution_steps SET turn_idx=?,event_type=?,display_kind=?,list_text=?,detail_text=?,payload_json=?,visible=?
                       WHERE chat_id=? AND logical_key=?""",
                    (self._optional_int(incoming.get("turn_idx")), str(incoming.get("event_type") or ""),
                     str(incoming.get("display_kind") or ""), str(incoming.get("list_text") or incoming.get("step") or ""),
                     str(incoming.get("detail_text") or incoming.get("message") or incoming.get("step") or ""),
                     json.dumps(incoming, ensure_ascii=False), self._execution_visible(incoming), normalized, logical_key),
                )
            else:
                next_row = conn.execute("SELECT COALESCE(MAX(step_index),-1)+1 n FROM execution_steps WHERE chat_id=?", (normalized,)).fetchone()
                step_index = int(next_row["n"] or 0)
                conn.execute(
                    """INSERT INTO execution_steps(chat_id,step_index,turn_idx,event_type,display_kind,list_text,detail_text,payload_json,visible,logical_key)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (normalized, step_index, self._optional_int(incoming.get("turn_idx")), str(incoming.get("event_type") or ""),
                     str(incoming.get("display_kind") or ""), str(incoming.get("list_text") or incoming.get("step") or ""),
                     str(incoming.get("detail_text") or incoming.get("message") or incoming.get("step") or ""),
                     json.dumps(incoming, ensure_ascii=False), self._execution_visible(incoming), logical_key),
                )
        return incoming

    @staticmethod
    def _record_execution_conflict(conn, chat_id: str, logical_key: str, fragment_id: str, reason: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO execution_fragment_conflicts(chat_id,logical_key,fragment_id,reason,payload_json) VALUES(?,?,?,?,?)",
            (chat_id, logical_key, fragment_id, reason, json.dumps(payload, ensure_ascii=False)),
        )

    @staticmethod
    def execution_logical_key(scope: dict[str, Any]) -> str:
        canonical = [
            str(scope.get("chat_id") or "").strip(),
            int(scope.get("revision", 0)),
            str(scope.get("thread_id") or scope.get("session_id") or "").strip(),
            str(scope.get("turn_id") if scope.get("turn_id") is not None else "").strip(),
            str(scope.get("provider") or "").strip(),
            str(scope.get("agent_id") or "").strip(),
            str(scope.get("native_id") or "").strip(),
        ]
        return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _execution_scope_retired(conn, chat_id: str, scope: dict) -> bool:
        if scope.get("provider") != "kimi":
            return False
        current = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (chat_id,)).fetchone()
        return current is not None and current["revision"] > int(scope["revision"])

    @staticmethod
    def _invalidate_execution_snapshots(conn, chat_id: str) -> None:
        conn.execute("DELETE FROM execution_projection_rows WHERE snapshot_id IN "
                     "(SELECT snapshot_id FROM execution_projection_snapshots WHERE chat_id=?)", (chat_id,))
        conn.execute("DELETE FROM execution_projection_snapshots WHERE chat_id=?", (chat_id,))

    def apply_execution_fragment(self, chat_id: str, logical_key: str, scope: dict[str, Any], *,
                                 fragment_id: str, offset: int | None, text: str,
                                 projection_seed: dict[str, Any] | None = None) -> dict[str, Any]:
        """Durably accept a fragment and return contiguous assembled content."""
        normalized = str(chat_id or "").strip()
        logical_key = str(logical_key or "").strip()
        fragment_id = str(fragment_id or "").strip()
        if not normalized or not logical_key or (offset is None and not fragment_id):
            raise ValueError("AMBIGUOUS_EXECUTION_FRAGMENT")
        if self.execution_logical_key(scope) != logical_key or str(scope.get("chat_id") or "").strip() != normalized:
            raise ValueError("EXECUTION_LOGICAL_KEY_MISMATCH")
        signature = hashlib.sha256((str(offset) + "\0" + text).encode("utf-8")).hexdigest()
        incoming_private = bool(isinstance(projection_seed, dict) and projection_seed.get("private_reasoning"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._execution_scope_retired(conn, normalized, scope):
                return {"accepted": False, "stale": True}
            row = conn.execute("SELECT scope_json,state_json FROM execution_assemblers WHERE chat_id=? AND logical_key=?",
                               (normalized, logical_key)).fetchone()
            state = self._json_dict(row["state_json"]) if row else {"fragments": {}, "segments": {}, "ordered": [], "pending_gap": False}
            if row and self._json_dict(row["scope_json"]) != scope:
                self._record_execution_conflict(conn, normalized, logical_key, fragment_id, "ASSEMBLER_SCOPE_CONFLICT", {"scope": scope})
                return {"accepted": False, "conflict": True, **state}
            private_fragment = bool(state.get("private_reasoning")) or incoming_private
            if private_fragment and not bool(state.get("private_reasoning")):
                state["segments"] = {key: "\0" * len(value) for key, value in state.get("segments", {}).items()}
                state["ordered"] = [dict(part, text="\0" * len(str(part.get("text") or ""))) for part in state.get("ordered", [])]
                state["assembled"] = "\0" * len(str(state.get("assembled") or ""))
                conn.execute("UPDATE execution_fragment_conflicts SET payload_json='{}' WHERE chat_id=? AND logical_key=?",
                             (normalized, logical_key))
                self._invalidate_execution_snapshots(conn, normalized)
            state["private_reasoning"] = private_fragment
            stored_text = ("\0" * len(text)) if private_fragment else text
            if private_fragment:
                safe_seed = dict(projection_seed) if isinstance(projection_seed, dict) else {}
                # Older thinking projections use this fixed activity label.
                caption = "正在分析问题" if safe_seed.get("display_kind") == "thinking" and safe_seed.get("list_text") == "正在分析问题" else ""
                safe_seed.update({"private_reasoning": True, "list_text": caption, "kimi_summary": "", "detail_text": "", "raw_text": "", "text": "",
                                  "source_detail": {}, "source_detail_history": [], "diagnostic_history": []})
                projection_seed = safe_seed
            prior = state["fragments"].get(fragment_id) if fragment_id else None
            if prior:
                conflict = prior != signature
                if conflict:
                    self._record_execution_conflict(conn, normalized, logical_key, fragment_id, "FRAGMENT_ID_CONFLICT",
                                                    {"offset": offset, "text": "<redacted>" if private_fragment else text})
                    if not incoming_private:
                        return {"accepted": False, "conflict": True, **state}
                projection = conn.execute(
                    "SELECT payload_json FROM execution_steps WHERE chat_id=? AND logical_key=?",
                    (normalized, logical_key),
                ).fetchone()
                payload = self._json_dict(projection["payload_json"]) if projection else {}
                if projection is not None and isinstance(projection_seed, dict) and projection_seed:
                    for field in ("event_type", "display_kind", "list_text", "detail_text", "private_reasoning",
                                  "source_kind", "source_detail", "source_detail_history", "diagnostic_history",
                                  "raw_text", "text", "operation_kind", "kimi_summary"):
                        if field in projection_seed:
                            payload[field] = projection_seed[field]
                    payload["projection_ready"] = True
                    conn.execute(
                        """UPDATE execution_steps SET event_type=?,display_kind=?,list_text=?,detail_text=?,payload_json=?,visible=?
                           WHERE chat_id=? AND logical_key=?""",
                        (str(payload.get("event_type") or ""), str(payload.get("display_kind") or ""),
                         str(payload.get("list_text") or ""), str(payload.get("detail_text") or ""),
                        json.dumps(payload, ensure_ascii=False), self._execution_visible(payload), normalized, logical_key),
                    )
                conn.execute(
                    """INSERT INTO execution_assemblers(chat_id,logical_key,scope_json,state_json,updated_at) VALUES(?,?,?,?,?)
                       ON CONFLICT(chat_id,logical_key) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                    (normalized, logical_key, json.dumps(scope, ensure_ascii=False, sort_keys=True),
                     json.dumps(state, ensure_ascii=False), self._clock.time()),
                )
                return {"accepted": False, "conflict" if conflict else "replay": True, "projection": payload, **state}
            if offset is not None:
                for raw_offset, accepted in state["segments"].items():
                    accepted_offset = int(raw_offset)
                    overlap_start = max(offset, accepted_offset)
                    overlap_end = min(offset + len(text), accepted_offset + len(accepted))
                    if (not private_fragment and overlap_start < overlap_end
                            and text[overlap_start-offset:overlap_end-offset] != accepted[overlap_start-accepted_offset:overlap_end-accepted_offset]):
                        self._record_execution_conflict(conn, normalized, logical_key, fragment_id, "OFFSET_BYTES_CONFLICT", {"offset": offset, "text": text})
                        return {"accepted": False, "conflict": True, **state}
                same_start = state["segments"].get(str(offset))
                if same_start is None or len(stored_text) > len(same_start):
                    state["segments"][str(offset)] = stored_text
            else:
                state["ordered"].append({"id": fragment_id, "text": stored_text})
            if fragment_id:
                state["fragments"][fragment_id] = signature
            if state["segments"]:
                ordered = sorted((int(k), v) for k, v in state["segments"].items())
                origin = int(scope.get("origin_offset", 0) or 0)
                cursor = origin
                assembled = ""
                pending = False
                for segment_offset, segment in ordered:
                    if segment_offset > cursor:
                        pending = True
                        continue
                    if segment_offset + len(segment) <= cursor:
                        continue
                    overlap = max(0, cursor-segment_offset)
                    if overlap < len(segment):
                        assembled += segment[overlap:]
                        cursor = segment_offset + len(segment)
                state["assembled"] = assembled
                state["origin_offset"] = origin
                state["pending_gap"] = pending
            else:
                state["assembled"] = "".join(part["text"] for part in state["ordered"])
            conn.execute(
                """INSERT INTO execution_assemblers(chat_id,logical_key,scope_json,state_json,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(chat_id,logical_key) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (normalized, logical_key, json.dumps(scope, ensure_ascii=False, sort_keys=True), json.dumps(state, ensure_ascii=False), self._clock.time()),
            )
            projection = conn.execute(
                "SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? AND logical_key=?",
                (normalized, logical_key),
            ).fetchone()
            now = self._clock.time()
            if projection is None:
                next_row = conn.execute("SELECT COALESCE(MAX(step_index),-1)+1 n FROM execution_steps WHERE chat_id=?", (normalized,)).fetchone()
                step_index = int(next_row["n"] or 0)
                stable_id = f"execution-{logical_key}"
                seed = dict(projection_seed) if isinstance(projection_seed, dict) else {}
                ready = bool(seed)
                payload = {
                    **seed,
                    "logical_key": logical_key, "logical_scope": scope,
                    "logical_scope_hash": logical_key, "canonical_item_id": stable_id,
                    "event_id": stable_id, "_execution_uid": logical_key,
                    "created_at": now, "revision": int(scope.get("revision", 0)),
                    "provider": str(scope.get("provider") or ""), "thread_id": str(scope.get("thread_id") or ""),
                    "session_id": str(scope.get("thread_id") or ""), "turn_id": str(scope.get("turn_id") or ""),
                    "agent_id": str(scope.get("agent_id") or ""), "provider_event_id": str(scope.get("native_id") or ""),
                    "item_id": str(scope.get("native_id") or ""),
                    "event_type": str(seed.get("event_type") or "agent_message_delta"),
                    "display_kind": str(seed.get("display_kind") or "commentary"),
                    "list_text": str(seed.get("list_text") or "") if ready else "",
                    "detail_text": str(seed.get("detail_text") or "") if ready else "",
                    "projection_ready": ready, "incomplete": bool(state.get("pending_gap")),
                }
                conn.execute(
                    """INSERT INTO execution_steps(chat_id,step_index,turn_idx,event_type,display_kind,list_text,detail_text,payload_json,visible,logical_key)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (normalized, step_index, None, payload["event_type"], payload["display_kind"], payload["list_text"],
                     payload["detail_text"], json.dumps(payload, ensure_ascii=False), self._execution_visible(payload), logical_key),
                )
            else:
                payload = self._json_dict(projection["payload_json"])
                if isinstance(projection_seed, dict) and projection_seed:
                    for field in ("event_type", "display_kind", "list_text", "detail_text", "private_reasoning",
                                  "source_kind", "source_detail", "source_detail_history", "diagnostic_history",
                                  "raw_text", "text", "operation_kind", "kimi_summary"):
                        if field in projection_seed:
                            payload[field] = projection_seed[field]
                    payload["projection_ready"] = True
                payload["incomplete"] = bool(state.get("pending_gap"))
                conn.execute(
                    "UPDATE execution_steps SET list_text=?,detail_text=?,payload_json=?,visible=? WHERE chat_id=? AND logical_key=?",
                    (payload["list_text"], payload["detail_text"], json.dumps(payload, ensure_ascii=False),
                     self._execution_visible(payload), normalized, logical_key),
                )
        return {"accepted": True, "projection": payload, **state}

    def load_execution_fragment_conflicts(self, chat_id: str, logical_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM execution_fragment_conflicts WHERE chat_id=? AND logical_key=? ORDER BY id",
                                (str(chat_id or "").strip(), str(logical_key or "").strip())).fetchall()
        return [dict(row) for row in rows]

    def replace_execution_steps(self, chat_id: str, steps: list[dict[str, Any]]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM execution_steps WHERE chat_id = ?", (normalized,))
            for step in steps or []:
                if isinstance(step, dict):
                    self._append_execution_step_on_conn(conn, normalized, step)

    def load_execution_steps(self, chat_id: str, turn_idx: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [str(chat_id or "").strip()]
        where = "chat_id = ?"
        if turn_idx is not None:
            where += " AND turn_idx = ?"
            params.append(int(turn_idx))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT step_index, payload_json, turn_idx, event_type, display_kind, list_text, detail_text
                FROM execution_steps
                WHERE {where}
                ORDER BY step_index
                """,
                tuple(params),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = self._json_dict(row["payload_json"])
            if payload.get("projection_ready") is False:
                continue
            payload["_store_step_index"] = int(row["step_index"])
            payload.setdefault("turn_idx", row["turn_idx"])
            payload.setdefault("event_type", str(row["event_type"] or ""))
            payload.setdefault("display_kind", str(row["display_kind"] or ""))
            payload.setdefault("list_text", str(row["list_text"] or ""))
            payload.setdefault("detail_text", str(row["detail_text"] or ""))
            out.append(payload)
        return out

    def read_execution_projection_source(self, chat_id: str) -> dict[str, Any]:
        """Read one canonical owner and its turns/steps from one SQLite snapshot."""
        owner = self.normalize_chat_id(chat_id)
        with self._connect() as conn:
            conn.execute("BEGIN")
            state = conn.execute(
                "SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)
            ).fetchone()
            turn_rows = conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,)
            ).fetchall()
            step_rows = conn.execute(
                """SELECT step_index,payload_json,turn_idx,event_type,display_kind,
                          list_text,detail_text FROM execution_steps
                   WHERE chat_id=? ORDER BY step_index""", (owner,)
            ).fetchall()
            source_hash = self._execution_projection_source_hash(
                int(state["revision"]) if state else 0, turn_rows, step_rows,
            )
            turns = [self._json_dict(row["payload_json"]) for row in turn_rows]
            steps = []
            for row in step_rows:
                step = self._json_dict(row["payload_json"])
                if step.get("projection_ready") is False:
                    continue
                step["_store_step_index"] = int(row["step_index"])
                step.setdefault("turn_idx", row["turn_idx"])
                for field in ("event_type", "display_kind", "list_text", "detail_text"):
                    step.setdefault(field, str(row[field] or ""))
                steps.append(step)
        return {
            "chat_id": owner,
            "revision": int(state["revision"]) if state else 0,
            "source_hash": source_hash,
            "turns": turns,
            "steps": steps,
        }

    @staticmethod
    def _execution_projection_source_hash(revision, turn_rows, step_rows) -> str:
        source = [
            int(revision),
            [str(row["payload_json"]) for row in turn_rows],
            [[int(row["step_index"]), str(row["payload_json"])] for row in step_rows],
        ]
        return hashlib.sha256(json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def load_recent_execution_steps(
        self,
        chat_id: str,
        *,
        turn_idx: int | None = None,
        limit: int = 100,
        before_step_index: int | None = None,
        include_total: bool = True,
        visible_only: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return 0, []
        params: list[Any] = [normalized]
        where = ("chat_id = ? AND visible=1" if visible_only else
                 "chat_id = ? AND COALESCE(json_extract(payload_json, '$.projection_ready'), 1) != 0")
        if turn_idx is not None:
            where += " AND turn_idx = ?"
            params.append(int(turn_idx))
        if before_step_index is not None:
            where += " AND step_index < ?"
            params.append(int(before_step_index))
        row_limit = max(1, int(limit or 1))
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM execution_steps WHERE {where}",
                tuple(params),
            ).fetchone() if include_total else None
            rows = conn.execute(
                f"""
                SELECT step_index, payload_json, turn_idx, event_type, display_kind, list_text, detail_text
                FROM execution_steps
                WHERE {where}
                ORDER BY step_index DESC
                LIMIT ?
                """,
                tuple(params + [row_limit]),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            payload = self._json_dict(row["payload_json"])
            if payload.get("projection_ready") is False:
                continue
            payload["_store_step_index"] = int(row["step_index"])
            payload.setdefault("turn_idx", row["turn_idx"])
            payload.setdefault("event_type", str(row["event_type"] or ""))
            payload.setdefault("display_kind", str(row["display_kind"] or ""))
            payload.setdefault("list_text", str(row["list_text"] or ""))
            payload.setdefault("detail_text", str(row["detail_text"] or ""))
            out.append(payload)
        return int(total_row["total"] if total_row is not None else 0), out
