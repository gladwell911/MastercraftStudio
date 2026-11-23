"""Kimi Code 2.6.3 Server API projection into MC's existing V2 contract."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import quote

from context_usage import native_token_count
from kimi_server_events import KimiEvent, KimiServerError, _provider_origin_timestamp, map_session_event
from kimi_transcript import Transcript


def _id(value):
    return value if isinstance(value, str) and value else ""


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _usage(raw):
    if not isinstance(raw, dict):
        return {}
    usage = {
        "inputOther": native_token_count(raw.get("input_other")),
        "inputCacheRead": native_token_count(raw.get("input_cache_read")),
        "inputCacheCreation": native_token_count(raw.get("input_cache_creation")),
        "output": native_token_count(raw.get("output")),
    }
    if None not in usage.values():
        usage.update(inputTokens=usage["inputOther"] + usage["inputCacheCreation"],
                     cachedTokens=usage["inputCacheRead"], outputTokens=usage["output"])
    return usage


def _prompt_ids(item):
    values = item.get("prompt_ids")
    return [value for value in values if _id(value)] if isinstance(values, list) else []


def _public(item):
    return (not any(item.get(key) for key in ("hidden", "private", "isPrivate", "non_disclosable", "nonDisclosable"))
            and item.get("disclosable") is not False and item.get("isDisclosable") is not False
            and item.get("visibility") not in ("private", "hidden", "non_disclosable", "non-disclosable"))


def project_turn(turn):
    turn_id = _id(turn.get("turn_id"))
    if not turn_id or turn.get("kind") != "agent":
        return None
    items = _rows(turn.get("items"))
    openers = [item for item in items if item.get("type") == "user_message" and item.get("placement") == "opener"
               and _object(item.get("origin")).get("kind") == "user" and len(_prompt_ids(item)) == 1]
    prompt_id = _prompt_ids(openers[0])[0] if len(openers) == 1 else ""
    status = turn.get("status")
    state = status if status in ("running", "queued", "blocked") else ""
    if status == "ended":
        outcome, reason = turn.get("outcome"), turn.get("end_reason")
        if reason == "user_cancelled" or outcome == "cancelled":
            state = "cancelled"
        elif reason == "error" or outcome in ("error", "failed"):
            state = "failed"
        elif outcome == "completed" and not reason:
            state = "completed"
    steps = {}
    for step in _rows(turn.get("steps")):
        index = step.get("index")
        if type(index) is int:
            steps[index] = {
                "stepId": str(index), "ordinal": index,
                "state": step.get("outcome") or "running",
                "usage": _usage(step.get("usage")), "frames": [],
            }
    for item in items:
        index = item.get("step_index")
        identity = _id(item.get("item_id"))
        if type(index) is not int or not identity:
            continue
        step = steps.get(index)
        if step is None and state == "running":
            step = steps.setdefault(index, {"stepId": str(index), "ordinal": index, "state": "running", "frames": []})
        if step is None:
            continue
        kind = item.get("type")
        if kind == "tool_call":
            step["frames"].append({"kind": "tool", "frameId": identity})
        elif kind in ("assistant_message", "thinking", "reasoning", "user_message") and _public(item):
            text = item.get("text")
            if not isinstance(text, str):
                continue
            if state == "completed" and kind == "assistant_message" and (
                item.get("status") != "ended" or item.get("outcome") != "completed"
            ):
                continue
            step["frames"].append({
                "kind": "thinking" if kind in ("thinking", "reasoning") else "text",
                "role": "user" if kind == "user_message" else "assistant",
                "frameId": identity, "text": text, "promptIds": _prompt_ids(item),
                "origin": item.get("origin"),
            })
    error = turn.get("error")
    return {
        "kind": "turn", "turnId": turn_id, "ordinal": turn.get("ordinal"),
        "triggerPromptId": prompt_id, "state": state, "steps": list(steps.values()),
        "startedAt": turn.get("started_at"), "endedAt": turn.get("ended_at"),
        "prompt": openers[0].get("text", "") if len(openers) == 1 and _public(openers[0]) else "",
        "origin": openers[0].get("origin") if len(openers) == 1 else None,
        "usage": _usage(turn.get("usage")),
        "error": error.get("message") if isinstance(error, dict) else None,
    }


class KimiServerApiMixin:
    def get_transcript(self, session_id, *, before_turn=None, timeout=None, agent_id="main"):
        if not self._server_api:
            return super().get_transcript(session_id, before_turn=before_turn, timeout=timeout, agent_id=agent_id)
        sid, agent = quote(session_id, safe=""), quote(agent_id, safe="")
        params = {"page_size": 100}
        if before_turn is not None:
            params["before_ordinal"] = before_turn
        page = self._request_data("GET", f"/api/v1/sessions/{sid}/agents/{agent}/turns", params=params, timeout=timeout)
        raw = page.get("turns")
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            raise KimiServerError("Kimi Server API returned invalid turn history")
        ordinals = [row["ordinal"] for row in raw if type(row.get("ordinal")) is int]
        with self._lifecycle_lock:
            turns = [turn for row in raw
                     if (turn := project_turn(self._server_api_filter_items(session_id, row))) is not None]
        turns.sort(key=lambda turn: turn["ordinal"] if type(turn["ordinal"]) is int else -1)
        return {"agent_id": agent_id, "items": turns, "has_more": page.get("has_more", False),
                "next_cursor": str(min(ordinals)) if ordinals else None}

    def _handle_server_api_frame(self, message, ws, generation):
        kind = message.get("type")
        if kind == "ping":
            return None  # shared heartbeat send and socket validation
        if kind == "response":
            request_id = _id(message.get("request_id"))
            with self._lifecycle_lock:
                sessions = self._subscribe_requests.get(request_id, ())
                if self._v2_request_generations.get(request_id) != self._ws_generation or not sessions:
                    return True
                ok = type(message.get("code")) is int and message["code"] == 0
                ack = {"id": request_id, "payload": {"accepted": list(sessions) if ok else []}}
                try:
                    self._handle_ack(ack, generation=generation)
                except KimiServerError as exc:
                    self._invalidate_ws(ws, str(exc), recover=True, generation=generation)
                    return False
            return True
        sid = _id(message.get("session_id"))
        agent = _id(message.get("agent_id"))
        with self._lifecycle_lock:
            if self._closed or self._ws is not ws or generation != self._ws_generation:
                return False
            if not sid or self._v2_ready_generations.get(sid) != generation or agent != "main":
                return True
            if kind == "usage":
                self._consume_v2_usage_window(message, ws=ws, generation=generation)
            elif kind == "interaction":
                self._server_api_interaction(sid, agent, message)
            elif kind == "turns":
                for turn in _rows(message.get("turns")):
                    if turn.get("status") == "running" and _id(turn.get("turn_id")):
                        self._server_api_snapshot(sid, turn)
            elif kind in ("turn.started", "item.started", "item.updated", "item.delta", "item.ended", "item.removed"):
                self._server_api_stream(sid, message)
        if kind == "turn.ended":
            self._server_api_terminal(sid, _object(message.get("turn")), ws, generation)
        return True

    def _server_api_check_pending(self, sid, identity, agent, kind):
        key = (sid, agent)
        timeout = min(self.rest_timeout, self.ws_connect_timeout, 2.0)
        with self._lifecycle_lock:
            generation, ws = self._ws_generation, self._ws
            if not self._server_api_socket_current(sid, ws, generation) or key in self._server_api_pending_checks:
                return ""
            request_id = self._next_id()
            event = Event()
            check = self._server_api_pending_checks[key] = (request_id, event)
            self._subscribe_requests[request_id] = {sid}
            self._v2_request_generations[request_id] = generation
        try:
            self._send_ws({"type": "subscribe", "request_id": request_id, "session_id": sid,
                           "agent_ids": [agent]}, send_timeout=timeout)
            event.wait(timeout)
        except KimiServerError:
            return ""
        finally:
            with self._lifecycle_lock:
                observed = None
                if self._server_api_pending_checks.get(key) is check:
                    self._server_api_pending_checks.pop(key)
                    observed = self._server_api_pending.pop(key, None)
                self._subscribe_requests.pop(request_id, None)
                self._v2_request_generations.pop(request_id, None)
        with self._lifecycle_lock:
            if not self._server_api_socket_current(sid, ws, generation):
                return ""
            ended = self._server_api_interactions.get((sid, identity, agent))
            if ended is not None:
                return ended or "ended"
            return "pending" if observed == (identity, kind) else ""

    def _server_api_interaction(self, sid, agent, message):
        identity, status = _id(message.get("interaction_id")), message.get("status")
        kind, outcome = message.get("kind"), message.get("outcome")
        if not identity or kind not in ("approval", "question") or status not in ("pending", "ended"):
            return
        known_outcome = outcome if outcome in ("approved", "rejected", "cancelled", "answered", "dismissed") else ""
        pending_key = (sid, agent)
        check = self._server_api_pending_checks.get(pending_key)
        if check is not None and check[0] not in self._v2_request_generations:
            if status == "pending":
                self._server_api_pending[pending_key] = (identity, kind)
            elif self._server_api_pending.get(pending_key) == (identity, kind):
                self._server_api_pending.pop(pending_key)
            check[1].set()
        if status == "ended":
            key = (sid, identity, agent)
            self._server_api_interactions[key] = known_outcome
            if len(self._server_api_interactions) > 512:
                self._server_api_interactions.pop(next(iter(self._server_api_interactions)))
        self._enqueue_event(KimiEvent(
            type="server_request" if status == "pending" else "server_request_ended",
            thread_id=sid, session_id=sid, request_id=identity, method=kind, status=status,
            params=_object(message.get("request")),
            origin_timestamp=_provider_origin_timestamp({"timestamp": message.get("event_created_at")}),
            data={"source_kind": "interaction", "agent_id": agent, "outcome": outcome},
        ))

    def _server_api_filter_items(self, sid, turn):
        restrictions = self._server_api_item_restrictions.get((sid, turn.get("turn_id")), {})
        if not restrictions:
            return turn
        return {**turn, "items": [
            dict(item, hidden=True) if restrictions.get(_id(item.get("item_id"))) == "hidden" else item
            for item in _rows(turn.get("items")) if restrictions.get(_id(item.get("item_id"))) != "removed"
        ]}

    def _server_api_restrict_item(self, sid, turn_id, identity, restriction):
        key = (sid, turn_id)
        restrictions = self._server_api_item_restrictions.setdefault(key, {})
        if restrictions.get(identity) != "removed":
            restrictions[identity] = restriction
        if len(self._server_api_item_restrictions) > 512:
            self._server_api_item_restrictions.pop(next(iter(self._server_api_item_restrictions)))

    def _server_api_snapshot(self, sid, wire):
        turn_id = wire["turn_id"]
        key = (sid, turn_id)
        previous = self._server_api_turns.get(key) or self._server_api_previous_turns.get(key, {})
        old_items = previous.get("wire_items", {})
        if not isinstance(wire.get("items"), list):
            self._server_api_turns[key] = {**wire, "wire_items": old_items}
            return
        incoming = {item["item_id"]: item for item in _rows(wire["items"]) if _id(item.get("item_id"))}
        if any(not isinstance(item, dict) or not _id(item.get("item_id")) for item in wire["items"]):
            return
        for identity in old_items.keys() - incoming.keys():
            self._server_api_restrict_item(sid, turn_id, identity, "removed")
        for identity, item in incoming.items():
            if not _public(item):
                self._server_api_restrict_item(sid, turn_id, identity, "hidden")
        filtered = self._server_api_filter_items(sid, wire)
        items = {item["item_id"]: item for item in filtered["items"]}
        self._server_api_previous_turns.pop(key, None)
        self._server_api_turns[key] = {**wire, "wire_items": items}
        self._server_api_publish_execution(sid, wire, old_items, items)

    def _server_api_publish_execution(self, sid, turn, old_items, items):
        projected = project_turn({**turn, "items": list(items.values())})
        old = project_turn({**turn, "items": list(old_items.values())})
        if projected is None:
            return ""
        prompt = projected["triggerPromptId"]
        old_prompt = old["triggerPromptId"]
        turn_id = turn["turn_id"]
        for identity, previous in old_items.items():
            item = items.get(identity, {})
            old_text, new_text = previous.get("text"), item.get("text")
            if old_prompt and (old_prompt != prompt or not item or _public(previous) and not _public(item)
                               or item.get("type") != previous.get("type")
                               or item.get("step_index") != previous.get("step_index")
                               or isinstance(old_text, str)
                               and not (isinstance(new_text, str) and new_text.startswith(old_text))):
                for frame_key in tuple(self._v2_frame_texts):
                    if frame_key[:3] == (sid, turn_id, old_prompt) and frame_key[-1] == identity:
                        self._v2_frame_texts.pop(frame_key)
                self._enqueue_event(KimiEvent(type="execution_item_withdrawn", thread_id=sid, turn_id=turn_id,
                    item_id=identity, data={"source_kind": "item.withdrawn", "agent_id": "main",
                                           "prompt_id": old_prompt, "authoritative": True}))
        transcript = Transcript()
        transcript.reset({"items": [projected]})
        for event in self._v2_execution_events(sid, transcript, "ws"):
            self._enqueue_event(event)
        return prompt

    def _server_api_stream(self, sid, message):
        kind = message["type"]
        if kind == "turn.started":
            turn = _object(message.get("turn"))
            turn_id = _id(turn.get("turn_id"))
            if turn_id:
                self._server_api_turns.setdefault((sid, turn_id), {**turn, "wire_items": {}})
            return
        turn_id = _id(message.get("turn_id"))
        turn = self._server_api_turns.get((sid, turn_id))
        if turn is None:
            return
        items = turn["wire_items"]
        restrictions = self._server_api_item_restrictions.get((sid, turn_id), {})
        old_items = dict(items)
        previous = {}
        if kind in {"item.updated", "item.removed"}:
            identity = _id(message.get("item_id"))
            if not identity or restrictions.get(identity) == "removed":
                return
            previous = items.get(identity, {})
            if kind == "item.removed":
                self._server_api_restrict_item(sid, turn_id, identity, "removed")
                items.pop(identity, None)
                item = {}
            elif previous and isinstance(message.get("set"), dict):
                item = items[identity] = {**previous, **message["set"], "item_id": identity}
            else:
                return
        elif kind == "item.delta":
            item = items.get(_id(message.get("item_id")))
            delta = message.get("delta")
            if item is None or message.get("field") != "text" or not isinstance(delta, str):
                return
            previous = item.get("text")
            if previous is not None and not isinstance(previous, str):
                return
            items[item["item_id"]] = item = {**item, "text": (previous or "") + delta}
        else:
            item = message.get("item")
            if not isinstance(item, dict) or not _id(item.get("item_id")):
                return
            identity = item["item_id"]
            if restrictions.get(identity) == "removed":
                return
            previous = items.get(identity, {})
            items[identity] = item = {**previous, **item}
        if kind != "item.delta" and item:
            if not _public(item):
                self._server_api_restrict_item(sid, turn_id, identity, "hidden")
            if self._server_api_item_restrictions.get((sid, turn_id), {}).get(identity) == "hidden":
                items[identity] = item = dict(item, hidden=True)
        prompt = self._server_api_publish_execution(sid, turn, old_items, items)
        if kind in {"item.started", "item.updated", "item.ended"} and item.get("type") == "tool_call" and _public(item):
            if prompt:
                finished = kind == "item.ended" or item.get("status") == "ended"
                source = "tool.result" if finished else "tool.call.started"
                arguments, display = _object(item.get("input")), _object(item.get("display"))
                event = map_session_event({
                    "type": source, "session_id": sid,
                    "payload": {
                        "turnId": turn_id, "promptId": prompt, "agentId": "main",
                        "toolCallId": item["item_id"], "name": item.get("name"),
                        "display": display, "command": display.get("command"),
                        "status": item.get("outcome"), "output": item.get("error") or item.get("output"),
                        "created_at": item.get("created_at"), "args": arguments,
                    },
                }, step_scope={"step_id": str(item.get("step_index", ""))})
                if event is not None:
                    event.data["authoritative"] = True
                    self._enqueue_event(event)

    def _server_api_terminal(self, sid, wire_turn, ws, generation):
        turn_id = _id(wire_turn.get("turn_id"))
        if not turn_id:
            return
        with self._lifecycle_lock:
            if not self._server_api_socket_current(sid, ws, generation):
                return
            previous = self._server_api_previous_turns.pop((sid, turn_id), None)
            cached = self._server_api_turns.pop((sid, turn_id), None) or previous
            projected = project_turn({**cached, "items": list(cached["wire_items"].values())}) if cached else None
            prompt = projected["triggerPromptId"] if projected else ""
            job = (generation, sid, turn_id)
            if job in self._server_api_terminal_jobs:
                return
            if len(self._server_api_terminal_jobs) >= 16:
                self._enqueue_control({"type": "transcript_recheck", "payload": {"session_ids": [sid]}})
                return
            if self._server_api_terminal_pool is None:
                self._server_api_terminal_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kimi-terminal")
            self._server_api_terminal_jobs.add(job)
            future = self._server_api_terminal_pool.submit(self._server_api_verify_terminal, job, ws, prompt)
            future.add_done_callback(lambda done: self._server_api_finish_terminal_job(job, ws, done))

    def _server_api_finish_terminal_job(self, job, ws, future):
        generation, sid, _ = job
        with self._lifecycle_lock:
            self._server_api_terminal_jobs.discard(job)
            if (not future.cancelled() and future.exception() is not None
                    and self._server_api_socket_current(sid, ws, generation)):
                self._enqueue_control({"type": "transcript_recheck", "payload": {"session_ids": [sid]}})

    def _server_api_socket_current(self, sid, ws, generation):
        return (ws is not None and not self._closed and self._ws is ws and generation == self._ws_generation
                and self._v2_ready_generations.get(sid) == generation)

    def _server_api_verify_terminal(self, job, ws, prompt):
        generation, sid, turn_id = job
        with self._lifecycle_lock:
            if not self._server_api_socket_current(sid, ws, generation):
                return
        try:
            page = self.get_transcript(sid, timeout=min(self.rest_timeout, 3.0))
        except KimiServerError:
            page = {}
        transcript = Transcript()
        transcript.reset({"items": [turn for turn in page.get("items", []) if turn["turnId"] == turn_id
                                    and (not prompt or turn["triggerPromptId"] == prompt)]})
        results = transcript.results()
        with self._lifecycle_lock:
            if not self._server_api_socket_current(sid, ws, generation):
                return
            if not results:
                self._enqueue_control({"type": "transcript_recheck", "payload": {"session_ids": [sid]}})
                return
            self._record_v2_transcript_usage(sid, transcript)
            for turn, text in results:
                key = (sid, turn["triggerPromptId"], turn_id)
                if key not in self._v2_completed:
                    self._enqueue_event(self._v2_final_event(sid, turn, text))
                self._retire_v2_turn(sid, turn)
