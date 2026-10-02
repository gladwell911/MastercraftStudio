"""Pure transformations shared by execution-page projections."""

from __future__ import annotations

import re
import math
import hashlib
import json
from typing import Callable


def collapse_kimi_execution_lifecycle(steps: list) -> list:
    """Keep one row per Kimi tool item, preserving its first position."""
    collapsed = []
    positions = {}
    for step in steps or []:
        if not isinstance(step, dict) or not str(step.get("source_kind") or "").startswith(
            ("tool.", "shell.", "subagent.")
        ):
            collapsed.append(step)
            continue
        item_id = str(step.get("item_id") or "").strip()
        key = (
            str(step.get("thread_id") or ""),
            str(step.get("turn_id") or ""),
            item_id,
        )
        if item_id and key in positions:
            collapsed[positions[key]] = step
            continue
        if item_id:
            positions.setdefault(key, len(collapsed))
        collapsed.append(step)
    return collapsed


def execution_step_detail_text(step) -> str:
    if not isinstance(step, dict):
        return str(step or "").strip()
    return str(
        step.get("detail_text")
        or step.get("message")
        or step.get("step")
        or step.get("title")
        or step.get("text")
        or step.get("content")
        or step.get("description")
        or ""
    )


def should_show_execution_step(step) -> bool:
    if not isinstance(step, dict):
        return bool(str(step or "").strip())
    display_kind = str(step.get("display_kind") or "").strip()
    event_type = str(step.get("event_type") or "").strip()
    phase = str(step.get("phase") or "").strip()
    list_text = str(step.get("list_text") or "").strip()
    detail_text = execution_step_detail_text(step)
    if str(step.get("kimi_summary") or "").strip():
        return True
    if display_kind == "error":
        return False
    hidden_status_texts = {"开始处理本轮请求", "本轮处理结束", "active", "idle"}
    hidden_phases = {
        "开始执行：阶段：commentary", "完成执行：阶段：commentary",
        "开始执行：阶段：final_answer", "完成执行：阶段：final_answer",
    }
    if re.sub(r"\s+", " ", detail_text.strip()) in hidden_phases:
        return False
    if re.sub(r"\s+", " ", list_text.strip()) in hidden_phases:
        return False
    if event_type in {"turn_started", "turn_completed"} and (
        detail_text in hidden_status_texts or list_text in hidden_status_texts
    ):
        return False
    if event_type == "item_completed" and phase == "final_answer":
        return False
    if display_kind == "command":
        return False
    if display_kind == "status" and (
        detail_text in hidden_status_texts or list_text in hidden_status_texts
    ):
        return False
    if list_text in {"active", "idle"} or detail_text in {"active", "idle"}:
        return False
    if display_kind in {"commentary", "plan", "error", "user_input", "turn_context"}:
        return bool(list_text or detail_text)
    if event_type in {"agent_message_delta", "plan_updated", "stderr", "server_request"}:
        return bool(list_text or detail_text)
    if display_kind:
        return False
    return bool(list_text or detail_text)


def execution_list_text_from_detail(detail: str, kind: str) -> str:
    single_line = re.sub(r"\s+", " ", str(detail or "").strip())
    if not single_line:
        return ""
    prefix = {"command": "命令：", "error": "错误：", "plan": "计划："}.get(
        str(kind or "").strip(), ""
    )
    if prefix and not single_line.startswith(prefix):
        return f"{prefix}{single_line}"
    return single_line


def execution_command_list_text(
    event_type: str, title: str, command: str, exit_code, fallback_text: str = ""
) -> str:
    parts = []
    normalized_type = str(event_type or "").strip()
    if normalized_type == "item_started":
        parts.append("开始执行")
    elif normalized_type == "item_completed":
        parts.append("完成执行")
    title_text = str(title or "").strip()
    command_text = str(command or "").strip()
    fallback = str(fallback_text or "").strip()
    if title_text:
        parts.append(title_text)
    if command_text:
        parts.append(command_text)
    if not title_text and not command_text and fallback:
        parts.append(fallback)
    if normalized_type == "item_completed" and exit_code not in (None, ""):
        parts.append(f"退出码：{exit_code}")
    summary = " ".join(parts).strip()
    return f"命令：{summary}" if summary else "命令：commandExecution"


def _finite_timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    return timestamp if math.isfinite(timestamp) else None


def _execution_timestamp(step):
    if not isinstance(step, dict):
        return None
    created = _finite_timestamp(step.get("created_at"))
    return created if created is not None else _finite_timestamp(step.get("ts"))


def canonical_answer_timestamp(turn: dict, steps: list, turn_index: int):
    """Prefer a persisted answer time, then this owner's canonical final fact."""
    persisted = _finite_timestamp(turn.get("answer_at"))
    if persisted is not None:
        return persisted
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        try:
            index = int(step.get("turn_idx"))
        except (TypeError, ValueError):
            continue
        if index != turn_index:
            continue
        kind = str(step.get("raw_kind") or step.get("kind") or step.get("display_kind") or "")
        if kind == "final":
            timestamp = _execution_timestamp(step)
            if timestamp is not None:
                return timestamp
    return None


def execution_turn_context_steps(
    steps: list,
    turns: list,
    *,
    view_mode: str,
    active_turn_index: int,
    selected_model: str,
    requesting_text: str,
    answer_to_plain: Callable[[str, str], str],
) -> list:
    """Add the same question/answer context around an owner's execution steps."""
    if not turns:
        return list(steps or [])
    indices = []
    if view_mode == "active" and 0 <= active_turn_index < len(turns):
        indices = [active_turn_index]
    if not indices:
        seen = []
        for step in steps or []:
            if view_mode == "history" and not should_show_execution_step(step):
                continue
            if isinstance(step, dict) and "turn_idx" in step:
                try:
                    index = int(step.get("turn_idx"))
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(turns) and index not in seen:
                    seen.append(index)
        indices = seen or ([0] if len(turns) == 1 else [])
    if not indices:
        return list(steps or [])
    index = max(indices) if view_mode == "history" else indices[0]
    turn = turns[index] if isinstance(turns[index], dict) else {}
    question = str(turn.get("question") or "").strip()
    answer_md = str(turn.get("answer_md") or "").strip()
    answer = ""
    if answer_md and answer_md != requesting_text:
        answer = answer_to_plain(answer_md, str(turn.get("model") or selected_model or "")).strip()

    def timestamp(raw_kind: str):
        if raw_kind == "final":
            value = canonical_answer_timestamp(turn, steps, index)
            if value is not None:
                return value
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            try:
                step_index = int(step.get("turn_idx"))
            except (TypeError, ValueError):
                continue
            if step_index != index:
                continue
            kind = str(step.get("raw_kind") or step.get("kind") or step.get("display_kind") or "").strip()
            if kind == raw_kind:
                value = _execution_timestamp(step)
                if value is not None:
                    return value
        return _finite_timestamp(turn.get("created_at"))

    prefix = []
    if question:
        row = {"display_kind": "turn_context", "list_text": f"我：{question}",
               "detail_text": question, "turn_idx": index, "synthetic": "question"}
        value = timestamp("question")
        if value is not None:
            row["created_at"] = value
        prefix.append(row)
    suffix = []
    if answer:
        row = {"display_kind": "turn_context", "list_text": f"小诸葛：{answer}",
               "detail_text": answer, "turn_idx": index, "synthetic": "answer"}
        value = timestamp("final")
        if value is not None:
            row["created_at"] = value
        suffix.append(row)
    return prefix + list(steps or []) + suffix


def execution_row_id(chat_id: str, revision: int, step, occurrence: int = 0) -> str:
    """Stable owner-scoped identity for a canonical execution row."""
    owner = str(chat_id or "").strip()
    if isinstance(step, dict):
        if step.get("logical_key"):
            return f"execution:{owner}:logical:{step['logical_key']}"
        if step.get("synthetic"):
            return f"execution:{owner}:turn:{step.get('turn_idx')}:{step['synthetic']}"
        source_kind = str(step.get("source_kind") or "")
        item_id = str(step.get("item_id") or "").strip()
        if item_id and source_kind.startswith(("tool.", "shell.", "subagent.")):
            identity = [owner, revision, str(step.get("thread_id") or ""),
                        str(step.get("turn_id") or ""), item_id]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
            return f"execution:{owner}:kimi:{digest}"
        if step.get("_execution_uid"):
            return f"execution:{owner}:uid:{step['_execution_uid']}"
        if "_store_step_index" in step:
            return f"execution:{owner}:store:{step['_store_step_index']}"
        native_id = str(step.get("event_id") or step.get("id") or step.get("item_id") or "").strip()
        if native_id:
            provider = str(step.get("provider") or step.get("adapter") or "legacy").replace("_server", "").strip().lower()
            try:
                step_revision = int(step.get("revision"))
            except (TypeError, ValueError):
                step_revision = revision
            turn_value = step.get("turn_id") or step.get("turnId")
            if turn_value is None or str(turn_value).strip() == "":
                turn_value = step.get("turn_idx")
            identity = ["legacy-event", owner, step_revision,
                        "" if turn_value is None else str(turn_value).strip(), provider,
                        str(step.get("agent_id") or step.get("agentId") or "").strip(),
                        str(step.get("thread_id") or step.get("threadId") or step.get("session_id") or "").strip(),
                        str(step.get("session_id") or step.get("thread_id") or "").strip(),
                        native_id, int(occurrence)]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            return f"execution:{owner}:legacy-event:{digest}"
    if isinstance(step, dict):
        payload = {key: value for key, value in step.items() if key not in {
            "_store_step_index", "_execution_uid", "updated_at", "status",
        }}
    else:
        payload = {"value_type": type(step).__name__, "value": str(step or "")}
    fingerprint = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()
    identity = ["legacy-record", owner, fingerprint, int(occurrence)]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    return f"execution:{owner}:legacy-quarantine:{digest}"


def execution_meta_text(step) -> tuple[str, str]:
    """Return the desktop list and detail text for a visible step."""
    if not isinstance(step, dict):
        value = str(step or "").strip()
        return value, value
    detail = execution_step_detail_text(step)
    kind = str(step.get("display_kind") or "").strip()
    title = str(step.get("list_text") or "").strip()
    if not title:
        if kind == "command":
            fallback = (str(step.get("subtype") or "").strip()
                        or str(step.get("status") or "").strip()
                        or str(step.get("event_type") or "").strip())
            title = execution_command_list_text(
                str(step.get("event_type") or "").strip(),
                str(step.get("title") or "").strip(),
                str(step.get("command") or "").strip(),
                step.get("exit_code"), fallback,
            )
        else:
            title = execution_list_text_from_detail(detail, kind)
    return title, detail


def project_execution_rows(
    *, chat_id: str, revision: int, steps: list, turns: list,
    view_mode: str, active_turn_index: int, selected_model: str,
    requesting_text: str, answer_to_plain: Callable[[str, str], str],
) -> list[dict]:
    """Project canonical source into ordered, readable content rows."""
    canonical = collapse_kimi_execution_lifecycle(steps)
    context = execution_turn_context_steps(
        canonical, turns, view_mode=view_mode,
        active_turn_index=active_turn_index, selected_model=selected_model,
        requesting_text=requesting_text, answer_to_plain=answer_to_plain,
    )
    buckets = {}
    occurrences = [0] * len(context)
    for index in range(len(context) - 1, -1, -1):
        step = context[index]
        key = json.dumps(step, sort_keys=True, ensure_ascii=False, default=str)
        occurrences[index] = buckets.get(key, 0)
        buckets[key] = occurrences[index] + 1
    rows = []
    for index, step in enumerate(context):
        if not should_show_execution_step(step):
            continue
        title, detail = execution_meta_text(step)
        if not title.strip():
            continue
        rows.append({
            "row_id": execution_row_id(chat_id, revision, step, occurrences[index]),
            "list_text": title,
            "detail_text": detail,
            "created_at": _execution_timestamp(step),
            "turn_index": step.get("turn_idx") if isinstance(step, dict) else None,
        })
    return rows
