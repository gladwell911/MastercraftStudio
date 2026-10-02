"""Ephemeral, owner-scoped subscriptions to the desktop's information rows."""

import hashlib
import threading
import time


class ChatInformationSubscriptions:
    def __init__(self, frame):
        self.frame = frame
        self.subscriptions = {}
        self.requests = {}
        self.generation = 0
        self.snapshots = {}

    def prune(self):
        now = time.monotonic()
        for key, subscription in list(self.subscriptions.items()):
            if now - subscription["touched"] > 120:
                self.subscriptions.pop(key, None)

    def interested(self, chat_id):
        self.prune()
        if any(s["chat_id"] == chat_id for s in self.subscriptions.values()):
            return True
        dialog = getattr(self.frame, "_chat_information_dialog", None)
        return dialog is not None and not dialog.IsBeingDeleted() and dialog.identity[0] == chat_id

    def snapshot(self, chat, model, subscription):
        frame = self.frame
        rows = frame._chat_information_rows(chat, model)
        identity = frame._chat_information_identity(chat, model)
        provider = "codex" if model.startswith("codex/") else "kimi"
        owner = frame._information_cache_owner(chat, provider)
        # Account identity is only a comparison token on the phone.
        identity = list(identity)
        identity[3] = hashlib.sha256(str(owner[2]).encode()).hexdigest()
        key = identity[0]
        previous = self.snapshots.get(key)
        revision = previous[2] if previous else 0
        owner_identity = tuple(identity) + (int(owner[3]),)
        if previous is None or previous[:2] != (owner_identity, tuple(rows)):
            revision += 1
            self.snapshots[key] = (owner_identity, tuple(rows), revision)
        return {"chat_id": key, "subscription_id": subscription["subscription_id"],
                "generation": subscription["generation"], "identity": identity,
                "owner_generation": int(owner[3]), "revision": revision, "rows": rows}

    def command(self, payload):
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        chat_id = str(payload.get("chat_id") or "").strip()
        key = str(body.get("subscription_id") or "").strip()
        generation = body.get("generation")
        if not key or len(key) > 200 or isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            return 400, {"error": "invalid_information_subscription"}
        self.prune()
        previous = self.subscriptions.get(key)
        if previous is not None and generation < previous["generation"]:
            return 409, {"error": "stale_information_subscription"}
        if body.get("release"):
            if previous and previous["chat_id"] == chat_id:
                self.subscriptions.pop(key, None)
            return 200, {"chat_id": chat_id, "subscription_id": key, "generation": generation, "released": True}
        owner = self.frame._chat_information_owner_for_id(chat_id)
        if owner is None:
            return 404, {"error": "unsupported_chat_information"}
        subscription = {"chat_id": chat_id, "subscription_id": key,
                        "generation": generation, "touched": time.monotonic()}
        if previous is not None and previous["chat_id"] == chat_id and previous["generation"] == generation:
            subscription["sent"] = previous.get("sent")
        self.subscriptions[key] = subscription
        result = self.snapshot(*owner, subscription)
        if subscription.get("sent") is None:
            subscription["sent"] = (tuple(result["identity"]), result["owner_generation"], result["revision"])
        self.request(*owner, context_only=bool(body.get("context_only")))
        return 200, result

    def changed(self, chat):
        if not isinstance(chat, dict) or not self.interested(str(chat.get("id") or "")):
            return
        owner = self.frame._chat_information_owner_for_id(str(chat.get("id") or ""))
        if owner is None:
            return
        owner_changed = False
        for subscription in list(self.subscriptions.values()):
            if subscription["chat_id"] == str(chat.get("id") or ""):
                snapshot = self.snapshot(*owner, subscription)
                marker = (tuple(snapshot["identity"]), snapshot["owner_generation"], snapshot["revision"])
                sent = subscription.get("sent")
                if sent is not None and sent[:2] != marker[:2]:
                    owner_changed = True
                if subscription.get("sent") != marker:
                    subscription["sent"] = marker
                    self.frame._publish_remote_nats_event(dict(snapshot, type="chat_information_changed"))
        if owner_changed:
            self.request(*owner)

    def request(self, chat, model, *, context_only=False):
        chat_id = str(chat.get("id") or "")
        if not self.interested(chat_id):
            return False
        identity = self.frame._chat_information_identity(chat, model)
        provider = "codex" if model.startswith("codex/") else "kimi"
        owner = self.frame._information_cache_owner(chat, provider)
        pending = self.requests.get(chat_id)
        if pending and pending["owner"] == owner and time.monotonic() - pending["started"] < 30:
            if not context_only and pending["context_only"]:
                pending["full_refresh"] = True
            return True
        # Share a desktop read already in flight. Its completion calls changed().
        desktop = getattr(self.frame, "_chat_information_request", None)
        if provider == "codex" and desktop and desktop[2] == identity:
            started = getattr(self.frame, "_codex_information_started", 0)
            fresh = (time.monotonic() - started < 30
                     and getattr(self.frame, "_codex_information_context_generation", 0) == owner[3])
            if not fresh:
                self.frame._chat_information_request = None
                self.frame._codex_information_full_refresh = None
                desktop = None
        if provider == "codex" and desktop and desktop[2] == identity:
            desktop_context = getattr(self.frame, "_codex_information_context_only", False)
            self.requests[chat_id] = {"identity": identity, "owner": owner, "generation": desktop[1],
                "context_only": desktop_context, "started": getattr(self.frame, "_codex_information_started", time.monotonic()),
                "revision": getattr(self.frame, "_codex_information_revision", 0),
                "full_refresh": not context_only and desktop_context, "desktop": True}
            return True
        kimi_pending = self.frame._kimi_information_requests.get(chat_id)
        if provider == "kimi" and kimi_pending and kimi_pending.get("identity") == identity:
            fresh = (time.monotonic() - kimi_pending.get("started", 0) < 30
                     and tuple(kimi_pending.get("cache_owner") or ()) == owner)
            if not fresh:
                self.frame._kimi_information_requests.pop(chat_id, None)
                kimi_pending = None
        if provider == "kimi" and kimi_pending and kimi_pending.get("identity") == identity:
            kimi_pending["visible_only"] = False
            kimi_pending["scoped_observed"] = True
            desktop_context = bool(kimi_pending.get("context_only"))
            request = {"identity": identity, "owner": owner, "generation": kimi_pending["generation"],
                "context_only": desktop_context, "started": kimi_pending.get("started", time.monotonic()),
                "full_refresh": not context_only and desktop_context}
            self.requests[chat_id] = request
            if not desktop_context:
                self.frame._request_kimi_quota(chat, model, scoped=True)
            return True
        self.generation -= 1
        request = {"identity": identity, "owner": owner, "generation": self.generation,
                   "context_only": context_only, "started": time.monotonic(),
                   "revision": int(chat.get(provider + "_usage_revision" if provider == "codex" else "kimi_context_revision") or 0)}
        self.requests[chat_id] = request
        if provider == "kimi":
            if identity[2]:
                self.frame._request_kimi_chat_information(chat, model, context_only=context_only, scoped=True)
            else:
                request["usage_done"] = True
            if not context_only:
                self.frame._request_kimi_quota(chat, model, scoped=True)
            elif not identity[2]:
                self.finished(chat_id, request)
            return True

        try:
            client = self.frame._get_or_create_codex_client(chat_id, model)
        except Exception:
            self.apply_codex(chat_id, {}, {"identity": list(identity), "generation": request["generation"],
                "context_only": context_only, "usage_error": True, "account_error": True, "rate_limits_error": True})
            return True

        def read():
            try:
                client.start()
                client.read_chat_information(chat_id=chat_id, model=model, identity=list(identity),
                                             generation=request["generation"], context_only=context_only)
            except Exception:
                payload = {"identity": list(identity), "generation": request["generation"],
                           "context_only": context_only, "usage_error": True,
                           "account_error": True, "rate_limits_error": True}
                self.frame._call_after_if_alive(self.apply_codex, chat_id, {}, payload)
        threading.Thread(target=read, daemon=True, name="scoped-chat-information").start()
        return True

    def apply_codex(self, chat_id, message, payload):
        request = self.requests.get(chat_id)
        if not request or payload.get("generation") != request["generation"]:
            return False
        if request.get("desktop"):
            self.frame._chat_information_request = None
            self.frame._codex_information_full_refresh = None
        owner = self.frame._chat_information_owner_for_id(chat_id)
        if owner and self.interested(chat_id) and list(request["identity"]) == list(payload.get("identity") or []):
            if self.frame._information_cache_owner(owner[0], "codex") == request["owner"]:
                self.frame._apply_codex_chat_information(chat_id, message, payload, scoped=request)
        self.finished(chat_id, request)
        return True

    def finished(self, chat_id, request=None):
        pending = self.requests.get(chat_id)
        if pending is None or request is not None and pending is not request:
            return
        self.requests.pop(chat_id, None)
        owner = self.frame._chat_information_owner_for_id(chat_id)
        if owner:
            self.changed(owner[0])
            if pending.get("full_refresh") and self.interested(chat_id):
                self.request(*owner)
