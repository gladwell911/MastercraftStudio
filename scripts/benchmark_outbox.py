"""Isolated outbox query benchmark; never opens a source database writable."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chat_store import ChatStore


def fixture(path: Path, count: int) -> ChatStore:
    store = ChatStore(path)
    store.initialize()
    # Generate through the store's connection/transaction boundary. One real
    # commit establishes the production schema and canonical envelope shape.
    fact = store.commit_durable_fact(pair_id="bench", domain="events", envelope={
        "event_id": "seed", "kind": "state_changed", "chat_id": "bench", "body": {}})
    with store._connect() as conn:
        conn.execute("DELETE FROM publication_outbox")
        conn.execute("DELETE FROM durable_facts")
        for start in range(1, count + 1, 10000):
            values = [(f"bench-{i}", "hash", "state_changed", "bench", "events", "bench", 1, i, "{}")
                      for i in range(start, min(count + 1, start + 10000))]
            conn.executemany("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,revision,sync_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?)", values)
            conn.executemany("INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload,published_at) VALUES('bench','events',?,?,'events',?,1)",
                             [(v[7], v[0], b"{}") for v in values])
        conn.execute("UPDATE v2_feed_state SET sync_sequence=? WHERE pair_id='bench'", (count,))
    return store


def measure(store: ChatStore, samples: int, pair: str = "bench") -> dict:
    output = {}
    for lane in ("notification", "other"):
        elapsed = []
        for _ in range(samples + 1):
            start = time.perf_counter()
            rows = store.pending_outbox(pair_id=pair, lane=lane)
            elapsed.append((time.perf_counter() - start) * 1000)
            pending_count = len(rows)
        traced = []
        with store._connect() as conn:
            # Same SQL produced by the public query, captured without changing it.
            import contextlib
            original = store._connect
            @contextlib.contextmanager
            def traced_connection():
                conn.set_trace_callback(traced.append)
                yield conn
            store._connect = traced_connection
            try:
                store.pending_outbox(pair_id=pair, lane=lane)
            finally:
                store._connect = original
            select = next(sql for sql in traced if sql.startswith("SELECT o.*"))
            plan = [tuple(r) for r in conn.execute("EXPLAIN QUERY PLAN " + select)]
        warm = sorted(elapsed[1:])
        output[lane] = {"returned_rows": pending_count, "cold_ms": elapsed[0], "warm_median_ms": statistics.median(warm),
                        "warm_p95_ms": warm[min(len(warm)-1, int(len(warm)*.95))], "query_plan": plan}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=196167)
    parser.add_argument("--growth", type=int, default=10)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--pair", default="", help="Pair in copied source; default first pair")
    parser.add_argument("--source", type=Path, help="Optional source opened mode=ro, copied with SQLite backup API")
    args = parser.parse_args()
    if args.rows <= 0:
        parser.error("--rows must be positive")
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    if args.growth <= 1:
        parser.error("--growth must be greater than 1")
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    result = {"source_revision": revision, "samples": args.samples, "scope": "temporary copy only", "runs": []}
    with tempfile.TemporaryDirectory(prefix="mc-outbox-benchmark-") as tmp:
        for multiplier in (1, args.growth):
            path = Path(tmp) / f"outbox-{multiplier}.db"
            if args.source:
                if multiplier != 1:
                    continue
                source = sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)
                target = sqlite3.connect(path)
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
                store = ChatStore(path)
                with store._connect() as conn:
                    row = conn.execute("SELECT pair_id FROM publication_outbox ORDER BY pair_id LIMIT 1").fetchone()
                    pair = args.pair or (str(row[0]) if row else "bench")
            else:
                store = fixture(path, args.rows * multiplier)
                pair = args.pair or "bench"
            with store._connect() as conn:
                selected_counts = {
                    "outbox": conn.execute("SELECT COUNT(*) FROM publication_outbox WHERE pair_id=?", (pair,)).fetchone()[0],
                    "facts": conn.execute("SELECT COUNT(*) FROM durable_facts WHERE pair_id=?", (pair,)).fetchone()[0],
                    "pending": conn.execute("SELECT COUNT(*) FROM publication_outbox WHERE pair_id=? AND published_at IS NULL", (pair,)).fetchone()[0],
                }
                if not selected_counts["outbox"] and not selected_counts["facts"]:
                    parser.error(f"Selected pair {pair!r} has no outbox or durable facts")
            with store._connect() as conn:
                conn.execute("DROP INDEX IF EXISTS idx_outbox_pending_pair")
                conn.execute("DROP INDEX IF EXISTS idx_outbox_pending_domain")
                conn.execute("DROP INDEX IF EXISTS idx_outbox_retention_prefix")
                conn.execute("DROP INDEX IF EXISTS idx_outbox_retention_age")
                conn.execute("DROP TABLE IF EXISTS publication_progress")
                conn.execute("DELETE FROM meta WHERE key='publication_progress_initialized'")
                counts = dict(conn.execute("SELECT 'outbox',COUNT(*) FROM publication_outbox UNION ALL SELECT 'facts',COUNT(*) FROM durable_facts"))
            before = measure(store, args.samples, pair)
            start = time.perf_counter()
            store.initialize()
            upgrade = (time.perf_counter() - start) * 1000
            after = measure(store, args.samples, pair)
            result["runs"].append({"multiplier": multiplier, "pair": pair, "counts": counts, "selected_pair_counts": selected_counts, "upgrade_ms": upgrade,
                                   "migration_timing_scope": "full initialize; write-lock duration not separately instrumented",
                                   "before": before, "after": after})
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
