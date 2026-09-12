from __future__ import annotations

import json
from pathlib import Path

import main


FIXTURE = Path(__file__).parent / "fixtures" / "accessible_browsing_contract.json"
RC_FIXTURE = Path(__file__).parents[2] / "rc" / "test" / "fixtures" / "accessible_browsing_contract.json"


def test_accessible_browsing_fixture_is_byte_identical_between_clients():
    assert FIXTURE.read_bytes() == RC_FIXTURE.read_bytes()


def test_shared_compact_markdown_vectors_include_long_outer_fence():
    matrix = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in matrix["compact_markdown"]:
        assert main.compact_markdown_summary(row["input"]) == row["output"]


def test_shared_execution_timestamp_fallback_and_unknown_vectors():
    matrix = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in matrix["execution_timestamps"]:
        actual = main._execution_timestamp(row)
        if row["known"]:
            assert actual == row["value"], row["name"]
        else:
            assert actual is None, row["name"]


def test_execution_sequence_precedes_malformed_time_and_epoch_zero():
    rows = [
        {"event_id": "second", "execution_sequence": 2, "created_at": 0},
        {"event_id": "first", "execution_sequence": 1, "created_at": "bad", "ts": "bad"},
    ]
    ordered = sorted(
        enumerate(rows),
        key=lambda indexed: main.ChatFrame._execution_merge_sort_key(indexed[1], indexed[0]),
    )
    assert [row["event_id"] for _, row in ordered] == ["first", "second"]
