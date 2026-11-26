"""Read-only inventory and replay of Kimi Code's main-agent transcript."""
import getpass
import json
import os
from pathlib import Path
import time

from kimi_server_client import DEFAULT_KIMI_MODEL, KimiServerError, kimi_code_home
from kimi_server_events import _provider_origin_timestamp
from kimi_transcript import Transcript, final_message_identity
from native_history import NativeDeletionRejected, NativeSessionIdentity, NativeSessionSnapshot, NativeTitleRejected


def kimi_history_storage() -> str:
    return os.path.normcase(str(kimi_code_home().resolve()))


def kimi_history_account() -> str:
    # This inventory is local to the Windows account and Kimi Home. A Kimi
    # model provider login is optional and does not own the local store.
    return getpass.getuser().casefold()


class KimiHistoryReader:
    def __init__(self, client, pair_id, is_user_prompt):
        self.pair_id = pair_id
        self.is_user_prompt = is_user_prompt
        self.client = client

    def rename_session(self, sid, cwd, storage, account, title):
        original_storage = storage or os.path.normcase(str((Path.home() / ".kimi-code").resolve()))
        if ((original_storage != kimi_history_storage())
                or (account and account != kimi_history_account())):
            raise NativeTitleRejected("Kimi Code 原会话归属已变化。")
        try:
            self.client.start(timeout=10)
            row = self.client.get_session(sid)
        except KimiServerError as exc:
            raise NativeTitleRejected("无法连接 Kimi Code 或读取原会话，请检查后重试。") from exc
        actual_cwd = (row.get("metadata") or {}).get("cwd")
        if row.get("id") != sid or not actual_cwd or (cwd and Path(actual_cwd).resolve() != Path(cwd).resolve()):
            raise NativeTitleRejected("无法在原工作目录找到 Kimi Code 会话。")
        try:
            self.client.set_session_title(sid, title)
        except KimiServerError as exc:
            if exc.status_code and not exc.result_unknown:
                raise NativeTitleRejected("Kimi Code 拒绝更改原生标题，请检查原会话后重试。") from exc
        row = self.client.get_session(sid)
        if row.get("id") == sid and row.get("title") == title:
            return title
        return None

    def delete_session(self, sid, cwd, storage, account):
        original = storage or os.path.normcase(str((Path.home() / ".kimi-code").resolve()))
        if original != kimi_history_storage() or (account and account != kimi_history_account()):
            raise NativeDeletionRejected("Kimi Code 原始存储或账号已变更，请切回后重试。")
        self.client.start(timeout=10)
        if self.client.runtime_version not in {"2.5.3", "2.6.3"}:
            raise NativeDeletionRejected("当前 Kimi Code 版本没有经验证的原生删除接口。")
        row = self.client.get_session(sid)
        actual_cwd = (row.get("metadata") or {}).get("cwd")
        if row.get("id") != sid or not actual_cwd or (cwd and Path(actual_cwd).resolve() != Path(cwd).resolve()):
            raise NativeDeletionRejected("无法在原工作目录确认 Kimi Code 会话。")
        if self.busy_status(row):
            raise NativeDeletionRejected("Kimi Code 原始会话正在使用，请等本轮结束后重试。")
        try:
            self.client.delete_session(sid)
        except KimiServerError as exc:
            if exc.status_code and not exc.result_unknown:
                raise NativeDeletionRejected("Kimi Code 拒绝删除原始会话，请检查原生会话状态。") from exc
        return self.client.session_exists(sid) is False

    def list_sessions(self):
        client = self.client
        try:
            client.start(timeout=10)
            storage = kimi_history_storage()
            account = kimi_history_account()
            before = None
            cursors = set()
            while True:
                observed_at = time.time()
                page = client.list_sessions(before_id=before)
                items = page.get("items")
                if not isinstance(items, list):
                    raise KimiServerError("Kimi Code 会话清单格式无效")
                for row in items:
                    if not isinstance(row, dict):
                        continue
                    sid, metadata = row.get("id"), row.get("metadata")
                    cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
                    if not isinstance(sid, str) or not sid or not isinstance(cwd, str) or not cwd:
                        continue
                    storage_fields = {"kimi_session_id": sid, "kimi_storage_id": storage}
                    # Unqualified sessions on older MC versions used the
                    # default local store. An override requires explicit proof.
                    legacy = ({"kimi_session_id": sid}
                              if storage == os.path.normcase(str((Path.home() / ".kimi-code").resolve()))
                              else storage_fields)
                    yield NativeSessionSnapshot(
                        NativeSessionIdentity(self.pair_id, "kimi", storage, sid),
                        account, DEFAULT_KIMI_MODEL, cwd, str(row.get("title") or ""),
                        _provider_origin_timestamp({"timestamp": row.get("updated_at")}) or 0,
                        storage_fields, status=self.busy_status(row), legacy_fields=legacy,
                        title_observed_at=observed_at, title_explicit=row.get("is_custom_title") is not False,
                    )
                if not page.get("has_more"):
                    return
                before = items[-1].get("id") if items and isinstance(items[-1], dict) else None
                if not before or before in cursors:
                    raise KimiServerError("Kimi Code 会话清单分页未前进")
                cursors.add(before)
        except (RuntimeError, OSError) as exc:
            raise KimiServerError(str(exc)) from exc
        finally:
            client.close()

    @staticmethod
    def busy_status(info):
        return ("Kimi Code 原生会话正在使用，结束后可刷新历史"
                if info.get("busy") is True or info.get("main_turn_active") is True else "")

    def read_session(self, session):
        if session.status:
            return
        try:
            self._read_transcript(session)
        except KimiServerError as exc:
            session.status = ("Kimi Code 原生会话正在使用，结束后可刷新历史"
                              if exc.status_code in (409, 40901) else "Kimi Code 原生历史暂不可读，请稍后刷新")

    def _read_transcript(self, session):
        sid = session.identity.session_id
        pages = []
        before, cursors = None, set()
        for _ in range(100):
            page = self.client.get_transcript(sid, before_turn=before)
            items = page.get("items")
            if not isinstance(items, list):
                raise KimiServerError("会话记录格式无效")
            turns = [item for item in items if isinstance(item, dict) and item.get("kind") == "turn"]
            pages.append(turns)
            if not page.get("has_more"):
                break
            before = page.get("next_cursor") or (turns[0].get("turnId") if turns else None)
            if not before or before in cursors:
                raise KimiServerError("会话记录分页未前进")
            cursors.add(before)
        else:
            raise KimiServerError("会话记录超出单次分页范围")
        transcript = Transcript()
        transcript.reset({"items": [turn for page in reversed(pages) for turn in page if turn.get("turnId")]})
        history = []
        steps = []
        for turn, answer in transcript.results():
            origin = turn.get("origin")
            pid = turn["triggerPromptId"]
            if not self.is_user_prompt({"role": "user", "origin": origin}):
                continue
            turn_id = turn["turnId"]
            index = len(history)
            question = str(turn.get("prompt") or "")
            completed = turn["state"] == "completed"
            row = {
                "model": DEFAULT_KIMI_MODEL, "provider": "kimi", "session_id": sid,
                "kimi_session_id": sid, "kimi_turn_id": turn_id, "kimi_prompt_id": pid,
                "turn_id": turn_id, "provider_user_message_id": pid, "question": question,
                "answer_md": answer if completed else str(turn.get("error") or "Kimi Code 回合已结束"),
                "request_status": "done" if completed else "failed",
            }
            if message_id := final_message_identity(turn):
                row.update(provider_message_id=message_id, native_final_explicit=True)
            for field, key in (("created_at", "startedAt"), ("answer_at", "endedAt")):
                if at := _provider_origin_timestamp({"timestamp": turn.get(key)}):
                    row[field] = at
            history.append(row)
            for step in turn.get("steps", []):
                for frame in step.get("frames", []):
                    if (any(frame.get(key) for key in ("private", "hidden", "non_disclosable"))
                            or frame.get("disclosable") is False):
                        continue
                    kind = frame.get("kind")
                    if kind in ("thinking", "tool"):
                        text = frame.get("text") or frame.get("output") or frame.get("input") or frame.get("name") or ""
                        if not isinstance(text, str):
                            text = json.dumps(text, ensure_ascii=False)
                        if text:
                            title = str(frame.get("name") or ("思考" if kind == "thinking" else "工具"))
                            steps.append({
                                "turn_idx": index, "raw_kind": kind, "display_kind": kind,
                                "step_id": f"kimi-history-{turn_id}-{frame.get('frameId')}",
                                "list_text": title, "kimi_summary": title,
                                "detail_text": text, "status": str(frame.get("state") or ""),
                            })
        session.turns = history
        session.execution_steps = steps
        if not session.title and history:
            session.title = history[0]["question"].splitlines()[0][:80] if history[0]["question"] else ""
