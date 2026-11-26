"""Dispatch authenticated terminal requests through the existing MC chat runtime."""
from __future__ import annotations

import re
from functools import partial

from codex_client import is_codex_model
from kimi_server_client import is_kimi_model
from shared_terminal_command import MAX_PROMPT_LENGTH

# Each enabled provider supplies a model predicate and its native session resolver.
PROVIDER_ROUTES = {
    "codex": (is_codex_model, "_codex_thread_id_for_chat"),
    "kimi": (is_kimi_model, "_kimi_session_id_for_chat"),
}
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")


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
    status, body = frame._run_remote_ui_route(partial(_dispatch_ui, frame, route, operation), payload)
    if operation == "watch" and status == 200 and frame.chat_store is not None:
        _, rows = frame.chat_store.load_recent_execution_steps(
            body["chat_id"], turn_idx=body["turn_index"], limit=50, include_total=False, visible_only=True,
        )
        # A clear or model switch can retire the turn while the worker reads steps.
        status, current = frame._run_remote_ui_route(partial(_dispatch_ui, frame, route, operation), payload)
        if status != 200 or current["turn_index"] != body["turn_index"]:
            return 409, {"error": "chat_changed"}
        body = current
        body["steps"] = [
            {"id": str(row.get("logical_key") or row.get("_execution_uid") or row["_store_step_index"]),
             "text": str(row.get("list_text") or "")[:512]}
            for row in rows if row.get("list_text")
        ]
    return status, body


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


def _dispatch_ui(frame, route, operation, payload):
    if getattr(frame, "_closing", False):
        return 503, {"error": "ui_unavailable"}
    model_matches, native_id_method = route
    native_id = getattr(frame, native_id_method)
    if operation == "list":
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
    owner = payload.get("chat_id")
    request_id = payload.get("request_id")
    session = payload.get("session_id")
    if (not isinstance(owner, str) or not owner or owner != owner.strip() or len(owner) > 512
            or not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
            or not isinstance(session, str) or not session or len(session) > 512):
        return 400, {"error": "invalid_chat_scope"}
    chat, turns, active = frame._chat_target_for_request(owner)
    if not chat or chat.get("id") != owner:
        return 404, {"error": "chat_not_found"}
    model = frame._model_for_chat_selection(chat)
    if not model_matches(model):
        return 404, {"error": "chat_not_found"}
    if native_id(chat) != session:
        return 409, {"error": "chat_changed"}
    client_id = f"terminal:{request_id}"
    # The hint accelerates observation, but the stored correlation authorizes the match.
    hint = payload.get("turn_index")
    existing = None
    if type(hint) is int and 0 <= hint < len(turns) and turns[hint].get("client_send_id") == client_id:
        existing = hint, turns[hint]
    if existing is None:
        existing = next(((index, turns[index]) for index in range(len(turns) - 1, -1, -1)
                         if turns[index].get("client_send_id") == client_id), None)
    if existing is None and not isinstance(chat.get("turns"), list) and frame.chat_store is not None:
        existing = frame.chat_store.find_turn_by_client_send_id(owner, client_id)
    if operation == "watch":
        if existing is None:
            return 404, {"error": "request_not_found"}
        index, turn = existing
        status = str(turn.get("request_status") or "pending")
        answer = str(turn.get("answer_md") or "")
        alias = turn.get("answer_alias_of")
        if status == "done" and alias:
            originals = turns if turns else frame.chat_store.load_turns(owner) if frame.chat_store is not None else []
            answer = next((str(row["answer_md"]) for row in reversed(originals)
                           if row.get("answer_identity") == alias and row.get("answer_md")), answer)
        return 200, {"state": "pending" if status == "running" else status, "chat_id": owner,
                     "turn_index": index, "answer": answer if status == "done" else "",
                     "message": str(turn.get("request_error") or "")}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROMPT_LENGTH:
        return 400, {"error": "invalid_prompt"}
    text = text.strip()
    if existing is not None:
        if existing[1].get("question") != text:
            return 409, {"error": "request_id_conflict"}
        return 200, {"accepted": True, "turn_index": existing[0]}
    ok, detail = frame._submit_archived_remote_question(
        chat, text, model, client_send_id=client_id, active=active, source="terminal",
    )
    if not ok:
        return 409, {"error": "provider_rejected_submission", "message": detail}
    frame._push_remote_state(owner)
    frame._push_remote_history_changed(owner)
    return 200, {"accepted": True, "turn_index": len(chat["turns"]) - 1}
