from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
import threading
import uuid
from typing import Any

from remote_nats_protocol import (
    FILE_EVENT_TYPES,
    NatsSubjects,
    build_error_response,
    build_response_event,
    encode_payload,
    make_event_id,
    validate_v2_ephemeral,
)


Callback = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]
CallbackInvoker = Callable[[Callable[[], tuple[int, dict[str, Any]]]], tuple[int, dict[str, Any]]]


class RemoteNatsTransport:
    def __init__(
        self,
        *,
        pair_id: str,
        token: str,
        jetstream: Any | None = None,
        on_message: Callback | None = None,
        on_new_chat: Callback | None = None,
        on_reply_request: Callback | None = None,
        on_state: Callback | None = None,
        on_rename_chat: Callback | None = None,
        on_update_settings: Callback | None = None,
        on_speed_options: Callback | None = None,
        on_set_speed: Callback | None = None,
        on_clear_context: Callback | None = None,
        on_model_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_common_commands_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_common_commands_create: Callback | None = None,
        on_common_commands_update: Callback | None = None,
        on_common_commands_delete: Callback | None = None,
        on_common_commands_pin: Callback | None = None,
        on_common_commands_unpin: Callback | None = None,
        on_common_commands_move_up: Callback | None = None,
        on_common_commands_move_down: Callback | None = None,
        on_history_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_history_read: Callback | None = None,
        on_notes_changes: Callback | None = None,
        on_notes_bulk_docs: Callback | None = None,
        on_file_command: Callback | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
        invoke_callback: CallbackInvoker | None = None,
        durable_store: Any | None = None,
    ) -> None:
        self.subjects = NatsSubjects.from_pair_id(pair_id)
        self.token = token
        self.jetstream = jetstream
        self._loop = event_loop
        self.on_message = on_message
        self.on_new_chat = on_new_chat
        self.on_reply_request = on_reply_request
        self.on_state = on_state
        self.on_rename_chat = on_rename_chat
        self.on_update_settings = on_update_settings
        self.on_speed_options = on_speed_options
        self.on_set_speed = on_set_speed
        self.on_clear_context = on_clear_context
        self.on_model_list = on_model_list
        self.on_common_commands_list = on_common_commands_list
        self.on_common_commands_create = on_common_commands_create
        self.on_common_commands_update = on_common_commands_update
        self.on_common_commands_delete = on_common_commands_delete
        self.on_common_commands_pin = on_common_commands_pin
        self.on_common_commands_unpin = on_common_commands_unpin
        self.on_common_commands_move_up = on_common_commands_move_up
        self.on_common_commands_move_down = on_common_commands_move_down
        self.on_history_list = on_history_list
        self.on_history_read = on_history_read
        self.on_notes_changes = on_notes_changes
        self.on_notes_bulk_docs = on_notes_bulk_docs
        self.on_file_command = on_file_command
        self._invoke_callback = invoke_callback
        self.durable_store = durable_store
        self.protocol_version = 1
        self.connection_epoch = uuid.uuid4().hex
        self._sessions: dict[tuple[str, str], tuple[int, str]] = {}
        self._outbox_lock: asyncio.Lock | None = None
        self._outbox_retry_task: asyncio.Task | None = None
        self._nats_client: Any | None = None
        self._command_subscription: Any | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._stop_requested = False

    async def initialize_streams(self) -> None:
        if self.jetstream is None:
            return

        await self._ensure_stream(
            name=self.subjects.command_stream,
            subjects=[self.subjects.commands],
        )
        await self._ensure_stream(
            name=self.subjects.event_stream,
            subjects=[self.subjects.events, self.subjects.files],
        )

    async def _ensure_stream(self, *, name: str, subjects: list[str]) -> None:
        stream_info = getattr(self.jetstream, "stream_info", None)
        if callable(stream_info):
            try:
                await stream_info(name)
                return
            except Exception:
                pass

        await self.jetstream.add_stream(
            name=name,
            subjects=subjects,
            storage="file",
        )

    async def start(self, url: str = "nats://127.0.0.1:4222") -> None:
        import nats

        self._nats_client = await nats.connect(url, token=self.token)
        self.jetstream = self._nats_client.jetstream()
        await self.initialize_streams()
        self._command_subscription = await self.jetstream.subscribe(
            self.subjects.commands,
            durable="desktop",
            manual_ack=True,
            cb=self._handle_nats_message,
        )
        await self.drain_outbox()

    def start_threaded(self, url: str = "nats://127.0.0.1:4222", timeout: float = 10) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._startup_error = None
        self._stop_requested = False

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start(url))
                self._started.set()
                loop.run_forever()
            except BaseException as exc:
                self._startup_error = exc
                self._started.set()
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(self._close_async())
                self.set_event_loop(None)
                loop.close()

        self._thread = threading.Thread(target=_runner, name="remote-nats", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            self.stop()
            raise TimeoutError("NATS transport did not start")
        if self._startup_error is not None:
            raise RuntimeError(f"NATS transport failed to start: {self._startup_error}") from self._startup_error

    async def _handle_nats_message(self, message: Any) -> None:
        try:
            payload = json.loads(bytes(message.data).decode("utf-8"))
            if isinstance(payload, dict):
                await self.handle_command(payload)
            ack = getattr(message, "ack", None)
            if callable(ack):
                result = ack()
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            nak = getattr(message, "nak", None)
            if callable(nak):
                result = nak()
                if asyncio.iscoroutine(result):
                    await result

    async def handle_command(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("id") or "")
        chat_id = str(payload.get("chat_id") or "")
        body_payload = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        device_id = str(payload.get("device_id") or body_payload.get("device_id") or "").strip()
        session_id = str(payload.get("session_id") or body_payload.get("session_id") or "").strip()
        session_key = (device_id, session_id)
        negotiated_version, negotiated_epoch = self._sessions.get(session_key, (1, ""))
        try:
            if str(payload.get("type") or "").lower() == "hello":
                offered = payload.get("protocol_versions", body_payload.get("protocol_versions", []))
                can_negotiate_v2 = (
                    device_id
                    and session_id
                    and isinstance(offered, list)
                    and 2 in offered
                    and self.durable_store is not None
                    and getattr(self.durable_store, "v2_writes_enabled", False)
                )
                negotiated_version = 2 if can_negotiate_v2 else 1
                negotiated_epoch = uuid.uuid4().hex if negotiated_version == 2 else ""
                self._sessions[session_key] = (negotiated_version, negotiated_epoch)
                self.protocol_version = max((state[0] for state in self._sessions.values()), default=1)
                watermark = (
                    self.durable_store.paired_feed_high_water(
                        pair_id=self.subjects.pair_id,
                        domain="events",
                    )
                    if negotiated_version == 2
                    else {}
                )
                event = build_response_event(request_id=request_id, status=200, body={
                    "accepted": True, "protocol_version": negotiated_version,
                    "capabilities": ["canonical-owner", "durable-outbox", "replay"],
                    "epoch": negotiated_epoch,
                    **watermark,
                }, chat_id=chat_id)
                await self.publish_event(event)
                return
            if negotiated_version == 2 and payload.get("epoch") != negotiated_epoch:
                raise ValueError("STALE_EPOCH")
            if negotiated_version == 2:
                validate_v2_ephemeral(payload, expected_epoch=negotiated_epoch)
            status, body = await asyncio.to_thread(self._invoke_route_command, payload)
            event = build_response_event(
                request_id=request_id,
                status=status,
                body=body,
                chat_id=chat_id,
            )
            if negotiated_version == 2:
                event.update({"protocol_version": 2, "epoch": negotiated_epoch,
                              "request_id": request_id, "chat_id": chat_id, "body": body})
        except Exception as exc:
            event = build_error_response(request_id, 500, str(exc) or "error")
            if negotiated_version == 2:
                event.update({"protocol_version": 2, "epoch": negotiated_epoch,
                              "request_id": request_id, "chat_id": chat_id,
                              "body": {"error": str(exc) or "error"}})
        await self.publish_event(event)

    async def publish_event(self, payload: dict[str, Any]) -> None:
        if self.jetstream is None:
            return

        event = dict(payload)
        event_type = str(event.get("type") or "event")
        if not event.get("event_id"):
            event["event_id"] = make_event_id(event_type)
        subject = self.subjects.files if event_type.startswith("file_") else self.subjects.events
        await self.jetstream.publish(subject, encode_payload(event))

    async def drain_outbox(self) -> int:
        """Publish serially. A failed/poison row prevents all later rows bypassing it."""
        if self.jetstream is None or self.durable_store is None:
            return 0
        if self._outbox_lock is None:
            self._outbox_lock = asyncio.Lock()
        if self._outbox_lock.locked():
            return 0
        published = 0
        async with self._outbox_lock:
          for row in self.durable_store.pending_outbox(pair_id=self.subjects.pair_id):
            if row.get("blocked_reason"):
                break
            subject = self.subjects.files if row["subject_domain"] == "files" else self.subjects.events
            try:
                ack = await self.jetstream.publish(subject, bytes(row["payload"]))
                # JetStream publish completion is the server ACK boundary.
                if ack is None and type(self.jetstream).__module__.startswith("nats"):
                    raise RuntimeError("missing_publish_ack")
                self.durable_store.mark_outbox_acked(
                    row["sync_sequence"], pair_id=row["pair_id"], domain=row["domain"],
                    consumer_id=f"publisher:{row['pair_id']}",
                )
                published += 1
            except Exception as exc:
                self.durable_store.record_outbox_failure(row["sync_sequence"], str(exc), pair_id=row["pair_id"], domain=row["domain"])
                latest = self.durable_store.pending_outbox(1, pair_id=self.subjects.pair_id)
                if latest and not latest[0].get("blocked_reason"):
                    self._schedule_outbox_retry()
                break
        return published

    def _schedule_outbox_retry(self) -> None:
        if self._outbox_retry_task is not None and not self._outbox_retry_task.done():
            return
        async def retry() -> None:
            await asyncio.sleep(0.25)
            await self.drain_outbox()
        self._outbox_retry_task = asyncio.create_task(retry())

    def repair_outbox_row(self, sync_sequence: int) -> None:
        if self.durable_store is None:
            return
        with self.durable_store._connect() as conn:
            conn.execute("UPDATE publication_outbox SET attempts=0,blocked_reason=NULL WHERE pair_id=? AND sync_sequence=?", (self.subjects.pair_id,int(sync_sequence)))

    def stop(self) -> None:
        self._stop_requested = True
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._close_async(), loop)
                future.result(timeout=5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None

    async def _close_async(self) -> None:
        if self._outbox_retry_task is not None:
            self._outbox_retry_task.cancel()
            self._outbox_retry_task = None
        subscription = self._command_subscription
        self._command_subscription = None
        if subscription is not None:
            close = getattr(subscription, "unsubscribe", None) or getattr(subscription, "drain", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result
        client = self._nats_client
        self._nats_client = None
        if client is not None:
            close = getattr(client, "drain", None) or getattr(client, "close", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    with contextlib.suppress(Exception):
                        await result

    def set_event_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def publish_event_threadsafe(self, payload: dict[str, Any]) -> bool:
        loop = self._loop
        if loop is not None:
            try:
                if loop.is_running():
                    coro = self.publish_event(payload)
                    try:
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    except Exception:
                        coro.close()
                        return False
                    return True
            except Exception:
                return False

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        try:
            if running_loop.is_running():
                running_loop.create_task(self.publish_event(payload))
                return True
        except Exception:
            return False
        return False

    def _route_command(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        command_type = str(payload.get("type") or "").strip().lower()
        if command_type in {"execution_tail", "execution_history", "execution_snapshot"}:
            if self.durable_store is None:
                return 503, {"error": "execution_authority_unavailable"}
            body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
            try:
                return 200, self.durable_store.load_execution_page(
                    pair_id=self.subjects.pair_id,
                    domain=str(body.get("sequence_domain") or "events"),
                    chat_id=str(payload.get("chat_id") or body.get("chat_id") or ""),
                    secret=self.token,
                    limit=int(body.get("limit") or 100),
                    cursor="" if command_type in {"execution_tail", "execution_snapshot"} else str(body.get("cursor") or ""),
                )
            except (TypeError, ValueError) as exc:
                return 409, {"error": str(exc), "recovery": "SNAPSHOT_REQUIRED"}
        if command_type == "execution_backfill":
            if self.durable_store is None:
                return 503, {"error": "execution_authority_unavailable"}
            body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
            try:
                return 200, self.durable_store.load_execution_range(
                    pair_id=self.subjects.pair_id,
                    domain=str(body.get("sequence_domain") or "events"),
                    chat_id=str(payload.get("chat_id") or body.get("chat_id") or ""),
                    revision=int(body.get("revision")),
                    start_sequence=int(body.get("from_sequence") or 0),
                    end_sequence=int(body.get("to_sequence") or 0),
                )
            except (TypeError, ValueError) as exc:
                return 409, {"error": str(exc), "recovery": "SNAPSHOT_REQUIRED"}
        if command_type == "message" and callable(self.on_message):
            return self.on_message(payload)
        if command_type == "new_chat" and callable(self.on_new_chat):
            return self.on_new_chat(payload)
        if command_type == "reply_request" and callable(self.on_reply_request):
            return self.on_reply_request(payload)
        if command_type == "state" and callable(self.on_state):
            return self.on_state(payload)
        if command_type == "model_list" and callable(self.on_model_list):
            return self.on_model_list()
        if command_type == "common_commands_list" and callable(self.on_common_commands_list):
            return self.on_common_commands_list()
        if command_type == "common_commands_create" and callable(self.on_common_commands_create):
            return self.on_common_commands_create(payload)
        if command_type == "common_commands_update" and callable(self.on_common_commands_update):
            return self.on_common_commands_update(payload)
        if command_type == "common_commands_delete" and callable(self.on_common_commands_delete):
            return self.on_common_commands_delete(payload)
        if command_type == "common_commands_pin" and callable(self.on_common_commands_pin):
            return self.on_common_commands_pin(payload)
        if command_type == "common_commands_unpin" and callable(self.on_common_commands_unpin):
            return self.on_common_commands_unpin(payload)
        if command_type == "common_commands_move_up" and callable(self.on_common_commands_move_up):
            return self.on_common_commands_move_up(payload)
        if command_type == "common_commands_move_down" and callable(self.on_common_commands_move_down):
            return self.on_common_commands_move_down(payload)
        if command_type == "rename_chat" and callable(self.on_rename_chat):
            return self.on_rename_chat(payload)
        if command_type == "update_settings" and callable(self.on_update_settings):
            return self.on_update_settings(payload)
        if command_type == "speed_options" and callable(self.on_speed_options):
            return self.on_speed_options(payload)
        if command_type == "set_speed" and callable(self.on_set_speed):
            return self.on_set_speed(payload)
        if command_type == "clear_context" and callable(self.on_clear_context):
            return self.on_clear_context(payload)
        if command_type == "history_list" and callable(self.on_history_list):
            return self.on_history_list()
        if command_type == "history_read" and callable(self.on_history_read):
            return self.on_history_read(payload)
        if command_type == "notes_changes" and callable(self.on_notes_changes):
            return self.on_notes_changes(payload)
        if command_type == "notes_bulk_docs" and callable(self.on_notes_bulk_docs):
            return self.on_notes_bulk_docs(payload)
        if command_type in FILE_EVENT_TYPES and callable(self.on_file_command):
            return self.on_file_command(payload)
        return 404, {"accepted": False, "error": "unknown_type"}

    def _invoke_route_command(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self._invoke_callback is None:
            return self._route_command(payload)
        return self._invoke_callback(lambda: self._route_command(payload))
