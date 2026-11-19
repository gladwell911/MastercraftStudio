"""Codex session requests, events, and worker ownership."""

from __future__ import annotations

import codex_worker_protocol
import copy
import re
import wx
from codex_client import (
    CODEX_APPROVAL_MODES,
    CODEX_SANDBOX_MODES,
    CodexEvent,
    DEFAULT_CODEX_APPROVAL_MODE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_SANDBOX,
    codex_approval_parameters,
    codex_service_tier_label,
    is_codex_model,
    normalize_codex_approval_mode,
    normalize_codex_sandbox,
    normalize_codex_service_tier,
)
from context_usage import context_usage_from_dict, format_context_usage_label
from mc_frame_settings import (
    CODEX_UI_EVENT_BATCH_DELAY_MS,
    CODEX_UI_EVENT_BATCH_SIZE,
    CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE,
    REQUESTING_TEXT,
)
from pathlib import Path

import mc_frame_bindings as bindings


class CodexSessionsMixin:
    @staticmethod
    def _parse_codex_local_command(question: str) -> dict | None:
        text = str(question or "").strip()
        if not text.startswith("/") or text == "/":
            return None
        body = text[1:]
        parts = body.split(None, 1)
        name = parts[0].strip().lower()
        if not name:
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9:_\.-]*", name):
            return None
        args = parts[1].strip() if len(parts) > 1 else ""
        return {"raw": text, "name": name, "args": args}

    @staticmethod
    def _codex_local_command_name(question: str) -> str:
        command = bindings.app.ChatFrame._parse_codex_local_command(question)
        return str(command.get("name") or "") if command else ""

    @staticmethod
    def _codex_supported_local_commands() -> tuple[tuple[str, str], ...]:
        return (
            ("status", "显示账号、模型、沙箱、线程、上下文和额度状态"),
            ("help", "列出本程序支持的 Codex 斜杠命令"),
            ("compact", "请求 Codex 压缩当前线程上下文"),
            ("model", "不带参数显示当前模型；带 Codex 模型名时切换模型"),
            ("speed", "不带参数显示当前速度；支持 fast、standard、normal"),
            ("new", "开始一个新的本地聊天"),
            ("clear", "清除当前聊天关联的 Codex 线程状态"),
            ("stop", "中断当前活跃 Codex turn"),
        )

    def _build_codex_help_markdown(self) -> str:
        lines = [
            "## Codex 斜杠命令",
            "",
            "本程序会识别所有以 `/` 开头的 Codex 输入。以下命令已在桌面端实现：",
            "",
        ]
        lines.extend(f"- `/{name}`：{description}" for name, description in self._codex_supported_local_commands())
        lines.extend(
            [
                "",
                "其他 `/...` 命令会被识别为 Codex 斜杠命令，但当前桌面端暂不支持，不会被当成普通聊天发送。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_codex_unsupported_command_markdown(command: str, args: str = "") -> str:
        suffix = f" {args}" if str(args or "").strip() else ""
        return (
            "## Codex 斜杠命令暂不支持\n\n"
            f"`/{command}{suffix}` 已被识别为 Codex 斜杠命令，但当前桌面端还没有对应实现。\n\n"
            "为避免误操作，这条输入不会作为普通聊天内容发送给 Codex。"
        )

    def _codex_thread_id_for_chat(self, chat: dict | None) -> str:
        thread_id = str((chat or {}).get("codex_thread_id") or "").strip()
        if thread_id:
            return thread_id
        if chat is self._current_chat_state:
            return str(self.active_codex_thread_id or "").strip()
        return ""

    def _codex_turn_id_for_chat(self, chat: dict | None) -> str:
        turn_id = str((chat or {}).get("codex_turn_id") or "").strip()
        if turn_id:
            return turn_id
        if chat is self._current_chat_state:
            return str(self.active_codex_turn_id or "").strip()
        return ""

    def _build_codex_status_markdown(self, client, chat: dict, model: str) -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        turn_id = self._codex_turn_id_for_chat(chat)
        flags = list((chat or {}).get("codex_thread_flags") or self.active_codex_thread_flags or [])
        thread_status = "active" if flags else ("idle" if thread_id else "notLoaded")
        account_status = "未查询"
        rate_limit_status = "未查询"
        if client is not None:
            try:
                if hasattr(client, "read_thread") and thread_id:
                    thread_status, flags = self._codex_thread_status_from_response(client.read_thread(thread_id, include_turns=False))
            except Exception as exc:
                thread_status = f"查询失败：{exc}"
            try:
                if hasattr(client, "read_account"):
                    account_status = self._format_codex_account_status(client.read_account(refresh_token=False))
            except Exception as exc:
                account_status = f"查询失败：{exc}"
            try:
                if hasattr(client, "read_rate_limits"):
                    rate_limit_status = self._format_codex_rate_limit_status(client.read_rate_limits())
            except Exception as exc:
                rate_limit_status = f"查询失败：{exc}"
        usage = format_context_usage_label(context_usage_from_dict((chat or {}).get("context_usage")))
        flags_text = "、".join(str(item) for item in flags) if flags else "无"
        lines = [
            "## Codex 状态",
            "",
            f"- 模型：{bindings.app.model_display_name(model) or model or DEFAULT_CODEX_MODEL}",
            f"- 工作目录：{self._workspace_dir_for_codex()}",
            f"- 请求审批方式：{normalize_codex_approval_mode(getattr(self, 'codex_approval_policy', None))}",
            f"- 请求沙箱：{normalize_codex_sandbox(getattr(self, 'codex_sandbox', None))}",
            f"- 最近确认线程配置：{((chat or {}).get('codex_thread_configuration') if (chat or {}).get('codex_thread_configuration_thread_id') == thread_id else None) or '未确认（未取得原生响应）'}",
            f"- 线程：{thread_id or '未创建'}",
            f"- 当前轮次：{turn_id or '无'}",
            f"- 运行状态：{thread_status or '未知'}",
            f"- 活跃标记：{flags_text}",
            f"- 上下文：{usage}",
            f"- 账号：{account_status}",
            f"- 额度：{rate_limit_status}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _codex_service_tier_for_chat(chat: dict | None) -> str:
        if not isinstance(chat, dict):
            return ""
        if "codex_service_tier" in chat:
            return normalize_codex_service_tier(chat.get("codex_service_tier"))
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        for turn in reversed(turns):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            tier = normalize_codex_service_tier(turn.get("codex_service_tier"))
            if tier and is_codex_model(model):
                return tier
        return ""

    def _visible_codex_speed_chat(self) -> dict:
        if self.view_mode == "history":
            chat = self._find_archived_chat(self.view_history_id)
            if isinstance(chat, dict):
                return chat
        if not isinstance(getattr(self, "_current_chat_state", None), dict):
            self._current_chat_state = {}
        return self._current_chat_state

    def _current_codex_service_tier(self) -> str:
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread() and hasattr(self, "codex_speed_combo"):
            return normalize_codex_service_tier(self.codex_speed_combo.GetValue())
        return self._codex_service_tier_for_chat(self._visible_codex_speed_chat())

    def _sync_codex_speed_combo_enabled_for_model(self, model: str | None = None) -> None:
        combo = getattr(self, "codex_speed_combo", None)
        if combo is None:
            return
        resolved_model = bindings.app.model_id_from_display_name(str(model or self.selected_model or ""))
        combo.Enable(is_codex_model(resolved_model))

    def _sync_codex_speed_combo_from_chat(self, chat: dict | None) -> None:
        combo = getattr(self, "codex_speed_combo", None)
        if combo is None:
            return
        tier = self._codex_service_tier_for_chat(chat)
        label = codex_service_tier_label(tier)
        if combo.GetValue() != label:
            combo.SetValue(label)
        model = str((chat or {}).get("model") or self.selected_model or "").strip()
        if not model and isinstance(chat, dict):
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            for turn in reversed(turns):
                if isinstance(turn, dict) and str(turn.get("model") or "").strip():
                    model = str(turn.get("model") or "").strip()
                    break
        self._sync_codex_speed_combo_enabled_for_model(model)

    def _set_codex_service_tier_for_chat(self, chat: dict | None, service_tier: str | None) -> str:
        tier = normalize_codex_service_tier(service_tier)
        if isinstance(chat, dict):
            chat["codex_service_tier"] = tier
        if chat is getattr(self, "_current_chat_state", None):
            self._current_chat_state["codex_service_tier"] = tier
        self._invalidate_remote_state_cache()
        return tier

    @staticmethod
    def _codex_service_tier_options_payload() -> list[dict]:
        return [
            {"value": "standard", "label": codex_service_tier_label("standard")},
            {"value": "fast", "label": codex_service_tier_label("fast")},
        ]

    def _codex_speed_payload_for_chat(self, chat: dict | None) -> dict:
        tier = self._codex_service_tier_for_chat(chat) or "standard"
        return {
            "codex_service_tier": tier,
            "codex_service_tier_label": codex_service_tier_label(tier),
            "codex_service_tier_options": self._codex_service_tier_options_payload(),
        }

    def _chat_id_for_speed_chat(self, chat: dict | None) -> str:
        if isinstance(chat, dict):
            chat_id = str(chat.get("id") or chat.get("chat_id") or "").strip()
            if chat_id:
                return chat_id
        return str(self.active_chat_id or self.current_chat_id or "").strip()

    def _on_codex_speed_changed(self, _):
        chat = self._visible_codex_speed_chat()
        previous_tier = self._codex_service_tier_for_chat(chat)
        requested_tier = normalize_codex_service_tier(self.codex_speed_combo.GetValue())
        if requested_tier == previous_tier:
            return
        tier = self._set_codex_service_tier_for_chat(chat, requested_tier)
        label = codex_service_tier_label(tier)
        if self.codex_speed_combo.GetValue() != label:
            self.codex_speed_combo.SetValue(label)
        self._defer_chat_state_save()
        suffix = "，将在下一条 Codex 请求生效" if bool(getattr(self, "is_running", False)) else ""
        self.SetStatusText(f"Codex 速度已切换为{codex_service_tier_label(tier)}{suffix}")
        return

    @staticmethod
    def _event_turn_id(event: CodexEvent) -> str:
        return str(getattr(event, "turn_id", "") or "").strip()

    @staticmethod
    def _event_thread_id(event: CodexEvent) -> str:
        return str(getattr(event, "thread_id", "") or "").strip()

    @staticmethod
    def _kimi_event_prompt_id(event: CodexEvent) -> str:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        return str(data.get("prompt_id") or data.get("promptId") or "").strip()

    @staticmethod
    def _kimi_event_agent_id(event: CodexEvent) -> str:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        return str(data.get("agent_id") or data.get("agentId") or "main").strip() or "main"

    def _kimi_event_is_authoritative(self, event: CodexEvent) -> bool:
        return self._kimi_event_agent_id(event) == "main"

    @staticmethod
    def _event_data_turn_idx_value(event: CodexEvent) -> int:
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        if "turn_idx" not in data:
            return -1
        try:
            value = int(data.get("turn_idx"))
        except Exception:
            return -1
        return value if value >= 0 else -1

    def _event_scoped_turn_index(self, turns: list, event: CodexEvent) -> int:
        data_idx = self._event_data_turn_idx_value(event)
        turn_id = self._event_turn_id(event)
        if isinstance(turns, list) and 0 <= data_idx < len(turns):
            turn = turns[data_idx]
            if isinstance(turn, dict):
                bound_id = str(turn.get("codex_turn_id") or turn.get("turn_id") or "").strip()
                if not turn_id or bound_id in {"", turn_id}:
                    return data_idx
            return -1
        if turn_id:
            matches = []
            for idx, turn in enumerate(turns or []):
                if not isinstance(turn, dict):
                    continue
                for key in ("turn_id", "codex_turn_id", "id"):
                    if str(turn.get(key) or "").strip() == turn_id:
                        matches.append(idx)
                        break
            if len(matches) == 1:
                return matches[0]
        return -1

    def _event_turn_index(self, turns: list, event: CodexEvent) -> int:
        return self._event_scoped_turn_index(turns, event)

    def _active_codex_event_target_index(self, event: CodexEvent) -> int:
        return self._event_scoped_turn_index(self.active_session_turns, event)

    def _known_codex_turn_ids_for_chat(self, chat: dict | None) -> set[str]:
        ids: set[str] = set()
        if isinstance(chat, dict):
            for key in ("codex_turn_id", "turn_id"):
                value = str(chat.get(key) or "").strip()
                if value:
                    ids.add(value)
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                for key in ("codex_turn_id", "turn_id", "id"):
                    value = str(turn.get(key) or "").strip()
                    if value:
                        ids.add(value)
        if chat is getattr(self, "_current_chat_state", None):
            value = str(getattr(self, "active_codex_turn_id", "") or "").strip()
            if value:
                ids.add(value)
        return ids

    def _codex_event_turn_is_compatible_with_chat(self, chat: dict | None, event: CodexEvent) -> bool:
        turn_id = self._event_turn_id(event)
        known_turn_ids = self._known_codex_turn_ids_for_chat(chat)
        if not known_turn_ids:
            return True
        if not turn_id:
            turns = chat.get("turns") if isinstance(chat, dict) and isinstance(chat.get("turns"), list) else []
            if self._codex_event_requires_known_turn(event) and self._event_scoped_turn_index(turns, event) >= 0:
                return True
            return not self._codex_event_requires_known_turn(event)
        return turn_id in known_turn_ids

    def _codex_token_usage_matches_native_chat(self, chat: dict, event: CodexEvent) -> bool:
        current_thread = self._codex_thread_id_for_chat(chat)
        event_thread = self._event_thread_id(event)
        if current_thread and event_thread != current_thread:
            return False
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        turn_idx = self._event_scoped_turn_index(turns, event)
        if turn_idx < 0 or turn_idx != len(turns) - 1:
            return False
        turn = turns[turn_idx]
        if not isinstance(turn, dict):
            return False
        data = event.data if isinstance(event.data, dict) else {}
        try:
            event_generation = int(data.get("context_generation"))
        except (TypeError, ValueError):
            return False
        turn_thread = str(turn.get("codex_thread_id") or "").strip()
        turn_id = self._event_turn_id(event)
        known_turn_id = str(turn.get("codex_turn_id") or "").strip()
        if turn_id and known_turn_id and turn_id != known_turn_id:
            return False
        if event_generation != int(turn.get("codex_start_generation", turn.get("codex_context_generation") or 0)):
            return False
        if current_thread:
            return bool(event_thread and turn_thread == current_thread)
        if event_thread:
            retired = chat.get("codex_context_retired_threads")
            if isinstance(retired, list) and event_thread in retired:
                return False
            chat_turn_id = str(chat.get("codex_turn_id") or "").strip()
            return (turn_idx == len(turns) - 1
                    and str(turn.get("request_status") or "").strip() == "pending"
                    and event_generation == int(turn.get("codex_context_generation") or 0)
                    and event_generation == int(chat.get("codex_context_generation") or 0)
                    and bool(turn_id) and chat_turn_id in {"", turn_id}
                    and known_turn_id in {"", turn_id}
                    and turn_thread in {"", event_thread})
        return False

    @staticmethod
    def _codex_event_requires_known_turn(event: CodexEvent) -> bool:
        event_type = str(getattr(event, "type", "") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        if event_type in {"subagent_result", "turn_completed"}:
            return True
        if event_type == "item_completed" and (phase == "final_answer" or status == "imageView"):
            return True
        return False

    def _known_codex_event_chat_id(self, event: CodexEvent) -> str:
        turn_id = self._event_turn_id(event)
        thread_id = self._event_thread_id(event)
        candidates = []
        active_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if active_id:
            candidates.append(
                {
                    "id": active_id,
                    "codex_thread_id": str(getattr(self, "active_codex_thread_id", "") or "").strip(),
                    "codex_turn_id": str(getattr(self, "active_codex_turn_id", "") or "").strip(),
                    "turns": self.active_session_turns if isinstance(self.active_session_turns, list) else [],
                }
            )
        if isinstance(getattr(self, "_current_chat_state", None), dict):
            candidates.append(self._current_chat_state)
        candidates.extend(chat for chat in (self.archived_chats or []) if isinstance(chat, dict))

        if turn_id:
            matching_chat_ids = set()
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                if str(chat.get("codex_turn_id") or "").strip() == turn_id:
                    matching_chat_ids.add(chat_id)
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("codex_turn_id") or turn.get("turn_id") or turn.get("id") or "").strip() == turn_id:
                        matching_chat_ids.add(chat_id)
            if len(matching_chat_ids) == 1:
                return next(iter(matching_chat_ids))
            if len(matching_chat_ids) > 1:
                return ""

        if thread_id:
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                if str(chat.get("codex_thread_id") or "").strip() == thread_id:
                    return chat_id
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("codex_thread_id") or "").strip() == thread_id:
                        return chat_id

        return ""

    def _resolve_codex_event_chat_id(self, event: CodexEvent) -> str:
        known_chat_id = self._known_codex_event_chat_id(event)
        if known_chat_id:
            return known_chat_id
        if self._codex_event_requires_known_turn(event):
            return ""
        return self._active_codex_event_fallback_chat_id(event)

    def _active_codex_event_fallback_chat_id(self, event: CodexEvent) -> str:
        active_chat = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        active_id = str(self.active_chat_id or self.current_chat_id or active_chat.get("id") or "").strip()
        event_type = str(getattr(event, "type", "") or "").strip()
        active_running = (
            bool(getattr(self, "active_codex_turn_active", False))
            or bool(active_chat.get("codex_turn_active"))
            or event_type == "server_request"
        )
        if not active_running:
            turns = active_chat.get("turns") if isinstance(active_chat.get("turns"), list) else []
            active_idx = self._active_turn_index_value()
            if 0 <= active_idx < len(turns) and isinstance(turns[active_idx], dict):
                turn = turns[active_idx]
                active_running = (
                    str(turn.get("request_status") or "").strip() == "pending"
                    and is_codex_model(str(turn.get("model") or ""))
                )
        if not active_running:
            return ""
        if not active_id:
            active_id = self._ensure_active_chat_id()
        if not self._codex_event_turn_is_compatible_with_chat(active_chat, event):
            return ""
        return active_id

    def _workspace_dir_for_codex(self) -> str:
        return str(Path(bindings.app.__file__).resolve().parent)

    def _append_codex_local_turn(self, question: str) -> int:
        turn = {
            "question": str(question or ""),
            "answer_md": REQUESTING_TEXT,
            "model": DEFAULT_CODEX_MODEL,
            "created_at": bindings.app.time.time(),
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
        self._mark_turn_request_pending(turn, DEFAULT_CODEX_MODEL, str(question or ""))
        return self.active_turn_idx

    def _merge_codex_final_answer(self, answer: str) -> bool:
        turn = {
            "question": "",
            "answer_md": str(answer or ""),
            "model": DEFAULT_CODEX_MODEL,
            "created_at": bindings.app.time.time(),
        }
        self._apply_nonrecoverable_turn_metadata(turn, DEFAULT_CODEX_MODEL, "")
        turn["request_recovery_mode"] = "resume"
        turn["request_resume_token"] = {
            "thread_id": str(self.active_codex_thread_id or "").strip(),
            "turn_id": str(self.active_codex_turn_id or "").strip(),
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
        return True

    @staticmethod
    def _is_codex_subagent_result_answer(turn: dict) -> bool:
        return isinstance(turn, dict) and str(turn.get("answer_origin") or "").strip() == "codex-subagent-result"

    def _apply_codex_subagent_result_to_turn(self, turn: dict, text: str) -> bool:
        if not isinstance(turn, dict):
            return False
        result = str(text or "").strip()
        if not result:
            return False
        current = str(turn.get("answer_md") or "")
        if not current.strip() or current == REQUESTING_TEXT:
            turn["answer_md"] = result
        elif self._is_codex_subagent_result_answer(turn):
            if result in current:
                return False
            turn["answer_md"] = f"{current.rstrip()}\n\n{result}"
        else:
            return False
        turn["answer_origin"] = "codex-subagent-result"
        return True

    def _apply_codex_final_answer_to_turn(self, turn: dict, text: str) -> bool:
        if not isinstance(turn, dict):
            return False
        answer = str(text or "")
        current = str(turn.get("answer_md") or "")
        if not answer.strip() and self._is_codex_subagent_result_answer(turn) and current.strip():
            return False
        if current.strip() and current != REQUESTING_TEXT and not self._is_codex_subagent_result_answer(turn):
            return False
        turn["answer_md"] = answer
        self._schedule_accepted_answer_refresh(str(self.active_chat_id or self.current_chat_id or ""), turn)
        if self._is_codex_subagent_result_answer(turn):
            turn.pop("answer_origin", None)
        return True

    def _apply_codex_completed_answer_to_turn(self, turn: dict, event: CodexEvent) -> bool:
        if event.type == "turn_completed" and str(event.status or "").strip() not in {"", "completed"}:
            return False
        answer = str(event.text or "")
        if not answer.strip() or str(turn.get("request_status") or "") == "failed":
            return False
        item_id = str(event.item_id or "").strip()
        completed = turn.setdefault("codex_completed_answers", [])
        if any((item_id and item.get("item_id") == item_id) or item.get("text") == answer
               for item in completed if isinstance(item, dict)):
            return False
        current = str(turn.get("answer_md") or "")
        if current == answer:
            completed.append({"item_id": item_id, "text": answer})
            return False
        if str(turn.get("request_status") or "") == "done" and event.type != "turn_completed":
            return False
        if current.strip() and current != REQUESTING_TEXT and not self._is_codex_subagent_result_answer(turn):
            turn["answer_md"] = f"{current.rstrip()}\n\n{answer}"
        else:
            turn["answer_md"] = answer
        completed.append({"item_id": item_id, "text": answer})
        turn.pop("answer_origin", None)
        self._schedule_accepted_answer_refresh(str(self.active_chat_id or self.current_chat_id or ""), turn)
        return True

    @staticmethod
    def _codex_error_text(exc: Exception | str) -> str:
        return str(exc or "").strip().lower()

    def _is_codex_thread_missing_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "thread not found" in text or "unknown thread" in text

    def _is_codex_rollout_missing_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "no rollout found" in text

    def _is_codex_no_active_turn_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "no active turn to steer" in text

    def _build_codex_rollout_recovery_prompt(self, history_turns: list[dict], question: str) -> str:
        clean_question = str(question or "").strip()
        transcript_parts = []
        for turn in history_turns or []:
            if not isinstance(turn, dict):
                continue
            prior_question = str(turn.get("question") or "").strip()
            prior_answer = str(turn.get("answer_md") or "").strip()
            if prior_answer == REQUESTING_TEXT:
                prior_answer = ""
            if prior_question:
                transcript_parts.append(f"用户：{prior_question}")
            if prior_answer:
                transcript_parts.append(f"Codex：{prior_answer}")
        if not transcript_parts:
            return clean_question
        transcript = "\n".join(transcript_parts)
        return (
            "下面是当前聊天在本地保存的历史记录，请把它当作本次会话上下文继续：\n"
            f"{transcript}\n\n"
            "请基于以上上下文继续回答下面这个新问题：\n"
            f"{clean_question}"
        )

    def _codex_should_steer_turn(self, target_chat: dict, is_current_target: bool) -> bool:
        return bool(target_chat.get("codex_turn_active")) or (
            is_current_target and bool(self.active_codex_turn_active)
        )

    def _start_codex_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, model: str) -> None:
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        turns = target_chat.get("turns") if isinstance(target_chat, dict) and isinstance(target_chat.get("turns"), list) else []
        if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict):
            turns[turn_idx]["codex_context_generation"] = int(target_chat.get("codex_context_generation") or 0)
            turns[turn_idx]["codex_start_generation"] = turns[turn_idx]["codex_context_generation"]
        def _worker() -> None:
            self._run_codex_turn_worker(chat_id, turn_idx, question, model, from_recovery=False)

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _start_codex_local_command_worker_for_turn(self, chat_id: str, turn_idx: int, command: str, args: str, model: str) -> None:
        bindings.app.threading.Thread(
            target=self._run_codex_local_command_worker,
            args=(chat_id, turn_idx, command, args, model),
            daemon=True,
        ).start()

    def _run_codex_local_command_worker(self, chat_id: str, turn_idx: int, command: str, args: str, model: str) -> None:
        try:
            chat_id = str(chat_id or "").strip()
            current_ids = {
                str(self.active_chat_id or "").strip(),
                str(self.current_chat_id or "").strip(),
                str((self._current_chat_state or {}).get("id") or "").strip() if isinstance(getattr(self, "_current_chat_state", None), dict) else "",
                "",
            }
            is_current_target = chat_id in current_ids
            target_chat = self._current_chat_state if is_current_target else self._find_archived_chat(chat_id)
            if not isinstance(target_chat, dict):
                target_chat = self._current_chat_state if is_current_target else {}
            normalized_command = str(command or "").strip().lower()
            client = None
            if normalized_command in {"status", "compact", "stop"}:
                if is_current_target:
                    client = self._ensure_codex_client(model) if model != DEFAULT_CODEX_MODEL else self._ensure_codex_client()
                else:
                    client = self._get_or_create_codex_client(chat_id, model) if model != DEFAULT_CODEX_MODEL else self._get_or_create_codex_client(chat_id)
            if normalized_command == "status":
                answer = self._build_codex_status_markdown(client, target_chat, model)
            elif normalized_command == "help":
                answer = self._build_codex_help_markdown()
            elif normalized_command == "compact":
                answer = self._handle_codex_compact_command(client, target_chat, model=model)
            elif normalized_command == "model":
                answer = self._handle_codex_model_command(str(args or ""), target_chat)
            elif normalized_command == "speed":
                answer = self._handle_codex_speed_command(str(args or ""), target_chat)
            elif normalized_command == "new":
                answer = "## Codex 新聊天\n\n已请求开始新聊天。"
                self._call_after_if_alive(self._on_new_chat_clicked, None)
            elif normalized_command == "clear":
                answer = self._handle_codex_clear_command(target_chat)
            elif normalized_command == "stop":
                answer = self._handle_codex_stop_command(client, target_chat, model=model)
            else:
                answer = self._build_codex_unsupported_command_markdown(normalized_command or command, args)
            self._call_after_if_alive(self._on_done, turn_idx, answer, "", model, "", chat_id)
        except Exception as exc:
            self._call_after_if_alive(self._on_done, turn_idx, "", str(exc), model, "", chat_id)

    def _handle_codex_compact_command(self, client, chat: dict, model: str = "") -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        if not thread_id:
            return "## Codex 压缩\n\n当前聊天还没有 Codex 线程，无法执行 `/compact`。"
        if client is None or not hasattr(client, "compact_thread"):
            return "## Codex 压缩\n\n当前 Codex 客户端不支持 `thread/compact/start`。"
        chat_id = str((chat or {}).get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        client.compact_thread(thread_id, chat_id=chat_id, model=str(model or (chat or {}).get("model") or DEFAULT_CODEX_MODEL))
        return f"## Codex 压缩\n\n已开始压缩当前 Codex 线程：`{thread_id}`。"

    def _handle_codex_model_command(self, args: str, chat: dict) -> str:
        requested = str(args or "").strip()
        if not requested:
            model = str((chat or {}).get("model") or self.selected_model or DEFAULT_CODEX_MODEL)
            return f"## Codex 模型\n\n当前模型：`{bindings.app.model_display_name(model) or model}`。"
        model_id = bindings.app.model_id_from_display_name(requested)
        if not is_codex_model(model_id):
            return f"## Codex 模型\n\n`{requested}` 不是本程序可用的 Codex 模型。"
        self.selected_model = model_id
        if isinstance(chat, dict):
            chat["model"] = model_id
        self._call_after_if_alive(self.model_combo.SetValue, bindings.app.model_display_name(model_id))
        self._save_state()
        return f"## Codex 模型\n\n已切换到：`{bindings.app.model_display_name(model_id) or model_id}`。"

    def _handle_codex_speed_command(self, args: str, chat: dict) -> str:
        requested = str(args or "").strip().lower()
        if not requested:
            tier = self._codex_service_tier_for_chat(chat)
            return f"## Codex 速度\n\n当前速度：`{codex_service_tier_label(tier)}`。"
        aliases = {
            "fast": "fast",
            "快速": "fast",
            "standard": "",
            "normal": "",
            "default": "",
            "标准": "",
        }
        if requested not in aliases:
            return "## Codex 速度\n\n可用参数：`fast`、`standard`、`normal`。"
        tier = self._set_codex_service_tier_for_chat(chat, aliases[requested])
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self._sync_codex_speed_combo_from_chat(chat)
        else:
            self._call_after_if_alive(self._sync_codex_speed_combo_from_chat, chat)
        self._save_state()
        suffix = "，将在下一条 Codex 请求生效" if bool(getattr(self, "is_running", False)) else ""
        return f"## Codex 速度\n\n已切换到：`{codex_service_tier_label(tier)}`{suffix}。"

    def _handle_codex_clear_command(self, chat: dict) -> str:
        if isinstance(chat, dict):
            self._clear_codex_context_usage_for_thread_change(chat)
            chat["codex_thread_id"] = ""
            chat["codex_turn_id"] = ""
            chat["codex_turn_active"] = False
            chat["codex_pending_prompt"] = ""
            chat["codex_pending_request"] = None
            chat["codex_request_queue"] = []
            chat["codex_thread_flags"] = []
        if chat is self._current_chat_state:
            self.active_codex_thread_id = ""
            self.active_codex_turn_id = ""
            self.active_codex_turn_active = False
            self.active_codex_pending_prompt = ""
            self.active_codex_pending_request = None
            self.active_codex_thread_flags = []
        self._publish_codex_context_thread_change(chat)
        self._save_state()
        return "## Codex 清理\n\n已清除当前聊天关联的 Codex 线程状态。聊天记录不会被删除。"

    def _clear_codex_context_usage_for_thread_change(self, chat: dict, *, preserve: tuple[int, str, str] | None = None) -> None:
        retired_thread = self._codex_thread_id_for_chat(chat)
        if retired_thread:
            retired = chat.get("codex_context_retired_threads")
            retired = list(retired) if isinstance(retired, list) else []
            if retired_thread not in retired:
                retired.append(retired_thread)
            chat["codex_context_retired_threads"] = retired[-16:]
        chat["context_usage"] = None
        chat_id = str(chat.get("id") or "").strip()
        preserve_total = bool(preserve and chat_id and self._codex_early_context_usage_owner.get((chat_id, preserve[0])) == preserve[1:])
        if not preserve_total:
            chat["codex_session_total_tokens"] = None
            chat.pop("codex_total_observed_at", None)
            chat.pop("codex_usage_cache_owner", None)
        chat.pop("codex_context_error", None)
        chat.pop("codex_snapshot_error", None)
        chat["codex_context_generation"] = int(chat.get("codex_context_generation") or 0) + 1
        if chat_id:
            for key in list(self._pending_context_usage_by_turn):
                if key[0] == chat_id:
                    early_owner = self._codex_early_context_usage_owner.get(key)
                    if preserve is not None and key[1] == preserve[0] and early_owner == preserve[1:]:
                        continue
                    self._pending_context_usage_by_turn.pop(key, None)
                    self._codex_early_context_usage_owner.pop(key, None)

    def _publish_codex_context_thread_change(self, chat: dict) -> None:
        if getattr(self, "_scoped_chat_information", None) is not None:
            if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
                self._information_subscriptions().changed(chat)
            else:
                self._call_after_if_alive(self._information_subscriptions().changed, chat)
        if getattr(self, "_chat_information_dialog", None) is None:
            return
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self._rebind_chat_information_after_thread_change(chat)
        else:
            self._call_after_if_alive(self._rebind_chat_information_after_thread_change, chat)

    def _handle_codex_stop_command(self, client, chat: dict, model: str = "") -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        turn_id = self._codex_turn_id_for_chat(chat)
        if not thread_id or not turn_id:
            return "## Codex 中断\n\n当前没有可中断的 Codex turn。"
        if client is None or not hasattr(client, "interrupt_turn"):
            return "## Codex 中断\n\n当前 Codex 客户端不支持 `turn/interrupt`。"
        chat_id = str((chat or {}).get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        client.interrupt_turn(
            thread_id,
            turn_id,
            chat_id=chat_id,
            model=str(model or (chat or {}).get("model") or DEFAULT_CODEX_MODEL),
        )
        return f"## Codex 中断\n\n已请求中断 turn：`{turn_id}`。"

    def _ensure_codex_thread_resumed(self, client, thread_id: str, service_tier: str = "") -> None:
        thread_value = str(thread_id or "").strip()
        if not thread_value or not hasattr(client, "resume_thread"):
            return
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            resumed = set()
            setattr(client, "_zgwd_resumed_thread_ids", resumed)
        configuration = (getattr(self, "codex_approval_policy", DEFAULT_CODEX_APPROVAL_MODE), getattr(self, "codex_sandbox", None), service_tier)
        configurations = getattr(client, "_zgwd_resumed_configurations", {})
        if thread_value in resumed and configurations.get(thread_value) == configuration:
            return
        client.resume_thread(
            thread_value,
            **codex_approval_parameters(getattr(self, "codex_approval_policy", DEFAULT_CODEX_APPROVAL_MODE), getattr(self, "codex_sandbox", None)),
            personality="pragmatic",
            cwd=self._workspace_dir_for_codex(),
            service_tier=service_tier,
        )
        resumed.add(thread_value)
        configurations[thread_value] = configuration
        client._zgwd_resumed_configurations = configurations

    @staticmethod
    def _remember_codex_thread_resumed(client, thread_id: str) -> None:
        thread_value = str(thread_id or "").strip()
        if not thread_value:
            return
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            resumed = set()
            setattr(client, "_zgwd_resumed_thread_ids", resumed)
        resumed.add(thread_value)

    @staticmethod
    def _forget_codex_thread_resume(client, thread_id: str) -> None:
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            return
        resumed.discard(str(thread_id or "").strip())

    def _apply_codex_request_started(self, chat_id: str, turn_idx: int, request, *args) -> None:
        if self._model_startup_request_is_current(chat_id, turn_idx, request):
            self._mark_codex_worker_turn_request_started(chat_id, turn_idx, *args)

    def _run_codex_turn_worker(self, chat_id: str, turn_idx: int, question: str, model: str, from_recovery: bool = False) -> None:
        startup_request = self._capture_model_startup_request(chat_id, turn_idx)
        if startup_request is None:
            return
        is_current_target = chat_id in {self.active_chat_id, self.current_chat_id, ""}
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            if not is_current_target:
                return
            target_chat = self._current_chat_state
        thread_id = str(target_chat.get("codex_thread_id") or (self.active_codex_thread_id if is_current_target else "") or "").strip()
        turn_id = str(target_chat.get("codex_turn_id") or (self.active_codex_turn_id if is_current_target else "") or "").strip()
        recovery_context = bool(getattr(self, "_codex_recovery_context", False))
        client_chat_id = str(chat_id or (target_chat.get("id") if isinstance(target_chat, dict) else "") or self.active_chat_id or self.current_chat_id or "").strip()
        if not client_chat_id:
            client_chat_id = self._ensure_active_chat_id()
        client = (
            self._get_or_create_codex_client(client_chat_id, model)
            if model != DEFAULT_CODEX_MODEL
            else self._get_or_create_codex_client(client_chat_id)
        )

        try:
            target_turns = self.active_session_turns if is_current_target else (target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else [])
            if not isinstance(target_turns, list) or turn_idx < 0 or turn_idx >= len(target_turns):
                if not is_current_target:
                    return
                target_turns = self.active_session_turns
            service_tier = self._codex_service_tier_for_chat(target_chat)
            if 0 <= turn_idx < len(target_turns) and isinstance(target_turns[turn_idx], dict):
                service_tier = normalize_codex_service_tier(target_turns[turn_idx].get("codex_service_tier")) or service_tier
            turn_attachments = []
            if 0 <= turn_idx < len(target_turns):
                maybe_attachments = target_turns[turn_idx].get("attachments") if isinstance(target_turns[turn_idx], dict) else []
                if isinstance(maybe_attachments, list):
                    turn_attachments = [item for item in maybe_attachments if str((item or {}).get("status") or "") == "success"]
            send_question = question
            input_items = self._build_codex_input_items(send_question, turn_attachments)
            should_steer = self._codex_should_steer_turn(target_chat, is_current_target) and bool(turn_id)
            history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
            if (should_steer and history_turns and isinstance(history_turns[-1], dict)
                    and str(history_turns[-1].get("request_status") or "") == "failed"
                    and self._codex_active_request_turn_count(history_turns) == 0):
                native_owner = self._codex_worker_active_turns.get(client_chat_id)
                if not (isinstance(native_owner, dict)
                        and str(native_owner.get("turn_id") or "").strip() == turn_id):
                    should_steer = False
            client.start()
            if not self._model_startup_request_is_current(chat_id, turn_idx, startup_request):
                return
            client.start_turn(
                chat_id=client_chat_id,
                turn_idx=turn_idx,
                context_generation=int(target_turns[turn_idx].get("codex_context_generation") or 0),
                question=send_question,
                model=model,
                cwd=self._workspace_dir_for_codex(),
                thread_id=thread_id,
                turn_id=turn_id,
                input_items=input_items,
                attachments=turn_attachments,
                service_tier=service_tier,
                should_steer=should_steer,
                approval_policy=getattr(self, "codex_approval_policy", DEFAULT_CODEX_APPROVAL_MODE),
                sandbox=getattr(self, "codex_sandbox", DEFAULT_CODEX_SANDBOX),
                history_turns=history_turns,
            )
            self._call_after_if_alive(
                self._apply_codex_request_started,
                client_chat_id,
                turn_idx,
                startup_request,
                service_tier,
                thread_id,
                turn_id,
                from_recovery,
                recovery_context,
            )
        except Exception as exc:
            self._call_after_if_alive(self._finish_model_startup_failure, chat_id, turn_idx, startup_request, str(exc), model)

    def _mark_codex_worker_turn_request_started(
        self,
        chat_id: str,
        turn_idx: int,
        service_tier: str,
        thread_id: str,
        turn_id: str,
        from_recovery: bool = False,
        recovery_context: bool = False,
    ) -> None:
        target_chat, is_current_target = self._codex_worker_target_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_chat["codex_service_tier"] = normalize_codex_service_tier(service_tier)
        target_turns = self.active_session_turns if is_current_target else target_chat.get("turns")
        if isinstance(target_turns, list) and 0 <= int(turn_idx) < len(target_turns):
            turn = target_turns[int(turn_idx)]
            if isinstance(turn, dict):
                turn["request_recovery_mode"] = "resume"
                turn["request_resume_token"] = {"thread_id": str(thread_id or ""), "turn_id": str(turn_id or "")}
                if (not from_recovery) and (not recovery_context):
                    turn["request_status"] = "pending"
                self._mark_chat_turns_dirty(None if is_current_target else chat_id, int(turn_idx))
        self._defer_codex_state_save()

    def _codex_answer_filter_menu_label(self) -> str:
        return "取消过滤英文内容" if self.codex_answer_english_filter_enabled else "在回答中过滤英文内容"

    def _show_codex_sandbox(self) -> None:
        questions = [{"id": "sandbox", "header": "Codex 访问权限",
            "question": "选择后续线程启动或恢复的访问权限。当前正在执行的回合保持原设置，审批方式独立保存。",
            "options": [{"label": "只读", "value": "read-only"}, {"label": "工作区写入", "value": "workspace-write"}, {"label": "完全访问", "value": "danger-full-access"}]}]
        dlg = bindings.app.CodexUserInputDialog(self, questions)
        dlg._controls[0]["radio"].SetSelection(CODEX_SANDBOX_MODES.index(normalize_codex_sandbox(getattr(self, "codex_sandbox", None))))
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            values = dlg.get_answers().get("sandbox", [])
            self.codex_sandbox = normalize_codex_sandbox(values[0] if len(values) == 1 else None)
        finally:
            dlg.Destroy()
        self._save_state()
        self.SetStatusText("Codex 访问权限已保存，将用于后续线程启动或恢复。")

    def _show_codex_approval_policy(self) -> None:
        questions = [{"id": "policy", "header": "Codex 执行审批",
            "question": "选择适用于后续线程启动或恢复的审批方式。当前正在执行的回合保持原设置。访问权限独立设置。自动审查由 Codex 审核请求，不保证全部批准。",
            "options": [{"label": "不询问", "value": "never"}, {"label": "需要时询问", "value": "on-request"}, {"label": "自动审查", "value": "auto-review"}]}]
        dlg = bindings.app.CodexUserInputDialog(self, questions)
        dlg._controls[0]["radio"].SetSelection(CODEX_APPROVAL_MODES.index(normalize_codex_approval_mode(getattr(self, "codex_approval_policy", DEFAULT_CODEX_APPROVAL_MODE))))
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            values = dlg.get_answers().get("policy", [])
            self.codex_approval_policy = normalize_codex_approval_mode(values[0] if len(values) == 1 else None)
        finally:
            dlg.Destroy()
        self._save_state()
        self.SetStatusText("Codex 执行审批已保存，将用于后续线程启动或恢复。")

    def _toggle_codex_answer_filter(self) -> None:
        self.codex_answer_english_filter_enabled = not self.codex_answer_english_filter_enabled
        self._save_state()
        self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
        if self.view_mode in {"active", "history"}:
            self._render_answer_list()

    def _should_queue_codex_ui_event(self, chat_id: str, event: CodexEvent) -> bool:
        return True

    def _dispatch_codex_event_to_ui(self, chat_id: str, event: CodexEvent, source_client: object = None) -> None:
        if not event or not self._should_queue_codex_ui_event(chat_id, event):
            return
        if not self._is_ui_alive():
            return
        should_schedule = False
        with self._codex_ui_event_lock:
            self._pending_codex_ui_events.append((str(chat_id or "").strip(), event, source_client))
            if not self._codex_ui_event_flush_scheduled:
                self._codex_ui_event_flush_scheduled = True
                should_schedule = True
        if should_schedule and not self._call_after_if_alive(self._drain_codex_ui_events):
            with self._codex_ui_event_lock:
                self._codex_ui_event_flush_scheduled = False

    def _drain_codex_ui_events(self) -> None:
        batch_size = self._codex_ui_event_batch_size()
        with self._measure_ui_operation("codex_ui_event_drain", batch_size=batch_size):
            with self._codex_ui_event_lock:
                batch = self._pending_codex_ui_events[:batch_size]
                del self._pending_codex_ui_events[: len(batch)]
                has_more = bool(self._pending_codex_ui_events)
                self._codex_ui_event_flush_scheduled = has_more
            self._codex_ui_batch_depth += 1
            self._background_ui_update_depth += 1
            try:
                for queued_entry in batch:
                    queued_chat_id, queued_event = queued_entry[:2]
                    source_client = queued_entry[2] if len(queued_entry) > 2 else None
                    if source_client is not None and not self._codex_source_client_is_current(queued_chat_id, source_client):
                        continue
                    self._on_codex_event_for_chat(queued_chat_id, queued_event)
            finally:
                self._background_ui_update_depth = max(0, self._background_ui_update_depth - 1)
                self._codex_ui_batch_depth = max(0, self._codex_ui_batch_depth - 1)
                self._flush_deferred_execution_list_updates()
                if not self._navigation_quiet_active():
                    self._flush_pending_background_ui_updates()
                self._start_execution_step_persist_worker()
            if has_more:
                if self._wx_main_loop_running():
                    self._codex_ui_event_drain_timer = self._call_later_if_alive(
                        CODEX_UI_EVENT_BATCH_DELAY_MS,
                        self._drain_codex_ui_events,
                    )
                else:
                    self._codex_ui_event_drain_timer = None
                if (
                    self._codex_ui_event_drain_timer is None
                    and not self._call_after_if_alive(self._drain_codex_ui_events)
                ):
                    with self._codex_ui_event_lock:
                        self._codex_ui_event_flush_scheduled = False
            else:
                self._codex_ui_event_drain_timer = None

    def _codex_ui_event_batch_size(self) -> int:
        if self._primary_navigation_control_has_focus():
            return max(1, min(CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE, CODEX_UI_EVENT_BATCH_SIZE))
        return CODEX_UI_EVENT_BATCH_SIZE

    def _on_codex_event(self, event: CodexEvent) -> None:
        chat_id = self._resolve_codex_event_chat_id(event)
        if not chat_id:
            return
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            self._dispatch_codex_event_to_ui(chat_id, event)
            return
        self._on_codex_event_for_chat(chat_id, event)

    def _handle_codex_request_dialog(self, request: dict) -> None:
        self._push_remote_attention(str(request.get("chat_id") or ""), "action_required",
            str(request.get("request_id") or ""), "\u9700\u8981\u60a8\u7684\u786e\u8ba4\u6216\u8f93\u5165")
        if request.get("method") == "item/commandExecution/requestApproval":
            self._handle_codex_command_approval(request)
            return
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        questions = params.get("questions") if isinstance(params.get("questions"), list) else []
        dlg = bindings.app.CodexUserInputDialog(self, questions)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            answers = dlg.get_answers()
        finally:
            dlg.Destroy()
        chat_id = str(request.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        model = str(request.get("model") or (target_chat or {}).get("model") or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = self._get_or_create_codex_client(chat_id, model)
        start = getattr(client, "start", None)
        if callable(start):
            start()
        client.reply_user_input(chat_id, request.get("request_id"), answers)

    def _handle_codex_command_approval(self, request: dict) -> None:
        chat_id = str(request.get("chat_id") or "")
        client = getattr(self, "_codex_clients", {}).get(chat_id)
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        available = params.get("availableDecisions")
        allowed = available if isinstance(available, list) else ["accept", "decline"]
        fallback = "decline" if "decline" in allowed else "cancel" if "cancel" in allowed else None
        turn_idx = request.get("turn_idx")
        def current_owner():
            chat = self._current_chat_state
            turns = chat.get("turns", []) if isinstance(chat, dict) else []
            if chat_id != self.current_chat_id or chat_id != self.active_chat_id or not isinstance(turn_idx, int) or not 0 <= turn_idx < len(turns):
                return None
            turn = turns[turn_idx]
            if (getattr(self, "_codex_clients", {}).get(chat_id) is not client
                or str(turn.get("model") or chat.get("model") or DEFAULT_CODEX_MODEL) != request.get("model")
                or turn.get("codex_thread_id") != request.get("thread_id")
                or turn.get("codex_turn_id") != request.get("turn_id")
                or int(turn.get("codex_start_generation", turn.get("codex_context_generation") or 0)) != request.get("context_generation")
                or int(chat.get("codex_context_generation") or 0) != int(turn.get("codex_context_generation") or 0)
                or turn.get("request_status") not in {"pending", "running"}):
                return None
            return turn
        owner = current_owner()
        decision = fallback
        if client is None:
            return
        if owner is not None:
            options = []
            if "decline" in allowed:
                options.append({"label": "拒绝", "value": "decline"})
            if "accept" in allowed:
                options.append({"label": "批准一次", "value": "accept"})
            if options:
                questions = [{"id": "command_approval", "header": "Codex 命令执行审批",
                    "question": f"命令：{params.get('command') or ''}\n工作目录：{params.get('cwd') or ''}\n原因：{params.get('reason') or ''}",
                    "options": options}]
                dlg = bindings.app.CodexUserInputDialog(self, questions)
                try:
                    if dlg.ShowModal() == wx.ID_OK:
                        values = dlg.get_answers().get("command_approval", [])
                        if len(values) == 1 and values[0] in ("accept", "decline") and values[0] in allowed:
                            decision = values[0]
                finally:
                    dlg.Destroy()
                if current_owner() is not owner:
                    decision = fallback
        if decision is None:
            self.SetStatusText("此命令请求未提供可用的拒绝决定，未授权执行。")
            return
        client.reply_command_approval(**{key: request.get(key) for key in
            ("chat_id", "model", "request_id", "thread_id", "turn_id", "turn_idx", "context_generation")}, decision=decision)

    def _codex_pending_request_from_event(self, chat_id: str, target_chat: dict | None, event: CodexEvent) -> dict:
        event_data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        return {
            "chat_id": str(chat_id or "").strip(),
            "model": str(event_data.get("model") or (target_chat or {}).get("model") or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL,
            "request_id": event.request_id,
            "method": event.method,
            "params": event.params,
        }

    def _on_codex_event_for_chat(self, chat_id: str, event: CodexEvent) -> None:
        if not isinstance(event, CodexEvent):
            return
        chat_id = str(chat_id or "").strip()
        event_type = str(getattr(event, "type", "") or "").strip()
        event_turn_id = self._event_turn_id(event)
        event_thread_id = self._event_thread_id(event)
        if not chat_id and (event_turn_id or event_thread_id):
            resolved_chat_id = self._resolve_codex_event_chat_id(event)
            if resolved_chat_id and resolved_chat_id != chat_id:
                chat_id = resolved_chat_id
        elif chat_id and (event_turn_id or event_thread_id):
            known_chat_id = self._known_codex_event_chat_id(event)
            if known_chat_id and known_chat_id != chat_id:
                return
        is_current_chat = chat_id in {self.active_chat_id, self.current_chat_id, "", None}
        identity_chat = self._current_chat_state if is_current_chat else self._find_archived_chat(chat_id)
        if event_type == "turn_completed":
            event_data = event.data if isinstance(event.data, dict) else {}
            completion_owners = event_data.get("completion_owners")
            if isinstance(completion_owners, list):
                if not self._codex_event_turn_is_compatible_with_chat(identity_chat, event):
                    return
                turns = identity_chat.get("turns") if isinstance(identity_chat, dict) else []
                seen = set()
                for owner in completion_owners:
                    if not isinstance(owner, dict):
                        continue
                    idx = owner.get("turn_idx")
                    if not isinstance(idx, int) or idx in seen or not isinstance(turns, list) or not 0 <= idx < len(turns):
                        continue
                    seen.add(idx)
                    turn = turns[idx]
                    if str(turn.get("request_status") or "") not in {"pending", "running"}:
                        continue
                    if str(turn.get("codex_turn_id") or "") != event_turn_id or str(turn.get("codex_thread_id") or "") != event_thread_id:
                        continue
                    if str(turn.get("model") or DEFAULT_CODEX_MODEL) != str(event_data.get("model") or turn.get("model") or DEFAULT_CODEX_MODEL):
                        continue
                    if owner.get("context_generation") != turn.get("codex_start_generation", turn.get("codex_context_generation") or 0):
                        continue
                    if not event_data.get("ownership_error") and idx != self._event_scoped_turn_index(turns, event) and str(turn.get("answer_md") or "") in {"", REQUESTING_TEXT}:
                        turn["answer_md"] = ""
                        turn["request_carried_forward_to"] = self._event_scoped_turn_index(turns, event)
                    data = dict(event_data)
                    data.pop("completion_owners", None)
                    data.update(owner)
                    scoped_event = copy.copy(event)
                    scoped_event.data = data
                    if event_data.get("ownership_error"):
                        error = str(event_data["ownership_error"])
                        turn["codex_ownership_error"] = error
                        completed_answers = turn.get("codex_completed_answers")
                        valid_final = isinstance(completed_answers, list) and any(
                            isinstance(item, dict) and isinstance(item.get("text"), str)
                            and item["text"].strip() for item in completed_answers)
                        if not valid_final:
                            self._mark_turn_request_failed(turn, error)
                            partial = str(turn.get("answer_md") or "")
                            turn["answer_md"] = error if partial in {"", REQUESTING_TEXT} else f"{partial.rstrip()}\n\n{error}"
                            self._clear_codex_worker_active_turn(chat_id, idx, event_turn_id)
                            self._mark_chat_turns_dirty(chat_id, idx)
                            if is_current_chat:
                                self._update_active_answer_row(idx)
                            continue
                    if idx != self._event_scoped_turn_index(turns, event):
                        scoped_event.text = ""
                    self._on_codex_event_for_chat(chat_id, scoped_event)
                pending_owners = [turn for turn in turns if isinstance(turn, dict)
                    and is_codex_model(str(turn.get("model") or ""))
                    and str(turn.get("request_status") or "") in {"pending", "running"}]
                identity_chat["codex_turn_active"] = bool(pending_owners)
                if is_current_chat:
                    self.active_codex_turn_active = bool(pending_owners)
                    inflight = [turn for turn in turns if isinstance(turn, dict) and (
                        str(turn.get("request_status") or "") in {"pending", "running"} or
                        (not turn.get("request_status") and turn.get("answer_md") == REQUESTING_TEXT))]
                    self.is_running = bool(inflight)
                    self._active_request_count = len(inflight)
                self._defer_codex_state_save()
                return
        if self._codex_event_requires_known_turn(event):
            identity_turns = identity_chat.get("turns") if isinstance(identity_chat, dict) and isinstance(identity_chat.get("turns"), list) else []
            identity_idx = self._event_scoped_turn_index(identity_turns, event)
            if identity_idx < 0:
                return
            identity_turn = identity_turns[identity_idx]
            if str(identity_turn.get("request_status") or "") == "failed" and (
                event_type in {"turn_completed", "subagent_result"}
                or (event_type == "item_completed" and str(event.phase or "") == "final_answer")
            ):
                return
            bound_thread = str(identity_turn.get("codex_thread_id") or "").strip()
            if bound_thread and event_thread_id and bound_thread != event_thread_id:
                return
            event_data = event.data if isinstance(event.data, dict) else {}
            if "context_generation" in event_data:
                try:
                    event_generation = int(event_data["context_generation"])
                    turn_generation = int(identity_turn.get("codex_start_generation", identity_turn.get("codex_context_generation") or 0))
                except (TypeError, ValueError):
                    return
                if event_generation != turn_generation:
                    return
        early_codex_usage = False
        if event_type == "token_count" and isinstance(identity_chat, dict):
            if not self._codex_token_usage_matches_native_chat(identity_chat, event):
                return
            early_codex_usage = bool(event_thread_id and not self._codex_thread_id_for_chat(identity_chat))
            event_data = event.data if isinstance(event.data, dict) else {}
            if (event.usage or isinstance(event_data.get("session_total_tokens"), int)) and event_thread_id and not self._codex_thread_id_for_chat(identity_chat):
                turns = identity_chat.get("turns") if isinstance(identity_chat.get("turns"), list) else []
                turn_idx = self._event_scoped_turn_index(turns, event)
                if turn_idx >= 0:
                    key = self._context_usage_pending_key_from_chat(identity_chat, turn_idx)
                    self._codex_early_context_usage_owner[key] = (event_thread_id, event_turn_id)
        if isinstance(identity_chat, dict) and not early_codex_usage:
            if not self._codex_event_turn_is_compatible_with_chat(identity_chat, event):
                return
        if event_type == "token_count" and isinstance(identity_chat, dict):
            event_data = event.data if isinstance(event.data, dict) else {}
            identity_chat["codex_usage_revision"] = int(identity_chat.get("codex_usage_revision") or 0) + 1
            total = event_data.get("session_total_tokens")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                identity_chat["codex_total_observed_at"] = bindings.app.time.time()
                identity_chat["codex_snapshot_error"] = False
                identity_chat["codex_usage_cache_owner"] = self._information_cache_owner(identity_chat, "codex")
                total_changed = identity_chat.get("codex_session_total_tokens") != total
                identity_chat["codex_session_total_tokens"] = total
                self._refresh_chat_information(identity_chat)
                if total_changed:
                    self._defer_codex_state_save()
            if not event.usage:
                return
        if event_type == "server_request" and event.method == "item/commandExecution/requestApproval":
            event_data = event.data if isinstance(event.data, dict) else {}
            self._handle_codex_request_dialog({"chat_id": chat_id,
                "model": event_data.get("model"), "request_id": event.request_id,
                "method": event.method, "params": event.params,
                "thread_id": event_thread_id, "turn_id": event_turn_id,
                "turn_idx": event_data.get("turn_idx"), "context_generation": event_data.get("context_generation")})
            return
        execution_entry = None if event_type == "agent_message_delta" else self._build_execution_entry(event)
        appended_execution_step = False
        if not is_current_chat:
            if event_type == "agent_message_delta":
                self._buffer_execution_delta(chat_id, event)
                return
            target_chat = self._find_archived_chat(chat_id)
            target_turns = target_chat.get("turns") if isinstance(target_chat, dict) and isinstance(target_chat.get("turns"), list) else []
            target_idx = self._event_turn_index(target_turns, event)
            if event_type == "turn_completed" and target_idx >= 0 and str(target_turns[target_idx].get("request_status") or "").strip() == "done":
                return
            guarded_result = (
                event_type in {"subagent_result", "turn_completed"}
                or (event_type == "item_completed" and str(event.phase or "") == "final_answer")
            )
            if guarded_result and target_idx >= 0 and isinstance(target_turns[target_idx], dict):
                turn = target_turns[target_idx]
                event_data = event.data if isinstance(event.data, dict) else {}
                if "context_generation" in event_data:
                    try:
                        event_generation = int(event_data["context_generation"])
                    except (TypeError, ValueError):
                        return
                    if (
                        event_generation != int(turn.get("codex_start_generation", turn.get("codex_context_generation") or 0))
                        or int(turn.get("codex_context_generation") or 0) != int(target_chat.get("codex_context_generation") or 0)
                    ):
                        return
                if not self._accept_clear_operation_result(turn, chat_id):
                    if event_type == "turn_completed":
                        self._clear_codex_worker_active_turn(chat_id, target_idx, event_turn_id)
                    return
            self._flush_execution_delta(chat_id, event_turn_id or None)
            if event_type == "token_count" and event.usage and target_idx >= 0 and isinstance(target_chat, dict):
                turn = target_turns[target_idx] if target_idx < len(target_turns) and isinstance(target_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if str(turn.get("request_status") or "").strip() == "done":
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(target_chat, target_idx), None)
                    changed = self._set_chat_context_usage(target_chat, event.usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(target_chat, target_idx, event.usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
                return
            if execution_entry:
                self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
                appended_execution_step = True
            if (
                event_type == "server_request"
                and str(event.method or "") == "item/tool/requestUserInput"
                and isinstance(target_chat, dict)
            ):
                target_chat["codex_pending_request"] = self._codex_pending_request_from_event(chat_id, target_chat, event)
                target_chat["request_kind"] = "user_input"
                self._push_remote_attention(chat_id, "action_required", str(event.request_id or ""), "\u9700\u8981\u60a8\u7684\u786e\u8ba4\u6216\u8f93\u5165")
                target_chat["codex_turn_active"] = True
                self._mark_history_list_dirty()
                if target_idx >= 0:
                    self._mark_chat_turns_dirty(chat_id, target_idx)
                self._refresh_visible_history_chat(chat_id)
                self._defer_codex_state_save()
                return
            if target_idx >= 0 and isinstance(target_chat, dict):
                turn = target_turns[target_idx]
                if event_type == "item_completed" and str(event.phase or "") == "final_answer":
                    self._apply_codex_completed_answer_to_turn(turn, event)
                elif event_type == "subagent_result":
                    self._apply_codex_subagent_result_to_turn(turn, str(event.text or ""))
                elif event_type == "turn_completed":
                    self._mark_turn_request_done(turn)
                    turn["request_error"] = ""
                    self._clear_codex_worker_active_turn(chat_id, target_idx, event_turn_id)
                    self._apply_codex_completed_answer_to_turn(turn, event)
                    if str(event.status or "").strip() in {"", "completed"}:
                        self._record_chat_activity(target_chat, bindings.app.time.time())
                    self._refresh_context_usage_after_done(target_chat, target_turns, target_idx, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                    self._request_codex_chat_information(target_chat, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                    self._complete_clear_operation_turn(turn, failed=False)
                    self._queue_remote_final(chat_id, target_idx)
                    self._play_finish_sound()
                self._mark_chat_turns_dirty(chat_id, target_idx)
                self._refresh_visible_history_chat(chat_id)
            self._defer_codex_state_save()
            return
        if event_type == "agent_message_delta":
            self._buffer_execution_delta(chat_id, event)
        else:
            self._flush_execution_delta(chat_id, event_turn_id or None)
        if execution_entry:
            appended_execution_step = self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
        if event_type == "token_count" and event.usage:
            target_idx = self._event_turn_index(self.active_session_turns, event)
            if target_idx < 0:
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0:
                turn = self.active_session_turns[target_idx] if target_idx < len(self.active_session_turns) and isinstance(self.active_session_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if completed_turn:
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(self._current_chat_state, target_idx), None)
                    changed = self._set_chat_context_usage(self._current_chat_state, event.usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(self._current_chat_state, target_idx, event.usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
            return
        if event_type == "server_request":
            self.active_codex_pending_request = None
            self._push_remote_status("waiting_user_input", "user_input")
            self._play_finish_sound()
            if str(event.method or "") == "item/tool/requestUserInput":
                event_data = event.data if isinstance(getattr(event, "data", None), dict) else {}
                self._handle_codex_request_dialog(
                    {
                        "chat_id": chat_id,
                        "model": event_data.get("model") or self.selected_model or DEFAULT_CODEX_MODEL,
                        "request_id": event.request_id,
                        "method": event.method,
                        "params": event.params,
                    }
                )
            self._defer_chat_state_save()
            return
        if event_type == "subagent_result":
            target_idx = self._active_codex_event_target_index(event)
            if target_idx >= 0 and target_idx < len(self.active_session_turns):
                turn = self.active_session_turns[target_idx]
                if self._apply_codex_subagent_result_to_turn(turn, str(event.text or "")):
                    if self._background_ui_mutations_blocked():
                        self._mark_background_answer_list_dirty()
                    else:
                        self._update_active_answer_row(target_idx)
                        if self._find_answer_row_index(target_idx) < 0 and self.view_mode == "active":
                            self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
                    self._push_remote_final_answer(chat_id or self.active_chat_id or self.current_chat_id or "", str(turn.get("answer_md") or ""), turn_index=target_idx)
                    self._defer_codex_state_save()
            return
        if event_type == "turn_completed":
            if is_current_chat:
                self.active_codex_turn_active = False
                self.active_codex_pending_request = None
            target_idx = self._active_codex_event_target_index(event)
            turn = {}
            if target_idx >= 0 and target_idx < len(self.active_session_turns):
                turn = self.active_session_turns[target_idx]
                if str(turn.get("request_status") or "") == "done":
                    return
                if not self._accept_clear_operation_result(turn, chat_id or self.active_chat_id):
                    self._clear_codex_worker_active_turn(chat_id or self.active_chat_id, target_idx, event_turn_id)
                    return
                self._mark_turn_request_done(turn)
                turn["request_error"] = ""
                if str(event.status or "").strip() in {"", "completed"}:
                    self._record_chat_activity(self._current_chat_state, bindings.app.time.time())
                self._clear_codex_worker_active_turn(chat_id or self.active_chat_id or self.current_chat_id, target_idx, event_turn_id)
                self._apply_codex_completed_answer_to_turn(turn, event)
                self._refresh_context_usage_after_done(self._current_chat_state, self.active_session_turns, target_idx, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                self._request_codex_chat_information(self._current_chat_state, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                self._complete_clear_operation_turn(turn, failed=False)
                if self._background_ui_mutations_blocked():
                    self._mark_background_answer_list_dirty()
                else:
                    self._update_active_answer_row(target_idx)
                self._mark_chat_turns_dirty(start_index=target_idx)
                self._queue_remote_final(chat_id or self.active_chat_id or self.current_chat_id or "", target_idx)
                self._request_execution_list_sync(self._current_chat_state)
            if is_current_chat:
                self.is_running = False
                self._active_request_count = 0
                self.new_chat_button.Enable()
                self._set_input_hint_idle()
            if is_current_chat:
                self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
                self._play_finish_sound()
                self._defer_chat_state_save()
                if self.view_mode == "active":
                    if self._background_ui_mutations_blocked():
                        self._mark_background_answer_list_dirty()
                    elif self._find_answer_row_index(target_idx) < 0:
                        if self._append_completed_answer_to_answer_list(target_idx, turn):
                            self._focus_latest_answer()
                        else:
                            self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
            return
        if appended_execution_step and not str(getattr(event, "text", "") or "").strip():
            self._defer_codex_state_save()
        if event_type in {"item_completed", "agent_message_delta", "plan_updated", "diff_updated", "stderr", "turn_started", "item_started"}:
            self.active_codex_latest_assistant_text = str(event.text or "")
            self.active_codex_latest_assistant_phase = str(event.phase or "")
            if event_type == "item_completed" and str(event.status or "") == "imageView":
                target_idx = self._active_codex_event_target_index(event)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    path = str((event.data or {}).get("path") or "").strip()
                    if path and Path(path).is_file():
                        attachment = {
                            "name": Path(path).name,
                            "path": path,
                            "kind": "image",
                            "direction": "incoming",
                            "status": "success",
                            "open_path": path,
                            "source": "codex",
                        }
                        if self._record_received_attachment(self.active_session_turns[target_idx], attachment):
                            self._defer_codex_state_save()
                            if self._background_ui_mutations_blocked():
                                self._mark_background_answer_list_dirty()
                            else:
                                self._render_answer_list_compat(refresh_execution=self._detail_panel_mode() != "execution")
                return
            if event_type == "item_completed" and str(event.phase or "") == "final_answer":
                self.active_codex_pending_prompt = str(event.text or "")
                target_idx = self._active_codex_event_target_index(event)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    turn = self.active_session_turns[target_idx]
                    self._apply_codex_completed_answer_to_turn(turn, event)
                    if self._background_ui_mutations_blocked():
                        self._mark_background_answer_list_dirty()
                    else:
                        # A final answer may be the first visible response for
                        # this turn. Let the completion path append its rows
                        # below instead of rebuilding the whole answer list.
                        self._update_active_answer_row(target_idx, rebuild_if_missing=False)
                    self._mark_chat_turns_dirty(start_index=target_idx)
                    self._request_execution_list_sync(self._current_chat_state)
                self._defer_codex_state_save()
                self._push_remote_final_answer(chat_id or self.active_chat_id or self.current_chat_id or "", str(event.text or ""), turn_index=target_idx)
                if self._background_ui_mutations_blocked():
                    self._mark_background_answer_list_dirty()
                elif self._append_completed_answer_to_answer_list(target_idx, self.active_session_turns[target_idx] if 0 <= target_idx < len(self.active_session_turns) else {}):
                    self._focus_latest_answer()
                else:
                    self._render_answer_list_compat(refresh_execution=self._detail_panel_mode() != "execution")
                return
            if event.text:
                self._defer_codex_state_save()
            return

    def _codex_source_client_is_current(self, chat_id: str, client) -> bool:
        current = self._codex_clients.get(str(chat_id or "").strip())
        if current is None and chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            current = getattr(self, "_codex_client", None)
        return current is client

    def _on_codex_worker_message(
        self,
        default_chat_id: str,
        message: dict,
        client: bindings.app.CodexWorkerClient | None = None,
    ) -> None:
        if not isinstance(message, dict):
            return
        if client is not None and not self._codex_source_client_is_current(default_chat_id, client):
            return
        message_type = str(message.get("type") or "").strip()
        if (
            message_type in {"thread_state", "turn_started_ack", "error", "fatal", "protocol_error", "chat_information"}
            and bindings.app.threading.current_thread() is not bindings.app.threading.main_thread()
        ):
            self._call_after_if_alive(self._on_codex_worker_message, default_chat_id, message, client)
            return
        if message_type == "messages_pending":
            self._drain_codex_worker_client_messages(default_chat_id, client)
            return
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        chat_id = str(payload.get("chat_id") or default_chat_id or "").strip()
        if message_type == "chat_information":
            subscriptions = getattr(self, "_scoped_chat_information", None)
            if subscriptions is not None and subscriptions.apply_codex(chat_id, message, payload):
                return
            self._apply_codex_chat_information(chat_id, message, payload)
            return
        if message_type == "event":
            event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else payload
            try:
                event = codex_worker_protocol.event_from_payload(event_payload)
            except Exception:
                return
            self._apply_codex_worker_event_scope(event, payload)
            if client is None:
                self._dispatch_codex_event_to_ui(chat_id, event)
            else:
                self._dispatch_codex_event_to_ui(chat_id, event, client)
            return
        if message_type == "thread_state":
            self._apply_codex_worker_thread_state(chat_id, payload)
            return
        if message_type == "turn_started_ack":
            self._apply_codex_worker_turn_started_ack(chat_id, payload)
            return
        if message_type == "error":
            self._apply_codex_worker_error(
                chat_id,
                str(payload.get("message") or ""),
                payload.get("turn_idx"),
                payload.get("turn_id"),
                context_generation=payload.get("context_generation"),
            )
            return
        if message_type == "protocol_error":
            return
        if message_type == "fatal":
            self._apply_codex_worker_fatal(chat_id, payload)
            return

    @staticmethod
    def _apply_codex_worker_event_scope(event: CodexEvent, payload: dict) -> None:
        if not isinstance(event, CodexEvent) or not isinstance(payload, dict):
            return
        data = dict(event.data or {}) if isinstance(event.data, dict) else {}
        if "turn_idx" not in data and "turn_idx" in payload:
            data["turn_idx"] = payload.get("turn_idx")
        if "context_generation" in payload:
            data["context_generation"] = payload.get("context_generation")
        if "completion_owners" in payload:
            data["completion_owners"] = payload.get("completion_owners")
        model = str(payload.get("model") or "").strip()
        if model and "model" not in data:
            data["model"] = model
        if data:
            event.data = data

    def _apply_codex_worker_fatal(self, chat_id: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        normalized = str(chat_id or payload.get("chat_id") or "").strip()
        metadata = self._codex_worker_active_turns.get(normalized) if normalized else None
        if not isinstance(metadata, dict):
            metadata = {}
        has_explicit_scope = any(key in payload for key in ("chat_id", "turn_idx", "turn_id"))
        has_tracked_scope = bool(metadata)
        if not has_explicit_scope and not has_tracked_scope:
            return
        turn_idx = payload.get("turn_idx") if "turn_idx" in payload else metadata.get("turn_idx")
        turn_id = payload.get("turn_id") if "turn_id" in payload else metadata.get("turn_id")
        self._apply_codex_worker_error(
            normalized,
            str(payload.get("message") or "Codex worker failed"),
            turn_idx,
            str(turn_id or "").strip() or None,
        )

    def _drain_codex_worker_client_messages(
        self,
        default_chat_id: str,
        client: bindings.app.CodexWorkerClient | None,
    ) -> None:
        if client is None or not hasattr(client, "drain_pending_messages"):
            return
        try:
            messages = client.drain_pending_messages()
        except Exception:
            return
        for message in messages:
            self._on_codex_worker_message(default_chat_id, message, client)

    def _codex_worker_target_chat(self, chat_id: str) -> tuple[dict | None, bool]:
        normalized = str(chat_id or "").strip()
        is_current_target = normalized in {self.active_chat_id, self.current_chat_id, ""}
        if is_current_target:
            return self._current_chat_state, True
        chat = self._find_archived_chat(normalized)
        return (chat if isinstance(chat, dict) else None), False

    def _remember_codex_worker_active_turn(self, chat_id: str, payload: dict) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        metadata: dict = {}
        turn_idx = payload.get("turn_idx")
        if isinstance(turn_idx, int):
            metadata["turn_idx"] = turn_idx
        turn_id = str(payload.get("turn_id") or "").strip()
        if turn_id:
            metadata["turn_id"] = turn_id
        if metadata:
            self._codex_worker_active_turns[normalized] = metadata

    def _clear_codex_worker_active_turn(self, chat_id: str, turn_idx=None, turn_id: str | None = None) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        metadata = self._codex_worker_active_turns.get(normalized)
        if not isinstance(metadata, dict):
            return
        if isinstance(turn_idx, int) and metadata.get("turn_idx") != turn_idx:
            return
        turn_id_value = str(turn_id or "").strip()
        if turn_id_value and str(metadata.get("turn_id") or "").strip() != turn_id_value:
            return
        self._codex_worker_active_turns.pop(normalized, None)

    def _apply_codex_worker_thread_state(self, chat_id: str, payload: dict) -> None:
        target_chat, is_current_target = self._codex_worker_target_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        if "thread_id" in payload:
            ack_idx = payload.get("turn_idx")
            turns = self.active_session_turns if is_current_target else target_chat.get("turns")
            if not isinstance(ack_idx, int) or not isinstance(turns, list) or not 0 <= ack_idx < len(turns):
                return
            ack_turn = turns[ack_idx]
            if not isinstance(ack_turn, dict):
                return
            try:
                ack_generation = int(payload.get("context_generation"))
            except (TypeError, ValueError):
                return
            if ack_generation != int(target_chat.get("codex_context_generation") or 0):
                return
            if ack_generation != int(ack_turn.get("codex_context_generation") or 0):
                return
            ack_turn.setdefault("codex_start_generation", ack_generation)
        thread_id = str(payload.get("thread_id") or "").strip()
        turn_id = str(payload.get("turn_id") or "").strip()
        if "thread_id" in payload:
            thread_changed = str(target_chat.get("codex_thread_id") or "").strip() != thread_id
            if thread_changed:
                turn_idx = payload.get("turn_idx")
                preserve = (turn_idx, thread_id, turn_id) if isinstance(turn_idx, int) and turn_id else None
                self._clear_codex_context_usage_for_thread_change(target_chat, preserve=preserve)
                if preserve is not None:
                    key = self._context_usage_pending_key_from_chat(target_chat, turn_idx)
                    self._codex_early_context_usage_owner.pop(key, None)
            target_chat["codex_thread_id"] = thread_id
            if "thread_configuration" in payload:
                configuration = payload.get("thread_configuration")
                target_chat["codex_thread_configuration"] = dict(configuration) if isinstance(configuration, dict) else {}
                target_chat["codex_thread_configuration_thread_id"] = thread_id
            if is_current_target:
                self.active_codex_thread_id = thread_id
            if thread_changed:
                self._publish_codex_context_thread_change(target_chat)
        if "turn_id" in payload:
            target_chat["codex_turn_id"] = turn_id
            if is_current_target:
                self.active_codex_turn_id = turn_id
        if "active" in payload:
            target_chat["codex_turn_active"] = bool(payload.get("active"))
            if is_current_target:
                self.active_codex_turn_active = bool(payload.get("active"))
            if payload.get("active"):
                self._remember_codex_worker_active_turn(chat_id, payload)
            else:
                self._clear_codex_worker_active_turn(chat_id)
        target_turns = self.active_session_turns if is_current_target else target_chat.get("turns")
        turn_idx = payload.get("turn_idx")
        if isinstance(target_turns, list) and isinstance(turn_idx, int) and 0 <= turn_idx < len(target_turns):
            turn = target_turns[turn_idx]
            if isinstance(turn, dict):
                if "thread_id" in payload:
                    turn["codex_thread_id"] = thread_id
                    if thread_changed:
                        turn["codex_context_generation"] = int(target_chat.get("codex_context_generation") or 0)
                if "turn_id" in payload:
                    turn["codex_turn_id"] = turn_id
                if thread_id or turn_id:
                    turn["request_resume_token"] = {"thread_id": thread_id, "turn_id": turn_id}
                self._mark_chat_turns_dirty(None if is_current_target else chat_id, turn_idx)
        if not is_current_target:
            self._refresh_visible_history_chat(str(chat_id or "").strip())
        self._defer_codex_state_save()

    def _apply_codex_worker_turn_started_ack(self, chat_id: str, payload: dict) -> None:
        self._apply_codex_worker_thread_state(chat_id, payload)

    def _codex_active_request_turn_count(self, turns) -> int:
        if not isinstance(turns, list):
            return 0
        count = 0
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            status = str(turn.get("request_status") or "").strip()
            if status in {"done", "failed"}:
                continue
            answer_md = str(turn.get("answer_md") or "").strip()
            if status in {"pending", "running"} or answer_md == REQUESTING_TEXT:
                count += 1
        return count

    def _apply_codex_worker_error(self, chat_id: str, message: str, turn_idx=None, turn_id: str | None = None, *, context_generation=None) -> None:
        target_chat, is_current_target = self._codex_worker_target_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_turns = self.active_session_turns if is_current_target else target_chat.get("turns")
        if not isinstance(target_turns, list):
            return
        if isinstance(turn_idx, int):
            target_idx = turn_idx if 0 <= turn_idx < len(target_turns) else -1
        else:
            target_idx = -1
        if target_idx < 0:
            turn_id_value = str(turn_id or "").strip()
            if not turn_id_value:
                return
            for idx, turn in enumerate(target_turns):
                if isinstance(turn, dict) and str(turn.get("codex_turn_id") or "").strip() == turn_id_value:
                    target_idx = idx
                    break
            if target_idx < 0 and str(target_chat.get("codex_turn_id") or "").strip() == turn_id_value:
                active_idx = self.active_turn_idx if is_current_target else -1
                if 0 <= active_idx < len(target_turns):
                    target_idx = active_idx
        if target_idx < 0 or target_idx >= len(target_turns):
            return
        turn = target_turns[target_idx]
        if not isinstance(turn, dict):
            return
        known_turn_id = str(turn.get("codex_turn_id") or "").strip()
        error_turn_id = str(turn_id or "").strip()
        if error_turn_id and known_turn_id and error_turn_id != known_turn_id:
            return
        try:
            generation = int(target_chat.get("codex_context_generation") or 0)
            turn_generation = int(turn.get("codex_context_generation") or 0)
            start_generation = int(turn.get("codex_start_generation", turn_generation))
            if context_generation is not None:
                if isinstance(context_generation, bool) or int(context_generation) != start_generation or turn_generation != generation:
                    return
            elif generation and (not error_turn_id or error_turn_id != known_turn_id):
                return
        except (TypeError, ValueError):
            return
        if str(turn.get("request_status") or "") in {"done", "failed"}:
            return
        self._mark_turn_request_failed(turn, message)
        active_count = self._codex_active_request_turn_count(target_turns)
        target_chat["codex_turn_active"] = active_count > 0
        if is_current_target:
            self.active_codex_turn_active = active_count > 0
            if active_count > 0:
                self.is_running = True
                self._active_request_count = active_count
            else:
                self.active_codex_pending_request = None
                self.active_codex_pending_prompt = ""
                self.active_codex_thread_flags = []
                self.is_running = False
                self._active_request_count = 0
        if active_count == 0:
            target_chat["codex_pending_prompt"] = ""
            target_chat["codex_thread_flags"] = []
        self._clear_codex_worker_active_turn(chat_id, target_idx, turn.get("codex_turn_id"))
        self._mark_chat_turns_dirty(None if is_current_target else chat_id, target_idx)
        if not is_current_target:
            self._refresh_visible_history_chat(str(chat_id or "").strip())
        self._defer_codex_state_save()

    def _on_codex_worker_exit(self, chat_id: str, returncode, source_client=None) -> None:
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            if source_client is None:
                self._call_after_if_alive(self._on_codex_worker_exit, chat_id, returncode)
            else:
                self._call_after_if_alive(self._on_codex_worker_exit, chat_id, returncode, source_client)
            return
        if source_client is not None and not self._codex_source_client_is_current(chat_id, source_client):
            return
        if returncode in {None, 0}:
            return
        metadata = self._codex_worker_active_turns.get(str(chat_id or "").strip())
        if not isinstance(metadata, dict):
            return
        self._apply_codex_worker_error(
            chat_id,
            f"Codex worker exited with code {returncode}",
            metadata.get("turn_idx"),
            metadata.get("turn_id"),
        )

    def _get_or_create_codex_client(self, chat_id: str, model: str = "") -> bindings.app.CodexWorkerClient:
        key = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip() or self._ensure_active_chat_id()
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = self._codex_clients.get(key)
        if client is not None:
            setattr(client, "codex_model", codex_model)
            return client
        client_ref = {}

        def _on_message(message, cid=key, ref=client_ref):
            self._on_codex_worker_message(cid, message, ref.get("client"))

        client = bindings.app.CodexWorkerClient(
            on_message=_on_message,
            on_exit=lambda code, cid=key, ref=client_ref: self._on_codex_worker_exit(cid, code, ref.get("client")),
        )
        client_ref["client"] = client
        setattr(client, "codex_model", codex_model)
        self._codex_clients[key] = client
        return client

    def _ensure_codex_client(self, model: str = "") -> bindings.app.CodexWorkerClient:
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = getattr(self, "_codex_client", None)
        if client is not None:
            setattr(client, "codex_model", codex_model)
            return client
        key = str(self.active_chat_id or self.current_chat_id or "").strip() or self._ensure_active_chat_id()
        client_ref = {}

        def _on_message(message, cid=key, ref=client_ref):
            self._on_codex_worker_message(cid, message, ref.get("client"))

        client = bindings.app.CodexWorkerClient(
            on_message=_on_message,
            on_exit=lambda code, cid=key, ref=client_ref: self._on_codex_worker_exit(cid, code, ref.get("client")),
        )
        client_ref["client"] = client
        setattr(client, "codex_model", codex_model)
        self._codex_client = client
        return client

    def _reset_active_codex_session_state(self) -> None:
        self.active_codex_thread_id = ""
        self.active_codex_turn_id = ""
        self.active_codex_turn_active = False
        self.active_codex_pending_prompt = ""
        self.active_codex_pending_request = None
        self.active_codex_request_queue = []
        self.active_codex_thread_flags = []
        self.active_codex_latest_assistant_text = ""
        self.active_codex_latest_assistant_phase = ""

    def _write_active_codex_session_state_to_chat(self, chat: dict) -> None:
        chat["codex_thread_id"] = self.active_codex_thread_id
        chat["codex_turn_id"] = self.active_codex_turn_id
        chat["codex_turn_active"] = self.active_codex_turn_active
        chat["codex_pending_prompt"] = self.active_codex_pending_prompt
        chat["codex_pending_request"] = self.active_codex_pending_request
        chat["codex_request_queue"] = self.active_codex_request_queue
        chat["codex_thread_flags"] = self.active_codex_thread_flags
        chat["codex_latest_assistant_text"] = self.active_codex_latest_assistant_text
        chat["codex_latest_assistant_phase"] = self.active_codex_latest_assistant_phase
