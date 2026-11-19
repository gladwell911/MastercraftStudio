"""Kimi client event delivery and bounded UI-facing queue."""

from __future__ import annotations

from copy import deepcopy
from kimi_server_events import KimiEvent, _str, event_to_payload
from typing import Any


class KimiServerQueueMixin:
    def drain_pending_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        remaining = max(int(limit or 0), 0)
        drained: list[dict[str, Any]] = []
        if remaining == 0:
            return drained
        notify_pending = False
        with self._lock:
            while remaining and self._queue:
                entry = self._queue.popleft()
                drained.append(entry["message"])
                remaining -= 1
            if drained:
                if self._queue:
                    self._pending_notification = True
                    notify_pending = True
                else:
                    self._pending_notification = False
        if notify_pending and self.on_message is not None:
            self.on_message({"type": "messages_pending"})
        return drained

    def _enqueue_control(self, message: dict[str, Any]) -> None:
        notify = False
        with self._lock:
            self._queue.append({"kind": "message", "message": message})
            self._enforce_queue_limit_locked()
            if not self._pending_notification:
                self._pending_notification = True
                notify = True
        if notify and self.on_message is not None:
            self.on_message({"type": "messages_pending"})

    def _enqueue_event(self, event: KimiEvent) -> None:
        message = {"type": "event", "payload": {"event": event_to_payload(event)}}
        notify = False
        with self._lock:
            if event.type == "agent_message_delta":
                event_data = event.data if isinstance(event.data, dict) else {}
                agent_id = _str(event_data.get("agent_id") or event_data.get("agentId"))
                source_kind = _str(event_data.get("source_kind")).split(".", 1)[0]
                key = (event.thread_id, event.turn_id, event.item_id, event.display_kind, agent_id, source_kind,
                       event_data.get("transcript_source"))
                last_entry = self._queue[-1] if self._queue else None
                can_merge = bool(last_entry and last_entry.get("kind") == "delta" and last_entry.get("key") == key)
                if can_merge:
                    previous_event = ((last_entry.get("message") or {}).get("payload") or {}).get("event") or {}
                    previous_data = previous_event.get("data") if isinstance(previous_event.get("data"), dict) else {}
                    previous_offset = previous_data.get("offset")
                    incoming_offset = (event.data or {}).get("offset") if isinstance(event.data, dict) else None
                    previous_text = str(previous_event.get("text") or "")
                    # Preserve gaps, reverse fragments and conflicting bytes
                    # for the existing absolute-offset assembler.
                    if isinstance(previous_offset, int) and isinstance(incoming_offset, int):
                        relative = incoming_offset - previous_offset
                        overlap = min(max(0, len(previous_text) - relative), len(event.text))
                        can_merge = (0 <= relative <= len(previous_text)
                                     and previous_text[relative:relative + overlap] == event.text[:overlap])
                    elif previous_offset is not None or incoming_offset is not None:
                        can_merge = False
                if can_merge:
                    merged = deepcopy(last_entry["message"])
                    merged_event = (merged.get("payload") or {}).get("event") or {}
                    existing_text = str(merged_event.get("text") or "")
                    incoming_text = event.text
                    existing_data = merged_event.get("data") if isinstance(merged_event.get("data"), dict) else {}
                    incoming_data = event.data if isinstance(event.data, dict) else {}
                    for privacy_key, protected_value in (("private", True), ("hidden", True), ("non_disclosable", True), ("disclosable", False)):
                        if incoming_data.get(privacy_key) is protected_value:
                            existing_data[privacy_key] = protected_value
                    for privacy_key in ("visibility", "reasoning_visibility"):
                        if _str(incoming_data.get(privacy_key)).lower() in {"private", "hidden", "non_disclosable", "non-disclosable"}:
                            existing_data[privacy_key] = incoming_data[privacy_key]
                    start_offset = existing_data.get("offset")
                    incoming_offset = incoming_data.get("offset")
                    if isinstance(start_offset, int) and isinstance(incoming_offset, int):
                        relative_offset = incoming_offset - start_offset
                        if relative_offset < len(existing_text):
                            overlap = min(len(existing_text) - relative_offset, len(incoming_text))
                            if relative_offset >= 0 and existing_text[relative_offset:relative_offset + overlap] == incoming_text[:overlap]:
                                incoming_text = incoming_text[overlap:]
                    merged_event["text"] = existing_text + incoming_text
                    if "raw_text" in merged_event or event.raw_text:
                        existing_raw_text = str(merged_event.get("raw_text") or "")
                        raw_text = event.raw_text
                        if incoming_text != event.text and raw_text == event.text:
                            raw_text = incoming_text
                        merged_event["raw_text"] = existing_raw_text + raw_text
                    last_entry["message"] = merged
                else:
                    self._queue.append({"kind": "delta", "key": key, "message": message})
                    self._enforce_queue_limit_locked()
            else:
                self._queue.append({"kind": "message", "message": message})
                self._enforce_queue_limit_locked()
            if not self._pending_notification:
                self._pending_notification = True
                notify = True
        if notify and self.on_message is not None:
            self.on_message({"type": "messages_pending"})

    def _enforce_queue_limit_locked(self) -> None:
        overflow = len(self._queue) - self.queue_limit
        if overflow <= 0:
            return
        dropped = 0
        for _ in range(overflow):
            entry = self._queue.popleft()
            # A previous overflow warning carries its own dropped count; folding
            # it in keeps the cumulative total accurate instead of reporting
            # only this pass's overflow.
            if "dropped" in entry:
                dropped += int(entry["dropped"])
            else:
                dropped += 1
        self._queue.appendleft(
            {
                "kind": "message",
                "dropped": dropped,
                "message": {
                    "type": "event",
                    "payload": {
                        "event": event_to_payload(
                            KimiEvent(type="notification", display_kind="warning",
                                      text=f"kimi event queue overflow, dropped {dropped} oldest events")
                        )
                    },
                },
            }
        )
