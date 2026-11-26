"""Read-only Antigravity inventory and ACP history replay."""

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import time

from agy_acp import (
    AGY_MODEL_ID, AgyAcpClient, AgyError, agy_session_is_busy, normalize_agy_workspace, validate_workspace,
)
from native_history import NativeDeletionRejected, NativeSessionIdentity, NativeSessionSnapshot


def _timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return max(0, value)
    if not isinstance(value, str):
        return 0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError, OverflowError):
        return 0


def list_agy_sessions(executable: str, pair_id: str):
    with _reading_client(executable, str(Path.home())) as client:
        capabilities = client.capabilities.get("sessionCapabilities") or {}
        if not isinstance(capabilities.get("list"), dict):
            return
        cursor = ""
        seen = set()
        while True:
            observed_at = time.time()
            page = client.request("session/list", {"cursor": cursor} if cursor else {})
            for info in page.get("sessions") or []:
                if not isinstance(info, dict):
                    continue
                session_id, cwd = info.get("sessionId"), info.get("cwd")
                if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str) or not cwd:
                    continue
                cwd = normalize_agy_workspace(cwd)
                identity = NativeSessionIdentity(pair_id, "agy", client.native_storage(), session_id)
                account = client.native_account()
                legacy = {"agy_session_id": session_id, "agy_native_owner": client.native_owner(pair_id)}
                status = ("AGY 原生终端正在执行，结束后可刷新历史"
                          if agy_session_is_busy(info) else "")
                yield NativeSessionSnapshot(
                    identity, account, AGY_MODEL_ID, cwd, str(info.get("title") or ""),
                    _timestamp(info.get("updatedAt")), {"agy_workspace": cwd, **legacy},
                    status=status, legacy_fields=legacy, title_observed_at=observed_at,
                )
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                return
            seen.add(cursor)


def read_agy_session(executable: str, session: NativeSessionSnapshot) -> None:
    if session.status:
        return
    try:
        cwd = validate_workspace(session.cwd)
        with _reading_client(executable, cwd) as client:
            if client.native_storage() != session.identity.storage_id or client.native_account() != session.account:
                raise AgyError("AGY 原会话归属已变化，请恢复原账号")
            history = _AgyReplay(session.identity.session_id)
            client.open_session(session.identity.session_id, replay=True, on_update=history.update)
            session.turns, session.execution_steps = history.completed_history()
            session.chat_fields["agy_options"] = client.options
    except AgyError as exc:
        session.status = str(exc)


def verify_agy_session_absent(executable, pair, session_id, identity, account, cwd):
    if not executable:
        raise NativeDeletionRejected("未找到来源电脑上的 AGY，无法刷新确认。")
    cwd = validate_workspace(cwd)
    with _reading_client(executable, cwd) as client:
        actual = NativeSessionIdentity(pair, "agy", client.native_storage(), session_id)
        if actual.chat_id != identity or client.native_account() != account:
            raise NativeDeletionRejected("AGY 原始账号或存储已变更，请切回后刷新。")
        capabilities = client.capabilities.get("sessionCapabilities") or {}
        if ((client.initialization.get("agentInfo") or {}).get("version") != "1.5.17"
                or client.capabilities.get("loadSession") is not True
                or not isinstance(capabilities.get("list"), dict)):
            raise NativeDeletionRejected("当前 AGY 版本无法读取原始会话清单。")
        cursor = None
        seen = set()
        while True:
            page = client.request("session/list", {"cursor": cursor} if cursor else {})
            rows = page.get("sessions")
            if not isinstance(rows, list) or any(
                    not isinstance(row, dict) or not isinstance(row.get("sessionId"), str) for row in rows):
                return False
            if any(row["sessionId"] == session_id for row in rows):
                raise NativeDeletionRejected("AGY 原始会话仍在清单中，请在原生选择器删除后刷新确认。")
            cursor = page.get("nextCursor")
            if cursor is None or cursor == "":
                break
            if not isinstance(cursor, str) or cursor in seen:
                return False
            seen.add(cursor)
        try:
            client.request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []})
        except AgyError as exc:
            return exc.code == -32002 and exc.rpc_message == f"Session not found: {session_id}"
        return False


@contextmanager
def _reading_client(executable: str, cwd: str):
    client = AgyAcpClient(executable, cwd)
    try:
        client.initialize()
        yield client
    finally:
        client.close()


class _AgyReplay:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.turns = []
        self.steps = []
        self.tools = {}
        self.turn = None
        self.questions = []
        self.answers = []
        self.thoughts = []

    def update(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("sessionUpdate")
        content = event.get("content")
        text = content.get("text") if isinstance(content, dict) and content.get("type") == "text" else None
        message_id = event.get("messageId")
        if kind == "user_message_chunk" and isinstance(text, str):
            previous = self.turn.get("provider_user_message_id") if self.turn else ""
            if self.turn is None or self.answers or self.tools or self.thoughts or (previous and message_id and previous != message_id):
                self._finish_turn()
                self._start_turn(event)
            self.questions.append(text)
        elif kind in ("agent_message_chunk", "agent_thought_chunk") and isinstance(text, str):
            if self.turn is None:
                self._start_turn(event)
            if kind == "agent_message_chunk":
                self.answers.append(text)
                if isinstance(message_id, str):
                    self.turn["provider_message_id"] = message_id
                answer_at = _timestamp(event.get("timestamp"))
                if answer_at:
                    self.turn["answer_at"] = answer_at
            else:
                self.thoughts.append(text)
        elif kind in ("tool_call", "tool_call_update") and isinstance(event.get("toolCallId"), str):
            if self.turn is None:
                self._start_turn(event)
            tool_id = event["toolCallId"]
            self.tools.setdefault(tool_id, {}).update(event)

    def _start_turn(self, event):
        turn_id = str(event.get("turnId") or event.get("messageId") or f"replay-{len(self.turns)}")
        self.turn = {
            "model": AGY_MODEL_ID, "provider": "agy", "session_id": self.session_id,
            "turn_id": turn_id, "native_history": True,
        }
        if isinstance(event.get("turnId"), str) and event["turnId"]:
            self.turn["agy_native_turn_id"] = event["turnId"]
        message_id = event.get("messageId")
        if event.get("sessionUpdate") == "user_message_chunk" and isinstance(message_id, str):
            self.turn["provider_user_message_id"] = message_id
        created_at = _timestamp(event.get("timestamp"))
        if created_at:
            self.turn["created_at"] = created_at

    def _finish_turn(self):
        if self.turn is None:
            return
        self.turn.update(question="".join(self.questions), answer_md="".join(self.answers),
                         request_status="done",
                         assistant_only=not bool(self.questions))
        index = len(self.turns)
        self.turns.append(self.turn)
        if self.thoughts:
            self.steps.append({
                "turn_idx": index, "raw_kind": "thinking", "display_kind": "thinking",
                "step_id": f"agy-history-thought-{index}",
                "list_text": "思考", "detail_text": "".join(self.thoughts),
            })
        for tool_id, event in self.tools.items():
            raw_output = event.get("rawOutput")
            content = event.get("content")
            details = []
            if raw_output is not None:
                details.append(raw_output if isinstance(raw_output, str) else json.dumps(raw_output, ensure_ascii=False))
            for entry in content if isinstance(content, list) else ():
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "diff":
                    details.append(f"{entry.get('path', '')}\n{entry.get('oldText') or ''}\n{entry.get('newText') or ''}")
                elif entry.get("type") == "content" and isinstance(entry.get("content"), dict):
                    if entry["content"].get("type") == "text":
                        details.append(str(entry["content"].get("text") or ""))
            title = str(event.get("title") or tool_id)
            self.steps.append({
                "turn_idx": index, "raw_kind": "tool", "display_kind": "tool",
                "step_id": f"agy-history-tool-{index}-{tool_id}", "list_text": title,
                "detail_text": "\n".join(details) or title, "status": str(event.get("status") or ""),
            })
        self.turn = None
        self.questions.clear()
        self.answers.clear()
        self.thoughts.clear()
        self.tools.clear()

    def completed_history(self):
        # The final user-only or still-running tool block offers no settled answer.
        if self.answers and all(tool.get("status") in ("completed", "failed")
                                for tool in self.tools.values()):
            self._finish_turn()
        return self.turns, self.steps
