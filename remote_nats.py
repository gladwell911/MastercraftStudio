from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
import threading
from typing import Any

from remote_nats_protocol import (
    NatsSubjects,
    build_error_response,
    build_response_event,
    encode_payload,
    make_event_id,
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
        on_model_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_history_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_history_read: Callback | None = None,
        on_notes_changes: Callback | None = None,
        on_notes_bulk_docs: Callback | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
        invoke_callback: CallbackInvoker | None = None,
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
        self.on_model_list = on_model_list
        self.on_history_list = on_history_list
        self.on_history_read = on_history_read
        self.on_notes_changes = on_notes_changes
        self.on_notes_bulk_docs = on_notes_bulk_docs
        self._invoke_callback = invoke_callback
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
        try:
            status, body = await asyncio.to_thread(self._invoke_route_command, payload)
            event = build_response_event(
                request_id=request_id,
                status=status,
                body=body,
                chat_id=chat_id,
            )
        except Exception as exc:
            event = build_error_response(request_id, 500, str(exc) or "error")
        await self.publish_event(event)

    async def publish_event(self, payload: dict[str, Any]) -> None:
        if self.jetstream is None:
            return

        event = dict(payload)
        event_type = str(event.get("type") or "event")
        if not event.get("event_id"):
            event["event_id"] = make_event_id(event_type)
        await self.jetstream.publish(self.subjects.events, encode_payload(event))

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
        if command_type == "rename_chat" and callable(self.on_rename_chat):
            return self.on_rename_chat(payload)
        if command_type == "update_settings" and callable(self.on_update_settings):
            return self.on_update_settings(payload)
        if command_type == "history_list" and callable(self.on_history_list):
            return self.on_history_list()
        if command_type == "history_read" and callable(self.on_history_read):
            return self.on_history_read(payload)
        if command_type == "notes_changes" and callable(self.on_notes_changes):
            return self.on_notes_changes(payload)
        if command_type == "notes_bulk_docs" and callable(self.on_notes_bulk_docs):
            return self.on_notes_bulk_docs(payload)
        return 404, {"accepted": False, "error": "unknown_type"}

    def _invoke_route_command(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self._invoke_callback is None:
            return self._route_command(payload)
        return self._invoke_callback(lambda: self._route_command(payload))
