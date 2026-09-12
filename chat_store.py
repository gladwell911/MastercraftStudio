from __future__ import annotations

import json
import hashlib
import base64
import hmac
import sqlite3
import uuid
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


CHAT_PAYLOAD_FIELDS = {"turns", "execution_steps"}
MAX_INT64 = (1 << 63) - 1
V2_MIGRATION_KEY = "identity_v2_migration_phase"
CLEAR_OPERATION_STATES = frozenset({
    "requested", "clear_acknowledged", "resend_dispatched", "resend_blocked",
    "completed_with_resend", "completed_clear_only", "completed_no_message", "superseded",
})
CLEAR_TERMINAL_STATES = frozenset({
    "completed_with_resend", "completed_clear_only", "completed_no_message", "superseded",
})


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
                CREATE TABLE IF NOT EXISTS canonical_turns (
                    turn_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL,
                    legacy_turn_index INTEGER, provider_kind TEXT NOT NULL DEFAULT '',
                    provider_session_id TEXT NOT NULL DEFAULT '', provider_turn_id TEXT NOT NULL DEFAULT '',
                    UNIQUE(chat_id, legacy_turn_index)
                );
                CREATE TABLE IF NOT EXISTS canonical_messages (
                    message_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES canonical_turns(turn_id),
                    chat_id TEXT NOT NULL, role TEXT NOT NULL, provider_kind TEXT NOT NULL DEFAULT '',
                    provider_message_id TEXT NOT NULL DEFAULT '', legacy_turn_index INTEGER,
                    UNIQUE(chat_id, legacy_turn_index, role)
                );
                CREATE TABLE IF NOT EXISTS v2_chat_state (
                    chat_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision BETWEEN 0 AND 9223372036854775807),
                    execution_sequence INTEGER NOT NULL DEFAULT 0 CHECK(execution_sequence BETWEEN 0 AND 9223372036854775807)
                );
                CREATE TABLE IF NOT EXISTS v2_feed_state (
                    pair_id TEXT NOT NULL, domain TEXT NOT NULL, sync_sequence INTEGER NOT NULL DEFAULT 0
                        CHECK(sync_sequence BETWEEN 0 AND 9223372036854775807),
                    retained_from INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(pair_id, domain)
                );
                CREATE TABLE IF NOT EXISTS durable_facts (
                    event_id TEXT PRIMARY KEY, canonical_hash TEXT NOT NULL, kind TEXT NOT NULL,
                    pair_id TEXT NOT NULL, domain TEXT NOT NULL, chat_id TEXT NOT NULL, turn_id TEXT, revision INTEGER NOT NULL, sync_sequence INTEGER NOT NULL,
                    execution_sequence INTEGER, envelope_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch()),
                    UNIQUE(pair_id, domain, sync_sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_durable_execution_owner
                    ON durable_facts(pair_id, domain, chat_id, revision, execution_sequence);
                CREATE TABLE IF NOT EXISTS publication_outbox (
                    pair_id TEXT NOT NULL, domain TEXT NOT NULL, sync_sequence INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE REFERENCES durable_facts(event_id),
                    subject_domain TEXT NOT NULL, payload BLOB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    published_at REAL, blocked_reason TEXT, PRIMARY KEY(pair_id, domain, sync_sequence)
                );
                CREATE TABLE IF NOT EXISTS v2_checkpoints (
                    pair_id TEXT NOT NULL, consumer_id TEXT NOT NULL, domain TEXT NOT NULL, sync_sequence INTEGER NOT NULL,
                    PRIMARY KEY(pair_id, consumer_id, domain)
                );
                CREATE TABLE IF NOT EXISTS identity_quarantine (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL, event_id TEXT,
                    payload_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS clear_operations (
                    operation_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 9223372036854775807),
                    idempotency_key TEXT NOT NULL,
                    pair_id TEXT NOT NULL DEFAULT 'default',
                    domain TEXT NOT NULL DEFAULT 'events',
                    state TEXT NOT NULL CHECK(state IN (
                        'requested','clear_acknowledged','resend_dispatched','resend_blocked',
                        'completed_with_resend','completed_clear_only','completed_no_message','superseded'
                    )),
                    snapshot_json TEXT,
                    source_turn_id TEXT,
                    source_message_id TEXT,
                    dispatch_claimed_at REAL,
                    failure_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(chat_id, idempotency_key),
                    UNIQUE(chat_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_clear_operations_owner_state
                    ON clear_operations(chat_id, state, revision DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(clear_operations)")}
            if "pair_id" not in columns:
                conn.execute("ALTER TABLE clear_operations ADD COLUMN pair_id TEXT NOT NULL DEFAULT 'default'")
            if "domain" not in columns:
                conn.execute("ALTER TABLE clear_operations ADD COLUMN domain TEXT NOT NULL DEFAULT 'events'")
            self._advance_v2_migration(conn)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_durable_execution_sequence "
                    "ON durable_facts(pair_id,domain,chat_id,revision,execution_sequence) "
                    "WHERE execution_sequence IS NOT NULL"
                )
            except sqlite3.IntegrityError:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (V2_MIGRATION_KEY, "read-only-recovery"),
                )

    def _advance_v2_migration(self, conn: sqlite3.Connection) -> None:
        """Restartable additive migration. V2 is enabled only after validation."""
        row = conn.execute("SELECT value FROM meta WHERE key=?", (V2_MIGRATION_KEY,)).fetchone()
        phase = str(row["value"] if row else "schema")
        if phase in {"schema", "backfill"}:
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) SELECT id FROM chats")
            legacy_rows = conn.execute(
                "SELECT chat_id,turn_index,payload_json FROM turns ORDER BY chat_id,turn_index"
            ).fetchall()
            for legacy in legacy_rows:
                payload = self._json_dict(legacy["payload_json"])
                conn.execute(
                    "INSERT OR IGNORE INTO canonical_turns(turn_id,chat_id,legacy_turn_index,provider_kind,provider_session_id,provider_turn_id) VALUES(?,?,?,?,?,?)",
                    (
                        f"turn-{uuid.uuid4().hex}",
                        str(legacy["chat_id"]),
                        int(legacy["turn_index"]),
                        str(payload.get("provider") or payload.get("model") or ""),
                        str(payload.get("session_id") or payload.get("thread_id") or ""),
                        str(payload.get("turn_id") or ""),
                    ),
                )
                self._backfill_canonical_turns_conn(
                    conn, str(legacy["chat_id"]), [payload], int(legacy["turn_index"])
                )
            phase = "validate"
        if phase == "validate":
            invalid = conn.execute("SELECT COUNT(*) n FROM chats WHERE trim(id)='' OR id IS NULL").fetchone()["n"]
            phase = "read-only-recovery" if invalid else "enable-v2"
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (V2_MIGRATION_KEY, phase))

    @property
    def v2_writes_enabled(self) -> bool:
        return self.get_meta(V2_MIGRATION_KEY) == "enable-v2"

    def allocate_chat_revision(self, chat_id: str) -> int:
        owner = self.normalize_chat_id(chat_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) VALUES(?)", (owner,))
            current = int(conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()["revision"])
            if current >= MAX_INT64:
                raise OverflowError("CHAT_REVISION_OVERFLOW")
            revision = current + 1
            conn.execute("UPDATE v2_chat_state SET revision=? WHERE chat_id=?", (revision, owner))
            return revision

    def get_clear_reconciliation_authority(self, chat_id: str) -> dict[str, Any]:
        """Return content-free revision and latest clear authority for one owner."""
        owner = self.normalize_chat_id(chat_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT s.revision state_revision,o.operation_id,o.revision operation_revision,o.state "
                "FROM v2_chat_state s LEFT JOIN clear_operations o ON o.operation_id=("
                "SELECT operation_id FROM clear_operations WHERE chat_id=s.chat_id "
                "ORDER BY revision DESC,created_at DESC LIMIT 1) WHERE s.chat_id=?", (owner,)
            ).fetchone()
        state_revision = int(row["state_revision"]) if row else 0
        operation_revision = int(row["operation_revision"]) if row and row["operation_id"] else 0
        if operation_revision > state_revision:
            raise RuntimeError("CLEAR_REVISION_AHEAD_OF_CHAT")
        return {
            "revision": state_revision,
            "clear_operation": ({
                "operation_id": str(row["operation_id"]),
                "revision": operation_revision,
                "state": str(row["state"]),
            } if row is not None and row["operation_id"] else None),
        }

    @staticmethod
    def _eligible_clear_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or payload.get("local_command") or payload.get("deleted") or payload.get("is_deleted"):
            return False
        role = str(payload.get("role") or "user").strip().lower()
        if role not in {"", "user", "human"}:
            return False
        if str(payload.get("question") or payload.get("text") or "").strip():
            return True
        if isinstance(payload.get("attachments"), list) and payload["attachments"]:
            return True
        return any(payload.get(key) not in (None, "", False, [], {}) for key in (
            "voice", "voice_metadata", "audio", "audio_path", "import_metadata", "import_source"
        ))

    @staticmethod
    def _canonical_clear_snapshot(payload: dict[str, Any], turn_id: str | None,
                                  message_id: str | None) -> dict[str, Any]:
        allowed = {
            "question", "text", "model", "attachments", "voice", "voice_metadata", "audio", "audio_path",
            "import_metadata", "import_source", "origin", "question_origin", "provider", "provider_kind",
            "provider_message_id", "created_at", "codex_service_tier",
        }
        snapshot = {key: payload[key] for key in allowed if key in payload}
        snapshot["canonical_turn_id"] = turn_id
        snapshot["canonical_message_id"] = message_id
        return snapshot

    def begin_clear_operation(self, chat_id: str, *, idempotency_key: str,
                              pair_id: str = "default", domain: str = "events",
                              operation_id: str | None = None) -> dict[str, Any]:
        """Atomically snapshot the first eligible payload, reset the chat and publish clear."""
        if not self.v2_writes_enabled:
            raise RuntimeError("V2_READ_ONLY_RECOVERY")
        owner = self.normalize_chat_id(chat_id)
        key = str(idempotency_key or "").strip()
        if not key or "\x00" in key:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        pair = str(pair_id or "").strip()
        event_domain = str(domain or "").strip()
        if not pair or not event_domain:
            raise ValueError("INVALID_FEED_SCOPE")
        if operation_id is not None:
            candidate_operation_id = str(operation_id).strip()
            if not candidate_operation_id or "\x00" in candidate_operation_id:
                raise ValueError("INVALID_OPERATION_ID")
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT * FROM clear_operations WHERE chat_id=? AND idempotency_key=?", (owner, key)
            ).fetchone()
            if duplicate:
                replay = self._decode_clear_operation(duplicate)
                replay["idempotent_replay"] = True
                return replay
            chat = conn.execute("SELECT metadata_json FROM chats WHERE id=?", (owner,)).fetchone()
            if chat is None:
                raise KeyError("CHAT_NOT_FOUND")
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) VALUES(?)", (owner,))
            state = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            current_revision = int(state["revision"])
            if current_revision >= MAX_INT64:
                raise OverflowError("CHAT_REVISION_OVERFLOW")
            revision = current_revision + 1
            superseded = conn.execute(
                "SELECT operation_id,revision,pair_id,domain FROM clear_operations WHERE chat_id=? AND state NOT IN "
                "('completed_with_resend','completed_clear_only','completed_no_message','superseded')", (owner,)
            ).fetchall()
            for prior in superseded:
                conn.execute("UPDATE clear_operations SET state='superseded',updated_at=? WHERE operation_id=?", (now, prior["operation_id"]))
                self._insert_clear_fact_conn(conn, prior["pair_id"], prior["domain"], prior["operation_id"], owner,
                                             int(prior["revision"]), "superseded")
            source = None
            source_index = None
            for row in conn.execute("SELECT turn_index,payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,)):
                candidate = self._json_dict(row["payload_json"])
                if self._eligible_clear_payload(candidate):
                    source, source_index = candidate, int(row["turn_index"])
                    break
            source_turn_id = source_message_id = None
            if source_index is not None:
                identity = conn.execute(
                    "SELECT ct.turn_id,cm.message_id FROM canonical_turns ct "
                    "LEFT JOIN canonical_messages cm ON cm.turn_id=ct.turn_id AND cm.role='user' "
                    "WHERE ct.chat_id=? AND ct.legacy_turn_index=?", (owner, source_index)
                ).fetchone()
                if identity:
                    source_turn_id, source_message_id = identity["turn_id"], identity["message_id"]
            snapshot = None
            if source is not None:
                snapshot = self._canonical_clear_snapshot(source, source_turn_id, source_message_id)
            op_id = str(operation_id or f"clear-{uuid.uuid4().hex}").strip()
            initial_state = "clear_acknowledged" if snapshot is not None else "completed_no_message"
            conn.execute("UPDATE v2_chat_state SET revision=?,execution_sequence=0 WHERE chat_id=?", (revision, owner))
            metadata = self._json_dict(chat["metadata_json"])
            metadata.update({
                "openclaw_session_key": "openclaw/main", "openclaw_session_id": "", "openclaw_session_file": "",
                "openclaw_sync_offset": 0, "openclaw_last_event_id": "", "openclaw_last_synced_at": 0.0,
                "codex_thread_id": "", "codex_turn_id": "", "codex_turn_active": False,
                "codex_pending_prompt": "", "codex_pending_request": None, "codex_request_queue": [],
                "codex_thread_flags": [], "codex_latest_assistant_text": "", "codex_latest_assistant_phase": "",
                "kimi_session_id": "", "kimi_turn_id": "", "kimi_turn_active": False,
                "kimi_pending_prompt": "", "kimi_request_queue": [], "claudecode_session_id": "", "context_usage": None,
            })
            conn.execute("UPDATE chats SET updated_at=?,metadata_json=? WHERE id=?", (now, json.dumps(metadata, ensure_ascii=False), owner))
            conn.execute("DELETE FROM turns WHERE chat_id=?", (owner,))
            conn.execute("DELETE FROM execution_steps WHERE chat_id=?", (owner,))
            try:
                conn.execute(
                    "INSERT INTO clear_operations(operation_id,chat_id,revision,idempotency_key,pair_id,domain,state,snapshot_json,source_turn_id,source_message_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (op_id, owner, revision, key, pair, event_domain, "requested",
                     json.dumps(snapshot, ensure_ascii=False, sort_keys=True) if snapshot is not None else None,
                     source_turn_id, source_message_id, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("OPERATION_ID_CONFLICT") from exc
            self._insert_clear_fact_conn(conn, pair, event_domain, op_id, owner, revision, "requested")
            conn.execute("UPDATE clear_operations SET state=?,updated_at=? WHERE operation_id=?",
                         (initial_state, now, op_id))
            self._insert_clear_fact_conn(conn, pair, event_domain, op_id, owner, revision, initial_state)
            created = self._decode_clear_operation(conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (op_id,)).fetchone())
            created["idempotent_replay"] = False
            return created

    def _insert_clear_fact_conn(self, conn: sqlite3.Connection, pair_id: str, domain: str,
                                operation_id: str, chat_id: str, revision: int, state: str) -> None:
        conn.execute("INSERT OR IGNORE INTO v2_feed_state(pair_id,domain) VALUES(?,?)", (pair_id, "__pair__"))
        feed = conn.execute("SELECT sync_sequence FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'", (pair_id,)).fetchone()
        if int(feed["sync_sequence"]) >= MAX_INT64:
            raise OverflowError("SYNC_SEQUENCE_OVERFLOW")
        sync = int(feed["sync_sequence"]) + 1
        event_base = f"clear-event-{operation_id}-{state}"
        occurrence = int(conn.execute(
            "SELECT COUNT(*) n FROM durable_facts WHERE event_id=? OR event_id LIKE ?",
            (event_base, f"{event_base}-%"),
        ).fetchone()["n"])
        event_id = event_base if occurrence == 0 else f"{event_base}-{occurrence + 1}"
        envelope = {"protocol_version": 2, "event_id": event_id, "kind": "clear_context", "chat_id": chat_id,
                    "domain": domain, "sequence_domain": domain, "origin_client": "mc",
                    "revision": revision, "sync_sequence": sync,
                    "body": {"operation_id": operation_id, "state": state}}
        canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        envelope["canonical_hash"] = digest
        payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        conn.execute("UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id=? AND domain='__pair__'", (sync, pair_id))
        conn.execute("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,revision,sync_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?)",
                     (event_id, digest, "clear_context", pair_id, domain, chat_id, revision, sync, payload))
        conn.execute("INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload) VALUES(?,?,?,?,?,?)",
                     (pair_id, domain, sync, event_id, domain, payload.encode("utf-8")))

    @staticmethod
    def _decode_clear_operation(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json")) if result.get("snapshot_json") else None
        result["terminal"] = result["state"] in CLEAR_TERMINAL_STATES
        return result

    def get_clear_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone()
        return self._decode_clear_operation(row) if row else None

    def active_clear_operation(self, chat_id: str) -> dict[str, Any] | None:
        owner = self.normalize_chat_id(chat_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clear_operations WHERE chat_id=? AND state NOT IN "
                "('completed_with_resend','completed_clear_only','completed_no_message','superseded') ORDER BY revision DESC LIMIT 1", (owner,)
            ).fetchone()
        return self._decode_clear_operation(row) if row else None

    def recoverable_clear_operations(self) -> list[dict[str, Any]]:
        """Return unfinished operations; indeterminate dispatched work is explicitly blocked."""
        with self._connect() as conn:
            ids = [str(row["operation_id"]) for row in conn.execute(
                "SELECT operation_id FROM clear_operations WHERE state NOT IN "
                "('completed_with_resend','completed_clear_only','completed_no_message','superseded') ORDER BY created_at"
            )]
        recovered = []
        for operation_id in ids:
            operation = self.get_clear_operation(operation_id)
            if operation and operation["state"] == "resend_dispatched":
                operation = self.transition_clear_operation(
                    operation_id, "resend_blocked", failure_code="DELIVERY_OUTCOME_UNCERTAIN"
                )
            if operation:
                recovered.append(operation)
        return recovered

    def claim_clear_resend_dispatch(self, operation_id: str) -> dict[str, Any] | None:
        """Persist the at-most-once boundary before any provider call."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone()
            if not row or row["state"] not in {"requested", "clear_acknowledged"}:
                return None
            now = time.time()
            changed = conn.execute(
                "UPDATE clear_operations SET state='resend_dispatched',dispatch_claimed_at=?,updated_at=? "
                "WHERE operation_id=? AND state IN ('requested','clear_acknowledged')", (now, now, str(operation_id))
            ).rowcount
            if not changed:
                return None
            self._insert_clear_fact_conn(conn, row["pair_id"], row["domain"], str(operation_id),
                                         row["chat_id"], int(row["revision"]), "resend_dispatched")
            return self._decode_clear_operation(conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone())

    def transition_clear_operation(self, operation_id: str, state: str, *, failure_code: str = "") -> dict[str, Any]:
        target = str(state or "").strip()
        if target not in CLEAR_OPERATION_STATES:
            raise ValueError("INVALID_CLEAR_STATE")
        allowed = {
            "requested": {"clear_acknowledged", "completed_no_message", "superseded"},
            "clear_acknowledged": {"resend_dispatched", "resend_blocked", "completed_clear_only", "superseded"},
            "resend_dispatched": {"completed_with_resend", "resend_blocked", "superseded"},
            "resend_blocked": {"clear_acknowledged", "completed_clear_only", "resend_dispatched", "superseded"},
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone()
            if not row:
                raise KeyError("CLEAR_OPERATION_NOT_FOUND")
            current = str(row["state"])
            if current == target:
                return self._decode_clear_operation(row)
            if target not in allowed.get(current, set()):
                raise ValueError("INVALID_CLEAR_TRANSITION")
            safe_code = str(failure_code or "").strip()[:120]
            if any(sep in safe_code for sep in ("/", "\\")):
                safe_code = "PROVIDER_OR_ATTACHMENT_FAILURE"
            conn.execute("UPDATE clear_operations SET state=?,failure_code=?,updated_at=? WHERE operation_id=?",
                         (target, safe_code, time.time(), str(operation_id)))
            self._insert_clear_fact_conn(conn, row["pair_id"], row["domain"], str(operation_id),
                                         row["chat_id"], int(row["revision"]), target)
            return self._decode_clear_operation(conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone())

    def update_checkpoint(self, consumer_id: str, domain: str, sync_sequence: int, *, pair_id: str = "default") -> None:
        if int(sync_sequence) < 0 or int(sync_sequence) > MAX_INT64:
            raise ValueError("INVALID_SYNC_SEQUENCE")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO v2_checkpoints(pair_id,consumer_id,domain,sync_sequence) VALUES(?,?,?,?) "
                "ON CONFLICT(pair_id,consumer_id,domain) DO UPDATE SET sync_sequence=MAX(sync_sequence,excluded.sync_sequence)",
                (str(pair_id), str(consumer_id), str(domain), int(sync_sequence)),
            )

    def get_checkpoint(self, consumer_id: str, domain: str, *, pair_id: str = "default") -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT sync_sequence FROM v2_checkpoints WHERE pair_id=? AND consumer_id=? AND domain=?", (str(pair_id),str(consumer_id),str(domain))).fetchone()
        return int(row["sync_sequence"] if row else 0)

    @staticmethod
    def normalize_chat_id(chat_id: Any) -> str:
        value = str(chat_id or "").strip()
        if not value or "\x00" in value:
            raise ValueError("INVALID_CHAT_ID")
        return value

    def canonical_turn_id(self, chat_id: str, *, legacy_turn_index: int | None = None,
                          provider_kind: str = "", provider_session_id: str = "",
                          provider_turn_id: str = "") -> str:
        if not self.v2_writes_enabled:
            raise RuntimeError("V2_READ_ONLY_RECOVERY")
        owner = self.normalize_chat_id(chat_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if legacy_turn_index is not None:
                row = conn.execute("SELECT turn_id FROM canonical_turns WHERE chat_id=? AND legacy_turn_index=?", (owner, int(legacy_turn_index))).fetchone()
                if row: return str(row["turn_id"])
            turn_id = f"turn-{uuid.uuid4().hex}"
            conn.execute("INSERT INTO canonical_turns(turn_id,chat_id,legacy_turn_index,provider_kind,provider_session_id,provider_turn_id) VALUES(?,?,?,?,?,?)",
                         (turn_id, owner, legacy_turn_index, provider_kind, provider_session_id, provider_turn_id))
            return turn_id

    def commit_durable_fact(self, *, pair_id: str, domain: str, envelope: dict[str, Any],
                            execution: bool = False) -> dict[str, Any]:
        """Atomically store an immutable fact and its byte-stable publication row."""
        if not self.v2_writes_enabled:
            raise RuntimeError("V2_READ_ONLY_RECOVERY")
        owner = self.normalize_chat_id(envelope.get("chat_id"))
        normalized_pair = str(pair_id or "").strip()
        normalized_domain = str(domain or "").strip()
        if not normalized_pair or not normalized_domain or "\x00" in normalized_pair + normalized_domain:
            raise ValueError("INVALID_FEED_SCOPE")
        if any(key in envelope for key in ("epoch", "delivery_attempt", "delivered_at")):
            raise ValueError("FORBIDDEN_DURABLE_METADATA")
        supplied_sequence_domain = str(envelope.get("sequence_domain") or normalized_domain).strip()
        if supplied_sequence_domain != normalized_domain:
            raise ValueError("SEQUENCE_DOMAIN_MISMATCH")
        origin_client = str(envelope.get("origin_client") or "mc").strip().lower()
        if not origin_client or "\x00" in origin_client:
            raise ValueError("INVALID_ORIGIN_CLIENT")
        event_id = str(envelope.get("event_id") or f"event-{uuid.uuid4().hex}").strip()
        kind = str(envelope.get("kind") or envelope.get("type") or "").strip()
        if not event_id or not kind or "\x00" in event_id + kind or not isinstance(envelope.get("body"), dict):
            raise ValueError("INVALID_DURABLE_FACT")
        for key in ("revision", "sync_sequence", "execution_sequence"):
            minimum = 0 if key == "revision" else 1
            if key in envelope and (isinstance(envelope[key], bool) or not isinstance(envelope[key], int) or not minimum <= envelope[key] <= MAX_INT64):
                raise ValueError(f"INVALID_{key.upper()}")
        try:
            json.dumps(envelope, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("INVALID_CANONICAL_VALUE") from exc
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_id.startswith("projection:"):
                message_id = str((envelope.get("body") or {}).get("message_id") or "").strip()
                projection_kind = str((envelope.get("body") or {}).get("kind") or "").strip()
                expected_role = {"question": "user", "final": "assistant"}.get(projection_kind)
                projection = conn.execute(
                    "SELECT chat_id,role,turn_id FROM canonical_messages WHERE message_id=?",
                    (message_id,),
                ).fetchone()
                if (projection is None or str(projection["chat_id"]) != owner or str(projection["role"]) != expected_role
                        or str(envelope.get("turn_id") or "") != str(projection["turn_id"])):
                    self._quarantine_conn(conn, "STALE_CANONICAL_PROJECTION", envelope, event_id)
                    raise ValueError("STALE_CANONICAL_PROJECTION")
            existing = conn.execute("SELECT canonical_hash,envelope_json,pair_id,domain FROM durable_facts WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                original = json.loads(existing["envelope_json"])
                comparable = {k: v for k, v in envelope.items() if k != "canonical_hash"}
                if existing["pair_id"] != normalized_pair or existing["domain"] != normalized_domain or any(original.get(k) != v for k, v in comparable.items()):
                    self._quarantine_conn(conn, "EVENT_ID_CONFLICT", envelope, event_id)
                    conn.commit()
                    raise ValueError("EVENT_ID_CONFLICT")
                return original
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) VALUES(?)", (owner,))
            state = conn.execute("SELECT revision,execution_sequence FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            revision = int(envelope.get("revision", state["revision"]))
            if revision != int(state["revision"]): raise ValueError("STALE_REVISION")
            conn.execute("INSERT OR IGNORE INTO v2_feed_state(pair_id,domain) VALUES(?,?)", (str(pair_id), "__pair__"))
            feed = conn.execute("SELECT sync_sequence FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'", (str(pair_id),)).fetchone()
            if int(feed["sync_sequence"]) >= MAX_INT64: raise OverflowError("SYNC_SEQUENCE_OVERFLOW")
            sync = int(feed["sync_sequence"]) + 1
            execution_seq = None
            if execution:
                if int(state["execution_sequence"]) >= MAX_INT64: raise OverflowError("EXECUTION_SEQUENCE_OVERFLOW")
                execution_seq = int(state["execution_sequence"]) + 1
            normalized = {**envelope, "protocol_version": 2, "event_id": event_id, "kind": kind,
                          "chat_id": owner, "domain": normalized_domain,
                          "sequence_domain": supplied_sequence_domain,
                          "origin_client": origin_client,
                          "revision": revision, "sync_sequence": sync}
            normalized.pop("canonical_hash", None)
            if execution_seq is not None: normalized["execution_sequence"] = execution_seq
            canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            normalized["canonical_hash"] = digest
            canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id=? AND domain='__pair__'", (sync, str(pair_id)))
            if execution_seq is not None: conn.execute("UPDATE v2_chat_state SET execution_sequence=? WHERE chat_id=?", (execution_seq, owner))
            payload = canonical.encode("utf-8")
            conn.execute("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,turn_id,revision,sync_sequence,execution_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id,digest,kind,str(pair_id),str(domain),owner,normalized.get("turn_id"),revision,sync,execution_seq,canonical))
            conn.execute("INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload) VALUES(?,?,?,?,?,?)", (str(pair_id),str(domain),sync,event_id,str(domain),payload))
            return normalized

    def paired_feed_high_water(self, *, pair_id: str, domain: str = "events") -> dict[str, Any]:
        """Return one authoritative paired-feed watermark for a v2 startup handshake."""
        normalized_pair = str(pair_id or "").strip()
        normalized_domain = str(domain or "").strip()
        if not normalized_pair or not normalized_domain:
            raise ValueError("INVALID_FEED_SCOPE")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sync_sequence FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'",
                (normalized_pair,),
            ).fetchone()
        return {
            "sequence_domain": normalized_domain,
            "high_sync_sequence": int(row["sync_sequence"]) if row else 0,
        }

    def commit_message_notification_fact(
        self,
        *,
        pair_id: str,
        domain: str,
        chat_id: str,
        message_id: str,
        notification_kind: str,
        text: str,
        chat_title: str,
        origin_client: str = "mc",
    ) -> dict[str, Any]:
        """Commit one pair-scoped notification fact backed by a canonical message."""
        if not self.v2_writes_enabled:
            raise RuntimeError("V2_READ_ONLY_RECOVERY")
        owner = self.normalize_chat_id(chat_id)
        normalized_pair = str(pair_id or "").strip()
        normalized_domain = str(domain or "").strip()
        normalized_message = str(message_id or "").strip()
        normalized_kind = str(notification_kind or "").strip()
        expected_role = {"user_message": "user", "assistant_final": "assistant"}.get(
            normalized_kind
        )
        if (not normalized_pair or not normalized_domain or not normalized_message
                or expected_role is None or "\x00" in normalized_pair + normalized_domain):
            raise ValueError("INVALID_NOTIFICATION_FACT")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT cm.chat_id,cm.role,cm.turn_id,cm.legacy_turn_index,t.payload_json,c.title "
                "FROM canonical_messages cm "
                "JOIN turns t ON t.chat_id=cm.chat_id AND t.turn_index=cm.legacy_turn_index "
                "JOIN chats c ON c.id=cm.chat_id WHERE cm.message_id=?",
                (normalized_message,),
            ).fetchone()
            if row is None or str(row["chat_id"]) != owner or str(row["role"]) != expected_role:
                raise ValueError("STALE_CANONICAL_NOTIFICATION")
            turn = self._json_dict(row["payload_json"])
            canonical_text = str(turn.get("question" if expected_role == "user" else "answer_md") or "")
            canonical_title = str(row["title"] or "").strip()
            if not canonical_text.strip() or not canonical_title:
                raise ValueError("INCOMPLETE_CANONICAL_NOTIFICATION")
            normalized_origin = str(origin_client or "mc").strip().lower()
            if not normalized_origin or "\x00" in normalized_origin:
                raise ValueError("INVALID_ORIGIN_CLIENT")
            event_id = f"notification:{normalized_pair}:{normalized_message}:{normalized_kind}"
            envelope = {
                "protocol_version": 2,
                "event_id": event_id,
                "kind": normalized_kind,
                "chat_id": owner,
                "turn_id": str(row["turn_id"]),
                "domain": normalized_domain,
                "sequence_domain": normalized_domain,
                "origin_client": normalized_origin,
                "body": {
                    "message_id": normalized_message,
                    "text": canonical_text,
                    "chat_title": canonical_title,
                },
            }
            existing = conn.execute(
                "SELECT envelope_json,pair_id,domain FROM durable_facts WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing:
                original = json.loads(existing["envelope_json"])
                comparable = {key: value for key, value in envelope.items()
                              if key not in {"canonical_hash", "sync_sequence", "revision"}}
                if (existing["pair_id"] != normalized_pair
                        or existing["domain"] != normalized_domain
                        or any(original.get(key) != value for key, value in comparable.items())):
                    self._quarantine_conn(conn, "EVENT_ID_CONFLICT", envelope, event_id)
                    conn.commit()
                    raise ValueError("EVENT_ID_CONFLICT")
                return original
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) VALUES(?)", (owner,))
            state = conn.execute(
                "SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)
            ).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO v2_feed_state(pair_id,domain) VALUES(?,?)",
                (normalized_pair, "__pair__"),
            )
            feed = conn.execute(
                "SELECT sync_sequence FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'",
                (normalized_pair,),
            ).fetchone()
            if int(feed["sync_sequence"]) >= MAX_INT64:
                raise OverflowError("SYNC_SEQUENCE_OVERFLOW")
            sync_sequence = int(feed["sync_sequence"]) + 1
            normalized = {
                **envelope,
                "revision": int(state["revision"]),
                "sync_sequence": sync_sequence,
            }
            canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            normalized["canonical_hash"] = digest
            canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id=? AND domain='__pair__'",
                (sync_sequence, normalized_pair),
            )
            conn.execute(
                "INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,turn_id,revision,sync_sequence,execution_sequence,envelope_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, digest, normalized_kind, normalized_pair, normalized_domain,
                 owner, str(row["turn_id"]), int(state["revision"]), sync_sequence,
                 None, canonical),
            )
            conn.execute(
                "INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload) VALUES(?,?,?,?,?,?)",
                (normalized_pair, normalized_domain, sync_sequence, event_id,
                 normalized_domain, canonical.encode("utf-8")),
            )
            return normalized

    def _quarantine_conn(self, conn: sqlite3.Connection, reason: str, payload: Any, event_id: str = "") -> None:
        conn.execute("INSERT INTO identity_quarantine(reason,event_id,payload_json) VALUES(?,?,?)", (reason,event_id,json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        conn.execute("DELETE FROM identity_quarantine WHERE id NOT IN (SELECT id FROM identity_quarantine ORDER BY id DESC LIMIT 1000)")

    def quarantine(self, reason: str, payload: Any, event_id: str = "") -> None:
        with self._connect() as conn: self._quarantine_conn(conn, reason, payload, event_id)

    def pending_outbox(self, limit: int = 100, *, pair_id: str | None = None, domain: str | None = None) -> list[dict[str, Any]]:
        where, args = "published_at IS NULL", []
        if pair_id is not None: where += " AND pair_id=?"; args.append(str(pair_id))
        if domain is not None: where += " AND domain=?"; args.append(str(domain))
        with self._connect() as conn:
            rows=conn.execute(f"SELECT * FROM publication_outbox WHERE {where} ORDER BY sync_sequence LIMIT ?", tuple(args+[max(1,int(limit))])).fetchall()
        return [dict(r) for r in rows]

    def mark_outbox_acked(self, sync_sequence: int, *, pair_id: str | None = None,
                          domain: str | None = None, consumer_id: str | None = None) -> None:
        where, args = "sync_sequence=?", [int(sync_sequence)]
        if pair_id is not None: where += " AND pair_id=?"; args.append(str(pair_id))
        if domain is not None: where += " AND domain=?"; args.append(str(domain))
        with self._connect() as conn:
            conn.execute(f"UPDATE publication_outbox SET published_at=unixepoch() WHERE {where} AND published_at IS NULL", tuple(args))
            if consumer_id is not None and domain is not None:
                conn.execute(
                    "INSERT INTO v2_checkpoints(pair_id,consumer_id,domain,sync_sequence) VALUES(?,?,?,?) "
                    "ON CONFLICT(pair_id,consumer_id,domain) DO UPDATE SET sync_sequence=MAX(sync_sequence,excluded.sync_sequence)",
                    (str(pair_id or "default"), str(consumer_id), str(domain), int(sync_sequence)),
                )

    def record_outbox_failure(self, sync_sequence: int, reason: str, *, pair_id: str | None = None, domain: str | None = None) -> None:
        where, args = "sync_sequence=?", [int(sync_sequence)]
        if pair_id is not None: where += " AND pair_id=?"; args.append(str(pair_id))
        if domain is not None: where += " AND domain=?"; args.append(str(domain))
        with self._connect() as conn:
            conn.execute(f"UPDATE publication_outbox SET attempts=attempts+1, blocked_reason=CASE WHEN attempts+1>=5 THEN ? ELSE blocked_reason END WHERE {where}", tuple([str(reason)]+args))

    def replay_after(self, *, domain: str, sync_sequence: int, pair_id: str = "default", limit: int = 100) -> list[bytes]:
        with self._connect() as conn:
            state=conn.execute("SELECT retained_from n FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'", (pair_id,)).fetchone()
            if state is None:
                return []
            retained=int(state["n"] or 1)
            if int(sync_sequence)+1 < retained: raise ValueError("SNAPSHOT_REQUIRED")
            rows=conn.execute("SELECT envelope_json FROM durable_facts WHERE pair_id=? AND domain=? AND sync_sequence>? ORDER BY sync_sequence LIMIT ?", (pair_id,domain,int(sync_sequence),max(1,int(limit)))).fetchall()
        return [str(r["envelope_json"]).encode("utf-8") for r in rows]

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
        current_time = int(time.time() if now is None else now)
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
                "SELECT envelope_json,execution_sequence FROM durable_facts "
                "WHERE pair_id=? AND domain=? AND chat_id=? AND revision=? "
                "AND execution_sequence IS NOT NULL AND execution_sequence<=? AND execution_sequence<? "
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
                "SELECT envelope_json FROM durable_facts WHERE pair_id=? AND domain=? AND chat_id=? "
                "AND revision=? AND execution_sequence BETWEEN ? AND ? ORDER BY execution_sequence",
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

    def resolve_canonical_message_by_content(self, chat_id: str, *, role: str,
                                             content: str) -> str:
        """Resolve only an unambiguous current canonical message; never guess by position."""
        owner = self.normalize_chat_id(chat_id)
        normalized_role = str(role or "").strip().lower()
        content_key = "question" if normalized_role == "user" else "answer_md"
        if normalized_role not in {"user", "assistant"}:
            raise ValueError("INVALID_MESSAGE_ROLE")
        wanted = str(content or "")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT cm.message_id,t.payload_json FROM canonical_messages cm "
                "JOIN turns t ON t.chat_id=cm.chat_id AND t.turn_index=cm.legacy_turn_index "
                "WHERE cm.chat_id=? AND cm.role=?", (owner, normalized_role),
            ).fetchall()
        matches = [
            str(row["message_id"]) for row in rows
            if str(self._json_dict(row["payload_json"]).get(content_key) or "") == wanted
        ]
        if len(matches) != 1:
            raise ValueError("AMBIGUOUS_CANONICAL_MESSAGE")
        return matches[0]

    def resolve_canonical_message_by_turn(self, chat_id: str, *, role: str,
                                          turn_index: int) -> str:
        """Resolve a projection reference by its canonical owner and turn."""
        owner = self.normalize_chat_id(chat_id)
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"user", "assistant"} or int(turn_index) < 0:
            raise ValueError("INVALID_MESSAGE_PROJECTION")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT message_id FROM canonical_messages WHERE chat_id=? AND role=? "
                "AND legacy_turn_index=?", (owner, normalized_role, int(turn_index)),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("AMBIGUOUS_CANONICAL_MESSAGE")
        return str(rows[0]["message_id"])

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

    def load_chat(self, chat_id: str, *, include_execution_steps: bool = True) -> dict[str, Any] | None:
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
        if include_execution_steps:
            chat["execution_steps"] = self.load_execution_steps(normalized)
        return chat

    def delete_chat(self, chat_id: str) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM canonical_messages WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM canonical_turns WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM v2_chat_state WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM execution_steps WHERE chat_id = ?", (normalized,))
            conn.execute("DELETE FROM turns WHERE chat_id = ?", (normalized,))
            conn.execute("DELETE FROM chats WHERE id = ?", (normalized,))

    def delete_chats(self, chat_ids: list[str]) -> None:
        normalized_ids = [str(chat_id or "").strip() for chat_id in chat_ids or []]
        normalized_ids = [chat_id for chat_id in normalized_ids if chat_id]
        if not normalized_ids:
            return
        with self._connect() as conn:
            conn.executemany("DELETE FROM canonical_messages WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM canonical_turns WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM v2_chat_state WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM execution_steps WHERE chat_id = ?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM turns WHERE chat_id = ?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM chats WHERE id = ?", [(chat_id,) for chat_id in normalized_ids])

    def replace_turns(self, chat_id: str, turns: list[dict[str, Any]]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        incoming = [turn for turn in (turns or []) if isinstance(turn, dict)]
        with self._connect() as conn:
            existing = {int(row["turn_index"]): self._json_dict(row["payload_json"])
                        for row in conn.execute("SELECT turn_index,payload_json FROM turns WHERE chat_id=?", (normalized,))}
            for index, turn in enumerate(incoming):
                old = existing.get(index)
                if old is not None and not self._same_canonical_turn(old, turn):
                    conn.execute("DELETE FROM canonical_messages WHERE chat_id=? AND legacy_turn_index=?", (normalized,index))
                    conn.execute("DELETE FROM canonical_turns WHERE chat_id=? AND legacy_turn_index=?", (normalized,index))
            conn.execute("DELETE FROM canonical_messages WHERE chat_id=? AND legacy_turn_index>=?", (normalized,len(incoming)))
            conn.execute("DELETE FROM canonical_turns WHERE chat_id=? AND legacy_turn_index>=?", (normalized,len(incoming)))
            conn.execute("DELETE FROM turns WHERE chat_id = ?", (normalized,))
            conn.executemany(
                "INSERT INTO turns(chat_id, turn_index, payload_json) VALUES (?, ?, ?)",
                [
                    (normalized, idx, json.dumps(turn, ensure_ascii=False))
                    for idx, turn in enumerate(incoming)
                ],
            )
            self._backfill_canonical_turns_conn(conn, normalized, incoming, 0)

    def replace_turns_from(self, chat_id: str, turns: list[dict[str, Any]], *, start_index: int = 0) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        start = max(0, self._int_or(start_index, 0))
        suffix = turns or []
        with self._connect() as conn:
            existing = {int(row["turn_index"]): self._json_dict(row["payload_json"])
                        for row in conn.execute("SELECT turn_index,payload_json FROM turns WHERE chat_id=? AND turn_index>=?", (normalized,start))}
            for offset, turn in enumerate(suffix):
                index = start + offset
                old = existing.get(index)
                if isinstance(turn, dict) and old is not None and not self._same_canonical_turn(old, turn):
                    conn.execute("DELETE FROM canonical_messages WHERE chat_id=? AND legacy_turn_index=?", (normalized,index))
                    conn.execute("DELETE FROM canonical_turns WHERE chat_id=? AND legacy_turn_index=?", (normalized,index))
            end = start + len(suffix)
            conn.execute("DELETE FROM canonical_messages WHERE chat_id=? AND legacy_turn_index>=?", (normalized,end))
            conn.execute("DELETE FROM canonical_turns WHERE chat_id=? AND legacy_turn_index>=?", (normalized,end))
            conn.execute("DELETE FROM turns WHERE chat_id = ? AND turn_index >= ?", (normalized, start))
            conn.executemany(
                "INSERT INTO turns(chat_id, turn_index, payload_json) VALUES (?, ?, ?)",
                [
                    (normalized, start + offset, json.dumps(turn, ensure_ascii=False))
                    for offset, turn in enumerate(suffix)
                    if isinstance(turn, dict)
                ],
            )
            self._backfill_canonical_turns_conn(conn, normalized, suffix, start)

    @staticmethod
    def _same_canonical_turn(old: dict[str, Any], new: dict[str, Any]) -> bool:
        return all(str(old.get(key) or "") == str(new.get(key) or "") for key in (
            "question", "model", "provider", "provider_kind", "provider_user_message_id"
        ))

    def _backfill_canonical_turns_conn(self, conn: sqlite3.Connection, chat_id: str,
                                       turns: list[dict[str, Any]], start: int) -> None:
        for offset, payload in enumerate(turns):
            if not isinstance(payload, dict):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO canonical_turns(turn_id,chat_id,legacy_turn_index,provider_kind,provider_session_id,provider_turn_id) VALUES(?,?,?,?,?,?)",
                (f"turn-{uuid.uuid4().hex}", chat_id, start + offset,
                 str(payload.get("provider") or payload.get("model") or ""),
                 str(payload.get("session_id") or payload.get("thread_id") or ""),
                str(payload.get("turn_id") or "")),
            )
            canonical = conn.execute(
                "SELECT turn_id FROM canonical_turns WHERE chat_id=? AND legacy_turn_index=?",
                (chat_id, start + offset),
            ).fetchone()
            if canonical is None:
                continue
            provider = str(payload.get("provider") or payload.get("model") or "")
            for role, provider_key in (("user", "provider_user_message_id"), ("assistant", "provider_message_id")):
                content_key = "question" if role == "user" else "answer_md"
                if payload.get(content_key) is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO canonical_messages(message_id,turn_id,chat_id,role,provider_kind,provider_message_id,legacy_turn_index) VALUES(?,?,?,?,?,?,?)",
                    (f"message-{uuid.uuid4().hex}", str(canonical["turn_id"]), chat_id, role,
                     provider, str(payload.get(provider_key) or ""), start + offset),
                )

    def count_turns(self, chat_id: str) -> int:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM turns WHERE chat_id = ?",
                (normalized,),
            ).fetchone()
        return self._int_or(row["total"] if row is not None else 0, 0)

    def load_turns(self, chat_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id = ? ORDER BY turn_index",
                (str(chat_id or "").strip(),),
            ).fetchall()
        return [payload for payload in (self._json_dict(row["payload_json"]) for row in rows) if payload]

    def load_turns_page(
        self,
        chat_id: str,
        *,
        limit: int = 100,
        before_turn_index: int | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return 0, []
        row_limit = max(1, int(limit or 1))
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM turns WHERE chat_id = ?",
                (normalized,),
            ).fetchone()
            total = self._int_or(total_row["total"] if total_row is not None else 0, 0)
            try:
                end = int(before_turn_index) if before_turn_index is not None else total
            except Exception:
                end = total
            end = max(0, min(end, total))
            start = max(0, end - row_limit)
            rows = conn.execute(
                """
                SELECT payload_json FROM turns
                WHERE chat_id = ? AND turn_index >= ? AND turn_index < ?
                ORDER BY turn_index
                """,
                (normalized, start, end),
            ).fetchall()
        return total, [payload for payload in (self._json_dict(row["payload_json"]) for row in rows) if payload]

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
            payload["_store_step_index"] = int(row["step_index"])
            payload.setdefault("turn_idx", row["turn_idx"])
            payload.setdefault("event_type", str(row["event_type"] or ""))
            payload.setdefault("display_kind", str(row["display_kind"] or ""))
            payload.setdefault("list_text", str(row["list_text"] or ""))
            payload.setdefault("detail_text", str(row["detail_text"] or ""))
            out.append(payload)
        return out

    def load_recent_execution_steps(
        self,
        chat_id: str,
        *,
        turn_idx: int | None = None,
        limit: int = 100,
        before_step_index: int | None = None,
        include_total: bool = True,
    ) -> tuple[int, list[dict[str, Any]]]:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return 0, []
        params: list[Any] = [normalized]
        where = "chat_id = ?"
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
            payload["_store_step_index"] = int(row["step_index"])
            payload.setdefault("turn_idx", row["turn_idx"])
            payload.setdefault("event_type", str(row["event_type"] or ""))
            payload.setdefault("display_kind", str(row["display_kind"] or ""))
            payload.setdefault("list_text", str(row["list_text"] or ""))
            payload.setdefault("detail_text", str(row["detail_text"] or ""))
            out.append(payload)
        return int(total_row["total"] if total_row is not None else 0), out

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
        summary = self._metadata_from_row(row)
        summary.update({
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
        })
        return summary

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
