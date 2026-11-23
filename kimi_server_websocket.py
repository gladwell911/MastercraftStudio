"""Kimi WebSocket lifecycle, message dispatch and reconnect handling."""

from __future__ import annotations

import json
import threading
import time
import uuid
from copy import deepcopy
from kimi_server_events import (
    KimiEvent,
    KimiServerError,
    _payload_of,
    _provider_origin_timestamp,
    _str,
    map_session_event,
)
from typing import Any


WS_PATH = "/api/v1/ws"


class KimiServerWebSocketMixin:
    def subscribe(self, session_ids: list[str], *, timeout: float | None = None) -> None:
        ids = [str(s) for s in session_ids if str(s or "").strip()]
        if not ids:
            return
        if self.protocol_version == 2:
            for session_id in ids:
                self._subscribe_v2(session_id, timeout=timeout)
            return
        request_id = self._next_id()
        with self._lifecycle_lock:
            self._subscribed_sessions.update(ids)
            self._subscribe_requests[request_id] = set(ids)
        self._send_ws(
            {"type": "subscribe", "id": request_id, "payload": {"session_ids": ids}},
            send_timeout=timeout,
        )

    def _connect_ws(self, *, timeout: float | None = None) -> None:
        if timeout is None:
            self._connect_ws_locked()
        else:
            self._connect_ws_locked(timeout=timeout)

    def _connect_ws_locked(self, *, timeout: float | None = None) -> None:
        """Connect and handshake without holding the lifecycle lock.

        The historical name is retained for test and caller compatibility.  A
        socket generation reserves the attempt; only that generation may
        install the candidate or deliver frames.
        """
        with self._lifecycle_lock:
            if self._closed:
                raise KimiServerError("kimi websocket is closing")
            if self.process is None or self.process.poll() is not None:
                raise KimiServerError("kimi server process is not running")
            stale_ws = self._ws
            self._ws = None
            self._ws_generation += 1
            generation = self._ws_generation
            self._clear_v2_readiness()
            url = f"ws://127.0.0.1:{self.port}{WS_PATH}"
            headers = [f"Authorization: Bearer {self.token}"] if self.token else []
            subscriptions = sorted(self._subscribed_sessions)
            cursors = deepcopy(self._session_cursors)
            hello_id = self._next_id()
            subscribe_id = self._next_id() if subscriptions and not self._server_api else ""
            self._hello_ids = {generation: hello_id}
            self._transport_ready_events[generation] = threading.Event()
            self._hello_acknowledged.discard(generation)
            if self._server_api:
                self.protocol_version = 2
                self._hello_acknowledged.add(generation)
                if not subscriptions:
                    self._transport_ready_events[generation].set()
            self._subscription_acknowledged.discard(generation)
            if subscribe_id:
                self._subscribe_requests[subscribe_id] = set(subscriptions)
        self._close_ws_locked(stale_ws)
        candidate = None
        try:
            if self.ws_factory is not None:
                candidate = self.ws_factory(url, headers)
            else:
                import websocket

                candidate = websocket.create_connection(
                    url,
                    header=headers if not self._server_api else [],
                    **({"subprotocols": [f"kimi-code.bearer.{self.token}"]} if self._server_api else {}),
                    http_no_proxy=["127.0.0.1", "localhost", "::1"],
                    timeout=min(self.ws_connect_timeout, timeout) if timeout is not None else self.ws_connect_timeout,
                )
                # The timeout is a handshake boundary, not an idle lifetime.
                if hasattr(candidate, "settimeout"):
                    candidate.settimeout(min(self.ws_connect_timeout, timeout) if timeout is not None else self.ws_connect_timeout)
            if bool(getattr(candidate, "closed", False)):
                raise KimiServerError("kimi websocket factory returned a closed socket")
            if self.ws_factory is None and not self._server_api:
                greeting = json.loads(candidate.recv())
                if greeting.get("type") != "server_hello":
                    raise KimiServerError("Kimi handshake missing server_hello")
                version = _payload_of(greeting).get("protocol_version")
                if version not in (1, 2):
                    raise KimiServerError(f"Kimi handshake incompatible protocol {version}; supported: 1, 2")
                self.protocol_version = version
                self._v2_ready_sessions.clear()
                if hasattr(candidate, "settimeout"):
                    candidate.settimeout(None)
            if not self._server_api:
                self._send_on_socket_locked(
                    candidate,
                    {
                        "type": "client_hello",
                        "id": hello_id,
                        "payload": {
                            "client_id": "zgwd",
                            "subscriptions": [],
                            "cursors": cursors,
                        },
                    },
                )
                if subscriptions and self.protocol_version == 1:
                    self._send_on_socket_locked(
                        candidate,
                        {
                            "type": "subscribe",
                            "id": subscribe_id,
                            "payload": {"session_ids": subscriptions},
                        },
                    )
        except Exception as exc:
            self._close_ws_locked(candidate)
            if isinstance(exc, KimiServerError):
                raise
            raise KimiServerError(f"kimi websocket connection failed: {exc}") from exc
        with self._lifecycle_lock:
            valid = (
                not self._closed
                and generation == self._ws_generation
                and self.process is not None
                and self.process.poll() is None
            )
            if valid:
                self._ws = candidate
                if self.start_reader_thread:
                    self._ws_thread = threading.Thread(
                        target=self._ws_loop,
                        args=(candidate, generation),
                        daemon=True,
                    )
                    self._ws_thread.start()
                else:
                    self._ws_thread = None
        if valid and self.protocol_version == 2:
            for sid in subscriptions:
                self._subscribe_v2(sid)
        if not valid:
            self._close_ws_locked(candidate)
            raise KimiServerError("kimi websocket connection was superseded")

    def _send_ws(self, message: dict[str, Any], *, send_timeout: float | None = None) -> None:
        last_error: BaseException | None = None
        for attempt in range(2):
            with self._lifecycle_lock:
                closed = self._closed
                usable = self._ws_is_usable_locked()
            if closed:
                raise KimiServerError("kimi websocket is closing")
            try:
                if not usable:
                    self._connect_ws(timeout=send_timeout)
                with self._lifecycle_lock:
                    ws = self._ws
                    generation = self._ws_generation
                if ws is None:
                    raise KimiServerError("kimi websocket is not connected")
                self._send_on_socket_locked(ws, message, send_timeout=send_timeout)
                with self._lifecycle_lock:
                    if self._ws is not ws or self._ws_generation != generation:
                        raise KimiServerError("kimi websocket changed during send")
                return
            except Exception as exc:
                last_error = exc
                self._invalidate_ws(ws if "ws" in locals() else None, str(exc), recover=False)
                with self._lifecycle_lock:
                    may_retry = (
                        attempt == 0
                        and not self._closed
                        and self.process is not None
                        and self.process.poll() is None
                    )
                if may_retry:
                    continue
                break
        error = f"kimi websocket transport failed: {last_error}"
        self._notify_transport_failure_once(error)
        self._request_recovery(error)
        raise KimiServerError(error) from last_error

    def _send_on_socket_locked(
        self,
        ws: Any,
        message: dict[str, Any],
        *,
        send_timeout: float | None = None,
    ) -> None:
        if ws is None:
            raise KimiServerError("kimi websocket is not connected")
        line = json.dumps(message, ensure_ascii=False)
        with self._send_lock:
            previous_timeout = None
            timeout_changed = False
            try:
                if hasattr(ws, "gettimeout") and hasattr(ws, "settimeout"):
                    previous_timeout = ws.gettimeout()
                    bounded_timeout = self.ws_send_timeout
                    if send_timeout is not None:
                        bounded_timeout = max(0.05, min(bounded_timeout, float(send_timeout)))
                    ws.settimeout(bounded_timeout)
                    timeout_changed = True
                ws.send(line)
            finally:
                if timeout_changed:
                    try:
                        ws.settimeout(previous_timeout)
                    except Exception:
                        pass

    def _close_ws_locked(self, ws: Any) -> None:
        if ws is None:
            return
        with self._send_lock:
            try:
                ws.close()
            except Exception:
                pass

    def _ws_is_usable_locked(self) -> bool:
        if self._ws is None:
            return False
        if not self.start_reader_thread:
            return True
        return self._ws_thread is not None and self._ws_thread.is_alive()

    def _ws_loop(self, ws: Any | None = None, generation: int | None = None) -> None:
        if ws is None:
            with self._lifecycle_lock:
                ws = self._ws
                generation = self._ws_generation
        if ws is None:
            return
        if generation is None:
            with self._lifecycle_lock:
                generation = self._ws_generation
        while True:
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws or generation != self._ws_generation:
                    return
            try:
                raw = ws.recv()
            except Exception as exc:
                if self._is_ws_timeout(exc):
                    continue
                with self._lifecycle_lock:
                    if self._closed:
                        return
                self._invalidate_ws(ws, str(exc), recover=True, generation=generation)
                return
            if not raw:
                self._invalidate_ws(ws, "kimi websocket closed", recover=True, generation=generation)
                return
            # recv() may return after a replacement socket was installed.
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws or generation != self._ws_generation:
                    return
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if not self._handle_ws_message(message, ws=ws, generation=generation):
                return

    def _handle_ws_message(
        self,
        message: dict[str, Any],
        *,
        ws: Any | None = None,
        generation: int | None = None,
    ) -> bool:
        if ws is not None:
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws:
                    return False
                if generation is not None and generation != self._ws_generation:
                    return False
        if self._server_api and isinstance(message, dict):
            handled = self._handle_server_api_frame(message, ws, generation)
            if handled is not None:
                return handled
        if isinstance(message, dict) and message.get("type") == "server_hello":
            version = _payload_of(message).get("protocol_version", 1)
            if version not in (1, 2):
                raise KimiServerError(f"Kimi handshake incompatible protocol {version}; supported: 1, 2")
            self.protocol_version = version
            if version == 2:
                self._v2_ready_sessions.clear()
                for sid in sorted(self._subscribed_sessions):
                    self._subscribe_v2(sid)
            return True
        if self.protocol_version == 2 and isinstance(message, dict) and message.get("type") in {"transcript.reset", "transcript.ops"}:
            self._consume_v2(message, ws=ws, generation=generation)
            return True
        if self.protocol_version == 2 and isinstance(message, dict) and message.get("type") == "usage":
            self._consume_v2_usage_window(message, ws=ws, generation=generation)
            return True
        if self.protocol_version == 2 and isinstance(message, dict) and message.get("type") == "interaction":
            session_id = _str(message.get("session_id"))
            interaction_id = _str(message.get("interaction_id"))
            kind, status = message.get("kind"), message.get("status")
            if session_id and interaction_id and kind in {"approval", "question"} and status in {"pending", "ended"}:
                request = message.get("request")
                self._enqueue_event(KimiEvent(
                    type="server_request" if status == "pending" else "server_request_ended",
                    thread_id=session_id, session_id=session_id, request_id=interaction_id,
                    method=kind, status=status, params=request if isinstance(request, dict) else {},
                    origin_timestamp=_provider_origin_timestamp({"timestamp": message.get("event_created_at")}),
                    data={"source_kind": "interaction", "agent_id": message.get("agent_id"),
                          "outcome": message.get("outcome")},
                ))
            return True
        if isinstance(message, dict) and _str(message.get("type")) == "ack":
            try:
                self._handle_ack(message, generation=generation)
                return True
            except KimiServerError as exc:
                target = ws
                if target is None:
                    with self._lifecycle_lock:
                        target = self._ws
                self._invalidate_ws(target, str(exc), recover=True, generation=generation)
                return False
        if isinstance(message, dict) and _str(message.get("type")) == "ping":
            payload = _payload_of(message)
            target = ws
            if target is None:
                with self._lifecycle_lock:
                    target = self._ws
            try:
                with self._lifecycle_lock:
                    if self._closed or target is None or self._ws is not target:
                        return False
                    target_generation = self._ws_generation
                self._send_on_socket_locked(
                    target,
                    ({"type": "pong"} if self._server_api else {"type": "pong", "payload": {"nonce": payload.get("nonce")}}),
                )
                with self._lifecycle_lock:
                    if self._ws is not target or self._ws_generation != target_generation:
                        return False
                return True
            except Exception as exc:
                self._invalidate_ws(target, str(exc), recover=True, generation=generation)
                return False
        if not self._accept_session_event(message):
            return True
        event = self._map_session_event(message)
        if event is None:
            return True
        self._enqueue_event(event)
        return True

    def _map_session_event(self, message: dict[str, Any]) -> KimiEvent | None:
        """Bind SDK deltas to the real step lifecycle, never to a bare turn."""
        body = _payload_of(message)
        source_kind = _str(body.get("type") or message.get("type"))
        session_id = _str(message.get("session_id"))
        turn_id = _str(body.get("turnId"))
        agent_id = _str(body.get("agentId")) or "main"
        with self._lifecycle_lock:
            epoch = _str(message.get("epoch") or self._session_cursors.get(session_id, {}).get("epoch"))
            key = (session_id, epoch, turn_id, agent_id)
            if source_kind == "turn.started":
                for previous in list(self._session_steps):
                    if previous[0] == session_id and previous[3] == agent_id:
                        self._session_steps.pop(previous, None)
            if source_kind == "turn.step.started":
                self._session_steps[key] = {
                    "epoch": epoch, "step_id": _str(body.get("stepId") or body.get("step")),
                    "step_number": body.get("step"), "started_seq": message.get("seq"),
                }
            scope = self._session_steps.get(key)
            if source_kind in {"thinking.delta", "assistant.delta"} and scope:
                seq, started_seq = message.get("seq"), scope.get("started_seq")
                if isinstance(seq, int) and isinstance(started_seq, int) and seq < started_seq:
                    return None
            event = map_session_event(message, step_scope=scope)
            if source_kind in {"thinking.delta", "assistant.delta"} and scope and scope.get("closed") and event:
                data = event.data
                restricted = (data.get("disclosable") is False or data.get("private") is True
                              or data.get("hidden") is True or data.get("non_disclosable") is True
                              or _str(data.get("visibility") or data.get("reasoning_visibility")).lower()
                              in {"private", "hidden", "non_disclosable", "non-disclosable"})
                if not restricted:
                    return None
            if source_kind in {"turn.step.completed", "turn.step.interrupted"} and scope:
                scope["closed"] = True
            if source_kind in {"turn.ended", "prompt.completed", "prompt.aborted"}:
                self._session_steps.pop(key, None)
            return event

    def _invalidate_ws(
        self,
        ws: Any,
        error: str,
        *,
        notify: bool | None = None,
        recover: bool = False,
        generation: int | None = None,
    ) -> bool:
        """Mark only ``ws`` stale; an older reader must not clear its replacement."""
        invalidated = False
        fault_generation = -1
        with self._lifecycle_lock:
            if (
                not self._closed
                and self._ws is ws
                and (generation is None or generation == self._ws_generation)
            ):
                fault_generation = self._ws_generation
                self._ws = None
                self._ws_generation += 1
                self._clear_v2_readiness()
                invalidated = True
        if invalidated:
            self._close_ws_locked(ws)
        if invalidated and recover:
            self._notify_transport_failure_once(str(error), fault_generation)
            self._request_recovery(str(error))
        elif invalidated and notify:
            self._notify_transport_failure_once(str(error), fault_generation)
        return invalidated

    def _notify_transport_failure_once(self, error: str, generation: int | None = None) -> None:
        with self._lifecycle_lock:
            fault_generation = self._ws_generation if generation is None else int(generation)
            if self._active_transport_fault_id is not None:
                return
            if fault_generation in self._transport_faults_notified:
                return
            self._transport_faults_notified.add(fault_generation)
            self._active_transport_fault_id = fault_generation
            session_ids = sorted(self._subscribed_sessions)
        self._enqueue_control(
            {
                "type": "transport_error",
                "payload": {"error": str(error), "session_ids": session_ids},
            }
        )

    def _handle_ack(self, message: dict[str, Any], *, generation: int | None = None) -> None:
        if generation is not None and generation != self._ws_generation:
            return
        payload = _payload_of(message)
        code = message.get("code", payload.get("code", 0))
        if code not in (0, None):
            raise KimiServerError(
                f"kimi websocket request rejected ({code}): {message.get('msg') or payload.get('msg') or ''}"
            )
        request_id = _str(message.get("id"))
        with self._lifecycle_lock:
            requested_subscriptions = set(self._subscribe_requests.pop(request_id, set()))
            request_generation = self._v2_request_generations.pop(request_id, None)
            if self.protocol_version == 2 and requested_subscriptions and request_generation != self._ws_generation:
                return
        not_found = {
            _str(value) for value in (payload.get("not_found") or []) if _str(value)
        }
        accepted_value = payload.get("accepted")
        accepted = {
            _str(value) for value in (accepted_value or []) if _str(value)
        } if isinstance(accepted_value, list) else set()
        if self.protocol_version == 2 and requested_subscriptions and not isinstance(accepted_value, list):
            raise KimiServerError("Kimi subscribe_v2 ACK missing accepted sessions")
        if self.protocol_version == 2:
            with self._lifecycle_lock:
                if request_generation == self._ws_generation:
                    for sid in requested_subscriptions & accepted:
                        self._v2_ready_sessions.add(sid)
                        self._v2_ready_generations[sid] = self._ws_generation
                        self._v2_subscription_events.setdefault(sid, threading.Event()).set()
                        if sid in self._v2_usage_windows:
                            self._emit_v2_usage(sid, "status", self._v2_usage_windows[sid])
                        self._record_v2_transcript_usage(sid, self._v2_transcripts.get(sid))
        rejected = requested_subscriptions & not_found
        if requested_subscriptions and isinstance(accepted_value, list):
            rejected.update(requested_subscriptions - accepted)
        if rejected:
            # Do not let one stale session poison every later reconnect.  The
            # socket is still recovered so accepted sessions are resubscribed.
            with self._lifecycle_lock:
                self._subscribed_sessions.difference_update(rejected)
            raise KimiServerError(
                "kimi websocket subscribe rejected: " + ", ".join(sorted(rejected))
            )
        cursors = payload.get("cursors") if isinstance(payload.get("cursors"), dict) else {}
        with self._lifecycle_lock:
            active_generation = self._ws_generation if generation is None else generation
            if generation is not None and generation != self._ws_generation:
                return
            hello_id = self._hello_ids.get(active_generation)
            confirms_epoch = bool(hello_id and _str(message.get("id")) == hello_id)
            if confirms_epoch:
                self._hello_acknowledged.add(active_generation)
            if request_id and requested_subscriptions:
                self._subscription_acknowledged.add(active_generation)
            for session_id, cursor in cursors.items():
                if isinstance(cursor, dict):
                    epoch = _str(cursor.get("epoch"))
                    seq = cursor.get("seq")
                    if epoch and isinstance(seq, int):
                        normalized = _str(session_id)
                        current = self._session_cursors.get(normalized)
                        current_epoch = _str((current or {}).get("epoch")) if isinstance(current, dict) else ""
                        current_seq = int((current or {}).get("seq") or -1) if isinstance(current, dict) else -1
                        if current_epoch and epoch != current_epoch and not confirms_epoch:
                            continue
                        if epoch == current_epoch and seq < current_seq:
                            continue
                        self._session_cursors[normalized] = {"epoch": epoch, "seq": seq}
                        previous = self._stable_event_cursors.get(normalized)
                        if previous is None or (previous[0] != epoch and confirms_epoch) or (previous[0] == epoch and seq > previous[1]):
                            self._stable_event_cursors[normalized] = (epoch, seq)
            # An empty subscription set is ready after hello; otherwise the
            # corresponding subscribe ack is the readiness boundary.
            ready = (
                active_generation in self._hello_acknowledged
                and (
                    not self._subscribed_sessions
                    or (self._subscribed_sessions <= self._v2_ready_sessions if self.protocol_version == 2
                        else active_generation in self._subscription_acknowledged)
                )
            )
            if ready:
                event = self._transport_ready_events.get(active_generation)
                if event is not None:
                    event.set()
        resync = payload.get("resync_required")
        if resync:
            if isinstance(resync, list):
                resync_values = resync
            else:
                resync_values = requested_subscriptions or set(cursors) or self._subscribed_sessions
            session_ids = tuple(sorted({_str(value) for value in resync_values if _str(value)}))
            if not session_ids:
                return
            with self._lifecycle_lock:
                identity = (active_generation, session_ids)
                if identity in self._resync_notified:
                    return
                self._resync_notified.add(identity)
            self._enqueue_control(
                {
                    "type": "resync_required",
                    "payload": {"session_ids": list(session_ids)},
                }
            )

    def _accept_session_event(self, message: dict[str, Any]) -> bool:
        """Advance protocol cursors and reject replayed stable events.

        Volatile delta frames may legitimately share a sequence number and are
        distinguished by ``offset``.  Stable frames at or below the last stable
        sequence for the same epoch are replay and must be discarded before
        mapping so they cannot append duplicate answer text.
        """
        if not isinstance(message, dict):
            return False
        session_id = _str(message.get("session_id"))
        seq = message.get("seq")
        if not session_id or not isinstance(seq, int):
            return True
        epoch = _str(message.get("epoch"))
        body = _payload_of(message)
        body_type = _str(body.get("type") or message.get("type"))
        volatile = bool(message.get("volatile")) or body_type.endswith(".delta") or message.get("offset") is not None
        with self._lifecycle_lock:
            current = self._session_cursors.get(session_id)
            current_epoch = _str((current or {}).get("epoch")) if isinstance(current, dict) else ""
            # Sequenced frames may omit epoch after the handshake.  Inherit
            # the confirmed epoch instead of clearing it and reopening replay.
            effective_epoch = epoch or current_epoch
            if current_epoch and epoch and epoch != current_epoch:
                return False
            previous = self._stable_event_cursors.get(session_id)
            if previous is not None and previous[0] == effective_epoch and not volatile and seq <= previous[1]:
                return False
            if volatile:
                offset = message.get("offset")
                content_identity = ""
                if offset is None:
                    # Volatile state/usage frames can share seq without an
                    # offset.  Their stable payload identity distinguishes
                    # legitimate phase transitions while still suppressing
                    # byte-for-byte replay.
                    try:
                        content_identity = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    except (TypeError, ValueError):
                        content_identity = repr(body)
                else:
                    # A replay may withdraw disclosure without changing its
                    # sequence/offset. Let that restriction reach the existing
                    # monotonic redaction; exact metadata replays stay inert.
                    restrictive = {
                        key: body[key] for key, protected in (
                            ("private", True), ("isPrivate", True), ("hidden", True),
                            ("non_disclosable", True), ("nonDisclosable", True),
                            ("disclosable", False), ("isDisclosable", False),
                        ) if body.get(key) is protected
                    }
                    for key in ("visibility", "reasoningVisibility"):
                        if _str(body.get(key)).lower() in {"private", "hidden", "non_disclosable", "non-disclosable"}:
                            restrictive[key] = body[key]
                    content_identity = json.dumps(restrictive, sort_keys=True)
                replay_key = (
                    session_id,
                    effective_epoch,
                    seq,
                    _str(body.get("agentId")) or "main",
                    _str(body.get("turnId")),
                    _str(body.get("messageId") or body.get("itemId") or body_type),
                    offset,
                    content_identity,
                )
                if replay_key in self._volatile_replay_keys:
                    return False
                self._volatile_replay_keys.add(replay_key)
                self._volatile_replay_order.append(replay_key)
                while len(self._volatile_replay_order) > self.volatile_replay_window:
                    expired = self._volatile_replay_order.popleft()
                    self._volatile_replay_keys.discard(expired)
            if not volatile:
                self._stable_event_cursors[session_id] = (effective_epoch, seq)
            if not isinstance(current, dict) or (not current_epoch and epoch) or seq > int(current.get("seq") or -1):
                self._session_cursors[session_id] = {"epoch": effective_epoch, "seq": seq}
        return True

    def _request_recovery(self, error: str) -> None:
        """Coalesce every socket failure into one bounded recovery owner."""
        with self._lifecycle_lock:
            if self._closed or self.process is None or self.process.poll() is not None:
                return
            self._recovery_requested = True
            thread = self._recovery_thread
            if thread is not None and thread.is_alive():
                return
            self._recovery_generation += 1
            generation = self._recovery_generation
            thread = threading.Thread(
                target=self._recovery_loop,
                args=(generation, str(error)),
                daemon=True,
            )
            self._recovery_thread = thread
            thread.start()

    def _recovery_loop(self, generation: int, original_error: str) -> None:
        last_error = original_error
        try:
            with self._lifecycle_lock:
                if generation == self._recovery_generation:
                    self._recovery_requested = False
            for attempt in range(self.recovery_attempts):
                if attempt and self.recovery_backoff:
                    time.sleep(self.recovery_backoff * (2 ** (attempt - 1)))
                with self._lifecycle_lock:
                    if self._closed or generation != self._recovery_generation:
                        return
                    if self.process is None or self.process.poll() is not None:
                        break
                try:
                    self._connect_ws_locked()
                    with self._lifecycle_lock:
                        ready_event = self._transport_ready_events.get(self._ws_generation)
                    # Do not tell the UI that the transport recovered until
                    # hello/subscription ack has established the stream.
                    if ready_event is None or not ready_event.wait(timeout=self.ws_connect_timeout):
                        raise KimiServerError("kimi websocket recovery acknowledgement timed out")
                    with self._lifecycle_lock:
                        if self._closed or generation != self._recovery_generation:
                            return
                        if not self._ws_is_usable_locked():
                            raise KimiServerError("replacement websocket disconnected during recovery")
                        self._recovery_requested = False
                        self._active_transport_fault_id = None
                    with self._lifecycle_lock:
                        recovered_sessions = sorted(self._subscribed_sessions)
                    self._enqueue_control(
                        {
                            "type": "transport_recovered",
                            "payload": {"session_ids": recovered_sessions},
                        }
                    )
                    return
                except Exception as exc:
                    last_error = str(exc)
            with self._lifecycle_lock:
                pending_handoff = self._recovery_requested
            if not pending_handoff:
                self._notify_transport_failure_once(
                    f"kimi websocket recovery exhausted: {last_error}"
                )
        finally:
            handoff = False
            with self._lifecycle_lock:
                if generation == self._recovery_generation:
                    self._recovery_thread = None
                    if self._recovery_requested and not self._closed and self._ws_is_usable_locked():
                        self._recovery_requested = False
                    elif self._recovery_requested and not self._closed and self.process is not None and self.process.poll() is None:
                        # A replacement reader can fail while this worker is
                        # returning.  Hand ownership to a fresh bounded round.
                        handoff = True
            if handoff:
                self._request_recovery(last_error)

    @staticmethod
    def _next_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _is_ws_timeout(exc: BaseException) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        message = str(exc).lower()
        return "timed out" in message or "timeout" in message
