"""Read-only, identity-validated proposal for a misattributed accepted append.

Run only against an isolated SQLite snapshot. This utility has no apply mode.
Its report retains original rows and database SHA-256 for later audited review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def propose(snapshot, *, chat_id, thread_id, native_turn_id, model, generation,
            source_index, target_index):
    path = Path(snapshot).resolve(strict=True)
    if path == Path("D:/code/cx/history/chat_history.db").resolve():
        raise ValueError("Live database is forbidden; provide an isolated snapshot")
    if Path(str(path) + "-wal").exists():
        raise ValueError("Provide a standalone consistent snapshot without a WAL sidecar")
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if not all((chat_id, thread_id, native_turn_id, model)) or source_index >= target_index:
        raise ValueError("Complete ordered owner identities are required")
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        turns = {idx: json.loads(payload) for idx, payload in db.execute(
            "SELECT turn_index,payload_json FROM turns WHERE chat_id=?", (chat_id,))}
        steps = [(idx, json.loads(payload)) for idx, payload in db.execute(
            "SELECT step_index,payload_json FROM execution_steps WHERE chat_id=? ORDER BY step_index", (chat_id,))]
    for idx in (source_index, target_index):
        turn = turns.get(idx, {})
        if (turn.get("codex_thread_id"), turn.get("codex_turn_id"), turn.get("model"),
            turn.get("codex_start_generation"), turn.get("codex_context_generation")) != (
                thread_id, native_turn_id, model, generation, generation):
            raise ValueError("Owner identity mismatch or incomplete generation")
    source, target = turns[source_index], turns[target_index]
    if target.get("request_status") not in {"pending", "running"} or target.get("answer_md") not in {"", "正在请求..."}:
        raise ValueError("Target is not an unresolved accepted request")
    question = target.get("question")
    if not question or sum(turn.get("question") == question for turn in turns.values()) != 1:
        raise ValueError("Ambiguous accepted question")
    boundaries, finals, completions = {}, {}, []
    user_texts = {}
    first_items = {}
    def detail_for(step):
        detail = step.get("source_detail") or step.get("data") or {}
        if not isinstance(detail, dict):
            raise ValueError("Incomplete execution detail")
        return detail
    def validate_execution(step):
        detail = detail_for(step)
        threads = [value for value in (step.get("thread_id"), detail.get("thread_id"), detail.get("threadId")) if value is not None]
        if not threads or any(value != thread_id for value in threads):
            raise ValueError("Execution thread identity mismatch or missing")
        if step.get("turn_id") != native_turn_id or any(detail[key] != native_turn_id for key in ("turn_id", "turnId") if key in detail):
            raise ValueError("Execution native identity mismatch")
        if detail.get("context_generation") != generation or detail.get("model") != model:
            raise ValueError("Execution model/generation identity mismatch")
        if any(step[key] != expected for key, expected in (("context_generation", generation), ("model", model)) if key in step):
            raise ValueError("Conflicting execution model/generation")
        if step.get("turn_idx") != source_index or ("turn_idx" in detail and detail["turn_idx"] != source_index):
            raise ValueError("Execution existing owner mismatch")
        return detail
    for idx, step in steps:
        if step.get("turn_id") != native_turn_id:
            continue
        detail = detail_for(step)
        subtype = step.get("subtype") or detail.get("type")
        item_id = step.get("item_id") or detail.get("id")
        if item_id and step.get("type") != "turn_completed":
            first_items.setdefault(item_id, idx)
        if subtype == "userMessage":
            validate_execution(step)
            content = detail.get("content") or []
            if not isinstance(content, list):
                raise ValueError("Incomplete accepted user content")
            text = "\n".join(str(item.get("text") or "") for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "inputText"})
            if item_id in user_texts and user_texts[item_id] != text:
                raise ValueError("Conflicting duplicate accepted evidence")
            user_texts[item_id] = text
            if text == question:
                if not isinstance(item_id, str) or not item_id:
                    raise ValueError("Missing accepted item identity")
                boundaries.setdefault(item_id, idx)
            elif boundaries:
                raise ValueError("Later accepted boundary makes final ownership ambiguous")
        if step.get("phase") == "final_answer":
            validate_execution(step)
            text = detail.get("text")
            if not isinstance(text, str) or (not text.strip() and step.get("type") == "item_completed"):
                raise ValueError("Empty or non-string final evidence")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("Missing final item identity")
            previous = finals.get(item_id)
            if previous and previous[1].strip() and text.strip() and previous[1] != text:
                raise ValueError("Conflicting duplicate final evidence")
            if text.strip() and previous is None:
                finals[item_id] = (idx, text, item_id)
        if step.get("type") == "turn_completed":
            validate_execution(step)
            if detail.get("id", native_turn_id) != native_turn_id or detail.get("status", "completed") != "completed":
                raise ValueError("Conflicting native completion evidence")
            completions.append(idx)
    if len(boundaries) != 1 or len(finals) != 1 or not completions:
        raise ValueError("Incomplete or ambiguous accepted boundary/final/completion evidence")
    boundary_id, boundary_idx = next(iter(boundaries.items()))
    boundary, final = (boundary_idx, boundary_id), next(iter(finals.values()))
    if not boundary[0] < final[0] < completions[0] or source.get("answer_md") != final[1] or source.get("request_status") != "done":
        raise ValueError("Final evidence does not agree with stored source")
    proposed_source = {**source, "answer_md": "", "request_carried_forward_to": target_index}
    for key in ("codex_completed_answers", "answer_at", "answer_origin", "provider_message_id",
        "message_id", "answer_message_id", "answer_seq", "answer_generation", "readable_answer", "answered_at"):
        proposed_source.pop(key, None)
    replacements = {
        source_index: proposed_source,
        target_index: {**target, "answer_md": final[1], "request_status": "done",
            "answer_at": source.get("answer_at"), "codex_completed_answers": [{"item_id": final[2], "text": final[1]}]},
    }
    def belongs_after_boundary(idx, step):
        detail = detail_for(step)
        item_id = str(step.get("item_id") or (detail.get("id") if isinstance(detail, dict) else "") or "")
        selected = boundary[0] <= idx <= completions[-1] and (
            step.get("turn_id") == native_turn_id or step.get("turn_idx") == source_index)
        if selected and step.get("turn_id") != native_turn_id:
            validate_execution(step)
        if selected and step.get("type") != "turn_completed" and item_id and first_items.get(item_id, idx) < boundary[0]:
            return False
        if selected:
            validate_execution(step)
        return selected
    report = {"mode": "proposal-only", "snapshot_sha256": original_hash,
        "identity": {"chat_id": chat_id, "thread_id": thread_id, "native_turn_id": native_turn_id,
            "model": model, "generation": generation},
        "accepted_item_id": boundary[1], "final_item_id": final[2],
        "turn_changes": [{"index": idx, "original": turns[idx], "proposed": proposed}
            for idx, proposed in replacements.items()],
        "execution_changes": [{"step_index": idx, "original": step,
            "proposed_owner": target_index} for idx, step in steps
            if belongs_after_boundary(idx, step)],
        "publication": "Requires separately audited canonical/read/outbox transaction; no publication performed"}
    if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
        raise ValueError("Snapshot changed during proposal")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    for name in ("chat-id", "thread-id", "native-turn-id", "model"):
        parser.add_argument("--" + name, required=True)
    for name in ("generation", "source-index", "target-index"):
        parser.add_argument("--" + name, type=int, required=True)
    args = vars(parser.parse_args())
    try:
        print(json.dumps(propose(**args), ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error) as error:
        parser.exit(2, f"Refused: {error}\n")


if __name__ == "__main__":
    main()
