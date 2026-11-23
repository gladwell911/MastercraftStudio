"""Chat answer rows, previews, viewers, and continuation."""

import copy
import markdown
import re
import uuid
import wx
from claudecode_client import is_claudecode_model
from codex_client import is_codex_model
from kimi_server_client import is_kimi_model
from mc_frame_dialogs import AnswerViewerPayload
from mc_frame_presentation import (
    _finite_timestamp,
    answer_time_projection,
    compact_markdown_summary,
    filter_answer_display_text,
    md_to_plain_preserving_paragraphs,
    remove_emojis,
    wechat_time_label,
)
from mc_frame_settings import (
    ANSWER_LIST_DEFAULT_VISIBLE_ROWS,
    ANSWER_LIST_EXPAND_ROWS,
    REQUESTING_TEXT,
)
from openclaw_client import is_openclaw_model
from pathlib import Path

import mc_frame_bindings as bindings


class AnswersMixin:
    def _selected_answer_source_text(self) -> str:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return ""
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return ""
        item_type, turn_idx, plain, detail = self.answer_meta[idx]
        if item_type not in ("question", "answer"):
            return ""
        turns = self._get_view_turns()
        turn = turns[turn_idx] if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict) else {}
        if item_type == "question":
            return str(turn.get("question") or detail or plain or "")
        return str(turn.get("answer_md") or detail or plain or "")

    def _show_answer_menu(self) -> None:
        menu = wx.Menu()
        extract_id = wx.NewIdRef()
        menu.Append(int(extract_id), "提取文件")
        self.Bind(wx.EVT_MENU, lambda _evt: self._extract_files_from_selected_answer(), id=int(extract_id))
        self.PopupMenu(menu)
        menu.Destroy()

    def _build_question_detail_html(self, question: str) -> str:
        q_html = markdown.markdown(remove_emojis(question), extensions=["extra", "fenced_code", "tables", "sane_lists"])
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>问题详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;}"
            "</style></head><body>"
            "<h2>问题详情</h2>"
            f"{q_html}"
            "</body></html>"
        )

    def _ensure_question_detail_page(self, turn: dict, turn_idx: int) -> Path:
        created = int(float(turn.get("created_at") or bindings.app.time.time()))
        file_name = f"question_{created}_{turn_idx}_{uuid.uuid4().hex[:8]}.html"
        page_path = self.detail_pages_dir / file_name
        html = self._build_question_detail_html(str(turn.get("question") or ""))
        page_path.write_text(html, encoding="utf-8")
        turn["question_detail_page_path"] = str(page_path)
        return page_path

    def _ensure_answer_detail_page(self, turn: dict, turn_idx: int) -> Path:
        created = int(float(turn.get("created_at") or bindings.app.time.time()))
        file_name = f"answer_{created}_{turn_idx}_{uuid.uuid4().hex[:8]}.html"
        page_path = self.detail_pages_dir / file_name
        html = self._build_answer_detail_html(str(turn.get("answer_md") or ""))
        page_path.write_text(html, encoding="utf-8")
        turn["answer_detail_page_path"] = str(page_path)
        return page_path

    def _get_view_turns(self):
        if self.view_mode == "history":
            chat = self._hydrate_chat_from_store(
                self._find_archived_chat(self.view_history_id),
                include_execution_steps=False,
            )
            if chat:
                return chat.get("turns") or []
            return []
        return self.active_session_turns

    def _maybe_append_time_row_to_answer_list(self, turn_idx: int, turn: dict, role: str = "user") -> None:
        for meta in list(getattr(self, "answer_meta", []) or []):
            if meta and len(meta) > 1 and meta[0] == "time" and meta[1] == int(turn_idx) and (meta[3] or "user") == role:
                return
            if role == "assistant" and _finite_timestamp(turn.get("answer_at")) is None and meta and meta[0] == "time" and meta[1] == int(turn_idx):
                return
        # Derive the anchor from time rows actually present in this page.
        turns = self._get_view_turns()
        anchor = None
        for meta in getattr(self, "answer_meta", []):
            if meta and meta[0] == "time" and 0 <= meta[1] < len(turns):
                timestamp = self._answer_message_timestamp(turns[meta[1]], meta[3] or "user")
                if timestamp is not None:
                    anchor = timestamp
        timestamp = self._answer_message_timestamp(turn, role)
        label = wechat_time_label(timestamp, bindings.app.time.time()) if answer_time_projection([anchor, timestamp])[-1] else ""
        if not label:
            return
        meta = ("time", int(turn_idx), label, "assistant" if role == "assistant" else "")
        if hasattr(self, "answer_list_model"):
            self.answer_list_model.append(self._answer_row_id(meta), label)
        else:
            self.answer_list.Append(label)
        self.answer_meta.append(meta)

    def _answer_message_timestamp(self, turn: dict, role: str):
        if role == "assistant":
            answer_at = _finite_timestamp((turn or {}).get("answer_at"))
            if answer_at is not None:
                return answer_at
        return _finite_timestamp((turn or {}).get("created_at"))

    def _reset_answer_visible_row_limit(self) -> None:
        self.answer_visible_row_limit = ANSWER_LIST_DEFAULT_VISIBLE_ROWS

    def _show_more_answer_rows(self) -> None:
        current_limit = int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0)
        self.answer_visible_row_limit = max(ANSWER_LIST_DEFAULT_VISIBLE_ROWS, current_limit) + ANSWER_LIST_EXPAND_ROWS
        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")

    def _apply_answer_row_limit(self, header_count: int, *, force_has_more: bool = False) -> None:
        rows = [self.answer_list.GetString(idx) for idx in range(self.answer_list.GetCount())]
        metas = list(self.answer_meta)
        rows, metas, active_idx = self._answer_rows_with_limit(
            rows,
            metas,
            header_count,
            active_meta=metas[self._active_answer_row_index] if 0 <= self._active_answer_row_index < len(metas) else None,
            force_has_more=force_has_more,
        )
        self.answer_list.Clear()
        self.answer_meta = []
        self._active_answer_row_index = active_idx
        for row_text, meta in zip(rows, metas):
            self.answer_list.Append(row_text)
            self.answer_meta.append(meta)

    def _ordered_answer_message_keys(self, turns: list[dict]) -> list[tuple]:
        """Order lightweight identities before formatting any answer bodies."""
        entries, previous = [], 0
        for index, turn in enumerate(turns):
            raw = str(turn.get("answer_md") or "")
            roles = []
            if str(turn.get("question") or "").strip() or turn.get("attachments"):
                roles.append("user")
            if (raw != REQUESTING_TEXT and not (turn.get("suppress_empty_answer_row") and not raw.strip())) or turn.get("received_attachments"):
                roles.append("assistant")
            for role in roles:
                sequence = turn.get("answer_order" if role == "assistant" else "question_order")
                timestamp = _finite_timestamp(turn.get("answer_at" if role == "assistant" else "created_at"))
                if isinstance(sequence, int) and not isinstance(sequence, bool):
                    order = sequence
                elif timestamp is not None:
                    order = int(timestamp * 1_000_000_000)
                else:
                    order = previous
                previous = order
                entries.append((order, len(entries), (index, role)))
        if any(t.get("answer_at") is not None or t.get("answer_order") is not None for t in turns):
            entries.sort(key=lambda entry: (entry[0], entry[1]))
        return [entry[2] for entry in entries]

    def _answer_rows_with_limit(
        self,
        rows: list[str],
        metas: list[tuple],
        header_count: int,
        *,
        active_meta: tuple | None = None,
        force_has_more: bool = False,
    ) -> tuple[list[str], list[tuple], int]:
        header_count = max(0, min(int(header_count or 0), len(rows), len(metas)))
        header_rows = rows[:header_count]
        header_metas = metas[:header_count]
        content_rows = rows[header_count:]
        content_metas = metas[header_count:]
        turns = self._get_view_turns()
        groups = []
        current = None
        for row, meta in zip(content_rows, content_metas):
            if meta[0] == "time":
                continue
            role = "assistant" if meta[0] in {"ai", "answer"} else "user" if meta[0] in {"user", "question"} else (current[1] if current else "user")
            key = (meta[1], role)
            if current != key:
                groups.append([key, []])
                current = key
            groups[-1][1].append((row, meta))
        positions = {key: position for position, key in enumerate(self._ordered_answer_message_keys(turns))}
        ordered = []
        for ordinal, (key, entries) in enumerate(groups):
            ordered.append((positions.get(key, ordinal), ordinal, entries))
        ordered.sort(key=lambda entry: (entry[0], entry[1]))
        content_rows, content_metas = [], []
        for _, _, entries in ordered:
            for row, meta in entries:
                content_rows.append(row)
                content_metas.append(meta)
        self.answer_total_content_rows = len(content_rows)
        limit = max(ANSWER_LIST_DEFAULT_VISIBLE_ROWS, int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0))
        self.answer_visible_row_limit = limit
        has_more = force_has_more or len(content_rows) > limit
        if len(content_rows) > limit:
            content_rows = content_rows[-limit:]
            content_metas = content_metas[-limit:]
        turns = self._get_view_turns()
        content = [(row, meta) for row, meta in zip(content_rows, content_metas) if meta[0] != "time"]
        def project_time_rows(content):
            projected_rows, projected_metas = [], []
            emitted = set()
            anchor = None
            role = "user"
            for row, meta in content:
                index = meta[1]
                if meta[0] in {"user", "question"}:
                    role = "user"
                elif meta[0] in {"ai", "answer"}:
                    role = "assistant"
                key = (index, role)
                if index >= 0 and key not in emitted:
                    timestamp = self._answer_message_timestamp(turns[index], role) if index < len(turns) else None
                    legacy_unknown = role == "assistant" and timestamp is None and (index, "user") in emitted
                    if not legacy_unknown and answer_time_projection([anchor, timestamp])[-1]:
                        label = wechat_time_label(timestamp, bindings.app.time.time())
                        projected_rows.append(label)
                        projected_metas.append(("time", index, label, "assistant" if role == "assistant" else ""))
                        if timestamp is not None:
                            anchor = timestamp
                    emitted.add(key)
                projected_rows.append(row)
                projected_metas.append(meta)
            return projected_rows, projected_metas

        content_rows, content_metas = project_time_rows(content)
        while len(content_rows) > limit:
            # A newly visible head can require an extra anchor. Budget that
            # label too, then reproject from the surviving head of the page.
            has_more = True
            content = content[len(content_rows) - limit:]
            content_rows, content_metas = project_time_rows(content)
        limited_rows: list[str] = []
        limited_metas: list[tuple] = []
        if has_more:
            limited_rows.append("更多")
            limited_metas.append(("more", -1, "更多", ""))
        for row_text, meta in zip(header_rows + content_rows, header_metas + content_metas):
            limited_rows.append(row_text)
            limited_metas.append(meta)
        active_idx = -1
        if active_meta is not None:
            for idx, meta in enumerate(limited_metas):
                if meta == active_meta:
                    active_idx = idx
                    break
        return limited_rows, limited_metas, active_idx

    def _answer_row_id(self, meta: tuple) -> str:
        kind = str(meta[0] if meta else "")
        try:
            turn_idx = int(meta[1] if len(meta) > 1 else -1)
        except Exception:
            turn_idx = -1
        if kind == "time":
            return f"time:{turn_idx}:assistant" if len(meta) > 3 and meta[3] == "assistant" else f"time:{turn_idx}"
        if kind in {"user", "question", "ai", "answer", "attachment"}:
            detail = str(meta[3] if len(meta) > 3 else "")
            if kind == "attachment":
                return f"answer:{turn_idx}:attachment:{detail}"
            return f"answer:{turn_idx}:{kind}"
        if kind in {"context_usage", "current_model", "more", "info", "notice"}:
            return f"answer:special:{kind}"
        return f"answer:{turn_idx}:{kind}:{len(str(meta))}"

    def _replace_answer_list_rows(self, rows: list[str], metas: list[tuple], selected_idx: int | None) -> bool:
        if not hasattr(self, "answer_list_model"):
            return self._replace_listbox_items_if_changed(self.answer_list, rows, selected_idx)
        row_ids = [self._answer_row_id(meta) for meta in metas]
        selected_id = row_ids[selected_idx] if selected_idx is not None and 0 <= selected_idx < len(row_ids) else ""
        unique_rows = []
        unique_metas = []
        unique_ids = []
        seen_ids = set()
        for row_id, row, meta in zip(row_ids, rows, metas):
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            unique_ids.append(row_id)
            unique_rows.append(row)
            unique_metas.append(meta)
        rows[:] = unique_rows
        metas[:] = unique_metas
        row_ids = unique_ids
        return self.answer_list_model.replace_visible_page(list(zip(row_ids, rows)), selected_id=selected_id)

    def _refresh_answer_list_preserving_selection(self, refresh_execution: bool = True) -> None:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return
        selected_meta = None
        idx = self.answer_list.GetSelection() if hasattr(self, "answer_list") else wx.NOT_FOUND
        if idx != wx.NOT_FOUND and 0 <= idx < len(self.answer_meta):
            selected_meta = self.answer_meta[idx]
        self._render_answer_list_compat(refresh_execution=refresh_execution)
        if selected_meta is None:
            return
        for new_idx, meta in enumerate(self.answer_meta):
            if selected_meta[0] in {"context_usage", "current_model", "time"}:
                matched = meta[0] == "context_usage"
                if selected_meta[0] == "current_model":
                    matched = meta[0] == "current_model"
                if selected_meta[0] == "time":
                    matched = meta[0] == "time" and self._answer_row_id(meta) == self._answer_row_id(selected_meta)
            else:
                matched = meta == selected_meta
            if matched:
                if self.answer_list.GetSelection() != new_idx:
                    self.answer_list.SetSelection(new_idx)
                break

    def _render_answer_list_compat(self, refresh_execution: bool = True) -> None:
        if refresh_execution:
            self._render_answer_list()
            return
        self._render_answer_list(refresh_execution=False)

    def _render_answer_list(self, refresh_execution: bool = True, *, preserve_panel=False, select_latest_message=False):
        if preserve_panel and self.execution_list.IsShown():
            self._show_execution_loading_for_new_owner()
            self._render_execution_list(force=True)
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return
        mode = self._detail_panel_mode() if preserve_panel else self._apply_detail_panel_mode()
        refresh_execution = refresh_execution and not preserve_panel
        rows: list[str] = []
        metas: list[tuple] = []
        self._active_answer_row_index = -1
        turns = self._get_view_turns()
        visible_chat_id = (
            str(self.view_history_id or "").strip()
            if self.view_mode == "history"
            else str(self.active_chat_id or self.current_chat_id or (self._current_chat_state or {}).get("id") or "").strip()
        )
        context_usage_label = self._answer_usage_header_label()
        if self._should_show_context_usage_row(context_usage_label, turns):
            self._append_context_usage_row(rows, metas)
        self._append_current_model_row(rows, metas)
        header_count = len(metas)
        if not turns:
            self.answer_total_content_rows = 1
            rows.append("暂无对话内容")
            metas.append(("info", -1, "", ""))
            tail_notice = str(getattr(self, "_answer_list_tail_notice", "") or "").strip()
            tail_notice_chat_id = str(getattr(self, "_answer_list_tail_notice_chat_id", "") or "").strip()
            if tail_notice and (not tail_notice_chat_id or tail_notice_chat_id == visible_chat_id):
                rows.append(tail_notice)
                metas.append(("notice", -1, tail_notice, ""))
            selected_idx = 0 if rows else None
            changed = self._replace_answer_list_rows(rows, metas, selected_idx)
            self.answer_meta = metas
            if changed:
                self._request_listbox_repaint(self.answer_list)
            if mode == "execution" and refresh_execution:
                self._render_execution_list()
            return
        limit = max(
            ANSWER_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        message_keys = self._ordered_answer_message_keys(turns)
        visible_indexes = {index for index, _role in message_keys[-limit:]}
        force_has_more = len(message_keys) > limit
        visible_turns = [(index, turns[index]) for index in sorted(visible_indexes)]
        now_ts = bindings.app.time.time()
        time_anchor = None
        for i, t in visible_turns:
            q = str(t.get("question") or "")
            a_md, a = self._turn_answer_markdown(t)
            attachments = t.get("attachments") if isinstance(t.get("attachments"), list) else []
            received_attachments = t.get("received_attachments") if isinstance(t.get("received_attachments"), list) else []
            suppress_empty_answer_row = bool(t.get("suppress_empty_answer_row")) and (not str(a_md or "").strip())
            attachment_only_summary = bool(attachments) and suppress_empty_answer_row and any(
                token in q for token in ("上传成功", "已成功上传", "上传失败", "已部分上传")
            )
            show_pending_placeholder = (a_md != REQUESTING_TEXT) and (not suppress_empty_answer_row)
            show_user_rows = bool(attachments) or not (
                (is_openclaw_model(str(t.get("model") or "")) or is_codex_model(str(t.get("model") or "")) or is_claudecode_model(str(t.get("model") or "")))
                and (not q.strip())
                and bool(a_md.strip())
            )
            should_show_user_label = show_user_rows and ((q.strip() and not attachment_only_summary) or bool(attachments))
            turn_emits_rows = should_show_user_label or bool(attachments) or show_pending_placeholder or bool(received_attachments)
            if turn_emits_rows:
                timestamp = _finite_timestamp(t.get("created_at"))
                time_label = wechat_time_label(timestamp, now_ts) if answer_time_projection([time_anchor, timestamp])[-1] else ""
                if time_label and timestamp is not None:
                    time_anchor = timestamp
                if time_label:
                    rows.append(time_label)
                    metas.append(("time", i, time_label, ""))
            if should_show_user_label:
                rows.append("我")
                metas.append(("user", i, "我", ""))
                if q.strip() and not attachment_only_summary:
                    rows.append(q)
                    metas.append(("question", i, q, ""))
            if attachments:
                for label, meta in self._turn_attachment_rows(i, attachments, incoming=False):
                    rows.append(label)
                    metas.append(meta)
            if show_pending_placeholder:
                rows.append("小诸葛")
                metas.append(("ai", i, "小诸葛", ""))
                rows.append(a)
                metas.append(("answer", i, a, a_md))
                for label, meta in self._turn_attachment_rows(i, received_attachments, incoming=True):
                    rows.append(label)
                    metas.append(meta)
                if self.view_mode == "active" and i == self.active_turn_idx:
                    self._active_answer_row_index = len(rows) - 1
            elif received_attachments:
                for label, meta in self._turn_attachment_rows(i, received_attachments, incoming=True):
                    rows.append(label)
                    metas.append(meta)
        active_meta = metas[self._active_answer_row_index] if 0 <= self._active_answer_row_index < len(metas) else None
        rows, metas, active_idx = self._answer_rows_with_limit(rows, metas, header_count, active_meta=active_meta, force_has_more=force_has_more)
        tail_notice = str(getattr(self, "_answer_list_tail_notice", "") or "").strip()
        tail_notice_chat_id = str(getattr(self, "_answer_list_tail_notice_chat_id", "") or "").strip()
        if tail_notice and (not tail_notice_chat_id or tail_notice_chat_id == visible_chat_id):
            rows.append(tail_notice)
            metas.append(("notice", -1, tail_notice, ""))
        selected_idx = self.answer_list.GetSelection()
        if selected_idx == wx.NOT_FOUND:
            selected_idx = len(rows) - 1 if rows else None
        elif rows:
            selected_idx = max(0, min(int(selected_idx), len(rows) - 1))
        else:
            selected_idx = None
        if select_latest_message:
            selected_idx = next(
                (index for index in range(len(metas) - 1, -1, -1) if metas[index][0] in {"answer", "question"}),
                selected_idx,
            )
        changed = self._replace_answer_list_rows(rows, metas, selected_idx)
        self.answer_meta = metas
        self._active_answer_row_index = next(
            (idx for idx, meta in enumerate(metas) if meta == active_meta),
            -1,
        ) if active_meta is not None else active_idx
        if changed:
            self._request_listbox_repaint(self.answer_list)
        if mode == "execution" and refresh_execution:
            self._render_execution_list()

    def _active_answer_turn_is_authoritative(self, turn_idx: int, turn: dict | None = None) -> bool:
        """Return whether an answer fast path still targets the visible state.

        Incremental callbacks may arrive after a chat switch or after another
        turn became active.  The authoritative active-turn list is the only
        source an answer fast path is allowed to project from.
        """
        if self.view_mode != "active" or getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return False
        try:
            index = int(turn_idx)
        except (TypeError, ValueError):
            return False
        turns = self.active_session_turns if isinstance(getattr(self, "active_session_turns", None), list) else []
        if not (0 <= index < len(turns)) or not isinstance(turns[index], dict):
            return False
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        visible_id = str(self.active_chat_id or self.current_chat_id or state.get("id") or "").strip()
        state_id = str(state.get("id") or "").strip()
        if visible_id and state_id and state_id != visible_id:
            return False
        # The render projection reads the visible chat state.  Do not replace
        # legacy callers' local incremental test/setup data unless that state
        # is the same authoritative turn collection.
        if state.get("turns") is not turns:
            return False
        canonical_turn = turns[index]
        if turn is not None and turn is not canonical_turn and turn != canonical_turn:
            return False
        return True

    def _append_submitted_question_to_answer_list(self, turn_idx: int, turn: dict) -> bool:
        if not hasattr(self, "answer_list"):
            return False
        if self.view_mode != "active":
            return False
        # Incremental projection is safe only for the canonical visible turn.
        # A stale callback must fall back to its caller's reconciliation path;
        # rebuilding here would turn the normal send path into a foreground
        # list refresh and disturb screen-reader position.
        if not self._active_answer_turn_is_authoritative(turn_idx, turn):
            return False
        q = str((turn or {}).get("question") or "")
        attachments = (turn or {}).get("attachments") if isinstance((turn or {}).get("attachments"), list) else []
        if not q.strip() and not attachments:
            return False

        empty_rows: list[int] = []
        for row, meta in enumerate(list(self.answer_meta)):
            if row >= self.answer_list.GetCount():
                continue
            kind = meta[0] if meta else ""
            label = ""
            try:
                label = self.answer_list.GetString(row)
            except Exception:
                label = ""
            if kind == "info" or (kind == "context_usage" and label == "暂无"):
                empty_rows.append(row)
        if empty_rows:
            if hasattr(self, "answer_list_model"):
                for row in sorted(empty_rows, reverse=True):
                    try:
                        if row < len(self.answer_meta):
                            self.answer_list_model.remove(self._answer_row_id(self.answer_meta[row]))
                        else:
                            self.answer_list.Delete(row)
                    except Exception:
                        return False
            else:
                rows_to_delete = [0] * len(empty_rows) if empty_rows == list(range(len(empty_rows))) else sorted(empty_rows, reverse=True)
                for row in rows_to_delete:
                    try:
                        self.answer_list.Delete(row)
                    except Exception:
                        return False
            for row in sorted(empty_rows, reverse=True):
                del self.answer_meta[row]

        self._maybe_append_time_row_to_answer_list(int(turn_idx), turn)
        meta = ("user", int(turn_idx), "我", "")
        if hasattr(self, "answer_list_model"):
            self.answer_list_model.append(self._answer_row_id(meta), "我")
        else:
            self.answer_list.Append("我")
        self.answer_meta.append(meta)
        if q.strip():
            meta = ("question", int(turn_idx), q, "")
            if hasattr(self, "answer_list_model"):
                self.answer_list_model.append(self._answer_row_id(meta), q)
            else:
                self.answer_list.Append(q)
            self.answer_meta.append(meta)
        for label, meta in self._turn_attachment_rows(int(turn_idx), attachments, incoming=False):
            if hasattr(self, "answer_list_model"):
                self.answer_list_model.append(self._answer_row_id(meta), label)
            else:
                self.answer_list.Append(label)
            self.answer_meta.append(meta)

        cache = getattr(self, "_listbox_label_cache", None)
        if isinstance(cache, dict):
            cache[id(self.answer_list)] = tuple(self._listbox_strings(self.answer_list))
        return True

    def _find_answer_row_index(self, turn_idx: int) -> int:
        for row, meta in enumerate(self.answer_meta):
            if meta[0] == "answer" and meta[1] == turn_idx:
                return row
        return -1

    def _answer_list_structure_is_aligned(self) -> bool:
        if self.answer_list.GetCount() != len(self.answer_meta):
            return False
        model = getattr(self, "answer_list_model", None)
        model_ids = list(getattr(model, "visible_ids", []) or [])
        if not model_ids:
            return True
        if len(model_ids) != len(self.answer_meta):
            return False
        return all(
            model_ids[index] == self._answer_row_id(meta)
            for index, meta in enumerate(self.answer_meta)
        )

    def _has_visible_answer_turn_rows(self, turn_idx: int) -> bool:
        for meta in list(getattr(self, "answer_meta", []) or []):
            if not meta or len(meta) < 2:
                continue
            if meta[1] != turn_idx:
                continue
            if meta[0] in {"user", "question", "attachment", "attachment_error", "attachment_pending"}:
                return True
        return False

    def _append_completed_answer_to_answer_list(self, turn_idx: int, turn: dict) -> bool:
        if not hasattr(self, "answer_list"):
            return False
        if self.view_mode != "active":
            return False
        if self._active_answer_turn_is_authoritative(turn_idx, turn):
            answer_md = str((turn or {}).get("answer_md") or "")
            if not answer_md.strip() or answer_md == REQUESTING_TEXT:
                return False
            already_visible = self._find_answer_row_index(int(turn_idx)) >= 0
            if already_visible or not self._answer_list_structure_is_aligned():
                self._render_answer_list(refresh_execution=False)
                return (not already_visible) and self._find_answer_row_index(int(turn_idx)) >= 0
            # The visible question projection is current and only the newly
            # completed answer is absent. Use the row-level append below so a
            # completion does not clear/repaint old rows or disturb focus.
        if self._find_answer_row_index(int(turn_idx)) >= 0:
            return False
        attachments = (turn or {}).get("attachments") if isinstance((turn or {}).get("attachments"), list) else []
        if attachments and not self._has_visible_answer_turn_rows(int(turn_idx)):
            return False
        answer_md = str((turn or {}).get("answer_md") or "")
        if not answer_md.strip() or answer_md == REQUESTING_TEXT:
            return False
        _answer_md, answer_text = self._turn_answer_markdown(turn or {})
        answer_text = str(answer_text or "").strip()
        if not answer_text:
            return False
        self._refresh_context_usage_header_rows()
        self._maybe_append_time_row_to_answer_list(int(turn_idx), turn, "assistant")
        meta = ("ai", int(turn_idx), "小诸葛", "")
        if hasattr(self, "answer_list_model"):
            self.answer_list_model.append(self._answer_row_id(meta), "小诸葛")
        else:
            self.answer_list.Append("小诸葛")
        self.answer_meta.append(meta)
        meta = ("answer", int(turn_idx), answer_text, answer_md)
        if hasattr(self, "answer_list_model"):
            self.answer_list_model.append(self._answer_row_id(meta), answer_text)
        else:
            self.answer_list.Append(answer_text)
        self.answer_meta.append(meta)
        received_attachments = (turn or {}).get("received_attachments") if isinstance((turn or {}).get("received_attachments"), list) else []
        for label, meta in self._turn_attachment_rows(int(turn_idx), received_attachments, incoming=True):
            if hasattr(self, "answer_list_model"):
                self.answer_list_model.append(self._answer_row_id(meta), label)
            else:
                self.answer_list.Append(label)
            self.answer_meta.append(meta)
        self._active_answer_row_index = len(self.answer_meta) - 1
        cache = getattr(self, "_listbox_label_cache", None)
        if isinstance(cache, dict):
            cache[id(self.answer_list)] = tuple(self._listbox_strings(self.answer_list))
        return True

    def _update_active_answer_row(self, turn_idx: int, *, rebuild_if_missing: bool = True) -> bool:
        if self.view_mode != "active" or getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return False
        if (0 <= turn_idx < len(self.active_session_turns)
                and self.active_session_turns[turn_idx].get("request_status") == "done"
                and not self._answer_time_rows_are_current()):
            self._refresh_answer_list_preserving_selection(refresh_execution=False)
            self._active_answer_row_index = self._find_answer_row_index(turn_idx)
        if self._active_answer_turn_is_authoritative(turn_idx):
            # The submitted-question path already reconciles the full
            # canonical projection. Streaming deltas should update only their
            # answer row; rebuilding the whole list for every token makes long
            # conversations progressively slower. Fall back to reconciliation
            # only when the physical/model/metadata views have drifted.
            row = self._find_answer_row_index(int(turn_idx))
            if not self._answer_list_structure_is_aligned() or row < 0:
                if not rebuild_if_missing:
                    return False
                self._render_answer_list(refresh_execution=False)
                row = self._find_answer_row_index(int(turn_idx))
            self._active_answer_row_index = row
        row = self._active_answer_row_index
        if row < 0 or row >= self.answer_list.GetCount():
            row = self._find_answer_row_index(turn_idx)
            self._active_answer_row_index = row
        if row < 0:
            return False
        if not (0 <= turn_idx < len(self.active_session_turns)):
            return False
        answer_md, text = self._turn_answer_markdown(self.active_session_turns[turn_idx])
        item_type, idx, current_meta_text, current_meta_md = self.answer_meta[row]
        if current_meta_text == text and current_meta_md == answer_md:
            return True
        current_text = ""
        try:
            current_text = self.answer_list.GetString(row)
        except Exception:
            current_text = ""
        if current_text == text and current_meta_md == answer_md:
            return True
        row_id = self._answer_row_id(self.answer_meta[row])
        if hasattr(self, "answer_list_model"):
            self.answer_list_model.update_label(row_id, text)
        else:
            self.answer_list.SetString(row, text)
        self.answer_meta[row] = (item_type, idx, text, answer_md)
        self._request_listbox_repaint(self.answer_list)
        return True

    def _answer_markdown_for_output(self, answer_md: str, model: str = "") -> str:
        text = str(answer_md or "")
        if not text:
            return ""
        if self.codex_answer_english_filter_enabled and bindings.app.is_cli_filtered_model(model):
            text = re.sub(r"\[[^\]]+\]\([^)]+\)", "[文件路径]", text)
            text = re.sub(r"`[^`]*(?:[A-Za-z]:[\\/]|/)[^`]*`", "[文件路径]", text)
            text = re.sub(r"`[^`]*\.(?:py|md|txt|json|yaml|yml)[^`]*`", "[文件路径]", text)
            text = re.sub(r"(?<![\w/.-])(?:[A-Za-z]:[\\/][^\s\]\)]+|/[^\s\]\)]+)", "[文件路径]", text)
            text = re.sub(r"pytest\s+tests/[^\n]+", "pytest [文件路径]", text)
            text = re.sub(r"\btest_[A-Za-z0-9_]+\b", "[测试项]", text)
        return text

    def _turn_answer_markdown(self, turn: dict) -> tuple[str, str]:
        answer_md = str((turn or {}).get("answer_md") or "")
        model = str((turn or {}).get("model") or "")
        if answer_md == REQUESTING_TEXT:
            return answer_md, REQUESTING_TEXT
        if not answer_md.strip():
            status = str((turn or {}).get("request_status") or "").strip()
            if status == "failed":
                err = str((turn or {}).get("request_error") or "").strip()
                return "", err or "上次未完成回答恢复失败，可手动继续"
            return "", ""
        compact = compact_markdown_summary(self._answer_markdown_for_output(answer_md, model))
        return answer_md, filter_answer_display_text(remove_emojis(bindings.app.md_to_plain(compact)))

    def _build_answer_detail_html(self, answer_md: str, model: str = "") -> str:
        a_html = markdown.markdown(answer_md, extensions=["extra", "fenced_code", "tables", "sane_lists"])
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>回答详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;}"
            "</style></head><body>"
            "<h2>回答详情</h2>"
            f"{a_html}"
            "</body></html>"
        )

    def _next_answer_message_order(self) -> int:
        known = [int(t.get(field) or 0) for t in getattr(self, "active_session_turns", [])
                 if isinstance(t, dict) for field in ("question_order", "answer_order")]
        value = max(bindings.app.time.time_ns(), int(getattr(self, "_answer_message_order", 0)) + 1, max(known, default=0) + 1)
        self._answer_message_order = value
        return value

    def _schedule_accepted_answer_refresh(self, chat_id: str, turn: dict) -> None:
        if self.view_mode != "active" or not any(item is turn for item in self.active_session_turns):
            return
        visible_id = str(self.active_chat_id or self.current_chat_id or "")
        if str(chat_id or visible_id) != visible_id:
            return
        owner = (visible_id, id(self.active_session_turns))
        if getattr(self, "_answer_refresh_deadline_owner", None) == owner:
            if not any(item is turn for item in self._answer_refresh_deadline_turns):
                self._answer_refresh_deadline_turns.append(turn)
            return
        timer = getattr(self, "_answer_refresh_deadline_timer", None)
        if timer is not None:
            timer.Stop()
        self._answer_refresh_deadline_owner = owner
        self._answer_refresh_deadline_turns = [turn]
        self._answer_refresh_deadline_timer = self._call_later_if_alive(1000, self._flush_accepted_answer_refresh, owner)

    def _flush_accepted_answer_refresh(self, owner) -> None:
        if owner != getattr(self, "_answer_refresh_deadline_owner", None):
            return
        self._answer_refresh_deadline_timer = None
        self._answer_refresh_deadline_owner = None
        accepted_turns = self._answer_refresh_deadline_turns
        self._answer_refresh_deadline_turns = []
        current_owner = (str(self.active_chat_id or self.current_chat_id or ""), id(self.active_session_turns))
        if self.view_mode != "active" or owner != current_owner or not self._is_ui_alive():
            return
        # Reconcile the current owner and selection at the deadline, independent
        # of navigation quiet. Execution/history keep their existing schedule.
        self._background_answer_list_dirty = False
        if accepted_turns and all(self._accepted_answer_is_visible(turn) for turn in accepted_turns):
            return
        self._refresh_answer_list_preserving_selection(refresh_execution=False)

    def _answer_time_rows_are_current(self) -> bool:
        if not self._answer_list_structure_is_aligned():
            return False
        content = [(self.answer_list.GetString(index), meta)
                   for index, meta in enumerate(self.answer_meta) if meta[0] != "more"]
        header_count = 0
        for _, meta in content:
            if meta[1] >= 0:
                break
            header_count += 1
        _, projected, _ = self._answer_rows_with_limit(
            [row for row, _ in content], [meta for _, meta in content], header_count,
            force_has_more=any(meta[0] == "more" for meta in self.answer_meta),
        )
        return [(index, meta) for index, meta in enumerate(projected) if meta[0] == "time"] == [
            (index, meta) for index, meta in enumerate(self.answer_meta) if meta[0] == "time"]

    def _accepted_answer_is_visible(self, turn: dict) -> bool:
        index = next((i for i, item in enumerate(self.active_session_turns) if item is turn), -1)
        row = self._find_answer_row_index(index) if index >= 0 else -1
        if row < 0 or row >= len(self.answer_meta) or not self._answer_list_structure_is_aligned():
            return False
        answer_md, answer_text = self._turn_answer_markdown(turn)
        if self.answer_meta[row][2:4] != (answer_text, answer_md) or self.answer_list.GetString(row) != answer_text:
            return False
        if not self._answer_time_rows_are_current():
            return False
        received = turn.get("received_attachments") if isinstance(turn.get("received_attachments"), list) else []
        return all(meta in self.answer_meta for _, meta in self._turn_attachment_rows(index, received, incoming=True))

    def _stop_answer_refresh_deadline(self, event=None) -> None:
        if event is not None and event.GetId() != getattr(self, "_answer_refresh_destroy_window_id", None):
            event.Skip()
            return
        timer = getattr(self, "_answer_refresh_deadline_timer", None)
        if timer is not None:
            timer.Stop()
        self._answer_refresh_deadline_timer = None
        self._answer_refresh_deadline_owner = None
        self._answer_refresh_deadline_turns = []
        self._stop_navigation_quiet_flush()
        if event is not None:
            event.Skip()

    def _mark_background_answer_list_dirty(self) -> None:
        self._background_answer_list_dirty = True
        counts = getattr(self, "_deferred_background_ui_counts", {})
        counts["answer"] = int(counts.get("answer", 0)) + 1
        self._deferred_background_ui_counts = counts

    def _queue_answer_char_redirect(self, ch: str) -> None:
        if not ch or not self._is_ui_alive():
            return
        self._answer_committed_buffer += ch
        if self._answer_redirect_timer and self._answer_redirect_timer.IsRunning():
            self._answer_redirect_timer.Stop()
        self._answer_redirect_timer = self._call_later_if_alive(220, self._flush_answer_committed_buffer_to_input)

    def _flush_answer_committed_buffer_to_input(self) -> None:
        text = self._answer_committed_buffer
        self._answer_committed_buffer = ""
        if not text:
            return
        self.input_edit.SetFocus()
        old = self.input_edit.GetValue()
        self.input_edit.SetValue(old + text)
        self.input_edit.SetInsertionPointEnd()

    def _on_delta(self, turn_idx: int, delta: str, chat_id: str = ""):
        self._on_delta_for_chat(turn_idx, delta, chat_id or self.active_chat_id or self.current_chat_id or "")

    def _on_delta_for_chat(self, turn_idx: int, delta: str, chat_id: str = ""):
        if not delta:
            return
        target_chat, target_turns, is_current_chat = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        if turn_idx < 0 or turn_idx >= len(target_turns):
            return
        cur = str(target_turns[turn_idx].get("answer_md") or "")
        if cur == REQUESTING_TEXT:
            cur = ""
        target_turns[turn_idx]["answer_md"] = cur + remove_emojis(delta)
        target_turns[turn_idx]["request_last_attempt_at"] = bindings.app.time.time()
        resolved_chat_id = str(chat_id or (target_chat.get("id") if isinstance(target_chat, dict) else "") or self.active_chat_id or self.current_chat_id or "").strip()
        self._mark_chat_turns_dirty(resolved_chat_id, turn_idx)
        # 流式阶段不刷新回答列表；待完成后一次性展示完整回答。
        self._defer_chat_state_save()

    def _on_done(self, turn_idx: int, full: str, err: str, used_model: str, fallback_msg: str, chat_id: str = ""):
        target_chat, target_turns, is_current_chat = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        should_render = True
        if turn_idx < 0 and is_openclaw_model(used_model) and not err:
            should_render = False
        if not is_current_chat:
            should_render = False
        if 0 <= turn_idx < len(target_turns):
            callback_operation_id = str(target_turns[turn_idx].get("clear_operation_id") or "").strip()
            if callback_operation_id:
                store = getattr(self, "chat_store", None)
                operation = store.get_clear_operation(callback_operation_id) if store is not None and hasattr(store, "get_clear_operation") else None
                expected_revision = target_turns[turn_idx].get("clear_revision")
                if (
                    not operation or operation.get("state") == "superseded"
                    or int(operation.get("revision") or -1) != int(expected_revision or -2)
                ):
                    if store is not None and hasattr(store, "quarantine"):
                        store.quarantine("SUPERSEDED_CLEAR_RESULT", {
                            "operation_id": callback_operation_id,
                            "chat_id": str(chat_id or ""),
                        })
                    return
            was_done = str(target_turns[turn_idx].get("request_status") or "") == "done"
            clear_operation_id = str(target_turns[turn_idx].get("clear_operation_id") or "").strip()
            if not err and not was_done and not is_openclaw_model(used_model):
                self._record_chat_activity(target_chat, bindings.app.time.time())
            if used_model:
                target_turns[turn_idx]["model"] = used_model
            if err:
                target_turns[turn_idx]["answer_md"] = err
            else:
                if is_openclaw_model(used_model):
                    should_render = False
                elif is_codex_model(used_model):
                    if full.strip():
                        target_turns[turn_idx]["answer_md"] = remove_emojis(full.strip())
                elif is_claudecode_model(used_model):
                    clean = remove_emojis(full.strip())
                    target_turns[turn_idx]["answer_md"] = clean if clean else "未返回任何内容。"
                else:
                    clean = remove_emojis(full.strip())
                    target_turns[turn_idx]["answer_md"] = clean if clean else "未返回任何内容。"
            if str(target_turns[turn_idx].get("request_status") or "") == "pending":
                if err:
                    self._mark_turn_request_failed(target_turns[turn_idx], err)
                else:
                    self._mark_turn_request_done(target_turns[turn_idx])
            elif not err and not is_openclaw_model(used_model):
                self._mark_turn_request_done(target_turns[turn_idx])
            if clear_operation_id:
                store = getattr(self, "chat_store", None)
                if store is not None and hasattr(store, "transition_clear_operation"):
                    try:
                        store.transition_clear_operation(
                            clear_operation_id, "resend_blocked" if err else "completed_with_resend",
                            failure_code="PROVIDER_FAILURE" if err else "",
                        )
                    except (KeyError, ValueError):
                        pass
            if not err:
                for attachment in self._extract_existing_file_attachments_from_text(str(target_turns[turn_idx].get("answer_md") or ""), str(used_model or "")):
                    self._record_received_attachment(target_turns[turn_idx], attachment)
                self._refresh_context_usage_after_done(target_chat, target_turns, turn_idx, used_model)
            self._active_request_count = 0
            if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""} and isinstance(target_chat, dict):
                title = self._summarize_last_turn_locally(target_turns)
                if title and not target_chat.get("title_manual"):
                    target_chat["title"] = title
            resolved_dirty_chat_id = str(chat_id or (target_chat.get("id") if isinstance(target_chat, dict) else "") or self.active_chat_id or self.current_chat_id or "").strip()
            self._mark_chat_turns_dirty(resolved_dirty_chat_id, turn_idx)
            self._request_execution_list_sync(target_chat)

        if is_current_chat:
            self.is_running = False
            self.new_chat_button.Enable()
            self._set_input_hint_idle()
        if fallback_msg and is_current_chat:
            self.selected_model = used_model or self.selected_model
            if used_model:
                self.model_combo.SetValue(bindings.app.model_display_name(used_model))
            self.SetStatusText(fallback_msg)
        elif is_current_chat:
            if is_openclaw_model(used_model) and not err:
                self.SetStatusText("已发送，等待 OpenClaw 同步回复")
            else:
                self.SetStatusText("答复完成")
        resolved_chat_id = chat_id or self.active_chat_id or self.current_chat_id or ""
        if (not is_openclaw_model(used_model)) and resolved_chat_id:
            self._push_remote_state(resolved_chat_id)
            if not err and 0 <= turn_idx < len(target_turns):
                final_text = str(target_turns[turn_idx].get("answer_md") or "").strip()
                if final_text and final_text != REQUESTING_TEXT:
                    self._queue_remote_final(resolved_chat_id, turn_idx)
            self._push_remote_history_changed(resolved_chat_id)
        self._defer_chat_state_save()
        if self._is_ui_alive():
            if resolved_chat_id:
                self._upsert_history_row(
                    resolved_chat_id,
                    allow_reorder=True,
                )
            else:
                self._refresh_history(None)

        should_play_finish_sound = (not is_openclaw_model(used_model)) or err
        if should_play_finish_sound:
            self._play_finish_sound()
        if should_render and self.view_mode == "active":
            turn = target_turns[turn_idx] if 0 <= turn_idx < len(target_turns) else {}
            self._schedule_accepted_answer_refresh(resolved_chat_id, turn)
            if self._navigation_quiet_active():
                self._mark_background_answer_list_dirty()
                return
            if self._append_completed_answer_to_answer_list(turn_idx, turn):
                self._focus_latest_answer()
            else:
                self._refresh_answer_list_preserving_selection()

    def _focus_latest_answer(self):
        if getattr(self, "_chat_navigation_timer", None) is not None:
            return
        if self._detail_panel_mode() != "answers":
            return
        if not self._can_focus_completion_result():
            return
        for i in range(len(self.answer_meta) - 1, -1, -1):
            if self.answer_meta[i][0] == "answer":
                self.answer_list.SetSelection(i)
                self.answer_list.SetFocus()
                break

    def _on_answer_key_down(self, event):
        if self._handle_ctrl_history_navigation(event):
            return
        if (getattr(self, "_chat_navigation_answer_rows_deferred", False)
                and event.GetKeyCode() not in (wx.WXK_CONTROL, wx.WXK_SHIFT, wx.WXK_ALT)):
            self._cancel_chat_navigation_focus()
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_input_focus_space_shortcut(event):
            return
        self._touch_navigation_quiet_window(event)
        key = event.GetKeyCode()
        ctrl = self._event_control_down(event)
        alt = self._event_alt_down(event)
        shift = bool(getattr(event, "ShiftDown", lambda: False)())
        if not ctrl and not alt and key in (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_HOME, wx.WXK_END, wx.WXK_PAGEUP, wx.WXK_PAGEDOWN):
            if self._move_answer_list_selection_for_key(key):
                self._confirm_answer_read(self._selected_read_target())
                return
        if self._handle_primary_tab_navigation(event):
            return
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            event.Skip()
            return
        if ctrl and key in (ord("C"), ord("c")):
            self._copy_selected_answer_to_clipboard()
            stop_propagation = getattr(event, "StopPropagation", None)
            if callable(stop_propagation):
                stop_propagation()
            return
        item_type, turn_idx, plain, _ = self.answer_meta[idx]
        if self._is_context_menu_key(key):
            self._show_answer_menu()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            signature = self._answer_viewer_selection_signature()
            read_target = self._selected_read_target()
            handled = self._try_open_selected_answer_detail() if shift else self._open_selected_answer_text_viewer()
            if handled:
                self._confirm_answer_read(read_target)
                self._answer_viewer_duplicate_owner = signature
                self._answer_viewer_duplicate_until = bindings.app.time.monotonic() + 0.25
                self._answer_viewer_duplicate_events = 2
                return
        event.Skip()

    def _copy_selected_answer_to_clipboard(self) -> bool:
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return False
        item_type, _turn_idx, plain, detail = self.answer_meta[idx]
        if item_type not in ("question", "answer"):
            return False
        turns = self._get_view_turns()
        turn = turns[_turn_idx] if 0 <= _turn_idx < len(turns) and isinstance(turns[_turn_idx], dict) else {}
        canonical_value = turn.get("answer_md") if item_type == "answer" else turn.get("question")
        source_text = canonical_value if isinstance(canonical_value, str) else ""
        if not source_text:
            return False
        if not self._set_clipboard_text(source_text):
            return False
        self.SetStatusText("已复制")
        return True

    def _plain_text_for_clipboard(self, text: str) -> str:
        cleaned = remove_emojis(md_to_plain_preserving_paragraphs(str(text or ""))).strip()
        if cleaned:
            return cleaned
        return "\n\n".join(line.strip() for line in str(text or "").splitlines() if line.strip()).strip()

    def _move_answer_list_selection_for_key(self, key: int) -> bool:
        count = self.answer_list.GetCount()
        if count <= 0:
            return False
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = 0
        if key == wx.WXK_UP:
            new_idx = max(0, idx - 1)
        elif key == wx.WXK_DOWN:
            new_idx = min(count - 1, idx + 1)
        elif key == wx.WXK_HOME:
            new_idx = 0
        elif key == wx.WXK_END:
            new_idx = count - 1
        elif key == wx.WXK_PAGEUP:
            new_idx = max(0, idx - max(1, self.answer_list.GetClientSize().height // max(1, self.answer_list.GetCharHeight())))
        elif key == wx.WXK_PAGEDOWN:
            new_idx = min(count - 1, idx + max(1, self.answer_list.GetClientSize().height // max(1, self.answer_list.GetCharHeight())))
        else:
            return False
        if new_idx == idx:
            return True
        self.answer_list.SetSelection(new_idx)
        try:
            if not self.answer_list.HasFocus():
                self.answer_list.SetFocus()
        except Exception:
            pass
        return True

    def _on_answer_char(self, event):
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            self._cancel_chat_navigation_focus()
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._consume_answer_viewer_duplicate_event():
                return
            shift = bool(getattr(event, "ShiftDown", lambda: False)())
            read_target = self._selected_read_target()
            handled = self._try_open_selected_answer_detail() if shift else self._open_selected_answer_text_viewer()
            if handled:
                self._confirm_answer_read(read_target)
                return
        ch = self._extract_committed_char(event)
        if ch:
            self._queue_answer_char_redirect(ch)
        event.Skip()

    def _on_answer_activate(self, _event):
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            self._cancel_chat_navigation_focus()
            return
        if self._consume_answer_viewer_duplicate_event():
            return
        read_target = self._selected_read_target()
        if self._open_selected_answer_text_viewer():
            self._confirm_answer_read(read_target)

    def _answer_viewer_selection_signature(self) -> tuple[str, str, int] | None:
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return None
        meta = self.answer_meta[idx]
        return (self._visible_answer_owner_chat_id(), str(meta[0]), int(meta[1]))

    def _consume_answer_viewer_duplicate_event(self) -> bool:
        if bindings.app.time.monotonic() > float(getattr(self, "_answer_viewer_duplicate_until", 0.0) or 0.0):
            return False
        if getattr(self, "_answer_viewer_duplicate_owner", None) != self._answer_viewer_selection_signature():
            return False
        remaining = int(getattr(self, "_answer_viewer_duplicate_events", 0) or 0)
        if remaining <= 0:
            return False
        self._answer_viewer_duplicate_events = remaining - 1
        return True

    def _selected_answer_text_viewer_content(self) -> tuple[str, str] | None:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return None
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return None
        item_type, turn_idx, plain, detail = self.answer_meta[idx]
        if item_type not in ("question", "answer"):
            return None
        turns = self._get_view_turns()
        turn = turns[turn_idx] if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict) else {}
        if item_type == "question":
            source = str((turn or {}).get("question") or detail or plain or "")
            title = "回答详情"
        else:
            answer_md = str((turn or {}).get("answer_md") or detail or plain or "")
            if not answer_md or answer_md == REQUESTING_TEXT:
                return None
            source = answer_md
            title = "回答详情"
        text = str(source)
        if not text and item_type != "answer":
            return None
        return title, text

    def _open_answer_text_viewer(
        self, title: str, text: str, payload: AnswerViewerPayload | None = None,
        _answer_markdown: bool = False,
        _read_target: dict | None = None,
    ) -> bool:
        if bool(getattr(self, "_answer_viewer_open", False)):
            return False
        dlg = bindings.app.AnswerTextViewerDialog(
            self,
            title,
            text,
            payload=payload,
            on_continue=self._continue_from_answer_text_viewer if payload is not None else None,
        )
        self._answer_viewer_open = True
        self._active_answer_text_viewer = dlg
        shown = False
        if _read_target:
            def on_shown(event):
                if event.IsShown():
                    self._confirm_answer_read(_read_target)
                event.Skip()
            dlg.Bind(wx.EVT_SHOW, on_shown)
        try:
            if _answer_markdown:
                dlg._set_answer_display_text()
            dlg._shown_modally = True
            dlg.ShowModal()
            shown = True
        except Exception:
            shown = False
        finally:
            self._answer_viewer_open = False
            self._active_answer_text_viewer = None
            try:
                dlg.Destroy()
            except RuntimeError:
                pass
        return shown

    def _open_selected_answer_text_viewer(self) -> bool:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return False
        idx = self.answer_list.GetSelection()
        if idx != wx.NOT_FOUND and idx < len(self.answer_meta) and self.answer_meta[idx][0] == "more":
            self._show_more_answer_rows()
            try:
                if self.answer_list.GetCount() > 0:
                    self.answer_list.SetSelection(0)
            except Exception:
                pass
            return True
        if idx != wx.NOT_FOUND and idx < len(self.answer_meta) and self.answer_meta[idx][0] == "attachment":
            return self._try_open_selected_answer_detail()
        content = self._selected_answer_text_viewer_content()
        if not content:
            return False
        title, text = content
        payload = self._selected_answer_viewer_payload()
        return self._open_answer_text_viewer(title, text, payload, self.answer_meta[idx][0] == "answer", self._selected_read_target(idx))

    def _visible_answer_owner_chat_id(self) -> str:
        if self.view_mode == "history":
            return str(self.view_history_id or "").strip()
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        return str(self.active_chat_id or self.current_chat_id or state.get("id") or "").strip()

    def _selected_answer_viewer_payload(self) -> AnswerViewerPayload | None:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return None
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return None
        item_type, turn_idx, _plain, _detail = self.answer_meta[idx]
        if item_type != "answer":
            return None
        turns = self._get_view_turns()
        if not (0 <= turn_idx < len(turns)) or not isinstance(turns[turn_idx], dict):
            return None
        turn = turns[turn_idx]
        answer_md = str(turn.get("answer_md") or "")
        if not answer_md or answer_md == REQUESTING_TEXT:
            return None
        chat_id = self._visible_answer_owner_chat_id()
        if not chat_id:
            return None
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(chat_id)
        if not isinstance(chat, dict):
            return None
        model = bindings.app.normalize_model_id(str(turn.get("model") or chat.get("model") or ""))
        if is_codex_model(model):
            session_id = str(turn.get("codex_thread_id") or chat.get("codex_thread_id") or "").strip()
            turn_id = str(turn.get("codex_turn_id") or turn.get("turn_id") or "").strip()
        elif is_kimi_model(model):
            session_id = str(turn.get("kimi_session_id") or chat.get("kimi_session_id") or "").strip()
            turn_id = str(turn.get("kimi_turn_id") or turn.get("turn_id") or "").strip()
        elif is_claudecode_model(model):
            session_id = str(turn.get("claudecode_session_id") or chat.get("claudecode_session_id") or "").strip()
            turn_id = str(turn.get("turn_id") or turn.get("id") or "").strip()
        else:
            session_id = ""
            turn_id = str(turn.get("turn_id") or turn.get("id") or turn.get("answer_external_event_id") or "").strip()
        if not turn_id or ((is_codex_model(model) or is_kimi_model(model) or is_claudecode_model(model)) and not session_id):
            return None
        return AnswerViewerPayload(chat_id, int(turn_idx), turn_id, session_id, model, answer_md)

    def _continue_from_answer_text_viewer(self, payload: AnswerViewerPayload) -> bool:
        if (
            not isinstance(payload, AnswerViewerPayload)
            or not payload.chat_id
            or bool(getattr(self, "_closing", False))
            or not bindings.app._wx_target_is_alive(self)
        ):
            return False
        current = self._current_chat_state if payload.chat_id in {
            str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip(),
            str((self._current_chat_state or {}).get("id") or "").strip(),
        } else None
        chat = current if isinstance(current, dict) else self._find_archived_chat(payload.chat_id)
        if not isinstance(chat, dict):
            return False
        chat = self._hydrate_chat_from_store(chat, include_execution_steps=False) or chat
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        if not (0 <= payload.turn_idx < len(turns)) or not isinstance(turns[payload.turn_idx], dict):
            return False
        turn = turns[payload.turn_idx]
        if str(turn.get("answer_md") or "") != payload.answer_md:
            return False
        if bindings.app.normalize_model_id(str(turn.get("model") or chat.get("model") or "")) != payload.model:
            return False
        if any(str(item.get("request_status") or "").strip() == "pending" for item in turns if isinstance(item, dict)):
            return False
        if bool(chat.get("codex_turn_active")) or bool(chat.get("kimi_turn_active")) or bool(chat.get("kimi_request_queue")):
            return False
        if chat is self._current_chat_state and (
            bool(getattr(self, "active_codex_turn_active", False))
            or bool(getattr(self, "active_kimi_turn_active", False))
            or int(getattr(self, "_active_request_count", 0) or 0) > 0
        ):
            return False
        if is_claudecode_model(payload.model):
            with self._active_claudecode_client_lock:
                if self._active_claudecode_client is not None:
                    return False
        fresh = self._answer_viewer_identity_for_turn(turn, chat, payload.turn_idx, payload.model)
        if fresh != (payload.turn_id, payload.provider_session_id):
            return False
        snapshot = self._capture_answer_continue_state()
        if is_codex_model(payload.model):
            for key, value in (
                ("codex_thread_id", payload.provider_session_id),
                ("codex_turn_id", payload.turn_id),
            ):
                chat[key] = value
        elif is_kimi_model(payload.model):
            for key, value in (
                ("kimi_session_id", payload.provider_session_id),
                ("kimi_turn_id", payload.turn_id),
            ):
                chat[key] = value
        elif is_claudecode_model(payload.model):
            chat["claudecode_session_id"] = payload.provider_session_id
        if is_codex_model(payload.model):
            self.active_codex_thread_id = payload.provider_session_id
            self.active_codex_turn_id = payload.turn_id
        elif is_kimi_model(payload.model):
            self.active_kimi_session_id = payload.provider_session_id
            self.active_kimi_turn_id = payload.turn_id
        elif is_claudecode_model(payload.model):
            self.active_claudecode_session_id = payload.provider_session_id
        try:
            ok, _message = self._submit_question("继续", source="local", model=payload.model, chat_id=payload.chat_id)
        except Exception:
            ok = False
        if not ok:
            self._restore_answer_continue_state(snapshot)
            return False
        return True

    def _capture_answer_continue_state(self) -> dict:
        focus = wx.Window.FindFocus()
        provider_fields = (
            "active_codex_turn_active", "active_codex_pending_prompt", "active_codex_request_queue",
            "active_kimi_turn_active", "active_kimi_pending_prompt", "active_kimi_request_queue",
            "active_openclaw_session_key", "active_openclaw_session_id", "active_openclaw_session_file",
            "active_openclaw_sync_offset", "active_openclaw_last_event_id", "active_openclaw_last_synced_at",
        )
        state_graph = copy.deepcopy({
            "active_session_turns": self.active_session_turns,
            "current_chat_state": self._current_chat_state,
            "archived_chats": self.archived_chats,
        })
        return {
            **state_graph,
            "active_chat_id": self.active_chat_id,
            "current_chat_id": self.current_chat_id,
            "view_mode": self.view_mode,
            "view_history_id": self.view_history_id,
            "active_turn_idx": self.active_turn_idx,
            "selected_model": self.selected_model,
            "active_codex_thread_id": self.active_codex_thread_id,
            "active_codex_turn_id": self.active_codex_turn_id,
            "active_kimi_session_id": self.active_kimi_session_id,
            "active_kimi_turn_id": self.active_kimi_turn_id,
            "active_claudecode_session_id": self.active_claudecode_session_id,
            "active_request_count": self._active_request_count,
            "is_running": self.is_running,
            "pending_input_attachments": copy.deepcopy(self._pending_input_attachments),
            "input_value": self.input_edit.GetValue(),
            "input_selection": self.input_edit.GetSelection(),
            "model_value": self.model_combo.GetValue(),
            "answer_strings": list(self.answer_list.GetStrings()),
            "answer_meta": copy.deepcopy(self.answer_meta),
            "answer_visible_ids": list(self.answer_list_model.visible_ids),
            "answer_labels_by_id": dict(self.answer_list_model.labels_by_id),
            "answer_selection": self.answer_list.GetSelection(),
            "history_selection": self.history_list.GetSelection(),
            "history_strings": list(self.history_list.GetStrings()),
            "history_ids": list(self.history_ids),
            "history_visible_ids": list(self.history_list_model.visible_ids),
            "history_labels_by_id": dict(self.history_list_model.labels_by_id),
            "focus": focus,
            "provider_state": {name: copy.deepcopy(getattr(self, name, None)) for name in provider_fields},
        }

    def _restore_answer_continue_state(self, snapshot: dict) -> None:
        self.active_session_turns = snapshot["active_session_turns"]
        self._current_chat_state = snapshot["current_chat_state"]
        self.archived_chats = snapshot["archived_chats"]
        for key in (
            "active_chat_id", "current_chat_id", "view_mode", "view_history_id", "active_turn_idx",
            "selected_model", "active_codex_thread_id", "active_codex_turn_id",
            "active_kimi_session_id", "active_kimi_turn_id", "active_claudecode_session_id",
            "is_running",
        ):
            setattr(self, key, snapshot[key])
        self._active_request_count = snapshot["active_request_count"]
        self._pending_input_attachments = snapshot["pending_input_attachments"]
        for name, value in snapshot.get("provider_state", {}).items():
            setattr(self, name, value)
        self.input_edit.ChangeValue(snapshot["input_value"])
        self.input_edit.SetSelection(*snapshot.get("input_selection", (0, 0)))
        self.model_combo.SetValue(snapshot["model_value"])
        self.answer_meta = snapshot["answer_meta"]
        self.answer_list.Set(snapshot["answer_strings"])
        self.answer_list_model.visible_ids = list(snapshot["answer_visible_ids"])
        self.answer_list_model.labels_by_id = dict(snapshot["answer_labels_by_id"])
        answer_selection = snapshot["answer_selection"]
        if answer_selection != wx.NOT_FOUND and answer_selection < self.answer_list.GetCount():
            self.answer_list.SetSelection(answer_selection)
        self.history_ids = snapshot["history_ids"]
        self.history_list.Set(snapshot["history_strings"])
        self.history_list_model.visible_ids = list(snapshot["history_visible_ids"])
        self.history_list_model.labels_by_id = dict(snapshot["history_labels_by_id"])
        history_selection = snapshot["history_selection"]
        if history_selection != wx.NOT_FOUND and history_selection < self.history_list.GetCount():
            self.history_list.SetSelection(history_selection)
        focus = snapshot.get("focus")
        if bindings.app._wx_target_is_alive(focus):
            focus.SetFocus()

    @staticmethod
    def _answer_viewer_identity_for_turn(turn: dict, chat: dict, turn_idx: int, model: str) -> tuple[str, str]:
        if is_codex_model(model):
            return (
                str(turn.get("codex_turn_id") or turn.get("turn_id") or "").strip(),
                str(turn.get("codex_thread_id") or chat.get("codex_thread_id") or "").strip(),
            )
        if is_kimi_model(model):
            return (
                str(turn.get("kimi_turn_id") or turn.get("turn_id") or "").strip(),
                str(turn.get("kimi_session_id") or chat.get("kimi_session_id") or "").strip(),
            )
        if is_claudecode_model(model):
            return (
                str(turn.get("turn_id") or turn.get("id") or "").strip(),
                str(turn.get("claudecode_session_id") or chat.get("claudecode_session_id") or "").strip(),
            )
        return (str(turn.get("turn_id") or turn.get("id") or turn.get("answer_external_event_id") or "").strip(), "")

    def _try_open_selected_answer_detail(self) -> bool:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return False
        read_target = self._selected_read_target()
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return False
        item_type, turn_idx, _, detail = self.answer_meta[idx]
        if item_type == "more":
            self._show_more_answer_rows()
            try:
                if self.answer_list.GetCount() > 0:
                    self.answer_list.SetSelection(0)
            except Exception:
                pass
            return True
        if item_type == "attachment":
            path = str(detail or "").strip()
            if not path or not Path(path).is_file():
                return False
            try:
                bindings.app.os.startfile(path)  # type: ignore[attr-defined]
                self.SetStatusText("已打开附件")
            except Exception:
                wx.MessageBox("打开附件失败。", "提示", wx.OK | wx.ICON_WARNING)
                return False
            return True
        if item_type not in ("question", "answer"):
            return False
        turns = self._get_view_turns()
        if not (0 <= turn_idx < len(turns)):
            return False
        turn = turns[turn_idx]
        try:
            if item_type == "question":
                question = str(turn.get("question") or "").strip()
                if not question:
                    return False
                page_path = self._ensure_question_detail_page(turn, turn_idx)
                status_text = "已打开问题详情网页"
            else:
                answer_md = str(turn.get("answer_md") or "")
                if not answer_md or answer_md == REQUESTING_TEXT:
                    return False
                page_path = self._ensure_answer_detail_page(turn, turn_idx)
                status_text = "已打开回答详情网页"
            if self._open_local_webpage(page_path) is False:
                raise RuntimeError("详情网页未成功打开")
            self.SetStatusText(status_text)
            self._save_state()
            if item_type == "answer":
                self._confirm_answer_read(read_target)
        except Exception:
            wx.MessageBox("打开详情网页失败。", "提示", wx.OK | wx.ICON_WARNING)
            return False
        return True
