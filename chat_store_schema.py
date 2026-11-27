"""SQLite schema installation and restartable migrations."""

from __future__ import annotations

import sqlite3
import uuid


MAX_INT64 = (1 << 63) - 1


V2_MIGRATION_KEY = "identity_v2_migration_phase"


class ChatStoreSchemaMixin:
    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
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
                CREATE TABLE IF NOT EXISTS native_history_owners (
                    native_chat_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL UNIQUE,
                    account TEXT NOT NULL
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
                    logical_key TEXT NOT NULL DEFAULT '',
                    visible INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, step_index)
                );
                CREATE TABLE IF NOT EXISTS execution_assemblers (
                    chat_id TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(chat_id, logical_key)
                );
                CREATE TABLE IF NOT EXISTS execution_fragment_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    fragment_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE INDEX IF NOT EXISTS idx_execution_fragment_conflicts
                    ON execution_fragment_conflicts(chat_id, logical_key, id);
                CREATE TABLE IF NOT EXISTS execution_projection_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_projection_snapshot_owner
                    ON execution_projection_snapshots(chat_id, revision, expires_at);
                CREATE TABLE IF NOT EXISTS execution_projection_rows (
                    snapshot_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    row_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, row_index)
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS read_generations (
                    chat_id TEXT PRIMARY KEY, generation TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS readable_answers (
                    answer_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
                    generation TEXT NOT NULL, baseline INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_readable_owner
                    ON readable_answers(chat_id,generation,answer_seq);
                CREATE TABLE IF NOT EXISTS chat_read_state (
                    pair_id TEXT NOT NULL, chat_id TEXT NOT NULL, generation TEXT NOT NULL,
                    read_seq INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(pair_id,chat_id,generation)
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
                CREATE INDEX IF NOT EXISTS idx_outbox_pending_pair
                    ON publication_outbox(pair_id,sync_sequence) WHERE published_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_outbox_pending_domain
                    ON publication_outbox(pair_id,domain,sync_sequence) WHERE published_at IS NULL;
                DROP INDEX IF EXISTS idx_outbox_retention_age;
                CREATE INDEX IF NOT EXISTS idx_outbox_retention_prefix
                    ON publication_outbox(pair_id,domain,sync_sequence,published_at)
                    WHERE published_at IS NOT NULL AND blocked_reason IS NULL;
                CREATE TABLE IF NOT EXISTS publication_progress (
                    pair_id TEXT NOT NULL, domain TEXT NOT NULL, sync_sequence INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(pair_id,domain)
                );
                CREATE TABLE IF NOT EXISTS v2_checkpoints (
                    pair_id TEXT NOT NULL, consumer_id TEXT NOT NULL, domain TEXT NOT NULL, sync_sequence INTEGER NOT NULL,
                    PRIMARY KEY(pair_id, consumer_id, domain)
                );
                CREATE TABLE IF NOT EXISTS notification_devices (
                    pair_id TEXT NOT NULL, device_id TEXT NOT NULL, baseline INTEGER NOT NULL,
                    processed_sequence INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(pair_id,device_id)
                );
                CREATE TABLE IF NOT EXISTS remote_content_pages (
                    content_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, content TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_snapshots (
                    snapshot_id TEXT PRIMARY KEY, pair_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    high_water INTEGER NOT NULL, incarnation TEXT NOT NULL, row_count INTEGER NOT NULL,
                    delivered_count INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS notification_snapshot_rows (
                    snapshot_id TEXT NOT NULL, row_index INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id,row_index)
                );
                CREATE TABLE IF NOT EXISTS authority_cache_revision (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO authority_cache_revision VALUES(1,0);
                CREATE TRIGGER IF NOT EXISTS authority_chats_insert AFTER INSERT ON chats BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_chats_delete AFTER DELETE ON chats BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_read_generations_insert AFTER INSERT ON read_generations BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_read_generations_update AFTER UPDATE ON read_generations BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_read_generations_delete AFTER DELETE ON read_generations BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_readable_answers_insert AFTER INSERT ON readable_answers BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_readable_answers_update AFTER UPDATE ON readable_answers BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_readable_answers_delete AFTER DELETE ON readable_answers BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_chat_read_state_insert AFTER INSERT ON chat_read_state BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_chat_read_state_update AFTER UPDATE ON chat_read_state BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_chat_read_state_delete AFTER DELETE ON chat_read_state BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TRIGGER IF NOT EXISTS authority_owner_revision AFTER UPDATE OF revision ON v2_chat_state WHEN OLD.revision<>NEW.revision BEGIN UPDATE authority_cache_revision SET revision=revision+1 WHERE singleton=1; END;
                CREATE TABLE IF NOT EXISTS identity_quarantine (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL, event_id TEXT,
                    payload_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS remote_attachment_sends (
                    pair_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    send_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT,
                    state TEXT NOT NULL DEFAULT 'uploading',
                    PRIMARY KEY (pair_id, device_id, chat_id, send_id)
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
                    sound_permission INTEGER NOT NULL DEFAULT 0 CHECK(sound_permission IN (0,1)),
                    sound_consumed_at REAL,
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
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('notification_feed_id',?)", (uuid.uuid4().hex,))
        conn.execute("CREATE TABLE IF NOT EXISTS notification_feed_devices (pair_id TEXT NOT NULL, device_id TEXT NOT NULL, feed_id TEXT NOT NULL, baseline INTEGER NOT NULL, processed_sequence INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(pair_id,device_id,feed_id))")
        if "feed_id" not in {row[1] for row in conn.execute("PRAGMA table_info(notification_snapshots)")}:
            conn.execute("ALTER TABLE notification_snapshots ADD COLUMN feed_id TEXT NOT NULL DEFAULT ''")
        if conn.execute("SELECT 1 FROM meta WHERE key='publication_progress_initialized'").fetchone() is None:
            for scope in conn.execute("SELECT DISTINCT pair_id,domain FROM publication_outbox").fetchall():
                self._advance_publication_progress(conn, scope["pair_id"], scope["domain"])
            conn.execute("INSERT INTO meta(key,value) VALUES('publication_progress_initialized','1')")
        execution_columns = {row["name"] for row in conn.execute("PRAGMA table_info(execution_steps)")}
        if "logical_key" not in execution_columns:
            conn.execute("ALTER TABLE execution_steps ADD COLUMN logical_key TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_logical_key ON execution_steps(chat_id, logical_key) WHERE logical_key <> ''")
        if "visible" not in execution_columns:
            conn.execute("ALTER TABLE execution_steps ADD COLUMN visible INTEGER NOT NULL DEFAULT 0")
            rows = conn.execute("SELECT chat_id,step_index,payload_json FROM execution_steps").fetchall()
            conn.executemany("UPDATE execution_steps SET visible=? WHERE chat_id=? AND step_index=?",
                [(self._execution_visible(self._json_dict(row["payload_json"])), row["chat_id"], row["step_index"])
                 for row in rows])
        conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_visible_tail ON execution_steps(chat_id,step_index) WHERE visible=1")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clear_operations)")}
        if "pair_id" not in columns:
            conn.execute("ALTER TABLE clear_operations ADD COLUMN pair_id TEXT NOT NULL DEFAULT 'default'")
        if "domain" not in columns:
            conn.execute("ALTER TABLE clear_operations ADD COLUMN domain TEXT NOT NULL DEFAULT 'events'")
        if "sound_permission" not in columns:
            conn.execute("ALTER TABLE clear_operations ADD COLUMN sound_permission INTEGER NOT NULL DEFAULT 0")
        if "sound_consumed_at" not in columns:
            conn.execute("ALTER TABLE clear_operations ADD COLUMN sound_consumed_at REAL")
        self._advance_v2_migration(conn)
        if conn.execute("SELECT 1 FROM meta WHERE key='read_state_initialized'").fetchone() is None:
            for row in conn.execute("SELECT chat_id,turn_index,payload_json FROM turns").fetchall():
                self._backfill_canonical_turns_conn(conn, row["chat_id"],
                    [self._json_dict(row["payload_json"])], int(row["turn_index"]))
            conn.execute("UPDATE readable_answers SET baseline=1")
            conn.execute("INSERT INTO meta(key,value) VALUES('read_state_initialized','1')")
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
