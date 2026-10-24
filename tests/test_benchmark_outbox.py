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



@pytest.mark.parametrize("copied", [False, True])
def test_nonexistent_pair_is_rejected_without_source_changes(tmp_path, monkeypatch, copied):
    source = benchmark.fixture(tmp_path / "source.db", 10)
    before = source.db_path.read_bytes()
    args = ["benchmark_outbox.py", "--rows", "10", "--pair", "does-not-exist", "--samples", "1"]
    if copied:
        args += ["--source", str(source.db_path)]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        benchmark.main()
    assert error.value.code == 2
    assert source.db_path.read_bytes() == before


@pytest.mark.parametrize("pruned", [False, True])
def test_selected_pair_counts_are_distinct_from_whole_database_counts(tmp_path, monkeypatch, capsys, pruned):
    source = benchmark.fixture(tmp_path / "multi-pair.db", 10)
    source.commit_durable_fact(pair_id="other", domain="events", envelope={
        "event_id": "other-pending", "kind": "state_changed", "chat_id": "chat", "body": {}})
    if pruned:
        with source._connect() as conn:
            conn.execute("DELETE FROM publication_outbox WHERE pair_id='other'")
    monkeypatch.setattr(sys, "argv", ["benchmark_outbox.py", "--source", str(source.db_path),
        "--pair", "other", "--samples", "1"])
    benchmark.main()
    import json
    run = json.loads(capsys.readouterr().out)["runs"][0]
    assert run["pair"] == "other"
    assert run["counts"] == {"outbox": 10 if pruned else 11, "facts": 11}
    assert run["selected_pair_counts"] == {"outbox": 0 if pruned else 1, "facts": 1, "pending": 0 if pruned else 1}
    assert run["before"]["other"]["returned_rows"] == (0 if pruned else 1)
    assert run["after"]["other"]["returned_rows"] == (0 if pruned else 1)
