"""Kimi V2 subscription readiness, transcript replay and reconciliation."""

from __future__ import annotations

from collections import Counter
import logging
import threading
import time
from requests import Timeout as RequestsTimeout
from context_usage import cache_token_counts, native_token_count
from kimi_server_events import (
    KimiEvent,
    KimiServerError,
    _payload_of,
    _provider_origin_timestamp,
    _str,
)
from kimi_transcript import Transcript
from typing import Any


_USAGE_LOG = logging.getLogger(__name__)


class KimiUsageQueryError(KimiServerError):
    def __init__(self, message: str, *, stage: str, pages: int = 0, category: str = ""):
        super().__init__(message)
        self.stage = stage
        self.pages = pages
        self.category = category or type(self).__name__


def _usage_field_count(usage, fields: tuple[str, ...]) -> int:
    return sum(native_token_count(usage.get(field)) is not None for field in fields) if isinstance(usage, dict) else 0


def _sum_terminal_step_usage(steps: list[dict]) -> tuple[int, int, int] | None:
    uncached = cached = output = 0
    for step in steps:
        if step.get("state") not in {"completed", "failed", "cancelled", "interrupted"}:
            return None
        usage = step.get("usage") or {}
        other, read, created, generated = (native_token_count(usage.get(field)) for field in
                                            ("inputOther", "inputCacheRead", "inputCacheCreation", "output"))
        if None in (other, read, created, generated):
            return None
        uncached += other + created
        cached += read
        output += generated
    return (uncached, cached, output) if steps else None


def _turn_usage(turn: dict) -> tuple:
    steps = turn.get("steps", [])
    step = max((step for step in steps if step.get("state") == "completed"),
               key=lambda step: native_token_count(step.get("ordinal")) or 0, default=None)
    last = None
    if step is not None:
        usage = step.get("usage") or {}
        other, cached, created = (native_token_count(usage.get(field)) for field in
                                  ("inputOther", "inputCacheRead", "inputCacheCreation"))
        if None not in (other, cached, created):
            last = cache_token_counts(other + cached + created, cached)
    terminal = turn.get("state") in {"completed", "failed", "cancelled"}
    usage = turn.get("usage") or {}
    fields = ("inputTokens", "cachedTokens", "outputTokens")
    values = tuple(native_token_count(usage.get(field)) for field in fields)
    if terminal and None in values:
        restored = _sum_terminal_step_usage(steps)
        if restored is not None and all(
            (usage.get(field) is None) or value == step_value
            for field, value, step_value in zip(fields, values, restored)
        ):
            values = restored
    uncached, cached, output = values
    pair = cache_token_counts(uncached + cached, cached) if None not in (uncached, cached) else None
    total = pair["input_tokens"] + output if pair is not None and output is not None else None
    rank = native_token_count(turn.get("ordinal"))
    if rank is None:
        rank = native_token_count(str(turn["turnId"]).removeprefix("t")) or 0
    return rank, step is not None, last, terminal, pair, total


def _usage_totals(turns: dict) -> dict:
    last_turn = max((row for row in turns.values() if row[1]), key=lambda row: row[0], default=None)
    terminal = [row for row in turns.values() if row[3]]
    pairs = [row[4] for row in terminal]
    totals = [row[5] for row in terminal]
    return {
        "session_total_tokens": sum(totals) if totals and None not in totals else None,
        "cache_hits": {
            "last": last_turn[2] if last_turn is not None else None,
            "total": cache_token_counts(sum(p["input_tokens"] for p in pairs), sum(p["cached_tokens"] for p in pairs))
                if pairs and None not in pairs else None,
        },
    }


class KimiServerV2Mixin:
    def _subscribe_v2(self, session_id: str, *, timeout: float | None = None) -> None:
        request_id = self._next_id()
        with self._lifecycle_lock:
            self._subscribed_sessions.add(session_id)
            if self._v2_ready_generations.get(session_id) == self._ws_generation and self._ws_is_usable_locked():
                return
            if self._server_api and any(session_id in sessions and self._v2_request_generations.get(req) == self._ws_generation
                                        for req, sessions in self._subscribe_requests.items()):
                return
            self._subscribe_requests[request_id] = {session_id}
            self._v2_request_generations[request_id] = self._ws_generation
            self._v2_subscription_events.setdefault(session_id, threading.Event()).clear()
            transcript = self._v2_transcripts.get(session_id)
            payload = {"session_id": session_id, "transcript": {"main": "delta"}}
            if transcript is not None and transcript.seq is not None and not transcript.incomplete:
                payload["transcript_since"] = {"main": transcript.seq}
        message = ({"type": "subscribe", "request_id": request_id, "session_id": session_id, "agent_ids": ["main"]}
                   if self._server_api else {"type": "subscribe_v2", "id": request_id, "payload": payload})
        self._send_ws(message, send_timeout=timeout)

    def _require_v2_ready(self, session_id: str, *, timeout: float | None = None) -> None:
        try:
            self.subscribe([session_id], timeout=timeout)
        except Exception as exc:
            raise KimiUsageQueryError("Kimi subscribe_v2 send failed",
                                      stage="subscribe_send", category=type(exc).__name__) from exc
        ready = self._v2_subscription_events.setdefault(session_id, threading.Event())
        if not ready.wait(min(self.ws_connect_timeout, timeout) if timeout is not None else self.ws_connect_timeout):
            raise KimiUsageQueryError(
                "Kimi compatibility failure at subscribe_v2: target session ACK timed out; request was not sent",
                stage="subscribe_ack_timeout",
            )
        with self._lifecycle_lock:
            if self._v2_ready_generations.get(session_id) != self._ws_generation or not self._ws_is_usable_locked():
                raise KimiUsageQueryError(
                    "Kimi compatibility failure at subscribe_v2: target session not accepted on current socket; request was not sent",
                    stage="subscribe_ack_socket",
                )

    def _clear_v2_readiness(self) -> None:
        self._server_api_previous_turns.update(self._server_api_turns)
        while len(self._server_api_previous_turns) > 512:
            self._server_api_previous_turns.pop(next(iter(self._server_api_previous_turns)))
        self._server_api_turns.clear()
        self._server_api_pending.clear()
        for _, event in self._server_api_pending_checks.values():
            event.set()
        self._server_api_pending_checks.clear()
        self._v2_ready_sessions.clear()
        self._v2_ready_generations.clear()
        for ready in self._v2_subscription_events.values():
            ready.clear()
        for request_id in self._v2_request_generations:
            self._subscribe_requests.pop(request_id, None)
        self._v2_request_generations.clear()
        self._v2_usage_windows.clear()
        self._v2_usage_ledgers.clear()
        for wait in self._v2_usage_events.values():
            wait.set()
        self._v2_usage_events.clear()

    def is_current_v2_usage(self, session_id: str, generation: int, client_id: int) -> bool:
        with self._lifecycle_lock:
            return (not self._closed and client_id == id(self) and generation == self._ws_generation
                    and self._v2_ready_generations.get(session_id) == generation)

    def _consume_v2_usage_window(self, frame: dict, *, ws=None, generation=None) -> None:
        session = _str(frame.get("session_id"))
        used = native_token_count(frame.get("context_tokens"))
        if frame.get("agent_id") != "main" or used is None:
            return
        with self._lifecycle_lock:
            current = self._ws_generation
            if (self._closed or (ws is not None and self._ws is not ws)
                    or (generation is not None and generation != current)):
                return
            ready = self._v2_ready_generations.get(session) == current
            pending = any(self._v2_request_generations.get(key) == current and session in sessions
                          for key, sessions in self._subscribe_requests.items())
            if not ready and not pending:
                return
            usage = {"context_tokens": used, "max_context_tokens": native_token_count(frame.get("max_context_tokens"))}
            changed = self._v2_usage_windows.get(session) != usage
            self._v2_usage_windows[session] = usage
            self._v2_usage_events.setdefault(session, threading.Event()).set()
            if changed and ready:
                self._emit_v2_usage(session, "status", usage)

    def _v2_usage_socket_identity(self) -> dict:
        return {"socket_generation": self._ws_generation, "usage_client": id(self)}

    def _emit_v2_usage(self, session_id: str, kind: str, usage: dict) -> None:
        self._enqueue_event(KimiEvent(
            type="kimi_v2_usage", thread_id=session_id, session_id=session_id,
            data={"kind": kind, "usage": {**usage, **self._v2_usage_socket_identity()}},
        ))

    def get_v2_usage_context(self, session_id: str) -> dict | None:
        self._require_v2_ready(session_id)
        with self._lifecycle_lock:
            generation = self._ws_generation
            wait = self._v2_usage_events.setdefault(session_id, threading.Event())
        wait.wait(min(2.0, self.ws_connect_timeout))
        with self._lifecycle_lock:
            if not self.is_current_v2_usage(session_id, generation, id(self)):
                raise KimiUsageQueryError("Kimi usage context socket changed", stage="socket_generation")
            usage = self._v2_usage_windows.get(session_id)
            return {**usage, **self._v2_usage_socket_identity()} if usage is not None else None

    def _v2_usage_ledger(self, session_id: str) -> dict:
        return self._v2_usage_ledgers.setdefault(
            session_id, {"turns": {}, "serial": 0, "scan_lock": threading.Lock(),
                         "scan_generation": 0, "complete": False, "recovering": False},
        )

    def get_v2_usage_totals(self, session_id: str) -> dict:
        self._require_v2_ready(session_id)
        with self._lifecycle_lock:
            generation = self._ws_generation
            ledger = self._v2_usage_ledger(session_id)
        with ledger["scan_lock"]:
            with self._lifecycle_lock:
                if not self.is_current_v2_usage(session_id, generation, id(self)):
                    raise KimiUsageQueryError("Kimi transcript usage socket changed", stage="socket_generation")
                if ledger["complete"]:
                    return {**self._v2_usage_result(ledger), **self._v2_usage_socket_identity()}
                cursor_serial = ledger["serial"]
                scan_generation = ledger["scan_generation"]
            return self._scan_v2_usage(session_id, generation, ledger, cursor_serial, scan_generation)

    def _scan_v2_usage(self, session_id: str, generation: int, ledger: dict,
                       cursor_serial: int, scan_generation: int) -> dict:
        deadline = time.monotonic() + self.rest_timeout
        before = None
        cursors = set()
        turns = {}
        field_counts = Counter()
        for page_index in range(100):
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise KimiUsageQueryError("Kimi transcript usage deadline exhausted",
                                          stage="rest_timeout", pages=page_index)
            try:
                page = self.get_transcript(session_id, before_turn=before, timeout=timeout)
            except Exception as exc:
                cause = exc.__cause__ or exc
                timed_out = isinstance(cause, (TimeoutError, RequestsTimeout))
                raise KimiUsageQueryError("Kimi transcript usage REST request failed",
                    stage="rest_timeout" if timed_out else "rest_request",
                    pages=page_index, category=type(cause).__name__) from exc
            if (not isinstance(page, dict) or page.get("agent_id", "main") != "main"
                    or not isinstance(page.get("items"), list)
                    or any(not isinstance(item, dict) for item in page["items"])):
                raise KimiUsageQueryError("Kimi transcript usage page is invalid",
                                          stage="rest_page", pages=page_index)
            items = [turn for turn in page["items"] if turn.get("kind") == "turn"]
            for turn in items:
                turn_id = turn.get("turnId")
                if not isinstance(turn_id, str) or not turn_id:
                    raise KimiUsageQueryError("Kimi transcript usage turn identity is missing",
                                              stage="rest_page", pages=page_index)
                if turn_id in turns:
                    continue
                row = _turn_usage(turn)
                turns[turn_id] = (cursor_serial, row)
                state = turn.get("state")
                state = state if state in ("completed", "failed", "cancelled", "interrupted") else "other"
                field_counts[(state, _usage_field_count(turn.get("usage"),
                    ("inputTokens", "cachedTokens", "outputTokens")), row[4] is not None, row[5] is not None)] += 1
            if not page.get("has_more"):
                break
            before = page.get("next_cursor") or (items[0]["turnId"] if items else None)
            if not before or before in cursors:
                raise KimiUsageQueryError("Kimi transcript usage pagination made no progress",
                                          stage="pagination_cursor", pages=page_index + 1)
            cursors.add(before)
        else:
            raise KimiUsageQueryError("Kimi transcript usage page budget exhausted",
                                      stage="pagination_budget", pages=page_index + 1)
        with self._lifecycle_lock:
            if not self.is_current_v2_usage(session_id, generation, id(self)):
                raise KimiUsageQueryError("Kimi transcript usage socket changed",
                                          stage="socket_generation", pages=page_index + 1)
            if ledger["scan_generation"] != scan_generation:
                raise KimiUsageQueryError("Kimi transcript changed during usage scan",
                                          stage="scan_generation", pages=page_index + 1)
            newer = {key: value for key, value in ledger["turns"].items()
                     if value[0] > cursor_serial}
            ledger["turns"] = {**turns, **newer}
            ledger["complete"] = True
            ledger["serial"] += 1
            _USAGE_LOG.debug("Kimi usage scan stages=subscribe_ack,rest_complete pages=%d turn_field_counts=%s",
                             page_index + 1, dict(field_counts))
            return {**self._v2_usage_result(ledger), **self._v2_usage_socket_identity()}

    @staticmethod
    def _v2_usage_result(ledger: dict) -> dict:
        result = _usage_totals({key: value[1] for key, value in ledger["turns"].items()})
        return {**result, "serial": ledger["serial"], "scan_generation": ledger["scan_generation"]}

    def is_current_v2_usage_scan(self, session_id: str, scan_generation: int) -> bool:
        with self._lifecycle_lock:
            ledger = self._v2_usage_ledgers.get(session_id)
            return ledger is not None and ledger["scan_generation"] == scan_generation

    def _invalidate_v2_usage_scan(self, session_id: str) -> None:
        ledger = self._v2_usage_ledger(session_id)
        ledger["scan_generation"] += 1
        ledger["complete"] = False
        ledger["turns"].clear()
        if self._v2_ready_generations.get(session_id) == self._ws_generation:
            self._emit_v2_usage(session_id, "snapshot_invalidated", {
                "scan_generation": ledger["scan_generation"],
            })

    def _record_v2_transcript_usage(self, session_id: str, transcript: Transcript | None) -> None:
        generation = self._ws_generation
        if self._v2_ready_generations.get(session_id) != generation:
            return
        ledger = self._v2_usage_ledger(session_id)
        observed = {turn_id: _turn_usage(turn) for turn_id, turn in transcript.items.items()} if transcript else {}
        changes = {key: value for key, value in observed.items()
                   if ledger["turns"].get(key, (None, None))[1] != value}
        if changes:
            ledger["serial"] += 1
            ledger["turns"].update({key: (ledger["serial"], value) for key, value in changes.items()})
        if ledger["complete"] and changes:
            self._emit_v2_usage(session_id, "snapshot", self._v2_usage_result(ledger))
        if ledger["complete"] or ledger["recovering"]:
            return
        ledger["recovering"] = True
        scan_generation = ledger["scan_generation"]

        def recover():
            try:
                usage = self.get_v2_usage_totals(session_id)
                with self._lifecycle_lock:
                    if (self.is_current_v2_usage(session_id, generation, id(self))
                            and self.is_current_v2_usage_scan(session_id, usage["scan_generation"])):
                        self._emit_v2_usage(session_id, "snapshot", usage)
            except Exception:
                # An explicit information refresh reports its own read error.
                pass
            finally:
                with self._lifecycle_lock:
                    ledger["recovering"] = False
                    if (scan_generation != ledger["scan_generation"]
                            and self.is_current_v2_usage(session_id, generation, id(self))):
                        self._record_v2_transcript_usage(session_id, None)

        threading.Thread(target=recover, daemon=True, name="kimi-usage-history").start()

    def get_transcript(self, session_id: str, *, before_turn: str | None = None,
                       timeout: float | None = None, agent_id: str = "main") -> dict[str, Any]:
        params = {"agent_id": agent_id, "page_size": 100}
        if before_turn:
            params["before_turn"] = before_turn
        return self._request_data("GET", f"/api/v1/sessions/{session_id}/transcript", params=params, timeout=timeout)

    def reconcile_v2(self, session_id: str, prompt_id: str, *, timeout: float | None = None) -> KimiEvent | None:
        deadline = time.monotonic() + (timeout if timeout is not None else self.rest_timeout)
        before = None
        cursors = set()
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KimiServerError("Kimi transcript recovery deadline exhausted")
            page = self.get_transcript(session_id, before_turn=before, timeout=remaining)
            transcript = Transcript()
            transcript.reset(page)
            matching = [turn for turn in transcript.items.values() if turn.get("triggerPromptId") == prompt_id]
            if len(matching) > 1:
                raise KimiServerError("Kimi transcript recovery has ambiguous prompt identity")
            for turn, text in transcript.results():
                if turn.get("triggerPromptId") == prompt_id:
                    event = self._v2_final_event(session_id, turn, text)
                    self._retire_v2_turn(session_id, turn)
                    return event
            ongoing = next((turn.get("state") for turn in matching if turn.get("state") in {"running", "queued", "blocked"}), None)
            if ongoing is None and not matching:
                ongoing = next((prompt.get("status") for prompt in page.get("prompts", [])
                                if prompt.get("promptId") == prompt_id and prompt.get("status") in {"running", "queued", "blocked"}), None)
            if ongoing:
                return KimiEvent(type="thread_status_changed", thread_id=session_id, status=ongoing,
                                 data={"prompt_id": prompt_id, "terminal": False})
            if matching or not page.get("has_more"):
                return None
            turns = [item for item in page.get("items", []) if item.get("kind") == "turn"]
            cursor = page.get("next_cursor") or (turns[0]["turnId"] if turns else None)
            if not cursor:
                raise KimiServerError("Kimi transcript recovery pagination made no progress")
            if cursor in cursors:
                raise KimiServerError("Kimi transcript recovery repeated pagination cursor")
            cursors.add(cursor)
            before = cursor
        raise KimiServerError("Kimi transcript recovery page budget exhausted")

    def recovery_messages_v2(self, session_id: str, *, timeout: float | None = None) -> list[dict]:
        """Project read-only transcript identities into existing migration rules."""
        deadline = time.monotonic() + (timeout if timeout is not None else self.rest_timeout)
        before, rows, seen = None, [], set()
        listed_prompts = {}
        cursors = set()
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KimiServerError("Kimi prompt attribution deadline exhausted")
            page = self.get_transcript(session_id, before_turn=before, timeout=remaining)
            page_rows = []
            turns = [item for item in page.get("items", []) if item.get("kind") == "turn"]
            for turn in turns:
                pid = turn.get("triggerPromptId")
                if pid and pid not in seen:
                    seen.add(pid)
                    page_rows.append({"id": pid, "role": "user", "content": str(turn.get("prompt") or ""),
                                      "created_at": turn.get("startedAt"), "metadata": {"origin": turn.get("origin")},
                                      "independent_turn": True, "prompt_status": turn.get("state")})
                    for step in turn.get("steps", []):
                        for frame in step.get("frames", []):
                            if frame.get("kind") != "text" or frame.get("role") != "user":
                                continue
                            for child in frame.get("promptIds") or []:
                                if child in seen:
                                    continue
                                seen.add(child)
                                page_rows.append({"id": child, "role": "user", "content": frame.get("text", ""),
                                                  "metadata": {"origin": frame.get("origin")},
                                                  "owner_prompt_id": pid, "prompt_status": turn.get("state")})
                    if turn.get("state") == "completed":
                        page_rows.append({"role": "assistant", "content": "", "created_at": turn.get("endedAt")})
            for prompt in page.get("prompts", []):
                pid = prompt.get("promptId")
                if pid and pid not in listed_prompts:
                    listed_prompts[pid] = {"id": pid, "role": "user", "content": prompt.get("content", ""),
                                           "created_at": prompt.get("createdAt"),
                                           "prompt_status": None if prompt.get("steeredAt") else prompt.get("status")}
            rows = page_rows + rows
            if not page.get("has_more"):
                return rows + [row for pid, row in listed_prompts.items() if pid not in seen]
            cursor = page.get("next_cursor") or (turns[0]["turnId"] if turns else None)
            if not cursor or cursor in cursors:
                raise KimiServerError("Kimi prompt attribution pagination made no progress")
            cursors.add(cursor)
            before = cursor
        raise KimiServerError("Kimi prompt attribution page budget exhausted")

    def _v2_final_event(self, session_id: str, turn: dict, text: str,
                        source_kind: str = "rest.reconciled") -> KimiEvent:
        return KimiEvent(type="turn_completed", thread_id=session_id, session_id=session_id,
                         turn_id=turn["turnId"], text=text if turn["state"] == "completed" else str(turn.get("error") or turn["state"]),
                         status=turn["state"],
                         origin_timestamp=_provider_origin_timestamp({"timestamp": turn.get("endedAt")}),
                         data={"prompt_id": turn["triggerPromptId"], "agent_id": "main", "agent_scope": "main",
                               "authoritative": True, "source_kind": source_kind, "terminal_verified": True,
                               "stream_complete": True})

    def _consume_v2(self, message: dict, *, ws: Any = None, generation: int | None = None) -> None:
        body = _payload_of(message)
        sid = _str(message.get("session_id"))
        if body.get("agent_id") != "main" or not sid:
            return

        def current_socket():
            return not self._closed and (ws is None or self._ws is ws) and (
                generation is None or self._ws_generation == generation
            )

        with self._lifecycle_lock:
            if not current_socket():
                return
            transcript = self._v2_transcripts.setdefault(sid, Transcript())
            seq = body.get("seq")
            invalidate = message["type"] == "transcript.reset"
            if message["type"] == "transcript.reset":
                # Reset is authoritative even when seq decreases after a restart.
                transcript.reset(body.get("snapshot") or {}, seq)
            else:
                ops = body.get("ops") or []
                applied = transcript.apply(ops, seq)
                invalidate = applied and any(op.get("op") in {"reset", "items.remove"} for op in ops)
            needs_catchup = transcript.incomplete
            if invalidate or needs_catchup:
                self._invalidate_v2_usage_scan(sid)
            since = transcript.seq or 0

        transcript_source = "ws"
        if needs_catchup:
            timeout = min(3.0, self.rest_timeout)
            catchup = page = None
            try:
                catchup = self._request_data("GET", f"/api/v1/sessions/{sid}/transcript/ops",
                    params={"agent_id": "main", "since_seq": since}, timeout=timeout)
                if not catchup.get("complete"):
                    page = self.get_transcript(sid, timeout=timeout)
            except KimiServerError:
                # The owner worker reports the original transport/REST error.
                pass
            with self._lifecycle_lock:
                if not current_socket() or self._v2_transcripts.get(sid) is not transcript:
                    return
                if transcript.incomplete and (transcript.seq or 0) == since:
                    if catchup is not None and catchup.get("complete"):
                        transcript_source = "rest"
                        transcript.incomplete = False
                        for batch in catchup.get("batches", []):
                            transcript.apply(batch["ops"], batch["seq"])
                    elif page is not None:
                        transcript_source = "rest"
                        transcript.reset(page, page.get("seq"))
            self._enqueue_control({"type": "transport_recovered", "payload": {"session_ids": [sid]}})

        with self._lifecycle_lock:
            if not current_socket() or self._v2_transcripts.get(sid) is not transcript:
                return
            if transcript.incomplete:
                self._record_v2_transcript_usage(sid, None)
                return
            events = self._v2_execution_events(sid, transcript, transcript_source)
            needs_recovery = transcript.incomplete
            if needs_recovery:
                self._invalidate_v2_usage_scan(sid)
            self._record_v2_transcript_usage(sid, None if needs_recovery else transcript)
        for event in events:
            self._enqueue_event(event)
        if needs_recovery:
            self._enqueue_control({"type": "transport_recovered", "payload": {"session_ids": [sid]}})

        with self._lifecycle_lock:
            if not current_socket() or self._v2_transcripts.get(sid) is not transcript:
                return
            finals = []
            source_kind = "transcript.terminal" if transcript_source == "ws" else "rest.reconciled"
            for turn, text in transcript.results():
                key = (sid, turn["triggerPromptId"], turn["turnId"])
                if key not in self._v2_completed:
                    finals.append(self._v2_final_event(sid, turn, text, source_kind))
                self._retire_v2_turn(sid, turn)
        for event in finals:
            self._enqueue_event(event)

    def _retire_v2_turn(self, sid: str, turn: dict) -> None:
        with self._lifecycle_lock:
            key = (sid, turn["triggerPromptId"], turn["turnId"])
            if key not in self._v2_completed:
                self._v2_completed.add(key)
                self._v2_completed_order.append(key)
                while len(self._v2_completed_order) > 512:
                    self._v2_completed.discard(self._v2_completed_order.popleft())
            # Delivered finals live in the owning consumer. Keep only active and
            # terminal-but-incomplete bodies for ongoing reconciliation.
            transcript = self._v2_transcripts.get(sid)
            if transcript is not None:
                current = transcript.items.get(turn["turnId"])
                if current is not None and current.get("triggerPromptId") == turn["triggerPromptId"]:
                    transcript.items.pop(turn["turnId"], None)
            self._v2_started.discard((sid, turn["turnId"], turn["triggerPromptId"]))
            for frame_key in list(self._v2_frame_texts):
                if frame_key[:3] == (sid, turn["turnId"], turn["triggerPromptId"]):
                    self._v2_frame_texts.pop(frame_key, None)
            idle_sessions = [session for session, state in self._v2_transcripts.items() if not state.items]
            for session in idle_sessions[:-32]:
                self._v2_transcripts.pop(session, None)

    def _v2_execution_events(self, sid: str, transcript: Transcript, transcript_source: str) -> list[KimiEvent]:
        events = []
        for turn in transcript.items.values():
            prompt = turn.get("triggerPromptId")
            if not prompt or turn.get("state") != "running":
                continue
            key = (sid, turn["turnId"], prompt)
            if (sid, prompt, turn["turnId"]) in self._v2_completed:
                continue
            base = {"agent_scope": "main", "agent_id": "main", "prompt_id": prompt, "authoritative": True,
                    "transcript_source": transcript_source}
            if key not in self._v2_started:
                events.append(KimiEvent(type="turn_started", thread_id=sid, turn_id=turn["turnId"],
                    text=str(turn.get("prompt") or ""), data={**base, "source_kind": "turn.started"}))
                self._v2_started.add(key)
            for step in turn.get("steps", []):
                for frame in step.get("frames", []):
                    if frame.get("kind") not in {"text", "thinking"}:
                        continue
                    if frame.get("kind") == "text" and frame.get("role") != "assistant":
                        continue
                    if any(frame.get(k) for k in ("private", "hidden", "non_disclosable")) or frame.get("disclosable", True) is False:
                        continue
                    frame_key = (*key, step["stepId"], frame["frameId"])
                    text = str(frame.get("text") or "")
                    old = self._v2_frame_texts.get(frame_key, "")
                    text_units = text.encode("utf-16-le", errors="surrogatepass")
                    old_units = old.encode("utf-16-le", errors="surrogatepass")
                    if not text_units.startswith(old_units):
                        transcript.incomplete = True
                        continue
                    if text != old:
                        kind = "thinking" if frame["kind"] == "thinking" else "assistant"
                        events.append(KimiEvent(type="agent_message_delta", thread_id=sid,
                            turn_id=turn["turnId"], item_id=frame["frameId"], stream_id=frame["frameId"],
                            text=text_units[len(old_units):].decode("utf-16-le", errors="surrogatepass"), offset=len(old), display_kind=kind,
                            data={**base, "source_kind": kind + ".delta", "step_id": step["stepId"], "offset": len(old)}))
                        self._v2_frame_texts[frame_key] = text
        return events
