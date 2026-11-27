from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from agy_acp import AGY_MODEL_ID
from chat_store_execution import ChatStoreExecutionMixin
from chat_store_notifications import ChatStoreNotificationsMixin
from chat_store_schema import ChatStoreSchemaMixin, MAX_INT64, V2_MIGRATION_KEY
from context_usage import NATIVE_USAGE_FIELDS
from execution_projection import canonical_answer_timestamp, should_show_execution_step
from native_history import (
    CODEX_STORAGE_FIELDS, NATIVE_CHAT_FIELDS, NativeSessionIdentity, NativeSessionSnapshot, merge_native_title,
)


CHAT_PAYLOAD_FIELDS = {"turns", "execution_steps"}
MC_OWNED_HISTORY = "COALESCE(json_extract(c.metadata_json,'$.native_identity'),'') <> c.id"


def is_mc_owned_chat(chat_id: str, chat: dict) -> bool:
    return bool(chat_id) and chat.get("native_identity") != chat_id


CLEAR_OPERATION_STATES = frozenset({
    "requested", "clear_acknowledged", "resend_dispatched", "resend_blocked",
    "completed_with_resend", "completed_clear_only", "completed_no_message", "superseded",
})
CLEAR_TERMINAL_STATES = frozenset({
    "completed_with_resend", "completed_clear_only", "completed_no_message", "superseded",
})


class ChatStore(ChatStoreSchemaMixin, ChatStoreNotificationsMixin, ChatStoreExecutionMixin):
    def __init__(self, db_path: Path, *, max_execution_steps_per_turn: int = 500) -> None:
        self.db_path = Path(db_path)
        self.max_execution_steps_per_turn = int(max_execution_steps_per_turn)
        self._native_title_observations = {}
        self._busy_timeout = ContextVar("chat_store_busy_timeout", default=5.0)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._initialize_schema(conn)

    @property
    def _clock(self):
        """Use the store clock for persistence and publication deadlines."""
        return time

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

    def remote_attachment_send(self, scope: tuple, *, reserve: bool = False):
        with self._connect() as conn:
            if reserve:
                conn.execute(
                    "INSERT OR IGNORE INTO remote_attachment_sends "
                    "(pair_id,device_id,chat_id,send_id,revision) VALUES(?,?,?,?,?)", scope
                )
            row = conn.execute(
                "SELECT revision,fingerprint,state FROM remote_attachment_sends "
                "WHERE pair_id=? AND device_id=? AND chat_id=? AND send_id=?", scope[:4]
            ).fetchone()
            return dict(row) if row is not None else None

    def claim_remote_attachment_send(self, scope: tuple, fingerprint: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "UPDATE remote_attachment_sends SET fingerprint=?,state='starting' "
                "WHERE pair_id=? AND device_id=? AND chat_id=? AND send_id=? AND revision=? "
                "AND state='uploading' AND (fingerprint IS NULL OR fingerprint=?)",
                (fingerprint, *scope, fingerprint),
            ).rowcount == 1

    def release_remote_attachment_send(self, scope: tuple) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE remote_attachment_sends SET state='uploading' "
                "WHERE pair_id=? AND device_id=? AND chat_id=? AND send_id=? AND revision=? "
                "AND state='starting'", scope
            )

    @staticmethod
    def _eligible_clear_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or payload.get("local_command") or payload.get("deleted") or payload.get("is_deleted"):
            return False
        role = str(payload.get("role") or "user").strip().lower()
        if role not in {"", "user", "human"}:
            return False
        texts = [payload[key].strip() for key in ("question", "text")
                 if isinstance(payload.get(key), str) and payload[key].strip()]
        return bool(texts) and not any(text.startswith("/") for text in texts)

    @staticmethod
    def _canonical_clear_snapshot(payload: dict[str, Any], turn_id: str | None,
                                  message_id: str | None) -> dict[str, Any]:
        allowed = {
            "question", "text", "model", "origin", "question_origin", "provider", "provider_kind",
            "provider_message_id", "created_at", "codex_service_tier",
        }
        snapshot = {key: payload[key] for key in allowed if key in payload}
        snapshot["canonical_turn_id"] = turn_id
        snapshot["canonical_message_id"] = message_id
        return snapshot

    def begin_clear_operation(self, chat_id: str, *, idempotency_key: str,
                              pair_id: str = "default", domain: str = "events",
                              operation_id: str | None = None,
                              pending_chat: dict | None = None, pending_turns: list | None = None,
                              expected_revision: int | None = None,
                              allow_new_owner: bool = False,
                              play_resend_sound: bool = True) -> dict[str, Any]:
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
            state = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            observed_revision = int(state["revision"]) if state is not None else 1
            if expected_revision is not None and observed_revision != expected_revision:
                raise RuntimeError("CLEAR_GENERATION_CHANGED")
            chat = conn.execute("SELECT * FROM chats WHERE id=?", (owner,)).fetchone()
            if chat is None and (not allow_new_owner or conn.execute(
                    "SELECT 1 FROM meta WHERE key=?", (f"deleted-chat:{owner}",),
            ).fetchone() is not None):
                raise KeyError("CHAT_NOT_FOUND")
            if pending_chat is not None:
                merged = {**pending_chat, "id": owner}
                if chat is not None:
                    for field in ("title", "title_revision", "title_source", "title_manual",
                                  "title_updated_at", "pinned", "detail_panel_mode"):
                        merged[field] = chat[field]
                self._upsert_chat_conn(conn, merged)
            chat = conn.execute("SELECT metadata_json FROM chats WHERE id=?", (owner,)).fetchone()
            if chat is None:
                raise KeyError("CHAT_NOT_FOUND")
            if pending_turns is not None:
                self._replace_turns_conn(conn, owner, pending_turns)
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
            conn.execute("INSERT INTO read_generations(chat_id,generation) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET generation=excluded.generation", (owner, uuid.uuid4().hex))
            metadata = self._json_dict(chat["metadata_json"])
            if metadata.get("codex_thread_id"):
                identity = metadata.get("native_identity")
                if not identity and (storage := metadata.get("codex_storage_id")):
                    identity = NativeSessionIdentity(pair, "codex", storage, metadata["codex_thread_id"]).chat_id
                if identity:
                    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?, '1')",
                                 (f"retired-native-history:{identity}",))
                    conn.execute("DELETE FROM native_history_owners WHERE native_chat_id=? AND chat_id=?",
                                 (identity, owner))
                for field in (*CODEX_STORAGE_FIELDS, *NATIVE_CHAT_FIELDS):
                    metadata.pop(field, None)
            metadata.update(dict.fromkeys(NATIVE_USAGE_FIELDS))
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
            conn.execute("DELETE FROM execution_assemblers WHERE chat_id=?", (owner,))
            conn.execute("DELETE FROM execution_fragment_conflicts WHERE chat_id=?", (owner,))
            self._invalidate_execution_snapshots(conn, owner)
            try:
                conn.execute(
                    "INSERT INTO clear_operations(operation_id,chat_id,revision,idempotency_key,pair_id,domain,state,snapshot_json,source_turn_id,source_message_id,sound_permission,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (op_id, owner, revision, key, pair, event_domain, "requested",
                     json.dumps(snapshot, ensure_ascii=False, sort_keys=True) if snapshot is not None else None,
                     source_turn_id, source_message_id, 1 if snapshot is not None and play_resend_sound else 0, now, now),
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
                                operation_id: str, chat_id: str, revision: int, state: str,
                                *, kind: str = "clear_context") -> None:
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
        envelope = {"protocol_version": 2, "event_id": event_id, "kind": kind, "chat_id": chat_id,
                    "domain": domain, "sequence_domain": domain, "origin_client": "mc",
                    "revision": revision, "sync_sequence": sync,
                    "body": {"operation_id": operation_id, "state": state}}
        canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        envelope["canonical_hash"] = digest
        payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        conn.execute("UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id=? AND domain='__pair__'", (sync, pair_id))
        conn.execute("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,revision,sync_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?)",
                     (event_id, digest, kind, pair_id, domain, chat_id, revision, sync, payload))
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

    def reconcile_completed_clear_operation(
        self, operation_id: str, *, chat_id: str, revision: int
    ) -> dict[str, Any] | None:
        """Complete a legacy resend only from one matching persisted done turn."""
        owner = self.normalize_chat_id(chat_id)
        expected_revision = self._clear_revision_value(revision)
        if expected_revision is None:
            return None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)
            ).fetchone()
            if (
                row is None
                or str(row["chat_id"]) != owner
                or int(row["revision"]) != expected_revision
                or str(row["state"]) == "superseded"
            ):
                return None
            current = str(row["state"])
            if current == "completed_with_resend":
                return self._decode_clear_operation(row)
            if current not in {"resend_dispatched", "resend_blocked"}:
                return None
            if current == "resend_blocked" and str(row["failure_code"]) != "DELIVERY_OUTCOME_UNCERTAIN":
                return None
            chat_state = conn.execute(
                "SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)
            ).fetchone()
            if chat_state is None or int(chat_state["revision"]) != expected_revision:
                return None
            matches = []
            for turn_row in conn.execute(
                "SELECT payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,)
            ):
                payload = self._json_dict(turn_row["payload_json"])
                if (
                    str(payload.get("clear_operation_id") or "").strip() == str(operation_id)
                    and self._clear_revision_value(payload.get("clear_revision")) == expected_revision
                    and str(payload.get("request_status") or "").strip() == "done"
                ):
                    matches.append(payload)
            if len(matches) != 1:
                return None
            now = time.time()
            changed = conn.execute(
                "UPDATE clear_operations SET state='completed_with_resend',failure_code='',updated_at=? "
                "WHERE operation_id=? AND chat_id=? AND revision=? AND "
                "(state='resend_dispatched' OR (state='resend_blocked' AND failure_code='DELIVERY_OUTCOME_UNCERTAIN'))",
                (now, str(operation_id), owner, expected_revision),
            ).rowcount
            if not changed:
                return None
            self._insert_clear_fact_conn(
                conn, row["pair_id"], row["domain"], str(operation_id), owner,
                expected_revision, "completed_with_resend",
            )
            completed = conn.execute(
                "SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)
            ).fetchone()
            return self._decode_clear_operation(completed)

    @staticmethod
    def _clear_revision_value(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
        return None

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
            if operation and operation["state"] in {"resend_dispatched", "resend_blocked"}:
                reconciled = self.reconcile_completed_clear_operation(
                    operation_id,
                    chat_id=str(operation["chat_id"]),
                    revision=int(operation["revision"]),
                )
                if reconciled is not None:
                    continue
            if operation and operation["state"] == "resend_dispatched":
                operation = self.transition_clear_operation(
                    operation_id, "resend_blocked", failure_code="DELIVERY_OUTCOME_UNCERTAIN"
                )
            if operation:
                recovered.append(operation)
        return recovered

    def claim_clear_resend_dispatch(self, operation_id: str, *, chat_id: str,
                                    revision: int) -> dict[str, Any] | None:
        """Persist the at-most-once boundary before any provider call."""
        owner = self.normalize_chat_id(chat_id)
        expected_revision = self._clear_revision_value(revision)
        if expected_revision is None:
            return None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone()
            if (
                not row
                or str(row["chat_id"]) != owner
                or int(row["revision"]) != expected_revision
                or row["state"] not in {"requested", "clear_acknowledged"}
            ):
                return None
            now = time.time()
            changed = conn.execute(
                "UPDATE clear_operations SET state='resend_dispatched',dispatch_claimed_at=?,updated_at=? "
                "WHERE operation_id=? AND chat_id=? AND revision=? "
                "AND state IN ('requested','clear_acknowledged')",
                (now, now, str(operation_id), owner, expected_revision)
            ).rowcount
            if not changed:
                return None
            self._insert_clear_fact_conn(conn, row["pair_id"], row["domain"], str(operation_id),
                                         row["chat_id"], int(row["revision"]), "resend_dispatched")
            return self._decode_clear_operation(conn.execute("SELECT * FROM clear_operations WHERE operation_id=?", (str(operation_id),)).fetchone())

    def release_unattempted_clear_resend(self, operation_id: str, *, chat_id: str, revision: int) -> None:
        """The coordinator still owns this claim and has never called a provider."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM clear_operations WHERE operation_id=? AND chat_id=? AND revision=? "
                               "AND state='resend_dispatched'", (operation_id, chat_id, revision)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE clear_operations SET state='clear_acknowledged',dispatch_claimed_at=NULL,updated_at=? "
                         "WHERE operation_id=?", (time.time(), operation_id))
            self._insert_clear_fact_conn(conn, row["pair_id"], row["domain"], operation_id,
                                         chat_id, revision, "clear_acknowledged")

    def consume_clear_resend_sound(self, operation_id: str, *, chat_id: str,
                                   revision: int) -> dict[str, Any] | None:
        """Atomically grant the one send sound for a verified accepted resend.

        Rows created before this capability have ``sound_permission=0`` and
        deliberately remain silent: their acceptance identity cannot be
        reconstructed safely after upgrade.
        """
        owner = self.normalize_chat_id(chat_id)
        expected_revision = self._clear_revision_value(revision)
        if expected_revision is None:
            return None
        op_id = str(operation_id or "").strip()
        if not op_id:
            return None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            changed = conn.execute(
                "UPDATE clear_operations SET sound_consumed_at=?,updated_at=? "
                "WHERE operation_id=? AND chat_id=? AND revision=? "
                "AND state='resend_dispatched' AND sound_permission=1 "
                "AND sound_consumed_at IS NULL",
                (now, now, op_id, owner, expected_revision),
            ).rowcount
            if changed != 1:
                return None
            row = conn.execute(
                "SELECT * FROM clear_operations WHERE operation_id=?", (op_id,)
            ).fetchone()
            return self._decode_clear_operation(row)

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
    def busy_timeout(self, seconds: float):
        token = self._busy_timeout.set(seconds)
        try:
            yield
        finally:
            self._busy_timeout.reset(token)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=self._busy_timeout.get())
        conn.row_factory = sqlite3.Row
        conn.create_function("unicode_casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_chat(self, chat: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_chat_conn(conn, chat)

    def stage_agy_handoff(self, chat, turn):
        """Reserve this native session without exposing an unfinished chat."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (
                f"agy-handoff:{chat['native_identity']}", json.dumps((chat, turn), ensure_ascii=False),
            ))

    def settle_agy_handoff(self, chat, turn):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._settle_agy_handoff_conn(conn, chat, turn)

    def _settle_agy_handoff_conn(self, conn, chat, turn):
        identity = chat["native_identity"]
        if turn is None:
            conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES (?, '1')",
                         (f"retired-native-history:{identity}",))
        else:
            self._upsert_chat_conn(conn, chat)
            self._replace_turns_conn(conn, chat["id"], [turn])
            conn.execute("INSERT INTO native_history_owners(native_chat_id,chat_id,account) VALUES(?,?,?)",
                         (identity, chat["id"], chat["native_account"]))
        conn.execute("DELETE FROM meta WHERE key=?", (f"agy-handoff:{identity}",))

    def recover_agy_handoffs(self):
        """A process interruption may have occurred after ACP accepted the prompt."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for entry in conn.execute("SELECT value FROM meta WHERE key LIKE 'agy-handoff:%'").fetchall():
                chat, turn = json.loads(entry["value"])
                self._settle_agy_handoff_conn(conn, chat, turn)

    def unresolved_agy_handoff(self, source_id):
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM chats WHERE json_extract(metadata_json,'$.agy_handoff_source_id')=? "
                "AND json_extract(metadata_json,'$.native_unresolved_turn') IS NOT NULL "
                "UNION ALL SELECT 1 FROM meta WHERE key LIKE 'agy-handoff:%' "
                "AND json_extract(value,'$[0].agy_handoff_source_id')=? LIMIT 1",
                (source_id, source_id),
            ).fetchone() is not None

    def _upsert_chat_conn(self, conn, chat):
        if not isinstance(chat, dict):
            return
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id or conn.execute("SELECT 1 FROM meta WHERE key=?", (f"deleted-chat:{chat_id}",)).fetchone():
            return
        if chat.get("native_identity") or any(chat.get(field) for field in
                                              ("agy_session_id", "kimi_session_id", "codex_thread_id")):
            chat = self._retain_committed_native_title(conn, chat_id, chat)
        created = self._float_or(chat.get("created_at"), 0.0)
        updated = self._float_or(chat.get("updated_at"), created)
        title_manual = self._bool_value(chat.get("title_manual"))
        title_source = str(chat.get("title_source") or ("manual" if title_manual else "default"))
        title_updated = self._float_or(chat.get("title_updated_at"), updated)
        detail_panel_mode = str(chat.get("detail_panel_mode") or "").strip()
        if detail_panel_mode != "execution":
            detail_panel_mode = "answers"
        metadata = {k: v for k, v in chat.items() if k not in CHAT_PAYLOAD_FIELDS}
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
                updated_at=MAX(chats.updated_at, excluded.updated_at),
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

    def _native_history_target(self, conn, snapshot: NativeSessionSnapshot, *, mc_owned_only: bool) -> tuple[str, bool]:
        native_id = snapshot.identity.chat_id
        if conn.execute("SELECT 1 FROM meta WHERE key IN (?,?,?,?)", (
            f"retired-native-history:{native_id}", f"hidden-native-history:{native_id}",
            f"deleted-native-history:{native_id}", f"agy-handoff:{native_id}",
        )).fetchone():
            return native_id, False
        row = conn.execute(
            "SELECT chat_id,account FROM native_history_owners WHERE native_chat_id=?", (native_id,)
        ).fetchone()
        if row is not None:
            owner = str(row["chat_id"])
            return owner, row["account"] == snapshot.account and (not mc_owned_only or owner != native_id)

        # Existing provider-created application chats may predate discovery.
        # The adapter supplies its complete legacy owner identity.
        for field in snapshot.legacy_fields:
            if not field.isidentifier():
                raise ValueError("Invalid legacy history field")
        if snapshot.legacy_fields:
            predicates = " AND ".join(f"json_extract(metadata_json,'$.{field}')=?" for field in snapshot.legacy_fields)
            compatible = {field: value for field, value in snapshot.chat_fields.items()
                          if field.endswith("_storage_id") and field not in snapshot.legacy_fields}
            for field in compatible:
                if not field.isidentifier():
                    raise ValueError("Invalid legacy storage field")
                predicates += f" AND COALESCE(json_extract(metadata_json,'$.{field}'),?)=?"
            model_filter, model = ("c.model LIKE ?", "codex/%") if snapshot.identity.provider == "codex" else ("c.model=?", snapshot.model)
            ownership = f"AND c.id <> ? AND {MC_OWNED_HISTORY} " if mc_owned_only else ""
            legacy = conn.execute(
                f"SELECT c.id FROM chats c LEFT JOIN native_history_owners n ON n.chat_id=c.id "
                f"WHERE n.chat_id IS NULL AND {model_filter} AND {predicates} "
                "AND COALESCE(json_extract(metadata_json,'$.native_identity'),?)=? "
                f"{ownership}ORDER BY c.created_at,c.id LIMIT 1",
                (model, *snapshot.legacy_fields.values(),
                 *(value for value in compatible.values() for _ in range(2)), native_id, native_id,
                 *((native_id,) if mc_owned_only else ())),
            ).fetchone()
            if legacy is not None:
                return str(legacy["id"]), True
        return native_id, not mc_owned_only

    def _retain_committed_native_title(self, conn, chat_id, incoming):
        saved = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if saved is None:
            return incoming
        metadata = self._metadata_from_row(saved)
        if not metadata.get("native_identity"):
            return incoming
        if incoming.get("native_identity"):
            if any(metadata.get(key) != incoming.get(key) for key in ("native_identity", "native_account")):
                return incoming
            adopted = False
        else:
            provider = str(incoming.get("model") or "").split("/", 1)[0]
            scope = {
                "agy": ("agy_session_id", ("agy_native_owner",)),
                "codex": ("codex_thread_id", CODEX_STORAGE_FIELDS),
                "kimi": ("kimi_session_id", ("kimi_storage_id",)),
            }.get(provider)
            if scope is None or str(saved["model"]).split("/", 1)[0] != provider:
                return incoming
            session, fields = scope
            if (not incoming.get(session) or incoming[session] != metadata.get(session)
                    or (provider == "agy" and not incoming.get("agy_native_owner"))
                    or any(incoming.get(field) not in (None, "", metadata.get(field))
                           for field in (*fields, "native_account"))):
                return incoming
            adopted = True
        chat = incoming.copy()
        if adopted:
            for field in (*NATIVE_CHAT_FIELDS, *fields):
                if field in metadata:
                    chat[field] = metadata[field]
        if self._int_or(metadata.get("native_title_revision"), 0) > self._int_or(chat.get("native_title_revision"), 0):
            for field in ("native_title", "native_title_revision"):
                chat[field] = metadata[field]
        if self._float_or(metadata.get("native_title_observed_at"), 0) > self._float_or(chat.get("native_title_observed_at"), 0):
            chat["native_title_observed_at"] = metadata["native_title_observed_at"]
        if saved["title_revision"] > self._int_or(chat.get("title_revision"), 1):
            for field in ("title", "title_source", "title_manual", "title_updated_at", "title_revision"):
                chat[field] = saved[field]
        return chat

    def native_history_target(self, snapshot: NativeSessionSnapshot, *, mc_owned_only: bool = False) -> tuple[str, float]:
        with self._connect() as conn:
            owner, matches = self._native_history_target(conn, snapshot, mc_owned_only=mc_owned_only)
            if not matches or conn.execute(
                "SELECT 1 FROM meta WHERE key=?", (f"deleted-chat:{owner}",)
            ).fetchone():
                return "", 0
            row = conn.execute("SELECT metadata_json FROM chats WHERE id=?", (owner,)).fetchone()
            metadata = self._metadata_from_row(row) if row else {}
            # Existing archives get one successful observation after upgrade.
            replayed_at = metadata.get("native_replayed_at") if metadata.get("native_notification_baseline") else 0
            return owner, self._float_or(replayed_at, 0)

    def mc_native_history_candidates(self) -> tuple[set[str], dict[str, set[str]]]:
        """Read lightweight candidate identities before enumerating provider history."""
        sessions = {"codex": set(), "kimi": set(), "agy": set()}
        identities = set()
        fields = {"codex": "codex_thread_id", "kimi": "kimi_session_id", "agy": "agy_session_id"}
        with self._connect() as conn:
            for row in conn.execute(
                f"SELECT SUBSTR(c.model,1,INSTR(c.model,'/')-1) AS provider, "
                "json_extract(c.metadata_json,'$.native_identity') AS identity, "
                "json_extract(c.metadata_json,'$.codex_thread_id') AS codex_thread_id, "
                "json_extract(c.metadata_json,'$.kimi_session_id') AS kimi_session_id, "
                "json_extract(c.metadata_json,'$.agy_session_id') AS agy_session_id "
                f"FROM chats c WHERE {MC_OWNED_HISTORY}"
            ):
                if row["identity"]:
                    identities.add(row["identity"])
                field = fields.get(row["provider"])
                if field and row[field]:
                    sessions[row["provider"]].add(row[field])
        return identities, sessions

    def merge_native_history(self, snapshot: NativeSessionSnapshot, *, expected_chat_id: str,
                             expected_revision: int = 0, merge_title: bool = True,
                             observed_between: tuple[float, float] | None = None,
                             mc_owned_only: bool = False) -> dict | None:
        """Commit only an append-compatible replay to the chat that authorized it."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner, matches = self._native_history_target(conn, snapshot, mc_owned_only=mc_owned_only)
            revision = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            if (int(revision[0]) if revision else 0) != expected_revision:
                return None
            if not matches or owner != expected_chat_id or conn.execute(
                "SELECT 1 FROM meta WHERE key=?", (f"deleted-chat:{owner}",)
            ).fetchone():
                return None
            row = conn.execute(
                "SELECT c.*,(SELECT COUNT(*) FROM turns WHERE chat_id=c.id) turn_count FROM chats c WHERE id=?",
                (owner,),
            ).fetchone()
            previous = self._summary_from_row(row) if row else {}
            if snapshot.updated_at and snapshot.updated_at < previous.get("native_updated_at", 0):
                return None
            old_count = int(previous.get("turn_count", 0))
            sent_count = old_count
            sent_turns = None
            if snapshot.identity.provider == "agy" and snapshot.turns is not None and old_count:
                rows = [(entry["turn_index"], self._json_dict(entry["payload_json"])) for entry in conn.execute(
                    "SELECT turn_index,payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,),
                )]
                prompt_rows = [(index, turn) for index, turn in rows
                               if turn.get("model") != "system" or turn.get("question")]
                first_queued = next((i for i, (_, turn) in enumerate(prompt_rows)
                                     if turn.get("model") == AGY_MODEL_ID and turn.get("request_status") == "queued"),
                                    None)
                if first_queued is not None:
                    if (len(snapshot.turns) != first_queued
                            or any(turn.get("model") != AGY_MODEL_ID or turn.get("request_status") != "queued"
                                   for _, turn in prompt_rows[first_queued:])):
                        # A native turn cannot be inserted ahead of already reserved local positions.
                        return None
                    sent_turns = prompt_rows[:first_queued]
                    sent_count = first_queued
                else:
                    sent_turns = rows
            conn.execute(
                "INSERT OR IGNORE INTO native_history_owners(native_chat_id,chat_id,account) VALUES(?,?,?)",
                (snapshot.identity.chat_id, owner, snapshot.account),
            )
            chat = previous.copy()
            for field, value in snapshot.chat_fields.items():
                chat.setdefault(field, value)
            chat.update(
                id=owner, model=previous.get("model") or snapshot.model,
                native_identity=snapshot.identity.chat_id, native_account=snapshot.account,
                native_cwd=previous.get("native_cwd") or snapshot.cwd,
                native_updated_at=max(previous.get("native_updated_at", 0), snapshot.updated_at),
                native_history_status=snapshot.status,
            )
            unresolved = previous.get("native_unresolved_turn")
            if unresolved and not snapshot.status:
                chat["native_history_status"] = unresolved["message"]
            appended = 0
            recovered = {}
            matched_agy_rows = set()
            accepted_replay = False
            if snapshot.turns is not None and len(snapshot.turns) >= sent_count:
                stored = sent_turns if sent_turns is not None else (
                    (entry["turn_index"], self._json_dict(entry["payload_json"])) for entry in conn.execute(
                        "SELECT turn_index,payload_json FROM turns WHERE chat_id=? ORDER BY turn_index", (owner,),
                    ))
                for (index, old), new in zip(stored, snapshot.turns):
                    handoff = index == 0 and self._is_first_agy_handoff_replay(
                        snapshot.identity, old, snapshot.turns,
                    )
                    if self._can_recover_terminal_final(snapshot.identity, old, new, agy_handoff=handoff):
                        recovered[index] = dict(old, answer_md=new["answer_md"], request_status="done",
                                                request_error="", terminal_final_pending=False,
                                                provider_message_id=new["provider_message_id"])
                        if new.get("answer_at"):
                            recovered[index]["answer_at"] = new["answer_at"]
                        if snapshot.identity.provider == "agy":
                            recovered[index]["agy_native_terminal_reason"] = "end_turn"
                            recovered[index].pop("agy_partial_answer_md", None)
                            for field in ("agy_native_turn_id", "provider_user_message_id"):
                                if new.get(field):
                                    recovered[index][field] = new[field]
                    elif (snapshot.identity.provider == "agy" and old.get("request_status") in
                          {"unknown", "cancelled", "failed"} and self._agy_replay_matches(snapshot.identity, old, new)
                          and new.get("agy_replay_followed")):
                        matched_agy_rows.add(index)
                    elif ((snapshot.identity.provider == "agy" and (
                            old.get("request_status") == "unknown" or
                            (old.get("request_status") == "pending" and old.get("agy_queue_claimed"))))
                          or not self._native_turn_matches(old, new)):
                        recovered.clear()
                        matched_agy_rows.clear()
                        if not unresolved:
                            chat["native_history_status"] = "原生历史与已保存内容不一致，已保留现有记录"
                        break
                else:
                    chat["native_replayed_at"] = snapshot.updated_at
                    accepted_replay = True
                    appended = len(snapshot.turns) - sent_count
            if unresolved and unresolved["index"] in recovered:
                chat["native_history_status"] = snapshot.status
                chat["native_unresolved_turn"] = None
            chat.setdefault("created_at", snapshot.updated_at)
            if snapshot.identity.provider == "codex" and old_count - 1 in recovered:
                chat["codex_turn_active"] = False
            if not previous or appended or recovered:
                chat["updated_at"] = max(previous.get("updated_at", 0), snapshot.updated_at)
            if not previous:
                chat.update(title=snapshot.title or snapshot.cwd, title_source="native",
                            title_updated_at=snapshot.title_observed_at or snapshot.updated_at,
                            title_revision=1)
            if merge_title:
                self._merge_observed_native_title(
                    chat, previous, snapshot.identity.provider, snapshot.title,
                    snapshot.title_observed_at or snapshot.updated_at, explicit=snapshot.title_explicit,
                )
            baseline = self._float_or(previous.get("native_notification_baseline"), 0)
            observed_until = 0.0
            if accepted_replay and not snapshot.status and observed_between:
                started_at, finished_at = observed_between
                if 0 < started_at <= finished_at and math.isfinite(finished_at):
                    observed_until = finished_at
                    if not baseline:
                        chat["native_notification_baseline"] = finished_at
            if accepted_replay and snapshot.identity.provider == "agy":
                for step in snapshot.execution_steps:
                    replay_index = step["turn_idx"]
                    if 0 <= replay_index < sent_count:
                        index = sent_turns[replay_index][0]
                        if index in matched_agy_rows or index in recovered:
                            self._merge_recovered_agy_step(
                                conn, owner, snapshot.identity.session_id, dict(step, turn_idx=index),
                            )
            if chat == previous and not appended and not recovered:
                return None
            self._upsert_chat_conn(conn, chat)
            for index, turn in recovered.items():
                conn.execute("UPDATE turns SET payload_json=? WHERE chat_id=? AND turn_index=?",
                             (json.dumps(turn, ensure_ascii=False), owner, index))
                self._backfill_canonical_turns_conn(conn, owner, [turn], index)
            incoming = []
            if appended:
                for replayed in snapshot.turns[old_count:]:
                    turn = dict(replayed, native_history=True)
                    turn.pop("native_final_notify_pair", None)
                    if baseline and self._is_new_native_final(turn, baseline, observed_until):
                        turn["native_final_notify_pair"] = snapshot.identity.pair_id
                    incoming.append(turn)
                conn.executemany(
                    "INSERT INTO turns(chat_id,turn_index,payload_json) VALUES(?,?,?)",
                    [(owner, index, json.dumps(turn, ensure_ascii=False))
                     for index, turn in enumerate(incoming, old_count)],
                )
                self._backfill_canonical_turns_conn(conn, owner, incoming, old_count)
                for step in snapshot.execution_steps:
                    if old_count <= step["turn_idx"] < len(snapshot.turns):
                        self._append_execution_step_on_conn(conn, owner, step)
            if (recovered or any(turn.get("native_final_notify_pair") for turn in incoming)) and self.v2_writes_enabled:
                self._reconcile_notification_finals_conn(conn, snapshot.identity.pair_id, owner)
            chat["turn_count"] = old_count + appended
            if recovered:
                chat["_recovered_terminal_turns"] = list(recovered)
            return chat

    def apply_native_session_title(self, chat_id, scope, revision, provider, title, observed_at, *,
                                   explicit=True, app_manual=False):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM turns WHERE chat_id=c.id) turn_count FROM chats c WHERE id=?",
                (chat_id,),
            ).fetchone()
            generation = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (chat_id,)).fetchone()
            if row is None or (int(generation[0]) if generation else 0) != revision:
                return None
            previous = self._summary_from_row(row)
            if any(previous.get(field) != value for field, value in scope.items()):
                return None
            changed = previous.copy()
            effective_at = self._merge_observed_native_title(
                changed, previous, provider, title, observed_at, explicit=explicit, app_manual=app_manual,
            )
            if changed == previous:
                if effective_at:
                    changed["native_title_observed_at"] = effective_at
                return changed
            self._upsert_chat_conn(conn, changed)
            return changed

    def _merge_observed_native_title(self, chat, previous, provider, title, observed_at, **options):
        # Callers hold a SQLite write transaction so both title entry points
        # compare against the same process-local observation.
        session_key = {"agy": "agy_session_id", "codex": "codex_thread_id", "kimi": "kimi_session_id"}[provider]
        scope = (chat["id"], provider, chat.get(session_key), chat.get("native_identity"), chat.get("native_account"))
        persisted_at = self._float_or(previous.get("native_title_observed_at"), 0)
        legacy_at = 0
        if not previous.get("native_identity") and previous.get(session_key) == chat.get(session_key):
            legacy_scope = (*scope[:3], previous.get("native_identity"), previous.get("native_account"))
            legacy_at = self._native_title_observations.get(legacy_scope, 0)
        chat["native_title_observed_at"] = max(persisted_at, legacy_at, self._native_title_observations.get(scope, 0))
        merge_native_title(chat, provider, title, observed_at, **options)
        effective_at = chat.pop("native_title_observed_at")
        titles_changed = any(chat.get(field) != previous.get(field) for field in
                             ("native_title_revision", "title_revision"))
        if titles_changed:
            chat["native_title_observed_at"] = effective_at
        elif "native_title_observed_at" in previous:
            chat["native_title_observed_at"] = persisted_at
        if effective_at:
            self._native_title_observations[scope] = effective_at
        return effective_at

    @staticmethod
    def _agy_replay_matches(identity: NativeSessionIdentity, stored: dict, replayed: dict) -> bool:
        observed_answer = stored.get("agy_observed_message_id") or stored.get("provider_message_id")
        return (
            stored.get("terminal_notification_pair") in (None, "", identity.pair_id)
            and stored.get("session_id") in (None, "", identity.session_id)
            and stored.get("agy_session_id") in (None, "", identity.session_id)
            and replayed.get("session_id") == identity.session_id
            and all(stored.get(field) and stored[field] == replayed.get(field)
                    for field in ("agy_native_turn_id", "provider_user_message_id"))
            and bool(observed_answer) and observed_answer == replayed.get("provider_message_id")
            and stored.get("provider_message_id") in (None, "", observed_answer)
        )

    @staticmethod
    def _is_first_agy_handoff_replay(identity, stored, replay):
        prompt = stored.get("agy_handoff_prompt")
        if (identity.provider != "agy" or not prompt or stored.get("request_status") != "unknown"
                or stored.get("terminal_notification_pair") != identity.pair_id
                or stored.get("agy_session_id") != identity.session_id):
            return False
        first = replay[0]
        if (first.get("session_id") != identity.session_id or first.get("question") != prompt
                or not first.get("provider_message_id")):
            return False
        for saved_field, native_field in (
            ("agy_native_turn_id", "agy_native_turn_id"),
            ("provider_user_message_id", "provider_user_message_id"),
            ("agy_observed_message_id", "provider_message_id"),
        ):
            if stored.get(saved_field) and stored[saved_field] != first.get(native_field):
                return False
        return sum(turn.get("question") == prompt for turn in replay) == 1

    def _merge_recovered_agy_step(self, conn, owner, session_id, step):
        for entry in conn.execute("SELECT payload_json FROM execution_steps WHERE chat_id=? AND turn_idx=?",
                                  (owner, step["turn_idx"])):
            saved = self._json_dict(entry[0])
            if saved.get("step_id") == step["step_id"]:
                return
            if saved.get("provider") == "agy" and saved.get("thread_id") == session_id:
                if (step.get("item_id") and saved.get("item_id") == step["item_id"]) or (
                    step.get("raw_kind") == "thinking" and saved.get("display_kind") in {"thinking", "reasoning"}
                ):
                    return
        self._append_execution_step_on_conn(conn, owner, step)

    @staticmethod
    def _can_recover_terminal_final(identity: NativeSessionIdentity, stored: dict, replayed: dict, *,
                                    agy_handoff: bool = False) -> bool:
        # Managed admission persisted the owner before the original process
        # exited. A matching, settled provider turn may finish that same row.
        if identity.provider == "agy":
            return (
                (stored.get("request_status") == "unknown"
                 or (stored.get("request_status") == "pending" and stored.get("agy_queue_claimed")))
                and (agy_handoff or ChatStore._agy_replay_matches(identity, stored, replayed))
                and replayed.get("native_final_explicit") is True
                and replayed.get("request_status") == "done"
                and bool(str(replayed.get("answer_md") or "").strip())
            )
        keys = {"codex": ("codex_thread_id", "codex_turn_id"),
                "kimi": ("kimi_session_id", "kimi_turn_id")}.get(identity.provider)
        if not keys or stored.get("terminal_notification_pair") != identity.pair_id:
            return False
        session_key, turn_key = keys
        status = stored.get("request_status")
        # Older Codex builds could settle a Plan turn before storing its public answer.
        missing_codex_final = (
            identity.provider == "codex" and status == "done"
            and stored.get("terminal_final_pending") is True
            and not stored.get("provider_message_id")
            and str(stored.get("answer_md") or "").strip() in {"", "正在请求..."}
        )
        return ((missing_codex_final or status in {"pending", "running", "failed", "unknown"})
                and stored.get(session_key) == replayed.get(session_key) == identity.session_id
                and bool(stored.get(turn_key)) and stored[turn_key] == replayed.get(turn_key)
                and replayed.get("native_final_explicit") is True and replayed.get("request_status") == "done"
                and bool(str(replayed.get("answer_md") or "").strip())
                and bool(replayed.get("provider_message_id"))
                and stored.get("provider_message_id") in (None, "", replayed["provider_message_id"]))

    def _is_new_native_final(self, turn: dict, baseline: float, observed_until: float) -> bool:
        # Provider turn times distinguish new replies from delayed indexing.
        # Session mtime, list position and the time of discovery cannot do that.
        started_at = self._float_or(turn.get("created_at"), 0)
        finished_at = self._float_or(turn.get("answer_at"), 0)
        return (turn.get("native_final_explicit") is True and turn.get("request_status") == "done"
                and all(isinstance(turn.get(key), str) and turn[key].strip()
                        for key in ("turn_id", "provider_message_id", "answer_md"))
                and baseline < started_at <= finished_at <= observed_until)

    @staticmethod
    def _native_turn_matches(stored: dict, replayed: dict) -> bool:
        for key in ("provider_user_message_id", "provider_message_id", "kimi_prompt_id",
                    "codex_turn_id", "kimi_turn_id", "agy_native_turn_id"):
            if stored.get(key) and replayed.get(key) and stored[key] != replayed[key]:
                return False
        provider_turn = {"codex": "codex_turn_id", "kimi": "kimi_turn_id",
                         "agy": "agy_native_turn_id"}.get(replayed.get("provider"))
        if (provider_turn and stored.get(provider_turn) and stored[provider_turn] == replayed.get(provider_turn)
                and stored.get("provider_message_id")
                and stored["provider_message_id"] == replayed.get("provider_message_id")):
            return True
        if replayed.get("question", "") not in (stored.get("question", ""), stored.get("kimi_prompt_question"),
                                                 stored.get("agy_handoff_prompt")):
            return False
        old_answer = str(stored.get("answer_md") or "")
        new_answer = str(replayed.get("answer_md") or "")
        return old_answer == new_answer or (bool(new_answer) and old_answer.startswith(new_answer))

    def load_chat_summary(self, chat_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM turns WHERE chat_id=c.id) turn_count FROM chats c WHERE id=?",
                (chat_id,),
            ).fetchone()
            return self._summary_from_row(row) if row else None

    def is_terminal_history_chat(self, chat_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                f"SELECT 1 FROM chats c WHERE c.id=? AND NOT ({MC_OWNED_HISTORY})",
                (chat_id,),
            ).fetchone() is not None

    def list_chat_summaries(self, *, mc_owned_only: bool = False) -> list[dict[str, Any]]:
        ownership = f"WHERE {MC_OWNED_HISTORY}" if mc_owned_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*, COUNT(t.turn_index) AS turn_count
                FROM chats c
                LEFT JOIN turns t ON t.chat_id = c.id
                {ownership}
                GROUP BY c.id
                ORDER BY c.pinned DESC, c.updated_at DESC, c.created_at DESC
                """
            ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def list_chat_summaries_page(self, *, limit: int = 30, offset: int = 0,
                                 query: str = "", pinned: bool = False, mc_owned_only: bool = False,
                                 exclude_chat_id: str = "") -> tuple[list[dict], bool]:
        size = max(1, min(30, int(limit)))
        ownership = f"AND {MC_OWNED_HISTORY} " if mc_owned_only else ""
        with self._connect() as conn:
            rows = conn.execute("SELECT c.*,(SELECT COUNT(*) FROM turns t WHERE t.chat_id=c.id) turn_count "
                "FROM chats c WHERE (?='' OR instr(unicode_casefold(c.title),unicode_casefold(?))>0) AND (?=0 OR c.pinned=1) "
                f"{ownership}AND c.id<>? ORDER BY c.updated_at DESC,c.id DESC LIMIT ? OFFSET ?",
                (query, query, int(pinned), exclude_chat_id, size + 1, max(0, int(offset)))).fetchall()
        return [self._summary_from_row(row) for row in rows[:size]], len(rows) > size

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

    def delete_chat(self, chat_id: str, *, native_id: str = "", native_deleted: bool = False) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            if native_id:
                disposition = "deleted" if native_deleted else "hidden"
                conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?, '1')",
                             (f"{disposition}-native-history:{native_id}",))
                if native_deleted:
                    conn.execute("DELETE FROM meta WHERE key=?", (f"hidden-native-history:{native_id}",))
            self._commit_chat_delete_conn(conn, normalized)
            conn.execute("DELETE FROM canonical_messages WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM canonical_turns WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM v2_chat_state WHERE chat_id=?", (normalized,))
            conn.execute("DELETE FROM execution_steps WHERE chat_id = ?", (normalized,))
            conn.execute("DELETE FROM turns WHERE chat_id = ?", (normalized,))
            conn.execute("DELETE FROM chats WHERE id = ?", (normalized,))

    def delete_chats(self, chat_ids: list[str], *, native_ids: dict[str, str] | None = None) -> None:
        normalized_ids = [str(chat_id or "").strip() for chat_id in chat_ids or []]
        normalized_ids = [chat_id for chat_id in normalized_ids if chat_id]
        if not normalized_ids:
            return
        with self._connect() as conn:
            for chat_id in normalized_ids:
                if native_id := (native_ids or {}).get(chat_id):
                    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?, '1')",
                                 (f"hidden-native-history:{native_id}",))
                self._commit_chat_delete_conn(conn, chat_id)
            conn.executemany("DELETE FROM canonical_messages WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM canonical_turns WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM v2_chat_state WHERE chat_id=?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM execution_steps WHERE chat_id = ?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM turns WHERE chat_id = ?", [(chat_id,) for chat_id in normalized_ids])
            conn.executemany("DELETE FROM chats WHERE id = ?", [(chat_id,) for chat_id in normalized_ids])

    def _commit_chat_delete_conn(self, conn: sqlite3.Connection, chat_id: str) -> None:
        conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?, '1')", (f"deleted-chat:{chat_id}",))
        record = conn.execute("SELECT metadata_json FROM chats WHERE id=?", (chat_id,)).fetchone()
        if record is None:
            return
        native = self._metadata_from_row(record)
        if native.get("native_identity") and native.get("native_account"):
            conn.execute(
                "INSERT OR IGNORE INTO native_history_owners(native_chat_id,chat_id,account) VALUES(?,?,?)",
                (native["native_identity"], chat_id, native["native_account"]),
            )
        row = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (chat_id,)).fetchone()
        revision = int(row[0]) + 1 if row else 1
        pairs = conn.execute("SELECT DISTINCT pair_id FROM notification_devices UNION SELECT pair_id FROM notification_feed_devices UNION SELECT pair_id FROM v2_feed_state WHERE domain='__pair__'").fetchall()
        for pair in pairs:
            self._insert_clear_fact_conn(conn, pair[0], "events", f"delete-{chat_id}", chat_id,
                revision, "deleted", kind="chat_deleted")

    def _turn_owner_conn(self, conn, chat_id, scope, revision):
        chat = conn.execute(
            "SELECT c.model,c.metadata_json,COALESCE(s.revision,1) AS revision "
            "FROM chats c LEFT JOIN v2_chat_state s ON s.chat_id=c.id "
            "WHERE c.id=? AND NOT EXISTS (SELECT 1 FROM meta WHERE key=?)",
            (chat_id, f"deleted-chat:{chat_id}"),
        ).fetchone()
        if chat is None or chat["revision"] != revision:
            return None
        saved_scope = dict(self._metadata_from_row(chat), model=chat["model"])
        return saved_scope if all(saved_scope.get(key) == value for key, value in scope.items()) else None

    def claim_native_tool_turn(self, chat_id, index, scope, revision, session_key, session_id, queued, pending):
        """Commit the exact FIFO entry before starting a native request."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = self._turn_owner_conn(conn, chat_id, scope, revision)
            if owner is None or (owner.get(session_key) or "") != session_id:
                return False
            prefix = queued["model"].partition("/")[0] + "/%"
            if conn.execute(
                    "SELECT 1 FROM turns WHERE chat_id=? AND turn_index<? "
                    "AND json_extract(payload_json,'$.model') LIKE ? "
                    "AND COALESCE(json_extract(payload_json,'$.local_command'),'')='' "
                    "AND json_extract(payload_json,'$.request_status') IN ('queued','pending','running','unknown')",
                    (chat_id, index, prefix),
            ).fetchone():
                return False
            row = conn.execute("SELECT payload_json FROM turns WHERE chat_id=? AND turn_index=?",
                               (chat_id, index)).fetchone()
            if (row is None or queued.get("request_status") != "queued"
                    or self._json_dict(row["payload_json"]) != self._json_dict(json.dumps(queued))):
                return False
            self._write_turn_conn(conn, chat_id, index, pending)
            return True

    def claim_agy_queued_turn(self, chat_id, index, scope, revision, queued, pending):
        """Claim a persisted AGY prompt before giving it to ACP."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = self._turn_owner_conn(conn, chat_id, scope, revision)
            if owner is None or owner.get("native_unresolved_turn"):
                return False
            for row in conn.execute(
                "SELECT turn_index,payload_json FROM turns WHERE chat_id=? AND turn_index<=? ORDER BY turn_index",
                (chat_id, index),
            ):
                saved = self._json_dict(row["payload_json"])
                if row["turn_index"] == index:
                    if saved != queued or saved.get("request_status") != "queued":
                        return False
                    self._write_turn_conn(conn, chat_id, index, pending)
                    return True
                if (saved.get("model") == AGY_MODEL_ID
                        and saved.get("request_status") in {"pending", "running", "unknown", "queued"}):
                    return False
            return False

    def commit_agy_terminal(self, chat_id, index, scope, revision, turn, terminal):
        """Commit the original accepted turn before reporting a known AGY result."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._turn_owner_conn(conn, chat_id, scope, revision) is None:
                return False
            row = conn.execute("SELECT payload_json FROM turns WHERE chat_id=? AND turn_index=?",
                               (chat_id, index)).fetchone()
            existing = self._json_dict(row["payload_json"]) if row is not None else {}
            token = turn.get("_provider_request_id")
            if not token or (row is not None and existing.get("_provider_request_id") != token):
                return False
            if row is None and conn.execute(
                "SELECT COALESCE(MAX(turn_index)+1,0) FROM turns WHERE chat_id=?", (chat_id,),
            ).fetchone()[0] != index:
                return False
            if existing.get("request_status") not in (None, "", "pending"):
                return all(existing.get(key) == value for key, value in terminal.items())
            settled = dict(turn, **existing)
            settled.update(terminal)
            self._write_turn_conn(conn, chat_id, index, settled)
            return True

    def _write_turn_conn(self, conn, chat_id, index, turn):
        conn.execute(
            "INSERT INTO turns(chat_id,turn_index,payload_json) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,turn_index) DO UPDATE SET payload_json=excluded.payload_json",
            (chat_id, index, json.dumps(turn, ensure_ascii=False)),
        )
        self._backfill_canonical_turns_conn(conn, chat_id, [turn], index)

    @staticmethod
    def _keep_settled_turn(old, new):
        if (isinstance(new, dict) and old.get("tool_queue_claimed")
                and new.get("request_status") == "queued"
                and all(old.get(key) == new.get(key) for key in
                        ("_provider_request_id", "model", "request_question", "created_at"))):
            return True
        if not isinstance(new, dict) or old.get("_provider_request_id") != new.get("_provider_request_id"):
            return False
        if old.get("agy_queue_claimed") and new.get("request_status") == "queued":
            return True
        return (old.get("agy_native_terminal_reason") in ("end_turn", "cancelled")
                and new.get("request_status") == "pending")

    def replace_turns(self, chat_id: str, turns: list[dict[str, Any]]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._replace_turns_conn(conn, normalized, turns)

    def _replace_turns_conn(self, conn, normalized, turns):
        incoming = [turn for turn in (turns or []) if isinstance(turn, dict)]
        existing = {int(row["turn_index"]): self._json_dict(row["payload_json"])
                    for row in conn.execute("SELECT turn_index,payload_json FROM turns WHERE chat_id=?", (normalized,))}
        for index, turn in enumerate(incoming):
            old = existing.get(index)
            if old is not None and self._keep_settled_turn(old, turn):
                incoming[index] = old
            elif old is not None and not self._same_canonical_turn(old, turn):
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

    def replace_turns_from(self, chat_id: str, turns: list[dict[str, Any]], *,
                           start_index: int = 0, attachment_scope: tuple = ()) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        start = max(0, self._int_or(start_index, 0))
        suffix = list(turns or [])
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if attachment_scope:
                updated = conn.execute(
                    "UPDATE remote_attachment_sends SET state='accepted' "
                    "WHERE pair_id=? AND device_id=? AND chat_id=? AND send_id=? AND revision=? "
                    "AND state='starting' AND revision=MAX(1,COALESCE("
                    "(SELECT revision FROM v2_chat_state WHERE chat_id=?),1))",
                    (*attachment_scope, normalized),
                )
                if updated.rowcount != 1:
                    raise ValueError("attachment_send_expired")
            existing = {int(row["turn_index"]): self._json_dict(row["payload_json"])
                        for row in conn.execute("SELECT turn_index,payload_json FROM turns WHERE chat_id=? AND turn_index>=?", (normalized,start))}
            for offset, turn in enumerate(suffix):
                index = start + offset
                old = existing.get(index)
                if old is not None and self._keep_settled_turn(old, turn):
                    suffix[offset] = old
                elif isinstance(turn, dict) and old is not None and not self._same_canonical_turn(old, turn):
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
        if str(old.get("question") or "") != str(new.get("question") or ""):
            return False
        if (old.get("request_status") == new.get("request_status") == "done"
                and str(old.get("answer_md") or "") != str(new.get("answer_md") or "")):
            return False
        # Missing provider identity is commonly filled after the first save.
        # Only conflicting established identities denote a different turn.
        return all(not old.get(key) or not new.get(key) or str(old[key]) == str(new[key])
                   for key in ("model", "provider", "provider_kind", "provider_user_message_id",
                               "session_id", "thread_id", "turn_id", "provider_message_id"))

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
                    "INSERT INTO canonical_messages(message_id,turn_id,chat_id,role,provider_kind,provider_message_id,legacy_turn_index) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,legacy_turn_index,role) DO UPDATE SET "
                    "provider_message_id=excluded.provider_message_id "
                    "WHERE canonical_messages.provider_message_id='' AND excluded.provider_message_id<>''",
                    (f"message-{uuid.uuid4().hex}", str(canonical["turn_id"]), chat_id, role,
                     provider, str(payload.get(provider_key) or ""), start + offset),
                )
            if str(payload.get("request_status") or "") == "done" and str(payload.get("answer_md") or "").strip():
                conn.execute("INSERT OR IGNORE INTO read_generations(chat_id,generation) VALUES(?,?)", (chat_id, uuid.uuid4().hex))
                conn.execute("INSERT OR IGNORE INTO readable_answers(message_id,chat_id,generation) SELECT m.message_id,m.chat_id,g.generation FROM canonical_messages m JOIN read_generations g ON g.chat_id=m.chat_id WHERE m.chat_id=? AND m.legacy_turn_index=? AND m.role='assistant'", (chat_id, start + offset))

        for pair in conn.execute("SELECT DISTINCT pair_id FROM notification_devices UNION SELECT pair_id FROM notification_feed_devices").fetchall():
            self._reconcile_notification_finals_conn(conn, pair[0], chat_id)

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
        turns = [payload for payload in (self._json_dict(row["payload_json"]) for row in rows) if payload]
        return self._restore_answer_times(chat_id, turns)

    def _restore_answer_times(self, chat_id: str, turns: list[dict], start: int = 0) -> list[dict]:
        missing = [start + index for index, turn in enumerate(turns)
                   if turn.get("answer_at") is None and str(turn.get("answer_md") or "").strip()]
        if not missing:
            return turns
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM execution_steps WHERE chat_id=? AND turn_idx>=? AND turn_idx<=? "
                "AND COALESCE(json_extract(payload_json,'$.raw_kind'),json_extract(payload_json,'$.kind'),json_extract(payload_json,'$.display_kind'))='final'",
                (str(chat_id or "").strip(), min(missing), max(missing)),
            ).fetchall()
        steps = [self._json_dict(row["payload_json"]) for row in rows]
        for index, turn in enumerate(turns):
            timestamp = canonical_answer_timestamp(turn, steps, start + index)
            if timestamp is not None:
                turn["answer_at"] = timestamp
        return turns

    def find_turn_by_client_send_id(self, chat_id: str, client_send_id: str) -> tuple[int, dict] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT turn_index, payload_json FROM turns
                WHERE chat_id=? AND json_extract(payload_json, '$.client_send_id')=?
                ORDER BY turn_index DESC LIMIT 1""",
                (self.normalize_chat_id(chat_id), client_send_id),
            ).fetchone()
        if row is not None:
            return int(row["turn_index"]), self._json_dict(row["payload_json"])
        return None

    def find_answer_by_identity(self, chat_id: str, identity: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT json_extract(payload_json, '$.answer_md') AS answer FROM turns
                WHERE chat_id=? AND json_extract(payload_json, '$.answer_identity')=?
                AND length(json_extract(payload_json, '$.answer_md')) > 0
                ORDER BY turn_index DESC LIMIT 1""",
                (self.normalize_chat_id(chat_id), identity),
            ).fetchone()
        return str(row["answer"]) if row is not None else ""

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
        turns = [payload for payload in (self._json_dict(row["payload_json"]) for row in rows) if payload]
        return total, self._restore_answer_times(normalized, turns, start)

    @staticmethod
    def _execution_visible(payload: dict) -> int:
        ready = payload.get("projection_ready")
        if isinstance(ready, (int, float)) and ready == 0:
            return 0
        return int(should_show_execution_step(payload))

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
