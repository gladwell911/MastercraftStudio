from __future__ import annotations


class NotesSyncService:
    def __init__(self, store, broadcaster=None, on_remote_ops_applied=None, on_status_changed=None) -> None:
        self.store = store
        self._broadcaster = broadcaster
        self._on_remote_ops_applied = on_remote_ops_applied
        self._on_status_changed = on_status_changed

    def _emit_status(self, status: str, *, message: str | None = None, cursor: str | None = None) -> None:
        if callable(self._on_status_changed):
            try:
                self._on_status_changed(status, message=message, cursor=cursor)
            except Exception:
                pass

    def snapshot(self) -> dict:
        return self.store.snapshot()

    def pull_since(self, cursor: str) -> dict:
        ops, next_cursor = self.store.list_ops_since(cursor)
        return {"cursor": next_cursor, "ops": ops}

    def push_ops(self, ops: list[dict]) -> dict:
        result = self.store.push_ops(list(ops or []))
        if callable(self._broadcaster):
            try:
                self._broadcaster(result)
            except Exception:
                pass
        return result

    def subscribe(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        return {"cursor": self.store.current_cursor(), "snapshot": self.snapshot(), "subscribed": True, "request": payload}

    def ack(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        op_ids = payload.get("op_ids")
        if not isinstance(op_ids, list):
            op_ids = [payload.get("op_id")] if payload.get("op_id") else []
        acked = self.store.mark_outbox_acked(op_ids)
        return {"cursor": self.store.current_cursor(), "acked": [op.op_id for op in acked], "request": payload}

    def ping(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        return {"cursor": self.store.current_cursor(), "pong": True, "request": payload}

    def claim_outbox_ops(self, limit: int = 100) -> list:
        ops = self.store.claim_outbox_ops(limit)
        if ops:
            self._emit_status("sending", message="同步中")
        return ops

    def ack_outbox_ops(self, op_ids) -> list:
        ops = self.store.mark_outbox_acked(op_ids)
        if ops:
            self._emit_status("acked", message="笔记已同步")
        return ops

    def fail_outbox_ops(self, op_ids) -> list:
        ops = self.store.mark_outbox_failed(op_ids)
        if ops:
            self._emit_status("failed", message="同步失败")
        return ops

    def apply_remote_ops(self, ops: list[dict]) -> dict:
        applied: list[dict] = []
        conflicts: list[dict] = []
        for op in list(ops or []):
            result = self.store.apply_remote_op(op)
            applied.append(result)
            conflicts.extend(list(result.get("conflicts") or []))
        result = {"cursor": self.store.current_cursor(), "applied": applied, "conflicts": conflicts}
        if callable(self._on_remote_ops_applied):
            try:
                self._on_remote_ops_applied(result)
            except Exception:
                pass
        return result
