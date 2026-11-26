"""Dispatch authenticated terminal requests through the existing MC chat runtime."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import partial

from agy_acp import AGY_MODEL_ID
from codex_client import is_codex_model
from kimi_server_client import is_kimi_model
from shared_terminal_command import MAX_PROMPT_LENGTH

# Each enabled provider supplies a model predicate and its native session resolver.
PROVIDER_ROUTES = {
    "codex": (is_codex_model, "_codex_thread_id_for_chat"),
    "kimi": (is_kimi_model, "_kimi_session_id_for_chat"),
    "agy": (lambda model: model == AGY_MODEL_ID, "_agy_session_id_for_chat"),
}
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")


@dataclass
class _TerminalScope:
    chat: dict
    turns: list | None
    active: bool
    model: str
    authority: tuple
    count: int

    def same_as(self, current):
        return (self.chat is current.chat and self.turns is current.turns
                and self.authority == current.authority and self.count == current.count)


def dispatch_terminal(frame, operation: str, payload: dict) -> tuple[int, dict]:
    if operation == "ping":
        return frame._run_remote_ui_route(
            lambda: ((503, {"error": "ui_unavailable"}) if getattr(frame, "_closing", False)
                     else (200, {"ready": True, "pair_id": frame._read_pair_id()}))
        )
    if operation == "open_native" and payload.get("provider") == "codex":
        return _open_codex(frame, payload)
    if operation not in {"list", "send", "watch"}:
        return 400, {"error": "invalid_operation"}
    provider = payload.get("provider")
    route = PROVIDER_ROUTES.get(provider) if isinstance(provider, str) else None
    if route is None:
        return 409, {"error": "provider_unavailable"}
    if provider == "codex" and operation != "list":
        return 400, {"error": "invalid_operation"}
    if operation == "list":
        return frame._run_remote_ui_route(partial(_list_ui, frame, route), payload)
    owner, request_id, session = (payload.get(key) for key in ("chat_id", "request_id", "session_id"))
    if (not isinstance(owner, str) or not owner or owner != owner.strip() or len(owner) > 512
            or not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
            or not isinstance(session, str) or not session or len(session) > 512):
        return 400, {"error": "invalid_chat_scope"}
    text = payload.get("text")
    if operation == "send" and (not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT_LENGTH):
        return 400, {"error": "invalid_prompt"}
    client_id = f"terminal:{request_id}"
    resolve = partial(_scope_ui, frame, route, owner, session)
    status, scope = frame._run_remote_ui_route(resolve)
    if status != 200:
        return status, scope
    for _attempt in range(2):
        existing = _find_request(frame.chat_store, owner, scope, client_id, payload.get("turn_index"))
        alias_answer = ""
        prepared_chat = None
        rows = []
        if operation == "send" and scope.turns is None and existing is None and frame.chat_store is not None:
            prepared_chat = frame.chat_store.load_chat(owner, include_execution_steps=False)
        if operation == "watch" and existing is not None:
            index, turn = existing
            if (alias := turn.get("answer_alias_of")) and turn.get("request_status") == "done":
                alias_answer = _read_alias(frame.chat_store, owner, scope.turns, alias)
            if frame.chat_store is not None:
                _, rows = frame.chat_store.load_recent_execution_steps(
                    owner, turn_idx=index, limit=50, include_total=False, visible_only=True,
                )
        status, result = frame._run_remote_ui_route(
            lambda: _settle_ui(frame, resolve, scope, owner, operation, payload.get("provider"),
                               client_id, existing, alias_answer, text, prepared_chat))
        if status != 202:
            if status == 200 and operation == "watch" and frame.chat_store is not None:
                result["steps"] = [
                    {"id": str(row.get("logical_key") or row.get("_execution_uid") or row["_store_step_index"]),
                     "text": str(row.get("list_text") or "")[:512]}
                    for row in rows if row.get("list_text")
                ]
            return status, result
        scope = result
    return 409, {"error": "chat_changed"}


def _find_request(store, owner, scope, client_id, hint):
    turns = scope.turns
    if turns is None:
        return store.find_turn_by_client_send_id(owner, client_id) if store is not None else None
    # Revalidate the selected row and list size on wx before using this read.
    if type(hint) is int and 0 <= hint < len(turns):
        candidate = turns[hint:hint + 1]
        if candidate and candidate[0].get("client_send_id") == client_id:
            return hint, dict(candidate[0])
    for index, turn in enumerate(reversed(turns), start=1):
        if turn.get("client_send_id") == client_id:
            return scope.count - index, dict(turn)
    return None


def _read_alias(store, owner, turns, alias):
    if turns is not None:
        return next((str(row["answer_md"]) for row in reversed(turns)
                     if row.get("answer_identity") == alias and row.get("answer_md")), "")
    return store.find_answer_by_identity(owner, alias) if store is not None else ""


def _open_codex(frame, payload):
    status, scope = frame._run_remote_ui_route(frame._prepare_codex_terminal_open, payload)
    if status != 200:
        return status, scope
    client = scope.pop("client")
    try:
        result = client.open_terminal(**scope)
    except (RuntimeError, OSError) as exc:
        return 503, {"error": "provider_unavailable", "message": str(exc)}
    return frame._run_remote_ui_route(lambda: frame._confirm_codex_terminal_open(client, scope, result))


def _list_ui(frame, route, payload):
    if getattr(frame, "_closing", False):
        return 503, {"error": "ui_unavailable"}
    model_matches, native_id_method = route
    native_id = getattr(frame, native_id_method)
    chats = []
    seen = set()
    for chat in (frame._current_chat_state, *frame.archived_chats):
        owner = str(chat.get("id") or "")
        if owner and owner not in seen and model_matches(frame._model_for_chat_selection(chat)):
            session_id = native_id(chat)
            if session_id or payload.get("provider") == "codex":
                chats.append({"chat_id": owner, "title": str(chat.get("title") or "新聊天"),
                              "session_id": session_id})
            seen.add(owner)
    return 200, {"chats": chats, "pair_id": frame._read_pair_id()}


def _scope_ui(frame, route, owner, session):
    if getattr(frame, "_closing", False):
        return 503, {"error": "ui_unavailable"}
    model_matches, native_id_method = route
    chat, turns, active = frame._chat_target_for_request(owner)
    if not chat or chat.get("id") != owner:
        return 404, {"error": "chat_not_found"}
    model = frame._model_for_chat_selection(chat)
    if not model_matches(model):
        return 404, {"error": "chat_not_found"}
    if getattr(frame, native_id_method)(chat) != session:
        return 409, {"error": "chat_changed"}
    turns = turns if active or isinstance(chat.get("turns"), list) else None
    count = len(turns) if turns is not None else int(chat.get("turn_count") or 0)
    return 200, _TerminalScope(chat, turns, active, model, _chat_authority(frame, model, session, chat), count)


def _chat_authority(frame, model, session, chat):
    return (frame._read_pair_id(), model, session, *(chat.get(field) for field in (
        "revision", "native_identity", "native_account", "native_cwd",
        "kimi_storage_id", "agy_native_owner", "agy_workspace",
    )))


def _settle_ui(frame, resolve, expected, owner, operation, provider, client_id, existing, alias_answer,
               text, prepared_chat):
    status, scope = resolve()
    if status != 200 or scope.authority != expected.authority:
        return 409, {"error": "chat_changed"}
    if not expected.same_as(scope):
        return 202, scope
    chat, turns = scope.chat, scope.turns
    if existing is not None and turns is not None:
        index, original = existing
        if not 0 <= index < len(turns) or turns[index].get("client_send_id") != client_id:
            return 202, scope
        current = turns[index]
        if (current.get("answer_alias_of") != original.get("answer_alias_of") or
                (current.get("answer_alias_of") and current.get("request_status") == "done"
                 and original.get("request_status") != "done")):
            return 202, scope
        existing = index, current
    if operation == "watch":
        if existing is None:
            return 404, {"error": "request_not_found"}
        index, turn = existing
        state = str(turn.get("request_status") or "pending")
        body = {"state": "pending" if state == "running" else state, "chat_id": owner,
                "turn_index": index, "answer": (alias_answer or str(turn.get("answer_md") or "")) if state == "done" else "",
                "message": str(turn.get("request_error") or "")}
        if provider == "agy" and body["state"] == "pending":
            run = frame._agy_run_for_chat(chat)
            if run and run.turn_idx == index:
                pending = frame._agy_interaction_payload(owner, run)
                if pending:
                    body["interaction_id"] = pending["event_id"]
        return 200, body
    text = text.strip()
    if existing is not None:
        if existing[1].get("question") != text:
            return 409, {"error": "request_id_conflict"}
        return 200, {"accepted": True, "turn_index": existing[0]}
    if prepared_chat is not None:
        _, native_id_method = PROVIDER_ROUTES[provider]
        authority = _chat_authority(frame, frame._model_for_chat_selection(prepared_chat),
                                    getattr(frame, native_id_method)(prepared_chat), prepared_chat)
        if authority != expected.authority or len(prepared_chat["turns"]) != expected.count:
            return 409, {"error": "chat_changed"}
    ok, detail = frame._submit_archived_remote_question(
        chat, text, scope.model, client_send_id=client_id, active=scope.active, source="terminal",
        prepared_chat=prepared_chat,
    )
    if not ok:
        return 409, {"error": "provider_rejected_submission", "message": detail}
    frame._push_remote_state(owner)
    frame._push_remote_history_changed(owner)
    return 200, {"accepted": True, "turn_index": len(chat["turns"]) - 1}
