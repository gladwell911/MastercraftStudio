"""F1 execution pages, projection, and step persistence."""

import copy
import hashlib
import json
import logging
import re
import sqlite3
import uuid
import wx
from codex_client import CodexEvent
from collections import Counter
from functools import partial
from execution_projection import (
    collapse_kimi_execution_lifecycle,
    execution_command_list_text,
    execution_list_text_from_detail,
    execution_row_id,
    execution_step_detail_text,
    execution_turn_context_steps,
    should_show_execution_step,
)
from html import escape
from mc_frame_execution_errors import ExecutionIdentityBlocked, ExecutionPagePending
from mc_frame_presentation import (
    _execution_timestamp,
    _finite_timestamp,
    execution_answer_to_plain,
    md_to_plain_preserving_paragraphs,
    remove_emojis,
    should_show_time,
    wechat_time_label,
)
from mc_frame_settings import (
    EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
    EXECUTION_LIST_EXPAND_ROWS,
    IDLE_UI_REFRESH_DELAY_MS,
    REQUESTING_TEXT,
)
from pathlib import Path

import mc_frame_bindings as bindings

logger = logging.getLogger(__name__)
_RETRYABLE_SQLITE_CODES = {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
                           sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_PROTOCOL}


class ExecutionPageMixin:
    def _ensure_execution_detail_page(self, step: dict, step_idx: int) -> Path:
        created = int(float(step.get("created_at") or bindings.app.time.time()))
        existing_path_raw = str(step.get("detail_page_path") or "").strip()
        page_path = None
        if existing_path_raw:
            try:
                existing_path = Path(existing_path_raw)
                if existing_path.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                    page_path = existing_path
            except Exception:
                page_path = None
        if page_path is None:
            file_name = f"execution_{created}_{step_idx}_{uuid.uuid4().hex[:8]}.html"
            page_path = self.detail_pages_dir / file_name
            if existing_path_raw:
                try:
                    old_path = Path(existing_path_raw)
                    if old_path.exists() and old_path.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                        old_path.unlink(missing_ok=True)
                except Exception:
                    pass
        title = str(
            step.get("list_text")
            or step.get("title")
            or step.get("step")
            or step.get("message")
            or step.get("event_type")
            or f"执行步骤 {step_idx + 1}"
        ).strip()
        detail_text = str(
            step.get("detail_text")
            or step.get("message")
            or step.get("step")
            or step.get("title")
            or step.get("text")
            or step.get("content")
            or step.get("description")
            or ""
        )
        detail_html = escape(detail_text).replace("\r\n", "\n").replace("\r", "\n")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>执行过程详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            ".meta{color:#475569;margin-bottom:12px;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;white-space:pre-wrap;word-break:break-word;}"
            "</style></head><body>"
            "<h2>执行过程详情</h2>"
            f"<div class='meta'>{escape(title)}</div>"
            f"<pre>{detail_html}</pre>"
            "</body></html>"
        )
        page_path.write_text(html, encoding="utf-8")
        step["detail_page_path"] = str(page_path)
        return page_path

    def _reset_execution_visible_row_limit(self) -> None:
        self._invalidate_execution_scan()
        self.execution_visible_row_limit = EXECUTION_LIST_DEFAULT_VISIBLE_ROWS

    def _show_more_execution_rows(self) -> None:
        current_limit = int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0)
        self.execution_visible_row_limit = max(EXECUTION_LIST_DEFAULT_VISIBLE_ROWS, current_limit) + EXECUTION_LIST_EXPAND_ROWS
        self._render_execution_list()

    def _detail_panel_mode(self) -> str:
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        if self.view_mode == "history":
            viewed_chat = self._find_archived_chat(self.view_history_id)
            if isinstance(viewed_chat, dict):
                state = viewed_chat
        mode = str((state or {}).get("detail_panel_mode") or "").strip()
        return "execution" if mode == "execution" else "answers"

    def _current_detail_tab_target(self):
        if self._detail_panel_mode() == "execution" and hasattr(self, "execution_list"):
            return self.execution_list
        return self.answer_list if hasattr(self, "answer_list") else None

    def _execution_scan_key(self) -> tuple:
        state = (self._find_archived_chat(self.view_history_id) if self.view_mode == "history"
                 else getattr(self, "_current_chat_state", None))
        steps = state.get("execution_steps") if isinstance(state, dict) else None
        return (self._visible_execution_chat_id(), self.view_mode,
                self._active_turn_index_value() if self.view_mode == "active" else None,
                max(EXECUTION_LIST_DEFAULT_VISIBLE_ROWS, int(getattr(self, "execution_visible_row_limit", 100))),
                int(getattr(self, "_execution_scan_generation", 0)),
                id(state), id(steps), len(steps) if isinstance(steps, list) else 0,
                self._safe_int((state or {}).get("revision"), 1))

    def _invalidate_execution_scan(self) -> None:
        self._execution_latest_intent = None
        self._execution_scan_generation = int(getattr(self, "_execution_scan_generation", 0)) + 1
        self._execution_scan_result = None
        pending = getattr(self, "_execution_scan_pending", None)
        if pending:
            pending[1].set()
        self._execution_scan_pending = None

    def _merge_execution_page(self, persisted: list, memory: list) -> list:
        # Storage positions disambiguate repeated provider item IDs. Matching
        # by provider ID is only a compatibility fallback for unindexed tails.
        merged = list(persisted)
        matches = {}
        legacy_matches = {}
        needs_legacy = any(isinstance(item, dict) and not item.get("_execution_uid")
                           and "_store_step_index" not in item for item in memory)
        used = set()
        for index, item in enumerate(merged):
            matches.setdefault(self._execution_merge_identity(item), []).append(index)
            if needs_legacy and isinstance(item, dict) and not item.get("_execution_uid"):
                legacy_matches.setdefault(self._execution_legacy_key(item), []).append(index)
        for item in memory:
            indices = [i for i in matches.get(self._execution_merge_identity(item), []) if i not in used]
            if not indices and isinstance(item, dict) and "_store_step_index" not in item and not item.get("_execution_uid"):
                indices = [i for i in legacy_matches.get(self._execution_legacy_key(item), []) if i not in used]
            if indices:
                index = indices.pop(0)
                used.add(index)
                replacement = dict(item) if isinstance(item, dict) else item
                if isinstance(replacement, dict) and "_store_step_index" in merged[index]:
                    replacement["_store_step_index"] = merged[index]["_store_step_index"]
                merged[index] = replacement
            else:
                merged.append(item)
        return [item for _, item in sorted(enumerate(merged),
                key=lambda pair: self._execution_merge_sort_key(pair[1], pair[0]))
                if self._should_show_execution_step(item)]

    def _execution_legacy_key(self, item) -> tuple:
        if not isinstance(item, dict):
            return ("value", str(item or ""))
        provider_id = str(item.get("id") or item.get("event_id") or item.get("item_id") or "")
        return ("content", provider_id, item.get("turn_idx"), item.get("created_at") or item.get("ts"),
                item.get("display_kind") or "", item.get("event_type") or "",
                self._normalize_execution_text_for_compare(self._execution_step_detail_text(item)))

    def _finish_execution_scan(self, key, stop, rows, error) -> None:
        if stop.is_set() or not self._is_ui_alive() or key != self._execution_scan_key():
            return
        self._execution_scan_pending = None
        self._execution_list_dirty = True
        if error is not None:
            if self._execution_list_visible_for_updates():
                self._schedule_idle_ui_refresh()
            return
        rows = rows[-(key[3] + 1):]
        self._execution_scan_result = (key, rows)
        self._execution_known_tail = ((key[0], key[1], key[2], key[-1]), rows[-(key[3] + 1):])
        if self._execution_list_visible_for_updates() and not self._navigation_quiet_active():
            self._rebuild_execution_list_from_state()
        elif self._execution_list_visible_for_updates():
            self._schedule_idle_ui_refresh()

    def _current_execution_steps_for_render(self) -> tuple[int, list]:
        key = self._execution_scan_key()
        cached = getattr(self, "_execution_scan_result", None)
        if cached and cached[0] == key:
            return len(cached[1]), cached[1]
        # The first frame uses only the current owner's bounded known tail.
        # Freshness changes never discard valid rows of the same revision.
        state = (self._find_archived_chat(self.view_history_id) if self.view_mode == "history"
                 else getattr(self, "_current_chat_state", None))
        memory = (state or {}).get("execution_steps", [])
        limit = key[3]
        memory = list(memory[-(limit + 1):]) if isinstance(memory, list) else []
        if key[1] == "active":
            memory = [item for item in memory if not isinstance(item, dict)
                      or "turn_idx" not in item or self._safe_int(item.get("turn_idx"), -1) == key[2]]
        owner = (key[0], key[1], key[2], key[-1])
        known = getattr(self, "_execution_known_tail", None)
        known_rows = known[1] if known and known[0] == owner else []
        memory_ids = {self._execution_merge_identity(item) for item in memory}
        if all(self._execution_merge_identity(item) in memory_ids for item in known_rows):
            rows = [item for item in memory if self._should_show_execution_step(item)]
        else:
            rows = self._merge_execution_page(known_rows, memory)
        self._execution_known_tail = (owner, rows[-(limit + 1):])
        self._prepare_execution_scan()
        if (not rows and key[1] == "history"
                and not isinstance((state or {}).get("execution_steps"), list)
                and getattr(self, "_execution_scan_pending", None)):
            raise ExecutionPagePending()
        return len(rows), rows

    def _current_execution_steps(self) -> list:
        if self.view_mode == "history":
            chat = self._hydrate_chat_from_store(self._find_archived_chat(self.view_history_id), include_execution_steps=False)
            if isinstance(chat, dict):
                steps = chat.get("execution_steps")
                if isinstance(steps, list):
                    return steps
            return []
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        steps = state.get("execution_steps")
        if not isinstance(steps, list):
            return []
        if self.view_mode == "active" and any(isinstance(step, dict) and "turn_idx" in step for step in steps):
            active_idx = self._active_turn_index_value()
            return [
                step
                for step in steps
                if not isinstance(step, dict)
                or ("turn_idx" not in step)
                or self._safe_int(step.get("turn_idx"), -1) == active_idx
            ]
        return steps

    def _execution_merge_identity(self, item) -> tuple:
        if not isinstance(item, dict):
            return ("value", self._normalize_execution_text_for_compare(str(item or "")))
        if item.get("logical_key"):
            return ("logical", item["logical_key"])
        if item.get("_execution_uid"):
            return ("uid", item["_execution_uid"])
        if "_store_step_index" in item:
            return ("store", item["_store_step_index"])
        stable_id = str(item.get("id") or item.get("event_id") or item.get("item_id") or "").strip()
        if stable_id:
            return ("id", stable_id)
        return (
            "content",
            self._safe_int(item.get("turn_idx"), -1),
            str(item.get("created_at") or item.get("ts") or "").strip(),
            str(item.get("display_kind") or item.get("event_type") or "").strip(),
            self._normalize_execution_text_for_compare(self._execution_step_detail_text(item)),
        )

    @staticmethod
    def _execution_merge_sort_key(item, fallback_index: int) -> tuple:
        if isinstance(item, dict):
            sequence = bindings.app.ChatFrame._safe_int(item.get("execution_sequence"), 0)
            if sequence > 0:
                return (0, bindings.app.ChatFrame._safe_int(item.get("revision"), 0), sequence, fallback_index)
            timestamp = _execution_timestamp(item)
            if timestamp is not None:
                return (1, timestamp, fallback_index)
        return (2, fallback_index, 0)

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _active_turn_index_value(self) -> int:
        return self._safe_int(getattr(self, "active_turn_idx", -1), -1)

    def _visible_execution_chat_id(self) -> str:
        if self.view_mode == "history":
            viewed_id = str(self.view_history_id or "").strip()
            if viewed_id:
                return viewed_id
        return str(self.active_chat_id or self.current_chat_id or "").strip()

    def _flush_relevant_execution_deltas_for_switch(self) -> bool:
        flushed = False
        visible_chat_id = self._visible_execution_chat_id()
        if visible_chat_id:
            flushed = self._flush_all_execution_deltas_for_chat(visible_chat_id) or flushed
        active_chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if active_chat_id and active_chat_id != visible_chat_id:
            flushed = self._flush_all_execution_deltas_for_chat(active_chat_id) or flushed
        return flushed

    def _chat_state_for_execution_steps(self, chat_id: str) -> dict | None:
        resolved_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            if not isinstance(getattr(self, "_current_chat_state", None), dict):
                self._current_chat_state = {}
            return self._current_chat_state
        chat = self._hydrate_chat_from_store(self._find_archived_chat(resolved_chat_id))
        return chat if isinstance(chat, dict) else None

    @staticmethod
    def _codex_execution_step_fallback(event: CodexEvent) -> str:
        for key in ("status", "phase", "text"):
            value = str(getattr(event, key, "") or "").strip()
            if value:
                return value
        return "步骤"

    @staticmethod
    def _codex_item_paths_text(item: dict) -> str:
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        paths = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or change.get("filePath") or change.get("targetPath") or "").strip()
            if path and path not in paths:
                paths.append(path)
        if paths:
            return ", ".join(paths[:3]) + (" 等" if len(paths) > 3 else "")
        for key in ("path", "filePath", "targetPath"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""

    def _codex_item_summary_text(self, event: CodexEvent) -> str:
        item = event.data if isinstance(event.data, dict) else {}
        item_type = str(item.get("type") or getattr(event, "status", "") or "").strip()
        title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        command = str(item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        text = str(item.get("text") or getattr(event, "text", "") or "").strip()
        phase = str(item.get("phase") or getattr(event, "phase", "") or "").strip()
        exit_code = item.get("exitCode")
        paths_text = self._codex_item_paths_text(item)

        if item_type == "commandExecution":
            parts = []
            if title:
                parts.append(title)
            if command:
                parts.append(f"命令：{command}")
            if exit_code not in (None, ""):
                parts.append(f"退出码：{exit_code}")
            if text and text not in {title, command}:
                parts.append(text)
            return " | ".join(parts)

        if item_type == "fileChange":
            if paths_text:
                return f"修改文件 {paths_text}"
            if title:
                return title

        if item_type == "agentMessage":
            if phase and text:
                return f"{phase}：{text}"
            if text:
                return text

        parts = []
        if title:
            parts.append(title)
        if paths_text and paths_text not in title:
            parts.append(paths_text)
        if text and text not in {title, paths_text}:
            parts.append(text)
        if phase and phase not in text:
            parts.append(f"阶段：{phase}")
        return " | ".join([part for part in parts if part])

    def _execution_display_kind(self, event: CodexEvent) -> str:
        display_kind = str(getattr(event, "display_kind", "") or "").strip()
        if display_kind:
            return display_kind
        event_type = str(getattr(event, "type", "") or "").strip()
        subtype = str(getattr(event, "subtype", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        item = event.data if isinstance(event.data, dict) else {}
        item_type = str(item.get("type") or "").strip()
        if event_type == "plan_updated":
            return "plan"
        if event_type == "stderr":
            return "error"
        if event_type == "diff_updated":
            return "diff"
        if event_type == "server_request":
            return "user_input"
        if event_type in ("turn_started", "turn_completed"):
            return "status"
        if subtype == "commandExecution" or status == "commandExecution" or item_type == "commandExecution":
            return "command"
        if "command" in {subtype, status, item_type}:
            return "command"
        return ""

    def _execution_detail_text_from_event(self, event: CodexEvent) -> str:
        event_type = str(getattr(event, "type", "") or "").strip()
        text = str(getattr(event, "text", "") or "").strip()
        raw_text = str(getattr(event, "raw_text", "") or "").rstrip()
        title = str(getattr(event, "title", "") or "").strip()
        command = str(getattr(event, "command", "") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        subtype = str(getattr(event, "subtype", "") or "").strip()
        item = event.data if isinstance(event.data, dict) else {}
        if not title:
            title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        if not command:
            command = str(item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        if event_type == "diff_updated":
            return "已生成代码变更"
        if event_type == "turn_started":
            return raw_text or text or "开始处理本轮请求"
        if event_type == "turn_completed":
            return raw_text or text or "本轮处理结束"
        if event_type == "server_request":
            return raw_text or text or "等待用户输入"
        if event_type == "plan_updated":
            return raw_text or text or "计划更新"
        if event_type == "stderr":
            return raw_text or text or "错误输出"
        if event_type == "item_completed" and phase == "final_answer":
            return raw_text or text or "已生成最终回答"
        if raw_text:
            return raw_text
        if event_type in ("item_started", "item_completed"):
            prefix = "开始执行：" if event_type == "item_started" else "完成执行："
            summary = self._codex_item_summary_text(event)
            if command:
                header = title or summary or self._codex_execution_step_fallback(event)
                lines = [f"{prefix}{header}"]
                lines.append(f"命令：{command}")
                exit_code = getattr(event, "exit_code", None)
                if exit_code in (None, ""):
                    exit_code = item.get("exitCode")
                if exit_code not in (None, "") and event_type == "item_completed":
                    lines.append(f"退出码：{exit_code}")
                if text and text not in {header, command}:
                    lines.append(text)
                return "\n".join(line for line in lines if str(line or "").strip())
            if title and self._kimi_protocol_event(event):
                return f"{prefix}{title}"
            if summary:
                return f"{prefix}{summary}"
            fallback = self._codex_execution_step_fallback(event)
            return f"{prefix}{fallback}" if fallback else ""
        if text:
            return text
        if title:
            return title
        if command:
            return command
        for fallback in (phase, status, subtype):
            if fallback:
                return fallback
        return ""

    @staticmethod
    def _execution_list_text_from_detail(detail: str, kind: str) -> str:
        return execution_list_text_from_detail(detail, kind)

    @staticmethod
    def _execution_command_list_text(event_type: str, title: str, command: str, exit_code, fallback_text: str = "") -> str:
        return execution_command_list_text(event_type, title, command, exit_code, fallback_text)

    @staticmethod
    def _kimi_protocol_event(event: CodexEvent) -> bool:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        return str(data.get("adapter") or data.get("provider") or "").strip().lower() in {
            "kimi", "kimi_server", "kimi-server"
        }

    @staticmethod
    def _kimi_private_reasoning(data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        visibility = str(data.get("visibility") or data.get("reasoning_visibility") or "").strip().lower()
        return (
            data.get("disclosable") is False
            or data.get("private") is True
            or data.get("hidden") is True
            or data.get("non_disclosable") is True
            or visibility in {"private", "hidden", "non_disclosable", "non-disclosable"}
        )

    @staticmethod
    def _bounded_kimi_diagnostic(text: str, limit: int = 2000) -> str:
        value = bindings.app.ChatFrame._strip_ansi_control_sequences(str(text or ""))[: max(0, int(limit))]
        value = re.sub(r"(?i)(api[_-]?key|access[_-]?token|password|secret)(\s*[:=]\s*)\S+", r"\1\2<redacted>", value)
        value = re.sub(r"(?i)(--token\s+|[?&]token=|token\s*[:=]\s*)\S+", r"\1<redacted>", value)
        return value

    @staticmethod
    def _safe_kimi_source_detail(value, depth: int = 0):
        if depth >= 5:
            return "<truncated>"
        if isinstance(value, dict):
            return {
                str(key): bindings.app.ChatFrame._safe_kimi_source_detail(item, depth + 1)
                for key, item in list(value.items())[:100]
            }
        if isinstance(value, list):
            return [bindings.app.ChatFrame._safe_kimi_source_detail(item, depth + 1) for item in value[:50]]
        if isinstance(value, str):
            return bindings.app.ChatFrame._bounded_kimi_diagnostic(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return bindings.app.ChatFrame._bounded_kimi_diagnostic(str(value))

    @staticmethod
    def _execution_exit_code(event: CodexEvent) -> int | None:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        value = getattr(event, "exit_code", None)
        if value in (None, ""):
            value = data.get("exitCode")
        if value in (None, ""):
            value = tool.get("exitCode")
        if isinstance(value, bool):
            return None
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _kimi_execution_summary(event: CodexEvent) -> str:
        """Return the stable Chinese F1 label for a Kimi protocol event."""
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        if not bindings.app.ChatFrame._kimi_protocol_event(event):
            return ""
        event_type = str(getattr(event, "type", "") or "").strip()
        display_kind = str(getattr(event, "display_kind", "") or "").strip()
        source_kind = str(data.get("source_kind") or "").strip().lower()
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        status = str(getattr(event, "status", "") or tool.get("status") or "").strip().lower()
        exit_code = bindings.app.ChatFrame._execution_exit_code(event)
        failed = status in {"failed", "error", "interrupted", "cancelled", "canceled"} or exit_code not in (None, 0)
        completed = event_type in {"item_completed", "subagent_result"} or status in {"completed", "done", "success"}
        if display_kind in {"session", "unmapped", "compaction", "goal"}:
            return ""
        if source_kind in {"tool.progress", "shell.output"}:
            return ""
        if display_kind == "thinking":
            return "" if bindings.app.ChatFrame._kimi_private_reasoning(data) else bindings.app.ChatFrame._kimi_thinking_excerpt(event.raw_text or event.text)
        if display_kind == "commentary" and source_kind == "assistant.delta":
            return "" if bindings.app.ChatFrame._kimi_private_reasoning(data) else bindings.app.ChatFrame._kimi_thinking_excerpt(event.raw_text or event.text)
        if display_kind == "assistant":
            return ""
        if display_kind == "warning":
            return "执行警告"
        tool_name = " ".join(
            str(tool.get(key) or "") for key in ("name", "kind", "toolKind")
        ).lower()
        # Tool/action events never become F1 list rows — including failed or
        # waiting ones (the generic 执行失败/正在等待 labels below must not
        # resurrect them). Turn-level errors and waiting states keep their
        # rows because those display kinds are not tool kinds. Thinking
        # narrative rows come from public streams and REST reconciliation.
        if display_kind in {"command", "tool", "file", "diff", "search", "test", "skill", "agent"}:
            return ""
        if any(name in tool_name for name in ("search", "grep", "glob", "find", "read", "cat", "write", "edit", "patch", "shell", "bash", "command", "powershell")):
            return ""
        if status in {"retrying", "waiting", "awaiting", "awaiting_input", "awaiting_approval"}:
            return "正在等待"
        if status in {"failed", "error", "interrupted", "cancelled", "canceled"}:
            return "执行失败"
        if display_kind == "status":
            return "正在整理回答" if source_kind == "prompt.completed" else ""
        if display_kind == "step":
            return ""
        if display_kind in {"waiting", "user_input"}:
            return "等待用户输入"
        if display_kind == "error" or event_type == "error":
            return "执行失败"
        if event_type in {"item_started", "item_completed", "subagent_result"}:
            return "执行失败" if failed else ("已完成任务" if completed else "正在处理任务")
        return ""

    def _build_execution_entry(self, event: CodexEvent) -> dict | None:
        if not isinstance(event, CodexEvent):
            return None
        detail_text = self._execution_detail_text_from_event(event)
        display_kind = self._execution_display_kind(event)
        kimi_summary = self._kimi_execution_summary(event)
        item = event.data if isinstance(event.data, dict) else {}
        narrative = self._kimi_protocol_event(event) and (
            display_kind in {"thinking", "assistant"}
            or (display_kind == "commentary" and item.get("source_kind") == "assistant.delta")
        )
        private_reasoning = (display_kind == "thinking" or narrative) and self._kimi_private_reasoning(item)
        if private_reasoning:
            detail_text = ""
        elif self._kimi_protocol_event(event):
            original_raw = str(getattr(event, "raw_text", "") or "")
            detail_text = original_raw if original_raw else detail_text
            if not narrative:
                detail_text = self._bounded_kimi_diagnostic(detail_text)
        if self._kimi_protocol_event(event) and not kimi_summary and not private_reasoning:
            return None
        if not detail_text and not kimi_summary and not private_reasoning:
            return None
        event_type = str(getattr(event, "type", "") or "").strip()
        if display_kind == "error":
            detail_text = self._sanitize_execution_error_text(detail_text)
            if not detail_text:
                return None
        title = str(getattr(event, "title", "") or item.get("title") or item.get("name") or item.get("label") or "").strip()
        command = str(getattr(event, "command", "") or item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        exit_code = self._execution_exit_code(event)
        subtype = str(getattr(event, "subtype", "") or item.get("type") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        command_fallback = subtype or str(getattr(event, "status", "") or "").strip() or event_type
        if kimi_summary:
            list_text = kimi_summary
        elif event_type == "diff_updated":
            list_text = "已生成代码变更"
        elif event_type == "item_completed" and phase == "final_answer":
            list_text = "已生成最终回答"
        elif display_kind == "command":
            list_text = self._execution_command_list_text(event_type, title, command, exit_code, command_fallback)
        else:
            list_text = self._execution_list_text_from_detail(detail_text, display_kind)
        if private_reasoning:
            list_text = ""
        if (display_kind != "error" and str(getattr(event, "status", "") or "").lower() not in {"failed", "error"}
                and exit_code in (None, 0)
                and str(getattr(event, "text", "") or "").strip().lower() in {"not loaded", "notloaded"}
                and not title and not command):
            return None
        safe_raw_text = "" if private_reasoning else str(getattr(event, "raw_text", "") or "")
        safe_text = "" if private_reasoning else str(getattr(event, "text", "") or "")
        if not narrative:
            safe_raw_text = self._bounded_kimi_diagnostic(safe_raw_text)
            safe_text = self._bounded_kimi_diagnostic(safe_text)
        safe_source_detail = {} if private_reasoning else self._safe_kimi_source_detail(item)
        origin_timestamp = _finite_timestamp(getattr(event, "origin_timestamp", None))
        entry = {
            "event_type": event_type,
            "display_kind": display_kind,
            "subtype": subtype,
            "list_text": list_text,
            "detail_text": detail_text,
            "raw_text": safe_raw_text,
            "text": safe_text,
            "title": title,
            "command": command,
            "exit_code": exit_code,
            "phase": phase,
            "status": str(getattr(event, "status", "") or "").strip(),
            "thread_id": self._event_thread_id(event),
            "turn_id": self._event_turn_id(event),
            "item_id": str(getattr(event, "item_id", "") or "").strip(),
            "agent_id": str(item.get("agent_id") or item.get("agentId") or "").strip(),
            "source_kind": str(item.get("source_kind") or "").strip(),
            "source_detail": safe_source_detail,
            "operation_kind": str(item.get("operation_kind") or display_kind or "").strip(),
            "private_reasoning": bool(private_reasoning),
            "created_at": origin_timestamp if origin_timestamp is not None else bindings.app.time.time(),
            "provider": str(getattr(event, "provider", "") or item.get("adapter") or "codex").strip(),
            "provider_event_id": str(getattr(event, "event_id", "") or item.get("event_id") or item.get("eventId") or "").strip(),
            "fragment_id": str(getattr(event, "fragment_id", "") or item.get("fragment_id") or item.get("fragmentId") or "").strip(),
            "fragment_offset": getattr(event, "offset", None) if getattr(event, "offset", None) is not None else item.get("offset"),
            "session_id": str(getattr(event, "session_id", "") or self._event_thread_id(event)).strip(),
            "stream_id": str(getattr(event, "stream_id", "") or "").strip(),
            "tool_call_id": str(getattr(event, "tool_call_id", "") or "").strip(),
        }
        if kimi_summary:
            entry["kimi_summary"] = kimi_summary
        if narrative:
            entry["narrative_snapshot"] = True
            entry["kimi_step_id"] = str(item.get("step_id") or "")
            entry["kimi_step_number"] = item.get("step_number")
            entry["kimi_epoch"] = str(item.get("epoch") or "")
        turn_idx = self._event_data_turn_idx_value(event)
        if turn_idx >= 0:
            entry["turn_idx"] = turn_idx
        return entry

    def _append_execution_step_to_chat(self, chat_id: str, step_text: str, *, save_state: bool = True) -> bool:
        text = str(step_text or "").strip()
        if not text:
            return False
        return self._append_execution_entry_to_chat(chat_id, {"step": text}, save_state=save_state)

    def _execution_row_id(self, step_idx: int, step, occurrence: int = 0) -> str:
        chat_id = self._visible_execution_chat_id()
        state = self._visible_execution_chat_state() or {}
        revision = self._safe_int(state.get("revision"), 1)
        return execution_row_id(chat_id, revision, step, occurrence)

    def _execution_legacy_native_scope(self, step: dict) -> list:
        state = self._visible_execution_chat_state() or {}
        chat_id = self._visible_execution_chat_id()
        provider = str(step.get("provider") or step.get("adapter") or "legacy").replace("_server", "").strip().lower()
        turn_value = step.get("turn_id") or step.get("turnId")
        if turn_value is None or str(turn_value).strip() == "":
            turn_value = step.get("turn_idx")
        return [
            chat_id,
            self._safe_int(step.get("revision"), self._safe_int(state.get("revision"), 1)),
            "" if turn_value is None else str(turn_value).strip(),
            provider,
            str(step.get("agent_id") or step.get("agentId") or "").strip(),
            str(step.get("thread_id") or step.get("threadId") or step.get("session_id") or "").strip(),
            str(step.get("session_id") or step.get("thread_id") or "").strip(),
            str(step.get("event_id") or step.get("id") or step.get("item_id") or "").strip(),
        ]

    def _execution_legacy_record_fingerprint(self, step) -> str:
        if not isinstance(step, dict):
            payload = {"value_type": type(step).__name__, "value": str(step or "")}
        else:
            payload = {key: value for key, value in step.items() if key not in {
                "_store_step_index", "_execution_uid", "updated_at", "status",
            }}
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()

    def _execution_legacy_identity_bucket(self, step) -> tuple:
        if isinstance(step, dict):
            if "_store_step_index" in step:
                return ("store", self._visible_execution_chat_id(), step.get("_store_step_index"))
            native_id = str(step.get("event_id") or step.get("id") or step.get("item_id") or "").strip()
            if native_id and not any(step.get(key) for key in ("logical_key", "synthetic", "_execution_uid")):
                return ("native", *self._execution_legacy_native_scope(step))
        return ("record", self._execution_legacy_record_fingerprint(step))

    def _execution_separator_anchor_id(self, step_idx: int, step, occurrence: int = 0) -> str:
        chat_id = self._visible_execution_chat_id()
        if isinstance(step, dict) and "_store_step_index" in step and not any(
            step.get(key) for key in ("logical_key", "synthetic", "_execution_uid", "event_id", "id", "item_id")
        ):
            revision = self._safe_int(step.get("revision"), 1)
            stable = hashlib.sha256(
                f"legacy-store-record\0{chat_id}\0{revision}\0{step['_store_step_index']}".encode("utf-8")
            ).hexdigest()
            return f"execution:{chat_id}:legacy-store:{stable}"
        if isinstance(step, dict) and "_store_step_index" in step:
            without_position = dict(step)
            without_position.pop("_store_step_index", None)
            return self._execution_row_id(step_idx, without_position, occurrence)
        return self._execution_row_id(step_idx, step, occurrence)

    def _visible_execution_chat_state(self) -> dict | None:
        if self._detail_panel_mode() != "execution":
            return None
        if self.view_mode == "history":
            chat = self._find_archived_chat(self.view_history_id)
            return chat if isinstance(chat, dict) else None
        state = getattr(self, "_current_chat_state", None)
        return state if isinstance(state, dict) else None

    def _execution_step_targets_visible_chat_while_hidden(self, target_chat: dict) -> bool:
        if not isinstance(target_chat, dict):
            return False
        if self._detail_panel_mode() == "execution":
            return False
        if self.view_mode == "history":
            chat = self._find_archived_chat(self.view_history_id)
            return chat is target_chat
        return target_chat is getattr(self, "_current_chat_state", None)

    def _execution_entry_is_authoritative_and_visible(self, target_chat: dict, step_idx: int, step) -> bool:
        """Check that an execution fast path still belongs to the visible turn."""
        if self._visible_execution_chat_state() is not target_chat:
            return False
        try:
            index = int(step_idx)
        except (TypeError, ValueError):
            return False
        steps = target_chat.get("execution_steps") if isinstance(target_chat, dict) else None
        if not isinstance(steps, list) or not (0 <= index < len(steps)):
            return False
        canonical_step = steps[index]
        if step is not canonical_step and step != canonical_step:
            return False
        if self.view_mode == "active" and isinstance(canonical_step, dict) and "turn_idx" in canonical_step:
            if self._safe_int(canonical_step.get("turn_idx"), -1) != self._active_turn_index_value():
                return False
        return True

    def _request_execution_list_sync(self, target_chat: dict) -> bool:
        if self._visible_execution_chat_state() is not target_chat:
            if self._execution_step_targets_visible_chat_while_hidden(target_chat):
                self._mark_execution_list_dirty()
            return False
        self._invalidate_execution_scan()
        if self._background_ui_mutations_blocked():
            chat_id = self._visible_execution_chat_id()
            self._pending_execution_tail_appends.setdefault(chat_id, []).append((-1, None))
            self._execution_list_dirty = True
            return True
        if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
            self._execution_list_deferred_repaint = True
            return True
        self._rebuild_execution_list_from_state()
        return True

    def _append_visible_execution_entry(self, target_chat: dict, step_idx: int, step) -> bool:
        if not hasattr(self, "execution_list"):
            return False
        if not self._execution_entry_is_authoritative_and_visible(target_chat, step_idx, step):
            return False
        return self._request_execution_list_sync(target_chat)

    def _flush_pending_background_ui_updates(self) -> None:
        if not self._is_ui_alive():
            return
        if self._navigation_quiet_active():
            self._schedule_navigation_quiet_flush()
            return
        self._flush_pending_execution_tail_appends()
        if not bool(getattr(self, "_background_answer_list_dirty", False)):
            return
        if self.view_mode == "active":
            self._background_answer_list_dirty = False
            self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
            return
        if self.view_mode == "history":
            viewed_id = str(getattr(self, "view_history_id", "") or "").strip()
            dirty_ids = getattr(self, "_background_history_dirty_ids", set())
            if viewed_id and viewed_id in dirty_ids:
                self._background_answer_list_dirty = False
                try:
                    dirty_ids.discard(viewed_id)
                except Exception:
                    pass
                self._background_history_dirty_ids = dirty_ids
                self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")

    def _flush_pending_execution_tail_appends(self) -> None:
        chat_id = self._visible_execution_chat_id()
        if not chat_id:
            return
        pending_by_chat = getattr(self, "_pending_execution_tail_appends", {})
        pending = list(pending_by_chat.get(chat_id) or [])
        if not pending:
            return
        if not self._execution_list_visible_for_updates():
            self._execution_list_dirty = True
            self._prepare_execution_scan()
            return
        # Deferred entries have already been persisted in their owning chat.
        # Rebuild once from that authoritative owner rather than replaying
        # append operations that can duplicate or revive an old tail.
        self._rebuild_execution_list_from_state()

    def _flush_deferred_execution_list_updates(self) -> None:
        if not bool(getattr(self, "_execution_list_deferred_repaint", False)):
            return
        if self._navigation_quiet_active() or not self._execution_list_visible_for_updates():
            self._execution_list_dirty = True
            self._prepare_execution_scan()
            return
        # A batch can contain several repeated events.  Reconcile its final
        # visible state once rather than repainting the last incrementally
        # appended tail.
        if self._execution_list_visible_for_updates():
            self._rebuild_execution_list_from_state()

    @staticmethod
    def _execution_step_detail_text(step) -> str:
        return execution_step_detail_text(step)

    @staticmethod
    def _normalize_execution_text_for_compare(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @staticmethod
    def _execution_texts_are_near_duplicate(previous_text: str, next_text: str) -> bool:
        left = str(previous_text or "").strip()
        right = str(next_text or "").strip()
        if not left or not right:
            return False
        if left == right:
            return True
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if len(shorter) < 16:
            return False
        return longer.startswith(shorter) or longer.endswith(shorter)

    def _execution_entries_should_dedupe(self, previous_step, next_step) -> bool:
        if not isinstance(previous_step, dict) or not isinstance(next_step, dict):
            return False
        previous_key = str(previous_step.get("logical_key") or "")
        next_key = str(next_step.get("logical_key") or "")
        if previous_key or next_key:
            return bool(previous_key and previous_key == next_key)
        # Identity-less entries are deliberately never coalesced.  They may
        # remain as separate legacy facts, but prose similarity is not proof
        # that they represent one provider event.
        return False
        previous_kind = str(previous_step.get("display_kind") or "").strip()
        next_kind = str(next_step.get("display_kind") or "").strip()
        if (
            previous_step.get("kimi_summary")
            and previous_step.get("kimi_summary") == next_step.get("kimi_summary")
            and previous_step.get("turn_id") == next_step.get("turn_id")
            and previous_kind == next_kind
            and previous_step.get("agent_id") == next_step.get("agent_id")
            and previous_step.get("source_kind") == next_step.get("source_kind")
            and str(previous_step.get("item_id") or "").strip()
            and previous_step.get("item_id") == next_step.get("item_id")
        ):
            return True
        if previous_kind != "commentary" or next_kind != "commentary":
            return False
        previous_detail = self._normalize_execution_text_for_compare(self._execution_step_detail_text(previous_step))
        next_detail = self._normalize_execution_text_for_compare(self._execution_step_detail_text(next_step))
        if not previous_detail or not next_detail:
            return False
        return self._execution_texts_are_near_duplicate(previous_detail, next_detail)

    def _remote_execution_entry_payload(self, chat_id: str, entry: dict) -> dict:
        if not isinstance(entry, dict):
            entry = {}
        title = str(entry.get("list_text") or entry.get("step") or entry.get("text") or "").strip()
        detail = str(entry.get("detail_text") or entry.get("detail") or title).strip()
        if entry.get("private_reasoning"):
            title = detail = ""
        kind = str(entry.get("display_kind") or entry.get("event_type") or "info").strip() or "info"
        canonical_item_id = str(entry.get("canonical_item_id") or entry.get("event_id")
                                or entry.get("id") or entry.get("_execution_uid") or "").strip()
        mutation_body = json.dumps(
            [canonical_item_id, str(entry.get("status") or ""), title, detail],
            ensure_ascii=False, separators=(",", ":"),
        )
        event_id = "execution-mutation-" + hashlib.sha256(mutation_body.encode("utf-8")).hexdigest()
        if not event_id:
            raise ValueError("MISSING_EXECUTION_EVENT_ID")
        ts = _execution_timestamp(entry)
        payload = {
            "type": "execution_entry",
            "chat_id": str(chat_id or ""),
            "event_id": event_id,
            "canonical_item_id": canonical_item_id,
            "kind": kind,
            "title": title,
            "detail": detail,
        }
        if ts is not None:
            payload["ts"] = ts
        return payload

    def _append_execution_entry_to_chat(self, chat_id: str, entry: dict, *, save_state: bool = True,
                                        logical_positions: dict | None = None, store_only: bool = False,
                                        on_persisted=None, sync_visible: bool = True) -> bool:
        if not isinstance(entry, dict):
            return False
        self._invalidate_remote_state_cache()
        target_chat = self._chat_state_for_execution_steps(chat_id)
        if not isinstance(target_chat, dict):
            return False
        if "turn_idx" not in entry and target_chat is getattr(self, "_current_chat_state", None):
            active_idx = self._active_turn_index_value()
            if active_idx >= 0:
                entry = dict(entry)
                entry["turn_idx"] = active_idx
        current_revision = self._safe_int(target_chat.get("revision", 1), 1)
        supplied_revision = entry.get("revision")
        revision = self._safe_int(supplied_revision, current_revision)
        if supplied_revision is not None and revision != current_revision:
            return False
        provider = str(entry.get("provider") or ("kimi" if str(entry.get("source_kind") or "") else "codex")).strip()
        turn_identity = entry.get("turn_id")
        if turn_identity in (None, ""):
            turn_identity = entry.get("turn_idx")
        canonical_turn = "" if turn_identity is None else str(turn_identity).strip()
        native_id = str(entry.get("tool_call_id") or entry.get("item_id") or entry.get("stream_id")
                        or entry.get("provider_event_id") or "").strip()
        agent_id = str(entry.get("agent_id") or "").strip()
        if canonical_turn and native_id:
            thread_scope = str(entry.get("session_id") or entry.get("thread_id") or "").strip()
            if not thread_scope:
                return False
            scope = json.dumps([str(chat_id or "").strip(), revision, thread_scope, canonical_turn, provider, agent_id, native_id], ensure_ascii=False, separators=(",", ":"))
            logical_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
            logical_scope = {"chat_id": str(chat_id or "").strip(), "revision": revision,
                             "thread_id": thread_scope, "turn_id": canonical_turn, "provider": provider,
                             "agent_id": agent_id, "native_id": native_id}
            entry = dict(entry, logical_key=logical_key, logical_scope=logical_scope,
                         logical_scope_hash=logical_key, revision=revision, provider=provider,
                         canonical_item_id=f"execution-{logical_key}")
            stable_id = f"execution-{logical_key}"
            entry.setdefault("event_id", stable_id)
            entry.setdefault("_execution_uid", logical_key)
        if store_only:
            return self._persist_execution_step_or_queue(chat_id, entry)
        steps = target_chat.get("execution_steps")
        if not isinstance(steps, list):
            steps = []
            target_chat["execution_steps"] = steps
        logical_key = str(entry.get("logical_key") or "")
        if logical_key:
            if logical_positions is None:
                existing = enumerate(steps)
            elif logical_key in logical_positions:
                indexed_step = logical_positions[logical_key]
                try:
                    existing = ((steps.index(indexed_step), indexed_step),)
                except ValueError:
                    existing = enumerate(steps)
            else:
                existing = ()
            for index, previous in existing:
                if not isinstance(previous, dict) or str(previous.get("logical_key") or "") != logical_key:
                    continue
                updated = dict(entry)
                for key in ("event_id", "_execution_uid", "created_at", "ts", "execution_sequence", "revision"):
                    if previous.get(key) not in (None, ""):
                        updated[key] = previous[key]
                previous_detail = str(previous.get("detail_text") or "")
                incoming_detail = str(updated.get("detail_text") or "")
                if not updated.get("narrative_snapshot") and previous_detail and incoming_detail and not incoming_detail.startswith(previous_detail):
                    updated["detail_text"] = previous_detail + incoming_detail
                for history_key in ("source_detail_history", "diagnostic_history"):
                    combined = list(previous.get(history_key) or [])
                    for value in list(updated.get(history_key) or []):
                        if value not in combined:
                            combined.append(value)
                    if combined:
                        updated[history_key] = combined
                if previous.get("private_reasoning"):
                    updated["private_reasoning"] = True
                for keep_key in ("operation_kind", "source_detail"):
                    if not updated.get(keep_key) and previous.get(keep_key):
                        updated[keep_key] = copy.deepcopy(previous[keep_key])
                if updated.get("private_reasoning"):
                    updated.update(list_text="", kimi_summary="", detail_text="", raw_text="", text="",
                                   source_detail={}, source_detail_history=[], diagnostic_history=[])
                if updated == previous:
                    if on_persisted is not None:
                        return self._persist_execution_step_or_queue(chat_id, previous, broadcast=True,
                                                                     on_persisted=on_persisted)
                    return False
                steps[index] = copy.deepcopy(updated)
                resolved_chat_id = str(chat_id or target_chat.get("id") or "").strip()
                if resolved_chat_id:
                    if not self._persist_execution_step_or_queue(resolved_chat_id, steps[index], broadcast=True,
                                                                 on_persisted=on_persisted):
                        steps[index] = previous
                        return False
                    self._notify_kimi_rest_candidate(resolved_chat_id, steps, index, previous, steps[index])
                if sync_visible:
                    self._request_execution_list_sync(target_chat)
                if save_state:
                    self._defer_chat_state_save()
                return True
        if not logical_key and str(entry.get("event_type") or "") == "item_completed" and str(entry.get("item_id") or "").strip():
            compatible_indexes = []
            for index, previous in enumerate(steps):
                if not isinstance(previous, dict):
                    continue
                previous_type = str(previous.get("event_type") or "")
                previous_source = str(previous.get("source_kind") or "")
                if (
                    previous_type in {"item_started", "agent_message_delta"}
                    and previous_source.startswith(("tool.", "shell.", "subagent."))
                    and str(previous.get("item_id") or "").strip() == str(entry.get("item_id") or "").strip()
                    and self._safe_int(previous.get("turn_idx"), -1) == self._safe_int(entry.get("turn_idx"), -1)
                    and str(previous.get("thread_id") or "") == str(entry.get("thread_id") or "")
                    and str(previous.get("turn_id") or "") == str(entry.get("turn_id") or "")
                ):
                    compatible_indexes.append(index)
            if compatible_indexes:
                index = compatible_indexes[0]
                previous = steps[index]
                entry = dict(entry)
                for identity_key in ("id", "event_id", "_execution_uid"):
                    if previous.get(identity_key):
                        entry[identity_key] = previous[identity_key]
                previous_timestamp = _execution_timestamp(previous)
                if previous_timestamp is not None:
                    entry["created_at"] = previous_timestamp
                previous_title = str(previous.get("title") or "").strip()
                previous_command = str(previous.get("command") or "").strip()
                if not str(entry.get("title") or "").strip() and previous_title:
                    entry["title"] = previous_title
                if not str(entry.get("command") or "").strip() and previous_command:
                    entry["command"] = previous_command
                previous_source_detail = previous.get("source_detail")
                if isinstance(previous_source_detail, dict) and previous_source_detail:
                    entry["source_detail_history"] = [copy.deepcopy(previous_source_detail)]
                if previous.get("diagnostic_history"):
                    entry["diagnostic_history"] = copy.deepcopy(previous["diagnostic_history"])
                operation_kind = str(previous.get("operation_kind") or previous.get("display_kind") or "").strip()
                if operation_kind:
                    entry["operation_kind"] = operation_kind
                if entry.get("kimi_summary"):
                    # Preserve the finite structured summary and merge permitted
                    # diagnostics only into detail. Completion must not rebuild
                    # a row from a title, path, command, output, or exit code.
                    details = []
                    previous_detail = str(previous.get("detail_text") or "")
                    current_detail = str(entry.get("detail_text") or "")
                    if previous_detail:
                        details.append(previous_detail)
                    if current_detail and current_detail not in details:
                        details.append(current_detail)
                    if entry.get("exit_code") not in (None, ""):
                        exit_detail = f"退出码：{entry['exit_code']}"
                        if exit_detail not in details:
                            details.append(exit_detail)
                    entry["detail_text"] = self._bounded_kimi_diagnostic("\n".join(details))
                    failure_prefix = "执行失败" if str(entry.get("status") or "").lower() in {
                        "failed", "error", "interrupted", "cancelled", "canceled"
                    } or entry.get("exit_code") not in (None, "", 0) else "已完成"
                    action_by_kind = {
                        "search": "搜索", "file": "读取文件", "diff": "修改文件",
                        "test": "测试", "command": "命令", "agent": "子任务",
                        "skill": "工具", "task": "任务", "tool": "命令",
                    }
                    action = action_by_kind.get(operation_kind, "任务")
                    entry["list_text"] = f"{failure_prefix}{action}"
                    entry["kimi_summary"] = entry["list_text"]
                    entry["display_kind"] = str(previous.get("display_kind") or entry.get("display_kind") or "")
                for remove_index in reversed(compatible_indexes):
                    del steps[remove_index]
                steps.insert(index, copy.deepcopy(entry))
                resolved_chat_id = str(chat_id or target_chat.get("id") or "").strip()
                if resolved_chat_id:
                    self._persist_execution_step_or_queue(resolved_chat_id, steps[index], broadcast=True)
                self._request_execution_list_sync(target_chat)
                if save_state:
                    self._defer_chat_state_save()
                return True
        if (
            not logical_key
            and
            str(entry.get("event_type") or "") == "item_completed"
            and str(entry.get("source_kind") or "").startswith(("tool.", "shell.", "subagent."))
            and str(entry.get("item_id") or "").strip()
        ):
            entry = dict(entry)
            completion_uid = uuid.uuid4().hex
            entry["_execution_uid"] = completion_uid
            entry["event_id"] = f"execution-{completion_uid}"
        if steps and self._execution_entries_should_dedupe(steps[-1], entry):
            return False
        if not any(entry.get(key) for key in ("id", "event_id", "item_id")):
            entry = dict(entry, id=uuid.uuid4().hex)
        if "created_at" not in entry and "ts" not in entry:
            # Locally-created live entries have an authoritative creation
            # moment. Preserve explicit null/malformed remote timestamps as
            # unknown instead of silently replacing those protocol values.
            entry = dict(entry, ts=bindings.app.time.time())
        if not entry.get("_execution_uid"):
            entry = dict(entry, _execution_uid=uuid.uuid4().hex)
        steps.append(copy.deepcopy(entry))
        resolved_chat_id = str(chat_id or target_chat.get("id") or "").strip()
        if resolved_chat_id:
            if not self._persist_execution_step_or_queue(resolved_chat_id, steps[-1], broadcast=True,
                                                         on_persisted=on_persisted):
                steps.pop()
                return False
            self._notify_kimi_rest_candidate(resolved_chat_id, steps, len(steps) - 1, None, steps[-1])
        self._prune_cached_execution_steps_for_turn(target_chat, steps[-1])
        steps = target_chat.get("execution_steps")
        if not isinstance(steps, list):
            steps = []
            target_chat["execution_steps"] = steps
        appended_visible = self._append_visible_execution_entry(target_chat, len(steps) - 1, steps[-1])
        if not appended_visible and self._execution_step_targets_visible_chat_while_hidden(target_chat):
            self._mark_execution_list_dirty()
        if save_state:
            self._defer_chat_state_save()
        return True

    def _persist_execution_step_or_queue(self, chat_id: str, step: dict, *, broadcast: bool = False,
                                         on_persisted=None) -> bool:
        if not chat_id or not isinstance(step, dict):
            return False
        if not getattr(self, "_chat_store_enabled", False) or getattr(self, "chat_store", None) is None:
            if broadcast:
                self._broadcast_remote_event(self._remote_execution_entry_payload(chat_id, step))
            if on_persisted is not None:
                on_persisted(True)
            return True
        if step.get("provider") == "kimi" and step.get("logical_key"):
            self._queue_kimi_execution_write(
                chat_id, partial(self._persist_kimi_execution_step, self.chat_store, chat_id, copy.deepcopy(step), broadcast),
                on_persisted,
            )
            return True
        if str(step.get("logical_key") or "").strip():
            try:
                self._persist_execution_step(self.chat_store, chat_id, step)
            except Exception:
                return False
        elif int(getattr(self, "_codex_ui_batch_depth", 0) or 0) <= 0:
            self._persist_execution_step(self.chat_store, chat_id, step)
        else:
            self._queue_execution_step_persist(chat_id, step)
        if broadcast:
            self._broadcast_remote_event(self._remote_execution_entry_payload(chat_id, step))
        return True

    def _persist_kimi_execution_step(self, store, chat_id: str, step: dict, broadcast: bool) -> bool:
        if step["logical_key"] in self._kimi_execution_private_keys:
            step.update(private_reasoning=True, list_text="", detail_text="", kimi_summary="", raw_text="", text="",
                        source_detail={}, source_detail_history=[], diagnostic_history=[])
        persisted = self._persist_execution_step(store, chat_id, step)
        if broadcast and persisted:
            payload = self._remote_execution_entry_payload(chat_id, persisted)
            payload["_source_revision"] = step["revision"]
            self._broadcast_remote_event(payload, retry_on_failure=False)
        return bool(persisted)

    @staticmethod
    def _persist_execution_step(store, chat_id: str, step: dict) -> dict | None:
        if str(step.get("logical_key") or "").strip():
            upsert = getattr(store, "upsert_execution_step", None)
            if not callable(upsert):
                raise RuntimeError("EXECUTION_UPSERT_UNAVAILABLE")
            return upsert(chat_id, step)
        replace_lifecycle = getattr(store, "replace_execution_lifecycle_step", None)
        if str(step.get("event_type") or "") == "item_completed" and callable(replace_lifecycle):
            if replace_lifecycle(chat_id, step):
                return
        update_identity = getattr(store, "update_execution_step_by_identity", None)
        if (str(step.get("logical_key") or "").strip() or str(step.get("item_id") or "").strip()) and callable(update_identity) and update_identity(chat_id, step):
            return
        store.append_execution_step(chat_id, step)

    def _queue_execution_step_persist(self, chat_id: str, step: dict) -> None:
        with self._execution_step_persist_lock:
            self._pending_execution_step_persists.append((str(chat_id or ""), copy.deepcopy(step)))
            # The close path drains this queue synchronously after producer
            # services have stopped. Starting another daemon here would let
            # SQLite work escape the lifetime of the frame (and, in tests,
            # the wx.App itself).
            if bool(getattr(self, "_closing", False)):
                return
            if self._execution_step_persist_scheduled or self._execution_step_persist_worker_running:
                return
            self._execution_step_persist_scheduled = True
        if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
            return
        self._start_execution_step_persist_worker()

    def _queue_kimi_execution_write(self, chat_id, write, on_result=None) -> None:
        self._kimi_execution_write_counts[chat_id] = self._kimi_execution_write_counts.get(chat_id, 0) + 1
        with self._execution_step_persist_lock:
            self._pending_execution_step_persists.append((chat_id, write, on_result))
        if not self._codex_ui_batch_depth:
            self._start_execution_step_persist_worker()

    def _drain_kimi_execution_write_results(self) -> None:
        if getattr(self, "_close_complete", False):
            return
        with self._execution_step_persist_lock:
            results = self._kimi_execution_write_results
            batch = [results.popleft() for _ in range(min(len(results), 16))]
            has_more = bool(results)
            self._kimi_execution_write_result_scheduled = has_more
        for chat_id, callback, result in batch:
            try:
                if callback is not None:
                    callback(result)
            except Exception as exc:
                logger.warning("Kimi execution result could not be applied for chat %s (%s)", chat_id, type(exc).__name__)
            count = self._kimi_execution_write_counts[chat_id] - 1
            if count:
                self._kimi_execution_write_counts[chat_id] = count
            else:
                self._kimi_execution_write_counts.pop(chat_id)
                self._resume_kimi_execution_boundaries(chat_id)
        if has_more:
            self._call_after_if_alive(self._drain_kimi_execution_write_results)
        if not self._kimi_execution_write_counts:
            if self._chat_state_flush_dirty and not self._chat_state_flush_scheduled:
                self._defer_chat_state_save()
            if self._codex_background_flush_dirty and not self._codex_background_flush_scheduled:
                self._defer_codex_state_save()
        if batch and getattr(self, "_close_started", False):
            self._schedule_close_progress()

    def _start_execution_step_persist_worker(self) -> None:
        with self._execution_step_persist_lock:
            self._execution_step_persist_scheduled = False
            if (
                (bool(getattr(self, "_closing", False)) and not getattr(self, "_kimi_execution_write_counts", None)
                 and not getattr(self, "_close_started", False))
                or self._execution_step_persist_worker_running
                or not self._pending_execution_step_persists
            ):
                return
            self._execution_step_persist_worker_running = True
            worker = bindings.app.threading.Thread(target=self._execution_step_persist_worker, daemon=True)
            self._execution_step_persist_thread = worker
            # Start while holding the lock so shutdown can never observe a
            # registered thread that has not started yet.
            worker.start()

    def _execution_step_persist_worker(self) -> None:
        with self._execution_step_persist_lock:
            current_worker = self._execution_step_persist_thread
        try:
            while True:
                with self._execution_step_persist_lock:
                    batch = list(self._pending_execution_step_persists)
                    self._pending_execution_step_persists.clear()
                    if not batch:
                        return
                store = getattr(self, "chat_store", None)
                if store is None:
                    continue
                for work in batch:
                    if len(work) == 3:
                        chat_id, write, callback = work
                        result = None
                        # Retries run before later writes. Fragments and
                        # logical projection upserts are idempotent.
                        delay = 0.05
                        attempt = 0
                        while True:
                            attempt += 1
                            try:
                                result = write()
                                break
                            except (sqlite3.OperationalError, OSError) as exc:
                                code = getattr(exc, "sqlite_errorcode", None)
                                if code is not None and (code & 0xff) not in _RETRYABLE_SQLITE_CODES:
                                    logger.warning("Kimi execution write rejected for chat %s (SQLite code %d)", chat_id, code)
                                    break
                                if attempt <= 4 or attempt % 30 == 0:
                                    logger.warning("Retrying failed Kimi execution write for chat %s (attempt %d, %s)",
                                                   chat_id, attempt, type(exc).__name__)
                                bindings.app.time.sleep(delay)
                                delay = min(2.0, delay * 2)
                            except Exception as exc:
                                logger.warning("Kimi execution write rejected for chat %s (%s)", chat_id, type(exc).__name__)
                                break
                        with self._execution_step_persist_lock:
                            self._kimi_execution_write_results.append((chat_id, callback, result))
                            schedule = not self._kimi_execution_write_result_scheduled
                            self._kimi_execution_write_result_scheduled = True
                        if schedule:
                            self._call_after_if_alive(self._drain_kimi_execution_write_results)
                        continue
                    chat_id, step = work
                    try:
                        self._persist_execution_step(store, chat_id, step)
                    except Exception:
                        continue
        finally:
            restart = False
            with self._execution_step_persist_lock:
                if self._execution_step_persist_thread is current_worker:
                    self._execution_step_persist_thread = None
                    self._execution_step_persist_worker_running = False
                    restart = bool(
                        self._pending_execution_step_persists
                        and (not bool(getattr(self, "_closing", False)) or getattr(self, "_kimi_execution_write_counts", None)
                             or getattr(self, "_close_started", False))
                    )
                    if restart:
                        self._execution_step_persist_scheduled = True
            if restart:
                self._start_execution_step_persist_worker()
            if getattr(self, "_close_started", False):
                self._schedule_close_progress()

    def _flush_execution_step_persists_sync(self, *, worker_timeout: float = 5.0) -> bool:
        if getattr(self, "_kimi_execution_write_counts", None):
            deadline = bindings.app.time.monotonic() + max(0.0, float(worker_timeout))
            while self._kimi_execution_write_counts:
                self._start_execution_step_persist_worker()
                with self._execution_step_persist_lock:
                    worker = self._execution_step_persist_thread
                if worker is not None:
                    worker.join(timeout=max(0.0, deadline - bindings.app.time.monotonic()))
                    if worker.is_alive():
                        return False
                self._drain_kimi_execution_write_results()
                if self._kimi_execution_write_counts and bindings.app.time.monotonic() >= deadline:
                    return False
        with self._execution_step_persist_lock:
            worker = self._execution_step_persist_thread
        if worker is not None and worker is not bindings.app.threading.current_thread():
            worker.join(timeout=max(0.0, float(worker_timeout)))
            if bool(getattr(worker, "is_alive", lambda: False)()):
                # Do not drain or release the backing store while the worker
                # can still be inside SQLite. The close path retries later.
                return False
        with self._execution_step_persist_lock:
            batch = list(self._pending_execution_step_persists)
            self._pending_execution_step_persists.clear()
            self._execution_step_persist_scheduled = False
        store = getattr(self, "chat_store", None)
        if store is None:
            return True
        for chat_id, step in batch:
            try:
                self._persist_execution_step(store, chat_id, step)
            except Exception:
                continue
        return True

    def _prune_cached_execution_steps_for_turn(self, chat: dict, latest_step: dict) -> None:
        if not isinstance(chat, dict) or not isinstance(latest_step, dict):
            return
        turn_idx = latest_step.get("turn_idx")
        if turn_idx is None:
            return
        steps = chat.get("execution_steps")
        if not isinstance(steps, list):
            return
        store = getattr(self, "chat_store", None)
        limit = getattr(store, "max_execution_steps_per_turn", 500)
        try:
            limit = int(limit)
        except Exception:
            limit = 500
        if limit <= 0:
            return
        matching_indexes = [
            idx
            for idx, step in enumerate(steps)
            if isinstance(step, dict) and self._safe_int(step.get("turn_idx"), -1) == self._safe_int(turn_idx, -2)
        ]
        overflow = len(matching_indexes) - limit
        if overflow <= 0:
            return
        chat_id = str(chat.get("id") or "")
        for idx in reversed(matching_indexes[:overflow]):
            previous = steps.pop(idx)
            self._notify_kimi_rest_candidate(chat_id, steps, idx, previous, None)

    def _notify_kimi_rest_candidate(self, chat_id, steps, position, previous, step) -> None:
        for index in self._kimi_rest_candidate_updates.get(chat_id, ()):
            index.replace(steps, position, previous, step)

    def _buffer_execution_delta(self, chat_id: str, event: CodexEvent) -> None:
        if not isinstance(event, CodexEvent):
            return
        display_kind = str(getattr(event, "display_kind", "") or "").strip()
        event_data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        if display_kind == "assistant" and not (self._kimi_protocol_event(event) and event_data.get("step_id")):
            return
        agent_id = str(event_data.get("agent_id") or event_data.get("agentId") or "").strip()
        source_kind = str(event_data.get("source_kind") or "").strip().split(".", 1)[0]
        native_id = str(getattr(event, "tool_call_id", "") or getattr(event, "item_id", "")
                        or getattr(event, "stream_id", "") or getattr(event, "event_id", "")
                        or event_data.get("event_id") or event_data.get("eventId") or "").strip()
        if not native_id:
            return
        target_chat = self._chat_state_for_execution_steps(chat_id)
        if not isinstance(target_chat, dict):
            return
        revision = self._safe_int(getattr(event, "revision", None), self._safe_int(target_chat.get("revision", 1), 1))
        if getattr(event, "revision", None) is not None and revision != self._safe_int(target_chat.get("revision", 1), 1):
            return
        thread_scope = str(getattr(event, "session_id", "") or self._event_thread_id(event)).strip()
        turn_scope = self._event_turn_id(event)
        provider = str(getattr(event, "provider", "") or event_data.get("adapter") or "codex").replace("_server", "").strip()
        if not thread_scope or not turn_scope:
            return
        if event_data.get("lifecycle_stream") and self._kimi_event_is_authoritative(event):
            turns = target_chat.get("turns", [])
            turn_idx = self._kimi_event_turn_index(turns, event)
            if turn_idx < 0:
                return
            if (str(turns[turn_idx].get("request_status") or "") in {"done", "failed"}
                    and not self._kimi_private_reasoning(event_data)):
                return
        scope_values = [str(chat_id or "").strip(), revision, thread_scope, turn_scope, provider, agent_id, native_id]
        logical_key = hashlib.sha256(json.dumps(scope_values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        previous = next((step for step in target_chat.get("execution_steps", [])
                         if isinstance(step, dict) and step.get("logical_key") == logical_key), {})
        narrative_private = (self._kimi_protocol_event(event) and display_kind in {"thinking", "assistant", "commentary"}
                             and self._kimi_private_reasoning(event_data))
        completed_kind = "commentary" if display_kind == "assistant" else display_kind
        rest_private = provider == "kimi" and event_data.get("step_number") in self._kimi_rest_withdrawals.get(
            (chat_id, revision, thread_scope, turn_scope, completed_kind), {})
        completed = [step for step in target_chat.get("execution_steps", []) if isinstance(step, dict)
                     and step.get("rest_message_id") and event_data.get("step_number") is not None
                     and step.get("kimi_step_number") == event_data["step_number"]
                     and str(step.get("session_id") or "") == thread_scope and str(step.get("turn_id") or "") == turn_scope
                     and str(step.get("agent_id") or "main") == (agent_id or "main")
                     and step.get("revision", revision) == revision
                     and step.get("display_kind") == completed_kind]
        if narrative_private:
            answer_owner = self._kimi_answer_stream_scope(chat_id, event)[2]
            for step in completed or ([previous] if previous else []):
                self._redact_kimi_narrative(chat_id, step, owner_key=answer_owner)
            if completed:
                return
        if completed and not narrative_private:
            return
        if previous.get("rest_message_id") and not narrative_private:
            return
        key = (str(chat_id or ""), turn_scope, native_id, display_kind, agent_id, source_kind)
        if provider == "kimi":
            key = (*key, thread_scope, revision)
        offset = getattr(event, "offset", None) if getattr(event, "offset", None) is not None else event_data.get("offset")
        accepted_origin = _finite_timestamp(getattr(event, "origin_timestamp", None))
        state = self._execution_delta_buffer.setdefault(
            key,
            {"parts": [], "segments": {}, "conflicts": [], "fragment_ids": set(), "fragment_signatures": {}, "event": event,
             "last_event_at": 0.0, "start_offset": offset, "private_reasoning": False,
             "origin_timestamp": accepted_origin if accepted_origin is not None else bindings.app.time.time()},
        )
        if narrative_private or rest_private or (display_kind == "thinking" and self._kimi_private_reasoning(event_data)):
            state["private_reasoning"] = True
        if previous.get("private_reasoning"):
            state["private_reasoning"] = True
        effective_private = bool(state.get("private_reasoning"))
        if effective_private and not state.get("segments_redacted"):
            state["segments"] = {key: "\0" * len(value) for key, value in state.get("segments", {}).items()}
            state["parts"] = ["\0" * len(str(value or "")) for value in state.get("parts", [])]
            state["segments_redacted"] = True
        fragment = str(getattr(event, "text", "") or getattr(event, "raw_text", "") or "")
        stored_fragment = "\0" * len(fragment) if effective_private else fragment
        if effective_private:
            event = self._private_kimi_execution_event(event)
        fragment_id = str(getattr(event, "fragment_id", "") or event_data.get("fragment_id") or event_data.get("fragmentId") or "").strip()
        store = getattr(self, "chat_store", None) if getattr(self, "_chat_store_enabled", False) else None
        if store is not None and hasattr(store, "apply_execution_fragment"):
            scope = {"chat_id": str(chat_id or "").strip(), "revision": revision, "thread_id": thread_scope,
                     "turn_id": turn_scope, "provider": provider, "agent_id": agent_id, "native_id": native_id}
            projection_seed = self._build_execution_entry(event)
            if self._kimi_protocol_event(event) and display_kind in {"thinking", "assistant"} and not state.get("private_reasoning"):
                # Publish only contiguous assembly, never a fragment suffix.
                projection_seed = None
            if effective_private:
                projection_seed = dict(projection_seed or {}, list_text="", detail_text="", raw_text="", text="",
                                       source_detail={}, private_reasoning=True)
            if provider == "kimi":
                if effective_private:
                    self._kimi_execution_private_keys.add(logical_key)
                event = copy.deepcopy(event)
                event.revision = revision
                state["event"] = event
                self._index_kimi_execution_buffer(key, state, event)
                state["logical_scope"] = scope
                state["logical_key"] = logical_key
                state["pending_writes"] = state.get("pending_writes", 0) + 1
                owner = self._kimi_owner_for_event(event)
                if isinstance(owner, dict):
                    owner = dict(owner)
                self._queue_kimi_execution_write(chat_id, partial(
                    self._write_kimi_execution_fragment, store, chat_id, logical_key, dict(scope), fragment_id=fragment_id,
                    offset=offset, text=fragment, projection_seed=copy.deepcopy(projection_seed),
                ), partial(self._apply_kimi_execution_fragment_result, key, state, event, owner))
                return
            try:
                result = store.apply_execution_fragment(str(chat_id or "").strip(), logical_key, scope,
                                                        fragment_id=fragment_id, offset=offset, text=fragment,
                                                        projection_seed=projection_seed)
            except ValueError:
                return
            if not result.get("accepted") and not result.get("replay"):
                return
            state["parts"] = [str(result.get("assembled") or "")]
            state["pending_gap"] = bool(result.get("pending_gap"))
            state["logical_key"] = logical_key
            state["private_reasoning"] = bool(result.get("private_reasoning"))
            state["event"] = event
            state["last_event_at"] = bindings.app.time.time()
            return
        signature = hashlib.sha256((str(offset) + "\0" + fragment).encode("utf-8")).hexdigest()
        if fragment_id and fragment_id in state["fragment_ids"]:
            if state["fragment_signatures"].get(fragment_id) != signature:
                state["conflicts"].append({"offset": offset, "fragment_id": fragment_id,
                                           "reason": "FRAGMENT_ID_CONFLICT"})
            return
        if isinstance(offset, int):
            segments = state["segments"]
            for accepted_offset, accepted in segments.items():
                overlap_start = max(offset, accepted_offset)
                overlap_end = min(offset + len(fragment), accepted_offset + len(accepted))
                if overlap_start < overlap_end:
                    incoming = stored_fragment[overlap_start - offset:overlap_end - offset]
                    existing = accepted[overlap_start - accepted_offset:overlap_end - accepted_offset]
                    if not effective_private and incoming != existing:
                        state["conflicts"].append({"offset": offset, "fragment_id": fragment_id})
                        return
            same_start = segments.get(offset)
            if same_start is None or len(stored_fragment) > len(same_start):
                segments[offset] = stored_fragment
            origin = self._safe_int(event_data.get("origin_offset", 0), 0)
            cursor = origin
            assembled = ""
            pending_gap = False
            for segment_offset, segment in sorted(segments.items()):
                if segment_offset > cursor:
                    pending_gap = True
                    continue
                if segment_offset + len(segment) <= cursor:
                    continue
                overlap = max(0, cursor - segment_offset)
                if overlap < len(segment):
                    assembled += segment[overlap:]
                    cursor = segment_offset + len(segment)
            state["origin_offset"] = origin
            state["pending_gap"] = pending_gap
            state["parts"] = [assembled] if assembled else []
        elif stored_fragment:
            state["parts"].append(stored_fragment)
        if fragment_id:
            state["fragment_ids"].add(fragment_id)
            state["fragment_signatures"][fragment_id] = signature
        state["event"] = event
        state["last_event_at"] = bindings.app.time.time()
        if provider == "kimi":
            self._index_kimi_execution_buffer(key, state, event)

    def _index_kimi_execution_buffer(self, key, state, event):
        chat_id, turn_id, item_id, kind, agent_id, _source, session_id, revision = key
        scope = (chat_id, revision, session_id, turn_id, agent_id or "main")
        ordinal = event.data.get("step_number") if isinstance(event.data, dict) else None
        indexes = ((*scope, item_id), (*scope, kind, ordinal))
        if indexes != state.get("buffer_indexes"):
            self._unindex_kimi_execution_buffer(key, state)
            state["buffer_indexes"] = indexes
            for index in indexes:
                self._kimi_execution_buffer_index.setdefault(index, set()).add(key)

    def _unindex_kimi_execution_buffer(self, key, state):
        for index in state.pop("buffer_indexes", ()):
            keys = self._kimi_execution_buffer_index[index]
            keys.discard(key)
            if not keys:
                self._kimi_execution_buffer_index.pop(index)

    def _discard_execution_delta(self, key):
        state = self._execution_delta_buffer.pop(key, None)
        if state is not None:
            self._unindex_kimi_execution_buffer(key, state)
        return state

    def _write_kimi_execution_fragment(self, store, chat_id, logical_key, scope, **fragment):
        if logical_key in self._kimi_execution_private_keys:
            fragment["projection_seed"] = dict(fragment["projection_seed"] or {}, private_reasoning=True,
                                               list_text="", detail_text="", raw_text="", text="", source_detail={})
        return store.apply_execution_fragment(chat_id, logical_key, scope, **fragment)

    def _apply_kimi_execution_fragment_result(self, key, state, event, owner, result) -> None:
        chat_id, turn_id, item_id, display_kind = key[:4]
        state["pending_writes"] -= 1
        if self._execution_delta_buffer.get(key) is not state:
            return
        chat = self._chat_state_for_execution_steps(chat_id)
        if (not isinstance(chat, dict) or self._safe_int(chat.get("revision", 1), 1) != event.revision
                or not self._kimi_event_is_compatible_with_chat(chat, event)
                or (owner is not None and not self._kimi_execution_owner_matches(chat, owner))
                or (isinstance(result, dict) and result.get("stale"))):
            self._discard_execution_delta(key)
            return
        if state.get("private_reasoning") or (isinstance(result, dict) and result.get("private_reasoning")):
            state["private_reasoning"] = True
            state["parts"] = []
            state["event"] = self._private_kimi_execution_event(state["event"])
            self._kimi_execution_private_keys.add(state["logical_key"])
            answer_owner = self._kimi_answer_stream_scope(chat_id, event, owner)[2]
            self._redact_kimi_answer_stream(chat_id, key[6], answer_owner, self._kimi_event_agent_id(event), item_id)
        if isinstance(result, dict) and (result.get("accepted") or result.get("replay")):
            state["pending_gap"] = bool(result.get("pending_gap"))
            if not state["private_reasoning"]:
                state["parts"] = [str(result.get("assembled") or "")]
                state["event"] = event
            elif display_kind == "thinking":
                entry = self._build_execution_entry(state["event"])
                if entry:
                    self._append_execution_entry_to_chat(chat_id, dict(entry, revision=event.revision), save_state=False)
            state["last_event_at"] = bindings.app.time.time()
        if not state["pending_writes"] and "flush_retain" in state:
            retain = state.pop("flush_retain")
            self._flush_execution_delta(chat_id, turn_id, item_id, display_kind, retain=retain, session_id=key[6])

    @staticmethod
    def _private_kimi_execution_event(event: CodexEvent) -> CodexEvent:
        private = copy.copy(event)
        private.text = private.raw_text = private.title = private.command = ""
        source = event.data if isinstance(event.data, dict) else {}
        private.data = {key: source[key] for key in (
            "source_kind", "adapter", "step_id", "step_number", "epoch", "agent_id", "agentId", "turn_idx",
            "prompt_id", "owner_generation", "lifecycle_stream", "offset", "fragment_id",
        ) if key in source}
        private.data["non_disclosable"] = True
        return private

    def _append_kimi_tool_diagnostic(self, chat_id: str, event: CodexEvent) -> bool:
        if not self._kimi_protocol_event(event):
            return False
        data = event.data if isinstance(event.data, dict) else {}
        if str(data.get("source_kind") or "").strip() not in {"tool.progress", "shell.output"}:
            return False
        item_id = str(getattr(event, "item_id", "") or "").strip()
        if not item_id:
            return False
        target_chat = self._chat_state_for_execution_steps(chat_id)
        if not isinstance(target_chat, dict):
            return False
        thread_id = self._event_thread_id(event)
        turn_id = self._event_turn_id(event)
        diagnostic = self._bounded_kimi_diagnostic(
            str(getattr(event, "raw_text", "") or getattr(event, "text", "") or "")
        )
        if not diagnostic:
            return False
        steps = target_chat.get("execution_steps") if isinstance(target_chat.get("execution_steps"), list) else []
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            if (
                str(step.get("item_id") or "") != item_id
                or str(step.get("thread_id") or "") != thread_id
                or str(step.get("turn_id") or "") != turn_id
            ):
                continue
            existing = str(step.get("detail_text") or "")
            separator = "\n" if existing and not existing.endswith("\n") else ""
            step["detail_text"] = self._bounded_kimi_diagnostic(existing + separator + diagnostic)
            history = list(step.get("diagnostic_history") or [])
            history.append({"source_kind": str(data.get("source_kind") or ""), "text": diagnostic})
            step["diagnostic_history"] = history[-20:]
            resolved_chat_id = str(chat_id or target_chat.get("id") or "").strip()
            if resolved_chat_id:
                self._persist_execution_step_or_queue(resolved_chat_id, step)
            return True
        return False

    def _promote_kimi_interim_assistant(self, chat_id: str, event: CodexEvent, *, buffer_keys=None) -> None:
        """A real tool boundary identifies the preceding step's prose as commentary."""
        data = event.data if isinstance(event.data, dict) else {}
        if data.get("source_kind") not in {"tool.call.started", "shell.started"} or not data.get("step_id"):
            return
        for key in list(self._execution_delta_buffer) if buffer_keys is None else buffer_keys:
            state = self._execution_delta_buffer.get(key)
            if state is None:
                continue
            base = state.get("event")
            if not isinstance(base, CodexEvent) or key[0] != chat_id or key[3] != "assistant":
                continue
            base_data = base.data if isinstance(base.data, dict) else {}
            if (self._event_thread_id(base) != self._event_thread_id(event)
                    or self._event_turn_id(base) != self._event_turn_id(event)
                    or self._kimi_event_agent_id(base) != self._kimi_event_agent_id(event)
                    or base_data.get("step_id") != data["step_id"]):
                continue
            promoted = copy.copy(base)
            promoted.display_kind = "commentary"
            promoted.phase = "commentary"
            state["event"] = promoted
            self._discard_execution_delta(key)
            commentary_key = (*key[:3], "commentary", *key[4:])
            self._execution_delta_buffer[commentary_key] = state
            if len(commentary_key) == 8:
                self._index_kimi_execution_buffer(commentary_key, state, promoted)
            # Keep the empty segmented stream as a boundary: legacy part lists
            # still contain these fragments, but must never become final text.
            session_id, _, owner_key = self._kimi_answer_stream_scope(chat_id, base)
            answer_key = (chat_id, session_id, owner_key, self._kimi_event_agent_id(base), key[2])
            if answer_key in self._kimi_turn_answer_segments:
                self._kimi_turn_answer_segments[answer_key] = {}
            self._flush_execution_delta(chat_id, key[1], key[2], "commentary", session_id=self._event_thread_id(base),
                                        buffer_keys=(commentary_key,))

    def _flush_execution_delta(
        self, chat_id: str, turn_id: str | None = None, item_id: str | None = None, display_kind: str | None = None,
        *, retain: bool = False, session_id: str | None = None, buffer_keys=None
    ) -> bool:
        flushed = False
        normalized_chat_id = str(chat_id or "")
        normalized_turn_id = None if turn_id is None else str(turn_id or "").strip()
        normalized_item_id = None if item_id is None else str(item_id or "").strip()
        normalized_display_kind = None if display_kind is None else str(display_kind or "").strip()
        for key in list(self._execution_delta_buffer) if buffer_keys is None else buffer_keys:
            buf_chat_id, buf_turn_id, buf_item_id, buf_display_kind = key[:4]
            if buf_chat_id != normalized_chat_id:
                continue
            if normalized_turn_id is not None and buf_turn_id != normalized_turn_id:
                continue
            if normalized_item_id is not None and buf_item_id != normalized_item_id:
                continue
            if normalized_display_kind is not None and buf_display_kind != normalized_display_kind:
                continue
            state = self._execution_delta_buffer.get(key)
            if not isinstance(state, dict):
                continue
            if session_id is not None and self._event_thread_id(state["event"]) != session_id:
                continue
            if state.get("pending_writes"):
                state["flush_retain"] = state.get("flush_retain", True) and retain
                continue
            if bool(state.get("pending_gap")):
                continue
            if not retain:
                self._discard_execution_delta(key)
            text = "".join(str(part or "") for part in (state.get("parts") or []))
            base_event = state.get("event")
            if not text.strip() or not isinstance(base_event, CodexEvent):
                continue
            base_data = dict(base_event.data or {}) if isinstance(getattr(base_event, "data", None), dict) else {}
            if bool(state.get("private_reasoning")):
                # Privacy is monotonic for the whole buffered stream: a later
                # sparse fragment must not erase an earlier restriction, and a
                # later restriction protects all already buffered fragments.
                base_data["non_disclosable"] = True
            merged_event = CodexEvent(
                type="agent_message_delta",
                thread_id=self._event_thread_id(base_event),
                turn_id=self._event_turn_id(base_event),
                item_id=str(getattr(base_event, "item_id", "") or buf_item_id or "").strip(),
                text=text,
                raw_text=text,
                phase=str(getattr(base_event, "phase", "") or "").strip(),
                status=str(getattr(base_event, "status", "") or "").strip(),
                subtype=str(getattr(base_event, "subtype", "") or "").strip() or "agentMessageDelta",
                display_kind=buf_display_kind or "commentary",
                data=base_data,
                provider=str(getattr(base_event, "provider", "") or "codex"),
                event_id=str(getattr(base_event, "event_id", "") or buf_item_id),
                fragment_id=str(getattr(base_event, "fragment_id", "") or ""),
                offset=getattr(base_event, "offset", None),
                session_id=str(getattr(base_event, "session_id", "") or self._event_thread_id(base_event)),
                stream_id=str(getattr(base_event, "stream_id", "") or buf_item_id),
                tool_call_id=str(getattr(base_event, "tool_call_id", "") or ""),
                revision=getattr(base_event, "revision", None),
                origin_timestamp=_finite_timestamp(state.get("origin_timestamp")),
            )
            entry = self._build_execution_entry(merged_event)
            if entry and self._append_execution_entry_to_chat(chat_id, entry, save_state=False):
                flushed = True
        return flushed

    def _flush_all_execution_deltas_for_chat(self, chat_id: str) -> bool:
        return self._flush_execution_delta(chat_id)

    def _execution_meta_tuple(self, step_idx: int, step) -> tuple:
        if isinstance(step, dict):
            detail_text = self._execution_step_detail_text(step)
            display_kind = str(step.get("display_kind") or "").strip()
            if display_kind == "error":
                detail_text = self._sanitize_execution_error_text(detail_text)
            list_text = str(step.get("list_text") or "").strip()
            if display_kind == "error":
                list_text = self._execution_list_text_from_detail(detail_text, display_kind)
            if not list_text:
                if display_kind == "command":
                    fallback_text = (
                        str(step.get("subtype") or "").strip()
                        or str(step.get("status") or "").strip()
                        or str(step.get("event_type") or "").strip()
                    )
                    list_text = self._execution_command_list_text(
                        str(step.get("event_type") or "").strip(),
                        str(step.get("title") or "").strip(),
                        str(step.get("command") or "").strip(),
                        step.get("exit_code"),
                        fallback_text,
                    )
                else:
                    list_text = self._execution_list_text_from_detail(detail_text, display_kind)
            return ("execution", step_idx, list_text, detail_text)
        text = str(step or "").strip()
        return ("execution", step_idx, text, text)

    def _execution_step_text(self, step) -> str:
        return self._execution_meta_tuple(-1, step)[2]

    @staticmethod
    def _collapse_kimi_execution_lifecycle(steps: list) -> list:
        return collapse_kimi_execution_lifecycle(steps)

    def _execution_turn_context_steps(self, steps: list) -> list:
        if getattr(self, "view_mode", "") == "history" and str(getattr(self, "view_history_id", "") or "").strip():
            viewed_chat = self._hydrate_chat_from_store(self._find_archived_chat(self.view_history_id), include_execution_steps=False)
            viewed_turns = viewed_chat.get("turns") if isinstance(viewed_chat, dict) else []
            turns = viewed_turns if isinstance(viewed_turns, list) else []
        else:
            state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
            turns = state.get("turns") if isinstance(state.get("turns"), list) else []
        return execution_turn_context_steps(
            steps, turns,
            view_mode=self.view_mode,
            active_turn_index=self._active_turn_index_value(),
            selected_model=self.selected_model,
            requesting_text=REQUESTING_TEXT,
            answer_to_plain=lambda answer_md, model: execution_answer_to_plain(
                answer_md, model, self.codex_answer_english_filter_enabled),
        )

    @staticmethod
    def _strip_ansi_control_sequences(text: str) -> str:
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))

    @staticmethod
    def _is_noisy_execution_error_line(line: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(line or "").strip()).lower()
        if not normalized:
            return True
        if (
            " warn " in f" {normalized} "
            and "codex_core_" in normalized
            and "ignoring interface" in normalized
        ):
            return True
        return False

    @staticmethod
    def _sanitize_execution_error_text(detail_text: str) -> str:
        cleaned_text = bindings.app.ChatFrame._strip_ansi_control_sequences(detail_text)
        lines = [str(line or "").strip() for line in str(cleaned_text or "").replace("\r", "").split("\n")]
        kept = []
        noise_prefixes = (
            "Output:",
            "At line:",
            "+ ",
            "+ ~",
            "CategoryInfo:",
            "FullyQualifiedErrorId:",
        )
        noise_exact = {"错误：Output:", "Output:"}
        noise_sentence_prefixes = (
            "deprecation:",
            "warning:",
            "runtimewarning:",
        )
        for line in lines:
            if not line:
                continue
            normalized_line = line.removeprefix("错误：").strip()
            if bindings.app.ChatFrame._is_noisy_execution_error_line(normalized_line):
                continue
            if line in noise_exact or normalized_line in noise_exact:
                continue
            if any(line.startswith(prefix) for prefix in noise_prefixes):
                continue
            if any(normalized_line.startswith(prefix) for prefix in noise_prefixes):
                continue
            if any(normalized_line.lower().startswith(prefix) for prefix in noise_sentence_prefixes):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _should_show_execution_step(self, step) -> bool:
        return should_show_execution_step(step)

    def _execution_page_projection(self) -> tuple[list, list]:
        total_steps, steps = self._current_execution_steps_for_render()
        steps = self._collapse_kimi_execution_lifecycle(steps)
        positions_by_object = {}
        for index, step in enumerate(steps):
            positions_by_object.setdefault(id(step), []).append(index)
        steps = self._execution_turn_context_steps(steps)
        occurrences = {}
        source_positions = []
        for index, step in enumerate(steps):
            occurrence = occurrences.get(id(step), 0)
            positions = positions_by_object.get(id(step), [])
            source_positions.append(positions[occurrence] if occurrence < len(positions) else index)
            occurrences[id(step)] = occurrence + 1
        self._execution_projection_legacy_keys = {}
        total_steps = max(total_steps, len(steps))
        visible_items = []
        identity_buckets = [self._execution_legacy_identity_bucket(step) for step in steps]
        owner_key = (
            self._visible_execution_chat_id(),
            self._safe_int((self._visible_execution_chat_state() or {}).get("revision"), 1),
        )
        prior_timelines = getattr(self, "_legacy_execution_authoritative_buckets", None)
        if not isinstance(prior_timelines, dict):
            prior_timelines = {}
            self._legacy_execution_authoritative_buckets = prior_timelines
        previous_buckets = prior_timelines.get(owner_key)
        if isinstance(previous_buckets, list):
            previous_counts = Counter(bucket for bucket in previous_buckets if bucket[0] == "record")
            current_counts = Counter(bucket for bucket in identity_buckets if bucket[0] == "record")
            if any(previous_counts[bucket] and count > previous_counts[bucket]
                   for bucket, count in current_counts.items()):
                raise ExecutionIdentityBlocked("ambiguous insertion of identity-less execution record")
        prior_timelines[owner_key] = list(identity_buckets)
        authoritative_occurrences = {}
        identity_occurrences = [0] * len(steps)
        # Count identical legacy records from the newest/end side. Historical
        # paging prepends older rows, so existing duplicate identities remain
        # unchanged. A late insertion of an indistinguishable newer record is
        # intentionally outside this migration contract and must not be used
        # as an identity inference.
        for index in range(len(steps) - 1, -1, -1):
            step = steps[index]
            bucket = identity_buckets[index]
            identity_occurrences[index] = authoritative_occurrences.get(bucket, 0)
            authoritative_occurrences[bucket] = authoritative_occurrences.get(bucket, 0) + 1
        for idx, step in enumerate(steps):
            if not self._should_show_execution_step(step):
                continue
            meta = self._execution_meta_tuple(idx, step)
            row_text = str(meta[2] or "").strip()
            if not row_text:
                continue
            occurrence = identity_occurrences[idx]
            visible_items.append((row_text, meta, step, source_positions[idx], occurrence))
        self.execution_total_content_rows = len(visible_items)
        limit = max(
            EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        self.execution_visible_row_limit = limit
        has_more = len(visible_items) > limit or (total_steps > len(steps) and len(visible_items) >= limit)
        rows: list[str] = []
        metas: list[tuple] = []
        if has_more:
            visible_items = visible_items[-limit:]
            rows.append("更多")
            metas.append(("more", -1, "更多", ""))
        if not visible_items:
            return [("__execution_info__", "暂无执行过程")], [("info", -1, "", "")]
        row_ids = ["__execution_more__"] if has_more else []
        separator_anchor_timestamp = None
        render_now = bindings.app.time.time()
        for row_text, meta, step, source_position, occurrence in visible_items:
            timestamp = _execution_timestamp(step)
            if should_show_time(separator_anchor_timestamp, timestamp):
                anchor_id = self._execution_separator_anchor_id(source_position, step, occurrence)
                revision = self._safe_int(
                    step.get("revision") if isinstance(step, dict) else None,
                    self._safe_int((self._visible_execution_chat_state() or {}).get("revision"), 1),
                )
                rows.append(wechat_time_label(timestamp, render_now))
                metas.append(("time", meta[1], "", ""))
                row_ids.append(f"execution:time:revision:{revision}:{anchor_id}")
                separator_anchor_timestamp = timestamp
            rows.append(row_text)
            metas.append(meta)
            row_id = self._execution_row_id(source_position, step, occurrence)
            row_ids.append(row_id)
            if not isinstance(step, dict) or not step.get("_execution_uid"):
                self._execution_projection_legacy_keys[row_id] = self._execution_legacy_key(step)
        return list(zip(row_ids, rows)), metas

    def _show_execution_loading_for_new_owner(self) -> None:
        owner = self._execution_scan_key()[:3]
        if getattr(self, "_execution_applied_owner", None) == owner:
            return
        row_id = f"execution:loading:{owner!r}"
        self._execution_owner_pending = True
        self.execution_meta = [("info", -1, "", "")]
        self._execution_applied_legacy_keys = {}
        try:
            changed = self.execution_list_model.replace_visible_page([(row_id, "正在加载执行过程")], selected_id=row_id)
            if changed:
                self._request_listbox_repaint(self.execution_list)
        except Exception:
            self._execution_list_dirty = True
            self._execution_retry_after = bindings.app.time.monotonic() + IDLE_UI_REFRESH_DELAY_MS / 1000.0
            self._schedule_idle_ui_refresh()
            return
        self._execution_applied_owner = owner
        self._execution_owner_pending = False

    def _rebuild_execution_list_from_state(self) -> bool:
        if bindings.app.time.monotonic() < float(getattr(self, "_execution_retry_after", 0.0)):
            self._show_execution_loading_for_new_owner()
            self._schedule_idle_ui_refresh()
            return False
        try:
            return self._apply_execution_page(self._execution_page_projection())
        except ExecutionPagePending:
            self._execution_list_dirty = True
            self._show_execution_loading_for_new_owner()
        except ExecutionIdentityBlocked:
            self._execution_list_dirty = False
            return False
        except Exception:
            self._execution_list_dirty = True
            self._show_execution_loading_for_new_owner()
            self._execution_retry_after = bindings.app.time.monotonic() + IDLE_UI_REFRESH_DELAY_MS / 1000.0
            self._schedule_idle_ui_refresh()
        return False

    def _apply_execution_page(self, projection) -> bool:
        page, metas = projection
        row_ids = [item_id for item_id, _ in page]
        rows = [label for _, label in page]
        has_more = bool(metas and metas[0][0] == "more")
        selected_idx = self.execution_list.GetSelection()
        if selected_idx == wx.NOT_FOUND:
            selected_idx = 0
        elif rows:
            selected_idx = max(0, min(int(selected_idx), len(rows) - 1))
        else:
            selected_idx = None
        if hasattr(self, "execution_list_model"):
            selected_id = self.execution_list_model.selected_id()
            lease = getattr(self, "_execution_focus_lease", None)
            if self._execution_focus_lease_matches_owner(lease):
                # The live native selection is authoritative while the same
                # owner is still projected. This captures keyboard movement
                # that happens after F1 acquires the focus lease, including
                # while a provider batch is already queued. The lease remains
                # the fallback while a new-owner loading row is visible.
                if (
                    selected_id
                    and getattr(self, "_execution_applied_owner", None)
                    == self._execution_scan_key()[:3]
                ):
                    lease["row_id"] = selected_id
                    lease["row_index"] = self.execution_list.GetSelection()
                else:
                    selected_id = str(lease.get("row_id") or selected_id)
            if selected_id not in row_ids and getattr(self, "_execution_applied_owner", None) == self._execution_scan_key()[:3]:
                legacy_key = getattr(self, "_execution_applied_legacy_keys", {}).get(selected_id)
                aliases = [row_id for row_id, key in getattr(self, "_execution_projection_legacy_keys", {}).items()
                           if legacy_key is not None and key == legacy_key]
                if len(aliases) == 1:
                    selected_id = aliases[0]
            if selected_id not in row_ids:
                # The selected content left the page: use the same clamped
                # position, skipping the pagination action when possible.
                selected_idx = max(1 if has_more else 0, selected_idx or 0)
                selected_id = row_ids[selected_idx]
            changed = self.execution_list_model.replace_visible_page(list(zip(row_ids, rows)), selected_id=selected_id)
            if self._execution_focus_lease_matches_owner(lease):
                lease["row_id"] = self.execution_list_model.selected_id()
                lease["row_index"] = self.execution_list.GetSelection()
        else:
            changed = self._replace_listbox_items_if_changed(self.execution_list, rows, selected_idx)
        self.execution_meta = metas
        self._execution_owner_pending = False
        self._execution_applied_owner = self._execution_scan_key()[:3]
        self._execution_applied_legacy_keys = getattr(self, "_execution_projection_legacy_keys", {})
        intent = getattr(self, "_execution_latest_intent", None)
        self._execution_latest_intent = None
        if intent == (self._execution_applied_owner, getattr(self, "_execution_scan_generation", 0)) and self.execution_list.HasFocus():
            self.execution_list.SetSelection(len(rows) - 1)
        if changed:
            self._request_listbox_repaint(self.execution_list)
        if self._execution_focus_lease_can_restore():
            try:
                if not self.execution_list.HasFocus():
                    self.execution_list.SetFocus()
            except Exception:
                pass
        self._execution_list_dirty = False
        self._execution_list_pending_turn_reset = False
        self._execution_list_deferred_repaint = False
        self._execution_list_deferred_select_latest = False
        self._pending_execution_tail_appends.pop(self._visible_execution_chat_id(), None)
        return changed

    def _execution_focus_owner(self) -> tuple:
        return tuple(self._execution_scan_key()[:3])

    def _execution_focus_lease_matches_owner(self, lease=None) -> bool:
        lease = lease if isinstance(lease, dict) else getattr(self, "_execution_focus_lease", None)
        return bool(lease and lease.get("active") and not lease.get("suspended")
                    and tuple(lease.get("owner") or ()) == self._execution_focus_owner()
                    and self._detail_panel_mode() == "execution")

    def _execution_focus_lease_can_restore(self) -> bool:
        if not self._execution_focus_lease_matches_owner() or bool(getattr(self, "_closing", False)):
            return False
        if not bool(getattr(self, "_application_active", True)):
            return False
        try:
            for window in wx.GetTopLevelWindows():
                if window is self:
                    continue
                if isinstance(window, wx.Dialog) and window.IsShown() and window.IsModal():
                    self._release_execution_focus_lease()
                    return False
        except Exception:
            return False
        return True

    def _acquire_execution_focus_lease(self) -> None:
        row_id = self.execution_list_model.selected_id() if hasattr(self, "execution_list_model") else ""
        self._execution_focus_lease = {
            "active": True, "suspended": False, "owner": self._execution_focus_owner(),
            "view": "execution", "row_id": row_id, "row_index": self.execution_list.GetSelection(),
        }

    def _release_execution_focus_lease(self) -> None:
        lease = getattr(self, "_execution_focus_lease", None)
        if isinstance(lease, dict):
            lease["active"] = False
            lease["suspended"] = True

    def _on_execution_focus_lost(self, event) -> None:
        self._release_execution_focus_lease()
        event.Skip()

    def _execution_list_visible_for_updates(self) -> bool:
        if not hasattr(self, "execution_list"):
            return False
        return self._detail_panel_mode() == "execution"

    def _prepare_execution_scan(self) -> None:
        key = self._execution_scan_key()
        cached = getattr(self, "_execution_scan_result", None)
        pending = getattr(self, "_execution_scan_pending", None)
        if (cached and cached[0] == key) or (pending and pending[0] == key):
            return
        store = getattr(self, "chat_store", None)
        chat_id, _mode, turn_idx, limit = key[:4]
        if not (getattr(self, "_chat_store_enabled", False) and store is not None
                and chat_id and hasattr(store, "load_recent_execution_steps")):
            return
        if pending:
            pending[1].set()
        stop = bindings.app.threading.Event()
        self._execution_scan_pending = (key, stop)
        state = (self._find_archived_chat(self.view_history_id) if self.view_mode == "history"
                 else getattr(self, "_current_chat_state", None))
        memory_source = state.get("execution_steps", []) if isinstance(state, dict) else []

        def scan():
            rows = []
            error = None
            try:
                memory = copy.deepcopy(memory_source) if isinstance(memory_source, list) else []
                if _mode == "active" and any(isinstance(step, dict) and "turn_idx" in step for step in memory):
                    memory = [step for step in memory if not isinstance(step, dict)
                              or "turn_idx" not in step or self._safe_int(step.get("turn_idx"), -1) == turn_idx]
                _total, page = store.load_recent_execution_steps(
                    chat_id, turn_idx=turn_idx, limit=limit)
                persisted = list(page)
                rows = self._merge_execution_page(persisted, memory)
                if _mode == "active" and len(memory) >= _total:
                    # Complete live memory owns insertion order, including
                    # legacy rows without timestamps ahead of new stream rows.
                    rows = [item for item in memory if self._should_show_execution_step(item)]
                    page = []
                while (not stop.is_set() and page and len(page) == limit
                       and "_store_step_index" in page[0] and len(rows) <= limit):
                    _, page = store.load_recent_execution_steps(
                        chat_id, turn_idx=turn_idx, limit=limit,
                        before_step_index=page[0]["_store_step_index"], include_total=False)
                    persisted = page + persisted
                    rows = self._merge_execution_page(persisted, memory)
                    persisted = [item for item in persisted if self._should_show_execution_step(item)]
            except Exception as exc:
                error = exc
            if not stop.is_set():
                wx.CallAfter(self._finish_execution_scan, key, stop, rows, error)
        bindings.app.threading.Thread(target=scan, name="execution-prepare", daemon=True).start()

    def _mark_execution_list_dirty(self) -> None:
        self._invalidate_execution_scan()
        self._execution_list_dirty = True
        self._prepare_execution_scan()
        if self._execution_list_visible_for_updates():
            self._schedule_idle_ui_refresh()

    def _render_execution_list(self, *, force: bool = False) -> None:
        if not hasattr(self, "execution_list"):
            return
        if not force and not self._execution_list_visible_for_updates():
            self._execution_list_dirty = True
            self._prepare_execution_scan()
            return
        self._rebuild_execution_list_from_state()

    def _execution_list_rows_match_current_steps(self) -> bool:
        if not hasattr(self, "execution_list"):
            return False
        page, metas = self._execution_page_projection()
        return (self.execution_list_model.visible_ids == [row_id for row_id, _ in page]
                and self.execution_meta == metas
                and [self.execution_list.GetString(i) for i in range(self.execution_list.GetCount())]
                == [label for _, label in page])

    def _reset_current_turn_execution_view(self) -> None:
        self._reset_execution_visible_row_limit()
        if hasattr(self, "execution_list"):
            if not self._execution_list_visible_for_updates():
                self._execution_list_pending_turn_reset = True
                self.execution_meta = []
                self._mark_execution_list_dirty()
                return
            if self._detail_panel_mode() == "execution" and self.execution_list.GetCount() > 0:
                self._execution_list_pending_turn_reset = True
                return
            self.execution_list.Clear()
            self.execution_meta = []
            self.execution_list.Append("暂无执行过程")
            self.execution_meta.append(("info", -1, "", ""))
            try:
                self.execution_list.SetSelection(0)
            except Exception:
                pass
            self._request_listbox_repaint(self.execution_list)

    def _apply_detail_panel_mode(self, mode: str | None = None, refresh_execution: bool = False) -> str:
        previous_mode = self._detail_panel_mode()
        normalized = "execution" if str(mode or self._detail_panel_mode()).strip() == "execution" else "answers"
        if not isinstance(getattr(self, "_current_chat_state", None), dict):
            self._current_chat_state = {}
        if self.view_mode != "history":
            self._current_chat_state["detail_panel_mode"] = normalized
            if not isinstance(self._current_chat_state.get("execution_steps"), list):
                self._current_chat_state["execution_steps"] = []
        else:
            viewed_chat = self._find_archived_chat(self.view_history_id)
            if isinstance(viewed_chat, dict):
                viewed_chat["detail_panel_mode"] = normalized
        show_answers = normalized != "execution"
        show_execution = normalized == "execution"
        if not refresh_execution and previous_mode == normalized and not bool(getattr(self, "_execution_list_dirty", False)):
            visible_matches = True
            if hasattr(self, "answer_list"):
                try:
                    visible_matches = visible_matches and bool(self.answer_list.IsShown()) == show_answers
                except Exception:
                    visible_matches = False
            if hasattr(self, "execution_list"):
                try:
                    visible_matches = visible_matches and bool(self.execution_list.IsShown()) == show_execution
                except Exception:
                    visible_matches = False
            label_matches = True
            if hasattr(self, "detail_title_label"):
                try:
                    label_matches = self.detail_title_label.GetLabel() == ("执行过程：" if normalized == "execution" else "回答：")
                except Exception:
                    label_matches = False
            if visible_matches and label_matches:
                return normalized
        if hasattr(self, "detail_title_label"):
            try:
                self.detail_title_label.SetLabel("执行过程：" if normalized == "execution" else "回答：")
            except Exception:
                pass
        answer_has_focus = False
        execution_has_focus = False
        try:
            answer_has_focus = bool(hasattr(self, "answer_list") and self.answer_list.HasFocus())
        except Exception:
            answer_has_focus = False
        try:
            execution_has_focus = bool(hasattr(self, "execution_list") and self.execution_list.HasFocus())
        except Exception:
            execution_has_focus = False
        if hasattr(self, "answer_list"):
            try:
                self.answer_list.Show(show_answers)
            except Exception:
                pass
        if hasattr(self, "execution_list"):
            try:
                self.execution_list.Show(show_execution)
            except Exception:
                pass
        if normalized == "execution" and answer_has_focus and hasattr(self, "execution_list"):
            try:
                self.execution_list.SetFocus()
            except Exception:
                pass
        elif normalized == "answers" and execution_has_focus and hasattr(self, "answer_list"):
            try:
                self.answer_list.SetFocus()
            except Exception:
                pass
        if normalized == "execution" and (
            previous_mode != normalized or bool(getattr(self, "_execution_list_dirty", False)) or refresh_execution
        ):
            # Absorb deltas before projecting the page, including when the
            # newly visible list already has focus. One switch is one sync.
            self._codex_ui_batch_depth = int(getattr(self, "_codex_ui_batch_depth", 0) or 0) + 1
            try:
                self._flush_all_execution_deltas_for_chat(self._visible_execution_chat_id())
            finally:
                self._codex_ui_batch_depth -= 1
            self._render_execution_list(force=True)
        self._notes_rebuild_tab_order()
        try:
            self.Layout()
        except Exception:
            pass
        return normalized

    def _focus_latest_execution_item(self) -> bool:
        if not hasattr(self, "execution_list"):
            return False
        if bool(getattr(self, "_execution_list_dirty", False)):
            self._execution_latest_intent = (self._execution_scan_key()[:3], getattr(self, "_execution_scan_generation", 0))
        count = self.execution_list.GetCount()
        if count <= 0:
            return False
        try:
            self.execution_list.SetSelection(count - 1)
            if not self.execution_list.HasFocus():
                self.execution_list.SetFocus()
        except Exception:
            return False
        return True

    def _toggle_detail_panel_mode(self, *, focus_detail: bool = False) -> str:
        current_mode = self._detail_panel_mode()
        try:
            if hasattr(self, "execution_list") and (self.execution_list.HasFocus() or self.execution_list.IsShown()):
                current_mode = "execution"
            elif hasattr(self, "answer_list") and (self.answer_list.HasFocus() or self.answer_list.IsShown()):
                current_mode = "answers"
        except Exception:
            pass
        next_mode = "answers" if current_mode == "execution" else "execution"
        self._apply_detail_panel_mode(next_mode, refresh_execution=True)
        if focus_detail:
            if next_mode == "execution":
                self._focus_latest_execution_item()
                self._acquire_execution_focus_lease()
            elif hasattr(self, "answer_list"):
                self._release_execution_focus_lease()
                self._focus_latest_answer()
        self._save_state()
        return next_mode

    def _on_execution_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        self._touch_navigation_quiet_window(event)
        key = event.GetKeyCode()
        ctrl = event.ControlDown()
        alt = self._event_alt_down(event)
        if key == wx.WXK_F1 and not ctrl and not alt:
            self._toggle_detail_panel_mode(focus_detail=True)
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            shift = bool(getattr(event, "ShiftDown", lambda: False)())
            handled = self._try_open_selected_execution_detail() if shift else self._open_selected_execution_text_viewer()
            if handled:
                return
        if ctrl and key in (ord("C"), ord("c")):
            if bool(getattr(self, "_execution_owner_pending", False)):
                return
            idx = self.execution_list.GetSelection()
            if idx == wx.NOT_FOUND:
                event.Skip()
                return
            detail_text = ""
            if 0 <= idx < len(self.execution_meta):
                detail_text = str(self.execution_meta[idx][3] or "")
            text = self._plain_text_for_clipboard(detail_text or str(self.execution_list.GetString(idx) or ""))
            if text and self._set_clipboard_text(text):
                self.SetStatusText("已复制")
            stop = getattr(event, "StopPropagation", None)
            if callable(stop):
                stop()
            return
        event.Skip()

    def _on_execution_selection_changed(self, event):
        lease = getattr(self, "_execution_focus_lease", None)
        if self._execution_focus_lease_matches_owner(lease):
            lease["row_id"] = self.execution_list_model.selected_id()
            lease["row_index"] = self.execution_list.GetSelection()
        event.Skip()

    def _on_execution_char(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            shift = bool(getattr(event, "ShiftDown", lambda: False)())
            handled = self._try_open_selected_execution_detail() if shift else self._open_selected_execution_text_viewer()
            if handled:
                return
        ch = self._extract_committed_char(event)
        if ch:
            self._queue_answer_char_redirect(ch)
        event.Skip()

    def _on_execution_activate(self, _event):
        self._open_selected_execution_text_viewer()

    def _try_open_selected_execution_detail(self) -> bool:
        if self._detail_panel_mode() != "execution":
            return False
        idx = self.execution_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.execution_meta):
            return False
        item_type, step_idx, _, detail_text = self.execution_meta[idx]
        if item_type == "more":
            self._show_more_execution_rows()
            try:
                if self.execution_list.GetCount() > 0:
                    self.execution_list.SetSelection(0)
            except Exception:
                pass
            return True
        if item_type != "execution" or step_idx < 0:
            return False
        row_text = str(self.execution_meta[idx][2] or "")
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        state_steps = state.get("execution_steps") if isinstance(state.get("execution_steps"), list) else []
        if str(detail_text or "").strip() and 0 <= step_idx < len(state_steps) and isinstance(state_steps[step_idx], dict):
            step = state_steps[step_idx]
        elif str(detail_text or "").strip():
            step = {"step": row_text, "list_text": row_text, "detail_text": str(detail_text or "")}
        else:
            if not row_text.strip():
                return False
            step = {"step": row_text, "list_text": row_text, "detail_text": row_text}
        detail_source = str(
            step.get("detail_text")
            or step.get("message")
            or step.get("step")
            or step.get("title")
            or step.get("text")
            or step.get("content")
            or step.get("description")
            or detail_text
            or ""
        ).strip()
        if not detail_source:
            return False
        try:
            page_path = self._ensure_execution_detail_page(step, step_idx)
            if self._open_local_webpage(page_path) is False:
                raise RuntimeError("详情网页未成功打开")
            self.SetStatusText("已打开执行过程详情网页")
            self._save_state()
        except Exception:
            wx.MessageBox("打开详情网页失败。", "提示", wx.OK | wx.ICON_WARNING)
            return False
        return True

    def _selected_execution_text_viewer_content(self) -> tuple[str, str] | None:
        if self._detail_panel_mode() != "execution":
            return None
        idx = self.execution_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.execution_meta):
            return None
        item_type, step_idx, row_text, detail_text = self.execution_meta[idx]
        if item_type == "more":
            self._show_more_execution_rows()
            try:
                if self.execution_list.GetCount() > 0:
                    self.execution_list.SetSelection(0)
            except Exception:
                pass
            return None
        if item_type != "execution" or step_idx < 0:
            return None
        source = str(detail_text or row_text or "")
        text = remove_emojis(md_to_plain_preserving_paragraphs(source)).strip()
        if not text:
            return None
        return "回答详情", text

    def _open_selected_execution_text_viewer(self) -> bool:
        idx = self.execution_list.GetSelection()
        if idx != wx.NOT_FOUND and idx < len(self.execution_meta) and self.execution_meta[idx][0] == "more":
            self._show_more_execution_rows()
            try:
                if self.execution_list.GetCount() > 0:
                    self.execution_list.SetSelection(0)
            except Exception:
                pass
            return True
        content = self._selected_execution_text_viewer_content()
        if not content:
            return False
        title, text = content
        self._execution_narrative_viewer_row = self.execution_list_model.selected_id()
        try:
            return self._open_answer_text_viewer(title, text)
        finally:
            self._execution_narrative_viewer_row = None
