"""Durable facts, notification feeds, read cursors and publication outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from chat_store_execution import _DISCLOSABLE_EXECUTION_FACT
from chat_store_schema import MAX_INT64
from typing import Any


class ChatStoreNotificationsMixin:
    def commit_durable_fact(self, *, pair_id: str, domain: str, envelope: dict[str, Any],
                            execution: bool = False, publication_deadline: float | None = None) -> dict[str, Any]:
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
            def check_publication_deadline():
                if publication_deadline is not None and self._clock.monotonic() >= publication_deadline:
                    conn.rollback()
                    raise TimeoutError("file_preparation_timeout")
            check_publication_deadline()
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
                    check_publication_deadline()
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
            check_publication_deadline()
            if publication_deadline is not None:
                # The SQLite/OS commit itself cannot be forcibly interrupted.
                conn.commit()
            return normalized

    def commit_attention_notification(self, *, pair_id: str, chat_id: str, kind: str,
                                      operation_id: str, text: str) -> dict[str, Any]:
        """An actionable fact has event identity, never an answer read sequence."""
        if kind not in {"action_required", "failed"} or not str(operation_id).strip():
            raise ValueError("INVALID_ATTENTION_NOTIFICATION")
        owner = self.normalize_chat_id(chat_id)
        state = self.get_chat_read_state(owner, pair_id=pair_id)
        with self._connect() as conn:
            row = conn.execute("SELECT title FROM chats WHERE id=?", (owner,)).fetchone()
        if row is None:
            raise ValueError("CHAT_NOT_FOUND")
        event_id = "attention:" + hashlib.sha256(json.dumps(
            [pair_id, owner, state["generation"], kind, operation_id], separators=(",", ":")).encode()).hexdigest()
        # Retries return the immutable original, including its original title.
        with self._connect() as conn:
            existing = conn.execute("SELECT envelope_json FROM durable_facts WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            return json.loads(existing[0])
        return self.commit_durable_fact(pair_id=pair_id, domain="events", envelope={
            "event_id": event_id, "kind": kind, "chat_id": owner, "origin_client": "mc",
            "body": {"generation": state["generation"], "chat_title": str(row[0] or "Chat"),
                     "text": str(text).strip(), "operation_id": str(operation_id)}})

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
            authority = int(conn.execute("SELECT revision FROM authority_cache_revision WHERE singleton=1").fetchone()[0])
        return {
            "notification_feed_id": self.get_meta("notification_feed_id"),
            "authority_high_sync_sequence": authority,
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
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._commit_message_notification_fact_conn(conn, pair_id=pair_id, domain=domain, chat_id=chat_id, message_id=message_id, notification_kind=notification_kind, text=text, chat_title=chat_title, origin_client=origin_client)

    def _commit_message_notification_fact_conn(
        self, conn: sqlite3.Connection,
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
        normalized_origin = str(origin_client or "mc").strip().lower()
        if not normalized_origin or "\x00" in normalized_origin:
            raise ValueError("INVALID_ORIGIN_CLIENT")
        event_id = f"notification:{normalized_pair}:{normalized_message}:{normalized_kind}"
        existing = conn.execute(
            "SELECT envelope_json,pair_id,domain FROM durable_facts WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing:
            original = json.loads(existing["envelope_json"])
            # A rename changes current presentation, never a committed fact.
            # Still validate every immutable owner and canonical message field.
            immutable = {
                "protocol_version": 2, "event_id": event_id,
                "kind": normalized_kind, "chat_id": owner,
                "turn_id": str(row["turn_id"]), "domain": normalized_domain,
                "sequence_domain": normalized_domain,
                "origin_client": normalized_origin,
            }
            original_body = original.get("body", {})
            if (existing["pair_id"] != normalized_pair
                    or existing["domain"] != normalized_domain
                    or any(original.get(key) != value for key, value in immutable.items())
                    or original_body.get("message_id") != normalized_message
                    or original_body.get("text") != canonical_text):
                self._quarantine_conn(conn, "EVENT_ID_CONFLICT", immutable, event_id)
                conn.commit()
                raise ValueError("EVENT_ID_CONFLICT")
            return original
        canonical_title = str(row["title"] or "").strip()
        if not canonical_text.strip() or not canonical_title:
            raise ValueError("INCOMPLETE_CANONICAL_NOTIFICATION")
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
        if expected_role == "assistant":
            readable = conn.execute("SELECT a.answer_seq,a.generation FROM readable_answers a JOIN read_generations g ON g.chat_id=a.chat_id AND g.generation=a.generation WHERE a.message_id=?", (normalized_message,)).fetchone()
            if readable is None or turn.get("request_status") != "done":
                raise ValueError("INCOMPLETE_CANONICAL_NOTIFICATION")
            normalized["body"].update({"answer_seq": int(readable[0]), "generation": readable[1]})
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

    def notification_sync(
        self, *, pair_id: str, device_id: str, baseline: int, incarnation: str,
        secret: str, cursor: str = "", limit: int = 100, read_only: bool = False, notification_feed_id: str = "",
    ) -> dict[str, Any]:
        """Materialize authority at business H; delivery is never a read receipt.

        Rows survive process death and delayed outbox publication. A continuation is
        authenticated and bound to the original installation and stream incarnation.
        Only an explicit completion receipt advances the installation checkpoint.
        """
        from remote_nats_protocol import compact_notification, NOTIFY_KINDS
        pair, device = str(pair_id).strip(), str(device_id).strip()
        if not pair or not device or not incarnation or isinstance(baseline, bool) or not 0 <= int(baseline) <= MAX_INT64:
            raise ValueError("INVALID_NOTIFICATION_SCOPE")
        feed = str(notification_feed_id)
        if feed and feed != self.get_meta("notification_feed_id"):
            raise ValueError("STALE_NOTIFICATION_FEED")
        page_limit = max(1, min(100, int(limit)))
        with self._connect() as conn:
            if "authority_high" not in {row[1] for row in conn.execute("PRAGMA table_info(notification_snapshots)")}:
                conn.execute("ALTER TABLE notification_snapshots ADD COLUMN authority_high INTEGER NOT NULL DEFAULT 0")
            if "processed_floor" not in {row[1] for row in conn.execute("PRAGMA table_info(notification_snapshots)")}:
                conn.execute("ALTER TABLE notification_snapshots ADD COLUMN processed_floor INTEGER NOT NULL DEFAULT 0")
            if "read_only" not in {row[1] for row in conn.execute("PRAGMA table_info(notification_snapshots)")}:
                conn.execute("ALTER TABLE notification_snapshots ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0")
            # Expire abandoned and completed snapshots equally. A reset never
            # advances the installation checkpoint, so all eligible facts remain.
            expired = "SELECT snapshot_id FROM notification_snapshots WHERE created_at<? OR snapshot_id NOT IN (SELECT snapshot_id FROM notification_snapshots ORDER BY created_at DESC,rowid DESC LIMIT 32)"
            conn.execute("DELETE FROM notification_snapshot_rows WHERE snapshot_id IN (" + expired + ")", (self._clock.time() - 86400,))
            conn.execute("DELETE FROM notification_snapshots WHERE snapshot_id IN (" + expired + ")", (self._clock.time() - 86400,))
            if feed:
                # The authoritative hello baseline replaces unscoped legacy progress.
                # Never infer a reset from a smaller sequence on the same feed.
                conn.execute("INSERT OR IGNORE INTO notification_feed_devices(pair_id,device_id,feed_id,baseline) VALUES(?,?,?,?)", (pair, device, feed, int(baseline)))
                installation = conn.execute("SELECT * FROM notification_feed_devices WHERE pair_id=? AND device_id=? AND feed_id=?", (pair, device, feed)).fetchone()
            else:
                conn.execute("INSERT OR IGNORE INTO notification_devices(pair_id,device_id,baseline) VALUES(?,?,?)", (pair, device, int(baseline)))
                installation = conn.execute("SELECT * FROM notification_devices WHERE pair_id=? AND device_id=?", (pair, device)).fetchone()
            original_baseline = int(installation["baseline"])
            processed_floor = max(original_baseline, int(installation["processed_sequence"]))
            conn.execute("INSERT OR IGNORE INTO read_generations(chat_id,generation) SELECT id,lower(hex(randomblob(16))) FROM chats")
        if not read_only and not cursor:
            self.reconcile_notification_finals(pair)
        if cursor:
            position = self._decode_execution_cursor(cursor, secret)
            snapshot_id, offset = str(position.get("snapshot_id", "")), position.get("offset")
        else:
            # A WAL read transaction captures H and every authority row without
            # retaining a database write lock while scanning the full inventory.
            with self._connect() as source:
                source.execute("BEGIN")
                high_water = int(source.execute("SELECT COALESCE(MAX(sync_sequence),0) FROM durable_facts WHERE pair_id=?", (pair,)).fetchone()[0])
                authority_high = int(source.execute("SELECT revision FROM authority_cache_revision WHERE singleton=1").fetchone()[0])
                reusable = source.execute("SELECT * FROM notification_snapshots WHERE pair_id=? AND device_id=? AND incarnation=? AND feed_id=? AND ((?=1 AND authority_high=?) OR (?=0 AND high_water=?)) AND read_only=? AND row_count>=0 AND (?=1 OR (completed=0 AND processed_floor=?)) ORDER BY created_at DESC LIMIT 1", (pair, device, incarnation, feed, int(read_only), authority_high, int(read_only), high_water, int(read_only), int(read_only), processed_floor)).fetchone()
                snapshot_id, offset = (str(reusable["snapshot_id"]) if reusable else uuid.uuid4().hex), 0
                if reusable is None:
                    with self._connect() as writer:
                        writer.execute("INSERT INTO notification_snapshots(snapshot_id,pair_id,device_id,high_water,incarnation,row_count,read_only,processed_floor,authority_high,feed_id) VALUES(?,?,?,?,?,-1,?,?,?,?)", (snapshot_id, pair, device, high_water, incarnation, int(read_only), processed_floor, authority_high, feed))
                    def records():
                        placeholders = ",".join("?" for _ in NOTIFY_KINDS)
                        if not read_only:
                            for row in source.execute(f"SELECT envelope_json FROM durable_facts WHERE pair_id=? AND sync_sequence>? AND sync_sequence<=? AND kind IN ({placeholders}) ORDER BY sync_sequence", (pair, processed_floor, high_water, *sorted(NOTIFY_KINDS))):
                                fact = json.loads(row[0])
                                current = source.execute("SELECT generation FROM read_generations WHERE chat_id=?", (fact["chat_id"],)).fetchone()
                                exists = source.execute("SELECT 1 FROM chats WHERE id=?", (fact["chat_id"],)).fetchone()
                                if not exists or (fact["kind"] in {"assistant_final", "action_required", "failed"} and (current is None or fact["body"].get("generation") != current[0])):
                                    yield {"type": "notification_tombstone", "event_id": fact["event_id"], "chat_id": fact["chat_id"], "sync_sequence": fact["sync_sequence"]}
                                else:
                                    yield compact_notification(fact, pair_id=pair, notification_feed_id=feed)
                        for row in source.execute("SELECT id FROM chats ORDER BY id"):
                            yield {"type": "read_state", **self._read_state_conn(source, row[0], pair)}
                    batch, count = [], 0
                    for record in records():
                        batch.append((snapshot_id, count, json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
                        count += 1
                        if len(batch) == 100:
                            with self._connect() as writer:
                                writer.executemany("INSERT INTO notification_snapshot_rows VALUES(?,?,?)", batch)
                            batch.clear()
                    with self._connect() as writer:
                        writer.executemany("INSERT INTO notification_snapshot_rows VALUES(?,?,?)", batch)
                        writer.execute("UPDATE notification_snapshots SET row_count=? WHERE snapshot_id=?", (count, snapshot_id))
        with self._connect() as conn:
            snapshot = conn.execute("SELECT * FROM notification_snapshots WHERE snapshot_id=? AND pair_id=? AND device_id=?", (snapshot_id, pair, device)).fetchone()
            if (snapshot is None or snapshot["incarnation"] != incarnation or snapshot["feed_id"] != feed or isinstance(offset, bool)
                    or not isinstance(offset, int) or not 0 <= offset <= snapshot["row_count"]):
                raise ValueError("INVALID_NOTIFICATION_CURSOR")
            result = {"snapshot_id": snapshot_id, "high_water": int(snapshot["high_water"]), "incarnation": incarnation, "baseline": original_baseline, "records": []}
            if feed:
                result["notification_feed_id"] = feed
            # Reserve continuation/envelope overhead; UTF-8 byte accounting is the
            # actual serialized payload, not a character estimate.
            budget = 60 * 1024
            size = 0
            for row in conn.execute("SELECT payload FROM notification_snapshot_rows WHERE snapshot_id=? AND row_index>=? ORDER BY row_index LIMIT ?", (snapshot_id, offset, page_limit)):
                encoded = str(row[0]).encode("utf-8")
                if size + len(encoded) > budget:
                    break
                result["records"].append(json.loads(row[0]))
                size += len(encoded)
            if not result["records"] and offset < int(snapshot["row_count"]):
                raise ValueError("NOTIFICATION_ROW_BUDGET_EXCEEDED")
            end = offset + len(result["records"])
            done = end == int(snapshot["row_count"])
            token = self._execution_cursor_token({"snapshot_id": snapshot_id, "offset": end}, secret)
            result.update({"done": done, "cursor": "" if done else token, "completion_token": token if done else ""})
            conn.execute("UPDATE notification_snapshots SET delivered_count=MAX(delivered_count,?) WHERE snapshot_id=?", (end, snapshot_id))
            if read_only and done:
                conn.execute("UPDATE notification_snapshots SET completed=1 WHERE snapshot_id=?", (snapshot_id,))
            return result

    def complete_notification_sync(self, *, pair_id: str, device_id: str, incarnation: str, completion_token: str, secret: str, notification_feed_id: str = "") -> int:
        if notification_feed_id and notification_feed_id != self.get_meta("notification_feed_id"):
            raise ValueError("STALE_NOTIFICATION_FEED")
        receipt = self._decode_execution_cursor(completion_token, secret)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM notification_snapshots WHERE snapshot_id=? AND pair_id=? AND device_id=? AND incarnation=?", (receipt.get("snapshot_id"), pair_id, device_id, incarnation)).fetchone()
            if row is None or row["feed_id"] != notification_feed_id or receipt.get("offset") != row["row_count"] or row["delivered_count"] != row["row_count"]:
                raise ValueError("INCOMPLETE_NOTIFICATION_SNAPSHOT")
            if notification_feed_id:
                conn.execute("UPDATE notification_feed_devices SET processed_sequence=MAX(processed_sequence,?) WHERE pair_id=? AND device_id=? AND feed_id=?", (row["high_water"], pair_id, device_id, notification_feed_id))
            else:
                conn.execute("UPDATE notification_devices SET processed_sequence=MAX(processed_sequence,?) WHERE pair_id=? AND device_id=?", (row["high_water"], pair_id, device_id))
            conn.execute("UPDATE notification_snapshots SET completed=1 WHERE snapshot_id=?", (row["snapshot_id"],))
            return int(row["high_water"])

    def reconcile_notification_finals(self, pair_id: str) -> None:
        while True:
            with self._connect() as conn:
                count = self._reconcile_notification_finals_conn(conn, pair_id)
            if count < 100:
                return

    def _reconcile_notification_finals_conn(self, conn: sqlite3.Connection, pair_id: str, chat_id: str | None = None) -> int:
        sql = "SELECT a.message_id,a.chat_id,t.payload_json,c.title FROM readable_answers a JOIN read_generations g ON g.chat_id=a.chat_id AND g.generation=a.generation JOIN canonical_messages m ON m.message_id=a.message_id JOIN turns t ON t.chat_id=m.chat_id AND t.turn_index=m.legacy_turn_index JOIN chats c ON c.id=a.chat_id WHERE NOT EXISTS (SELECT 1 FROM durable_facts f WHERE f.event_id='notification:' || ? || ':' || a.message_id || ':assistant_final')"
        args = (pair_id,)
        if chat_id is not None:
            sql = sql.replace("WHERE NOT EXISTS", "WHERE a.chat_id=? AND NOT EXISTS", 1)
            args = (chat_id, pair_id)
        candidates = conn.execute(sql + " AND COALESCE(json_extract(t.payload_json,'$.native_history'),0)=0 AND json_extract(t.payload_json,'$.request_status')='done' AND length(trim(json_extract(t.payload_json,'$.answer_md')))>0 AND length(trim(c.title))>0 LIMIT 100", args).fetchall()
        for row in candidates:
            turn = self._json_dict(row["payload_json"])
            if turn.get("request_status") == "done" and str(turn.get("answer_md") or "").strip():
                self._commit_message_notification_fact_conn(conn, pair_id=pair_id, domain="events", chat_id=row["chat_id"], message_id=row["message_id"], notification_kind="assistant_final", text=turn["answer_md"], chat_title=row["title"])

        return len(candidates)

    def content_continuation(self, *, chat_id: str, text: str, secret: str, prefix_bytes: int = 4096) -> tuple[str, str]:
        encoded = text.encode("utf-8")
        if len(encoded) <= prefix_bytes:
            return text, ""
        prefix = encoded[:prefix_bytes].decode("utf-8", errors="ignore")
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO v2_chat_state(chat_id) VALUES(?)", (chat_id,))
            state = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (chat_id,)).fetchone()
            revision = int(state[0]) if state else 1
            conn.execute("DELETE FROM remote_content_pages WHERE expires_at<?", (self._clock.time(),))
            content_id = hashlib.sha256(json.dumps([chat_id, revision, text], ensure_ascii=False).encode()).hexdigest()
            cached = "\x00raw:" + text if text.startswith("\x00") else text
            if len(encoded) > 64 * 1024 * 1024:
                reference = None
                for field in ("question", "answer_md"):
                    row = conn.execute("SELECT turn_index FROM turns WHERE chat_id=? AND json_extract(payload_json,?)=? LIMIT 1", (chat_id, "$." + field, text)).fetchone()
                    if row:
                        reference = {"table": "turns", "key": row[0], "field": field}
                        break
                if reference is None:
                    for field in ("list_text", "detail_text"):
                        row = conn.execute(f"SELECT step_index FROM execution_steps WHERE chat_id=? AND {field}=? LIMIT 1", (chat_id, text)).fetchone()
                        if row:
                            reference = {"table": "execution_steps", "key": row[0], "field": field}
                            break
                if reference is None:
                    raise ValueError("CONTENT_CACHE_BUDGET_EXCEEDED")
                cached = "\x00canonical:" + json.dumps(reference)
            conn.execute("INSERT INTO remote_content_pages VALUES(?,?,?,?,?) ON CONFLICT(content_id) DO UPDATE SET expires_at=excluded.expires_at", (content_id, chat_id, revision, cached, self._clock.time() + 86400))
            # Bound disposable cache to 64 MiB; canonical text is never removed.
            conn.execute("DELETE FROM remote_content_pages WHERE content_id IN (SELECT content_id FROM (SELECT content_id, SUM(length(CAST(content AS BLOB))) OVER (ORDER BY expires_at DESC,content_id) total FROM remote_content_pages) WHERE total>67108864) AND content_id<>?", (content_id,))
        token = self._execution_cursor_token({"content_id": content_id, "chat_id": chat_id,
            "revision": revision, "offset": len(prefix)}, secret)
        return prefix, token

    def read_content_page(self, *, chat_id: str, cursor: str, secret: str) -> dict:
        position = self._decode_execution_cursor(cursor, secret)
        if position.get("chat_id") != chat_id:
            raise ValueError("CONTENT_OWNER_MISMATCH")
        with self._connect() as conn:
            row = conn.execute("SELECT p.*,s.revision current_revision FROM remote_content_pages p JOIN v2_chat_state s ON s.chat_id=p.chat_id WHERE p.content_id=? AND p.chat_id=?", (position.get("content_id"), chat_id)).fetchone()
        if row is None or row["expires_at"] < self._clock.time() or row["revision"] != row["current_revision"]:
            raise ValueError("STALE_CONTENT_CURSOR")
        text, offset = row["content"], position.get("offset")
        if text.startswith("\x00raw:"):
            text = text[len("\x00raw:"):]
        elif text.startswith("\x00canonical:"):
            reference = json.loads(text[len("\x00canonical:"):])
            with self._connect() as conn:
                if reference["table"] == "turns" and reference["field"] in {"question", "answer_md"}:
                    value = conn.execute("SELECT json_extract(payload_json,?) FROM turns WHERE chat_id=? AND turn_index=?", ("$." + reference["field"], chat_id, reference["key"])).fetchone()
                elif reference["table"] == "execution_steps" and reference["field"] in {"list_text", "detail_text"}:
                    value = conn.execute(f"SELECT {reference['field']} FROM execution_steps WHERE chat_id=? AND step_index=?", (chat_id, reference["key"])).fetchone()
                else:
                    raise ValueError("STALE_CONTENT_CURSOR")
            if value is None:
                raise ValueError("STALE_CONTENT_CURSOR")
            text = str(value[0] or "")
            expected = hashlib.sha256(json.dumps([chat_id, row["revision"], text], ensure_ascii=False).encode()).hexdigest()
            if expected != position.get("content_id"):
                raise ValueError("STALE_CONTENT_CURSOR")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(text):
            raise ValueError("INVALID_CONTENT_CURSOR")
        chunk = text[offset:].encode("utf-8")[:16384].decode("utf-8", errors="ignore")
        end = offset + len(chunk)
        return {"chat_id": chat_id, "text": chunk, "complete": end == len(text),
                "cursor": "" if end == len(text) else self._execution_cursor_token({**position, "offset": end}, secret)}

    def _read_state_conn(self, conn, chat_id, pair_id):
        if conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is None:
            raise ValueError("CHAT_NOT_FOUND")
        if conn.execute("SELECT 1 FROM read_generations WHERE chat_id=?", (chat_id,)).fetchone() is None:
            conn.execute("INSERT OR IGNORE INTO read_generations(chat_id,generation) VALUES(?,?)", (chat_id, uuid.uuid4().hex))
        generation = conn.execute("SELECT generation FROM read_generations WHERE chat_id=?", (chat_id,)).fetchone()[0]
        latest = conn.execute("SELECT a.answer_seq,a.message_id FROM readable_answers a JOIN canonical_messages m ON m.message_id=a.message_id WHERE a.chat_id=? AND a.generation=? ORDER BY a.answer_seq DESC LIMIT 1", (chat_id,generation)).fetchone()
        baseline = conn.execute("SELECT COALESCE(MAX(answer_seq),0) FROM readable_answers WHERE chat_id=? AND generation=? AND baseline=1", (chat_id,generation)).fetchone()[0]
        read = conn.execute("SELECT read_seq FROM chat_read_state WHERE pair_id=? AND chat_id=? AND generation=?", (pair_id,chat_id,generation)).fetchone()
        revision = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (chat_id,)).fetchone()
        return {"chat_id": chat_id, "generation": generation, "revision": int(revision[0]) if revision else 1, "latest_readable_seq": int(latest[0]) if latest else 0,
                "latest_readable_message_id": str(latest[1]) if latest else "", "read_seq": max(int(baseline), int(read[0]) if read else 0)}

    def get_chat_read_state(self, chat_id: str, *, pair_id: str = "default") -> dict:
        with self._connect() as conn:
            return self._read_state_conn(conn, self.normalize_chat_id(chat_id), str(pair_id))

    def readable_answer(self, chat_id: str, *, turn_index: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT a.message_id,a.answer_seq,a.generation FROM readable_answers a JOIN canonical_messages m ON m.message_id=a.message_id JOIN read_generations g ON g.chat_id=a.chat_id AND g.generation=a.generation WHERE a.chat_id=? AND m.legacy_turn_index=? AND m.role='assistant'", (chat_id,int(turn_index))).fetchone()
        return dict(row) if row else None

    def readable_answer_for_turn(self, chat_id: str, *, turn_index: int, turn: dict,
                                 generation: str) -> dict | None:
        """Resolve the frozen visible turn and its identity in one snapshot."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.message_id,a.answer_seq,a.generation,t.payload_json "
                "FROM readable_answers a JOIN canonical_messages m ON m.message_id=a.message_id "
                "JOIN read_generations g ON g.chat_id=a.chat_id AND g.generation=a.generation "
                "JOIN turns t ON t.chat_id=m.chat_id AND t.turn_index=m.legacy_turn_index "
                "WHERE a.chat_id=? AND m.legacy_turn_index=? AND m.role='assistant' AND g.generation=?",
                (chat_id, int(turn_index), generation)).fetchone()
            if row is None:
                return None
            persisted = self._json_dict(row["payload_json"])
            if (persisted.get("request_status") != "done" or turn.get("request_status") != "done"
                    or not self._same_canonical_turn(persisted, turn)):
                return None
            return {key: row[key] for key in ("message_id", "answer_seq", "generation")}

    def mark_chat_read(self, *, pair_id: str, chat_id: str, generation: str,
                       message_id: str, answer_seq: int, operation_id: str, domain: str = "events") -> dict:
        owner = self.normalize_chat_id(chat_id)
        if isinstance(answer_seq, bool) or not isinstance(answer_seq, int) or answer_seq <= 0:
            raise ValueError("INVALID_READ_TARGET")
        if not isinstance(pair_id, str) or not pair_id.strip() or not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("INVALID_READ_SCOPE")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read_state_conn(conn, owner, pair_id)
            if generation != state["generation"]:
                raise ValueError("STALE_READ_GENERATION")
            row = conn.execute("SELECT a.answer_seq,t.payload_json FROM readable_answers a JOIN canonical_messages m ON m.message_id=a.message_id JOIN turns t ON t.chat_id=m.chat_id AND t.turn_index=m.legacy_turn_index WHERE a.message_id=? AND a.chat_id=? AND a.generation=?", (message_id,owner,generation)).fetchone()
            if row is None or int(row[0]) != answer_seq or self._json_dict(row[1]).get("request_status") != "done":
                raise ValueError("INVALID_READ_TARGET")
            if answer_seq <= state["read_seq"]:
                return state
            conn.execute("INSERT INTO chat_read_state(pair_id,chat_id,generation,read_seq) VALUES(?,?,?,?) ON CONFLICT(pair_id,chat_id,generation) DO UPDATE SET read_seq=MAX(read_seq,excluded.read_seq)", (pair_id,owner,generation,answer_seq))
            state["read_seq"] = answer_seq
            conn.execute("INSERT OR IGNORE INTO v2_feed_state(pair_id,domain) VALUES(?,'__pair__')", (pair_id,))
            sync = int(conn.execute("SELECT sync_sequence FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'", (pair_id,)).fetchone()[0]) + 1
            revision = conn.execute("SELECT revision FROM v2_chat_state WHERE chat_id=?", (owner,)).fetchone()
            envelope = {"protocol_version": 2, "kind": "chat_read_changed", "event_id": "read-" + uuid.uuid4().hex,
                        "chat_id": owner, "domain": domain, "sequence_domain": domain, "origin_client": "mc",
                        "revision": int(revision[0]) if revision else 1, "sync_sequence": sync,
                        "body": {**state, "message_id": message_id, "operation_id": operation_id}}
            canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            envelope["canonical_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id=? AND domain='__pair__'", (sync,pair_id))
            conn.execute("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,revision,sync_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?)", (envelope["event_id"],envelope["canonical_hash"],envelope["kind"],pair_id,domain,owner,envelope["revision"],sync,canonical))
            conn.execute("INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload) VALUES(?,?,?,?,?,?)", (pair_id,domain,sync,envelope["event_id"],domain,canonical.encode("utf-8")))
            return state

    def _quarantine_conn(self, conn: sqlite3.Connection, reason: str, payload: Any, event_id: str = "") -> None:
        conn.execute("INSERT INTO identity_quarantine(reason,event_id,payload_json) VALUES(?,?,?)", (reason,event_id,json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        conn.execute("DELETE FROM identity_quarantine WHERE id NOT IN (SELECT id FROM identity_quarantine ORDER BY id DESC LIMIT 1000)")

    def quarantine(self, reason: str, payload: Any, event_id: str = "") -> None:
        with self._connect() as conn: self._quarantine_conn(conn, reason, payload, event_id)

    def pending_outbox(self, limit: int = 100, *, pair_id: str | None = None,
                       domain: str | None = None, lane: str | None = None) -> list[dict[str, Any]]:
        where, args = "o.published_at IS NULL", []
        if pair_id is not None:
            where += " AND o.pair_id=?"; args.append(str(pair_id))
        if domain is not None:
            where += " AND o.domain=?"; args.append(str(domain))
        if lane is not None:
            if lane not in {"notification", "other"}:
                raise ValueError("INVALID_OUTBOX_LANE")
            where += (" AND f.kind='assistant_final'" if lane == "notification"
                      else " AND (f.kind IS NULL OR f.kind<>'assistant_final')")
            if lane == "notification":
                where += " AND (o.blocked_reason IS NULL OR o.blocked_reason NOT LIKE 'permanent:%')"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT o.* FROM publication_outbox o LEFT JOIN durable_facts f "
                f"ON f.event_id=o.event_id AND f.pair_id=o.pair_id AND f.domain=o.domain "
                f"WHERE {where} ORDER BY o.sync_sequence LIMIT ?",
                tuple(args + [max(1, int(limit))]),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _advance_publication_progress(conn: sqlite3.Connection, pair: str, domain: str) -> int:
        # Pair-wide sequences have legal gaps in each domain. Only an actual
        # confirmed row below the first pending row can advance this frontier.
        pending = conn.execute(
            "SELECT sync_sequence FROM publication_outbox WHERE pair_id=? AND domain=? "
            "AND published_at IS NULL ORDER BY sync_sequence LIMIT 1", (pair, domain)).fetchone()
        sql = ("SELECT sync_sequence FROM publication_outbox WHERE pair_id=? AND domain=? "
               "AND published_at IS NOT NULL")
        args = [pair, domain]
        if pending is not None:
            sql += " AND sync_sequence<?"
            args.append(int(pending[0]))
        row = conn.execute(sql + " ORDER BY sync_sequence DESC LIMIT 1", args).fetchone()
        prefix = int(row[0]) if row else 0
        conn.execute(
            "INSERT INTO publication_progress VALUES(?,?,?) ON CONFLICT(pair_id,domain) "
            "DO UPDATE SET sync_sequence=MAX(sync_sequence,excluded.sync_sequence)", (pair, domain, prefix))
        return int(conn.execute("SELECT sync_sequence FROM publication_progress WHERE pair_id=? AND domain=?",
                                (pair, domain)).fetchone()[0])

    def mark_outbox_acked(self, sync_sequence: int, *, pair_id: str | None = None,
                          domain: str | None = None, consumer_id: str | None = None) -> None:
        where, args = "sync_sequence=?", [int(sync_sequence)]
        if pair_id is not None: where += " AND pair_id=?"; args.append(str(pair_id))
        if domain is not None: where += " AND domain=?"; args.append(str(domain))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scopes = {(r[0], r[1]) for r in conn.execute(
                f"SELECT pair_id,domain FROM publication_outbox WHERE {where}", args)}
            # Duplicate ACK after pruning must still preserve the saved frontier.
            if domain is not None:
                scopes.add((str(pair_id or "default"), str(domain)))
            conn.execute(f"UPDATE publication_outbox SET published_at=unixepoch(),blocked_reason=NULL WHERE {where} AND published_at IS NULL", tuple(args))
            for pair, sequence_domain in scopes:
                prefix = self._advance_publication_progress(conn, pair, sequence_domain)
                if consumer_id is not None and domain is not None:
                    conn.execute(
                        "INSERT INTO v2_checkpoints(pair_id,consumer_id,domain,sync_sequence) VALUES(?,?,?,?) "
                        "ON CONFLICT(pair_id,consumer_id,domain) DO UPDATE SET sync_sequence=MAX(sync_sequence,excluded.sync_sequence)",
                        (pair, str(consumer_id), sequence_domain, prefix))

    def publication_domains(self, *, pair_id: str) -> list[str]:
        with self._connect() as conn:
            return [str(row[0]) for row in conn.execute(
                "SELECT domain FROM publication_progress WHERE pair_id=? ORDER BY domain", (str(pair_id),))]

    def retain_publication_outbox(self, *, pair_id: str, domain: str, dry_run: bool = True,
                                   batch_size: int = 500, now: float | None = None) -> dict[str, Any]:
        """Count/delete one old confirmed batch; busy writers yield immediately.

        Inspect at most batch_size safe published rows, then apply age. A
        recent prefix may defer older later rows; eligible counts this window.
        Only publication copies are disposable. Canonical facts, device cursors
        and broker/consumer policies are deliberately independent of this method.
        """
        limit = min(1000, max(1, int(batch_size)))
        cutoff = (self._clock.time() if now is None else float(now)) - 7 * 86400
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA busy_timeout=0")
                conn.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
                # LIMIT belongs inside this non-flattenable candidate window:
                # inspect at most one safe prefix batch before applying age.
                # A recent prefix may conservatively defer older later rows.
                rows = conn.execute(
                    "SELECT o.sync_sequence FROM ("
                    "SELECT sync_sequence,published_at FROM publication_outbox "
                    "WHERE pair_id=? AND domain=? AND published_at IS NOT NULL AND blocked_reason IS NULL "
                    "AND sync_sequence<=COALESCE((SELECT sync_sequence FROM publication_progress "
                    "WHERE pair_id=? AND domain=?),0) "
                    "ORDER BY sync_sequence LIMIT ?) o WHERE o.published_at<?",
                    (str(pair_id), str(domain), str(pair_id), str(domain), limit, cutoff)).fetchall()
                if not dry_run:
                    conn.executemany("DELETE FROM publication_outbox WHERE pair_id=? AND domain=? AND sync_sequence=?",
                                     [(str(pair_id), str(domain), int(r[0])) for r in rows])
                return {"eligible": len(rows), "deleted": 0 if dry_run else len(rows), "busy": False}
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            return {"eligible": 0, "deleted": 0, "busy": True}

    def record_outbox_failure(self, sync_sequence: int, reason: str, *, pair_id: str | None = None,
                              domain: str | None = None, permanent: bool = False) -> None:
        where, args = "sync_sequence=?", [int(sync_sequence)]
        if pair_id is not None: where += " AND pair_id=?"; args.append(str(pair_id))
        if domain is not None: where += " AND domain=?"; args.append(str(domain))
        with self._connect() as conn:
            conn.execute(f"UPDATE publication_outbox SET attempts=attempts+1,blocked_reason=? WHERE {where}",
                         tuple(["permanent:" + str(reason) if permanent else None] + args))

    def replay_after(self, *, domain: str, sync_sequence: int, pair_id: str = "default", limit: int = 100) -> list[bytes]:
        with self._connect() as conn:
            state=conn.execute("SELECT retained_from n FROM v2_feed_state WHERE pair_id=? AND domain='__pair__'", (pair_id,)).fetchone()
            if state is None:
                return []
            retained=int(state["n"] or 1)
            if int(sync_sequence)+1 < retained: raise ValueError("SNAPSHOT_REQUIRED")
            rows = conn.execute(
                f"SELECT envelope_json, {_DISCLOSABLE_EXECUTION_FACT} AS disclosable FROM durable_facts AS fact "
                "WHERE pair_id=? AND domain=? AND sync_sequence>? ORDER BY sync_sequence LIMIT ?",
                (pair_id, domain, int(sync_sequence), max(1, int(limit))),
            ).fetchall()
            if any(not row["disclosable"] for row in rows):
                raise ValueError("SNAPSHOT_REQUIRED")
        return [str(r["envelope_json"]).encode("utf-8") for r in rows]

    def execution_fact_is_withdrawn(self, event_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_DISCLOSABLE_EXECUTION_FACT} AS disclosable FROM durable_facts AS fact WHERE event_id=?",
                (event_id,),
            ).fetchone()
        return row is not None and not row["disclosable"]
