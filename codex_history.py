"""Discover and read Codex threads within a single Codex Home."""

from dataclasses import replace
import getpass
import hashlib
import os
from pathlib import Path
import time

from codex_client import (
    CODEX_MODEL_CONFIGS, DEFAULT_CODEX_MODEL, CodexAppServerClient, CodexRpcError, _event_from_item,
    _provider_origin_timestamp, _user_home, resolve_codex_launch_command,
)
from codex_worker_client import codex_worker_workspace
from native_history import NativeSessionIdentity, NativeSessionSnapshot, NativeTitleRejected


# The local App Server in 0.171.0 also writes threads with the vscode source.
_SOURCES = ("cli", "exec", "appServer", "vscode")
_UNAVAILABLE = "Codex 原生存储暂不可读，请检查本机运行时与目录"
_ACCOUNT_CHANGED = "Codex 原生会话属于之前的账号，请切回该账号后刷新"
_BUSY = "Codex 原生会话正在运行，请在本轮结束后继续"


def codex_history_homes():
    homes = {codex_worker_workspace() / ".codex-home": True, _user_home() / ".codex": False}
    if override := os.environ.get("CODEX_HOME"):
        homes.setdefault(Path(override).expanduser(), False)
    seen = set()
    for path, private in homes.items():
        try:
            path = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            yield path, private


def codex_history_account(client):
    result = client.read_account(refresh_token=False)
    account = result.get("account") or {}
    if account.get("type") == "chatgpt":
        user = result.get("userId") or account.get("email")
        if not isinstance(user, str) or not user:
            raise RuntimeError("Codex 无法确认当前登录账号，请检查原存储的登录状态")
        routing = result.get("workspaceRouting") or {}
        workspace = routing.get("chatgptAccountId") or ""
        return "chatgpt:" + hashlib.sha256(f"{user.casefold()}\x00{workspace}".encode()).hexdigest()
    if not account and result.get("requiresOpenaiAuth"):
        raise RuntimeError("Codex 原生存储未登录，请登录原账号后刷新")
    # The protocol does not identify individual API keys or external-provider credentials.
    return f"local:{getpass.getuser().casefold()}:{account.get('type') or 'provider'}"


def require_codex_history_owner(client, thread_id, cwd, account, *, allow_active=False):
    if account and codex_history_account(client) != account:
        raise RuntimeError(_ACCOUNT_CHANGED)
    thread = client.read_thread(thread_id, include_turns=False).get("thread") or {}
    actual_cwd = thread.get("cwd")
    if (thread.get("id") != thread_id or not actual_cwd
            or (cwd and Path(actual_cwd).resolve() != Path(cwd).resolve())):
        raise RuntimeError(f"Codex 无法在原存储和项目目录找到所选线程：{cwd}")
    if not allow_active and (thread.get("status") or {}).get("type") == "active":
        raise RuntimeError(_BUSY)


def codex_native_open_command(home, cwd, thread_id, launch):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    argv = " ".join(quote(part) for part in launch)
    return (f"& {{ $mcPreviousCodexHome = $env:CODEX_HOME; try {{ $env:CODEX_HOME = {quote(home)}; "
            f"& {argv} resume --cd {quote(cwd)} {quote(thread_id)} }} "
            "finally { $env:CODEX_HOME = $mcPreviousCodexHome } }")


class CodexHistoryReader:
    def __init__(self, home, private, pair_id, known_chats):
        self.home = home
        self.private = private
        self.pair_id = pair_id
        self.known_chats = known_chats
        self.storage_id = os.path.normcase(str(home))
        self.client = CodexAppServerClient(native_home=home, use_native_config=True, timeout=8)

    def rename_session(self, thread_id, cwd, account, title):
        try:
            require_codex_history_owner(self.client, thread_id, cwd, account, allow_active=True)
        except (RuntimeError, OSError, ValueError) as exc:
            raise NativeTitleRejected("无法确认 Codex 原会话及登录归属，请检查后重试。") from exc
        try:
            self.client.request("thread/name/set", {"threadId": thread_id, "name": title})
        except CodexRpcError as exc:
            raise NativeTitleRejected("Codex 拒绝更改原生标题，请检查原会话后重试。") from exc
        except (RuntimeError, OSError, TimeoutError):
            # A lost response can still be followed by an authoritative read.
            pass
        thread = self.client.read_thread(thread_id, include_turns=False).get("thread") or {}
        if thread.get("id") == thread_id and thread.get("name") == title:
            return title
        return None

    def _known_snapshots(self):
        known = {}
        for chat in self.known_chats():
            if chat.get("codex_storage_id") != self.storage_id or not chat.get("native_identity"):
                continue
            sid = chat.get("codex_thread_id")
            if not sid:
                continue
            identity = NativeSessionIdentity(self.pair_id, "codex", self.storage_id, sid)
            if chat["native_identity"] == identity.chat_id:
                known[sid] = NativeSessionSnapshot(
                    identity, chat.get("native_account", ""), chat["model"], chat.get("native_cwd", ""),
                    chat.get("native_title", ""),
                )
        return known

    def list_sessions(self):
        known = self._known_snapshots()
        seen = set()
        try:
            if not self.home.is_dir():
                raise OSError("Codex Home does not exist")
            self.launch = resolve_codex_launch_command()
            account = codex_history_account(self.client)
            for snapshot in known.values():
                if snapshot.account != account:
                    yield replace(snapshot, status=_ACCOUNT_CHANGED)
                    seen.add(snapshot.identity.session_id)
            cursor = None
            cursors = set()
            while True:
                observed_at = time.time()
                page = self.client.request("thread/list", {
                    "cursor": cursor, "limit": 100, "sourceKinds": list(_SOURCES), "currentAccountOnly": True,
                    "modelProviders": [],
                })
                rows = page.get("data")
                if not isinstance(rows, list):
                    raise RuntimeError("Codex thread/list response is missing data")
                for row in rows:
                    snapshot = self._snapshot(row, account, observed_at)
                    if snapshot and snapshot.identity.session_id not in seen:
                        seen.add(snapshot.identity.session_id)
                        yield snapshot
                cursor = page.get("nextCursor")
                if cursor is None:
                    return
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise RuntimeError("Codex thread/list cursor did not advance")
                cursors.add(cursor)
        except (RuntimeError, OSError, ValueError):
            for sid, snapshot in known.items():
                if sid not in seen:
                    yield replace(snapshot, status=_UNAVAILABLE)
        finally:
            self.client.close()

    def _updated_at(self, row):
        updated = _provider_origin_timestamp({"timestamp": row.get("updatedAt")}) or 0
        # Protocol timestamps have whole-second precision. The official
        # rollout path also identifies changes between scans within that second.
        path = row.get("path")
        if isinstance(path, str) and path:
            try:
                rollout = Path(path).resolve()
                if rollout.is_relative_to(self.home / "sessions"):
                    updated = max(rollout.stat().st_mtime, updated)
            except OSError:
                pass
        return updated

    def _snapshot(self, row, account, observed_at):
        if not isinstance(row, dict) or row.get("source") not in _SOURCES or row.get("ephemeral"):
            return None
        thread_id, cwd = row.get("id"), row.get("cwd")
        if not isinstance(thread_id, str) or not thread_id or not isinstance(cwd, str) or not Path(cwd).is_absolute():
            return None
        model = next((name for name, config in CODEX_MODEL_CONFIGS.items()
                      if config.get("model") == row.get("model")
                      and config.get("model_reasoning_effort") == row.get("reasoningEffort")), DEFAULT_CODEX_MODEL)
        fields = {
            "codex_thread_id": thread_id,
            "codex_home": str(self.home),
            "codex_storage_id": self.storage_id,
            "codex_user_home": not self.private,
            "native_open_command": codex_native_open_command(self.home, cwd, thread_id, self.launch),
        }
        return NativeSessionSnapshot(
            NativeSessionIdentity(self.pair_id, "codex", self.storage_id, thread_id),
            account, model, cwd, row.get("name") or str(row.get("preview") or "").split("\n", 1)[0][:80],
            self._updated_at(row), fields, status=_BUSY if (row.get("status") or {}).get("type") == "active" else "",
            legacy_fields={"codex_thread_id": thread_id} if self.private else {
                "codex_thread_id": thread_id, "codex_storage_id": self.storage_id,
            },
            title_observed_at=observed_at, title_explicit=bool(row.get("name")),
        )

    def read_session(self, session):
        if session.status:
            return
        thread_id = session.identity.session_id
        try:
            response = self.client.read_thread(thread_id)
            thread = response.get("thread") or {}
            if (thread.get("id") != thread_id or thread.get("cwd") != session.cwd
                    or not isinstance(thread.get("turns"), list)):
                raise RuntimeError("Codex history is no longer available")
            if (thread.get("status") or {}).get("type") == "active":
                session.status = _BUSY
                return
            if codex_history_account(self.client) != session.account:
                session.status = _ACCOUNT_CHANGED
                return
            session.turns, session.execution_steps = _thread_history(thread, session.model)
        except (RuntimeError, OSError, ValueError):
            session.status = "Codex 原生历史暂不可读，请稍后刷新"


def _thread_history(thread, model):
    turns, steps = [], []
    sid = thread["id"]
    for native_turn in thread["turns"]:
        turn_id = native_turn.get("id")
        status = native_turn.get("status")
        if not turn_id or status not in ("completed", "failed", "interrupted"):
            continue
        items = native_turn.get("items") or []
        user_items = [item for item in items if item.get("type") == "userMessage"]
        if not user_items:
            continue
        question = "\n".join(
            part["text"] for item in user_items for part in item.get("content") or []
            if part.get("type") == "text" and isinstance(part.get("text"), str)
        )
        messages = [item for item in items if item.get("type") == "agentMessage" and item.get("text")]
        finals = [item for item in messages if item.get("phase") in ("finalAnswer", "final_answer")]
        explicit_final = bool(finals)
        if not finals:
            finals = [item for item in messages if not item.get("phase")]
        final = finals[-1] if finals and status == "completed" else {}
        error = native_turn.get("error") or {}
        row = {
            "model": model, "provider": "codex", "session_id": sid,
            "codex_thread_id": sid, "codex_turn_id": turn_id, "turn_id": turn_id,
            "provider_user_message_id": user_items[0].get("id"),
            "provider_message_id": final.get("id"), "question": question, "answer_md": final.get("text") or "",
            "native_final_explicit": bool(final and explicit_final),
            "request_status": {"completed": "done", "failed": "failed", "interrupted": "cancelled"}[status],
            "request_error": error.get("message") or "",
        }
        for field, native_key in (("created_at", "startedAt"), ("answer_at", "completedAt")):
            if stamp := _provider_origin_timestamp({"timestamp": native_turn.get(native_key)}):
                row[field] = stamp
        index = len(turns)
        turns.append(row)
        for item in items:
            step = _history_step(item, sid, turn_id, index)
            if step:
                steps.append(step)
    return turns, steps


def _history_step(item, sid, turn_id, index):
    item_type = item.get("type")
    if item_type == "reasoning":
        # Codex's private reasoning content is distinct from its public summary.
        text = "\n".join(str(part) for part in item.get("summary") or [])
        kind, title = "thinking", "思考摘要"
    elif item_type == "agentMessage" and item.get("phase") == "commentary":
        kind, title, text = "commentary", "处理说明", item.get("text") or ""
    elif item_type in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"):
        event = _event_from_item("item/completed", {"item": item, "threadId": sid, "turnId": turn_id})
        kind = event.display_kind
        title = event.command or event.title or item.get("tool") or item.get("query") or event.text or item_type
        text = event.raw_text or event.text or title
        if item_type == "fileChange":
            changes = item.get("changes") or []
            text = "\n".join(str(change.get("diff") or change.get("path") or "") for change in changes) or text
    else:
        return None
    if not text or not item.get("id"):
        return None
    return {
        "turn_idx": index, "raw_kind": kind, "display_kind": kind,
        "step_id": f"codex-history-{turn_id}-{item['id']}",
        "list_text": title, "detail_text": text, "status": str(item.get("status") or ""),
    }
