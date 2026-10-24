from pathlib import Path
import contextlib
import importlib.util
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("outbox_benchmark", ROOT / "scripts" / "benchmark_outbox.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.mark.parametrize("args", [["--rows", "0"], ["--samples", "0"], ["--growth", "1"]])
def test_invalid_arguments_fail_before_fixture_creation(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["benchmark_outbox.py"] + args)
    monkeypatch.setattr(benchmark.tempfile, "TemporaryDirectory", lambda **_: pytest.fail("fixture created"))
    with pytest.raises(SystemExit) as error:
        benchmark.main()
    assert error.value.code == 2


@pytest.mark.parametrize("copied", [False, True])
def test_benchmark_upgrade_starts_without_new_indexes_or_progress(tmp_path, monkeypatch, capsys, copied):
    from chat_store import ChatStore
    if copied:
        source = benchmark.fixture(tmp_path / "source.db", 10)
    original = ChatStore.initialize
    upgrades = []
    def initialize(store):
        if store.db_path.exists():
            with contextlib.closing(sqlite3.connect(store.db_path)) as conn:
                indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
                assert not indexes.intersection({"idx_outbox_pending_pair", "idx_outbox_pending_domain", "idx_outbox_retention_prefix"})
                assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='publication_progress'").fetchone()
                assert not conn.execute("SELECT 1 FROM meta WHERE key='publication_progress_initialized'").fetchone()
            upgrades.append(True)
        return original(store)
    monkeypatch.setattr(ChatStore, "initialize", initialize)
    args = ["benchmark_outbox.py", "--rows", "10", "--growth", "2", "--samples", "1"]
    if copied:
        args += ["--source", str(source.db_path)]
    monkeypatch.setattr(sys, "argv", args)
    benchmark.main()
    import json
    output = json.loads(capsys.readouterr().out)
    assert len(upgrades) == len(output["runs"]) == (1 if copied else 2)
    assert all(run["upgrade_ms"] > 0 and "not separately instrumented" in run["migration_timing_scope"] for run in output["runs"])
    assert all("pending_index_upgrade_lock_ms" not in run for run in output["runs"])
