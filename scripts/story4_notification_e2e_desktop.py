"""Connected Story 4 notification handshake harness using MC production code.

This intentionally uses a core NATS subscription for commands so it can run
beside an installed desktop build without competing for that build's durable
consumer.  Responses and the seeded pre-handshake fact still go through the
production ``RemoteNatsTransport`` and ``ChatStore`` paths.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

import nats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_store import ChatStore
from remote_nats import RemoteNatsTransport


def update_fixture_chat_title(store: ChatStore, chat_id: str, title: str) -> None:
    chat = store.load_chat(chat_id)
    now = time.time()
    if chat is None:
        store.upsert_chat({"id": chat_id, "title": title, "created_at": now,
                           "updated_at": now, "title_revision": 1,
                           "title_updated_at": now, "title_source": "default"})
        return
    if chat.get("title") == title:
        return
    chat.update(title=title, title_manual=True, title_source="manual",
                title_updated_at=max(now, float(chat.get("title_updated_at") or 0) + 0.000001),
                title_revision=int(chat.get("title_revision") or 0) + 1,
                updated_at=max(now, float(chat.get("updated_at") or 0)))
    store.upsert_chat(chat)


def prepare_fixture_title(store: ChatStore, payload: dict, fact: dict | None) -> str | None:
    chat_id = str(payload.get("chat_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if fact is None:
        kind = str(payload.get("kind") or "assistant_final")
        if kind not in {"user_message", "assistant_final"}:
            return None
        update_fixture_chat_title(store, chat_id, title)
        return kind
    if chat_id != fact.get("chat_id"):
        return None
    if payload.get("replay") is True and payload.get("rename") is True:
        update_fixture_chat_title(store, chat_id, title)
    return str(fact["kind"])


async def run() -> None:
    endpoint = os.environ.get(
        "NATS_E2E_ENDPOINT", "wss://rc.tingyou.cc/nats"
    ).strip()
    token = os.environ.get(
        "NATS_E2E_TOKEN", "h9k2m7p4q8x1z6v3t5n9c2r7d4s8j1f6"
    ).strip()
    pair_id = os.environ.get(
        "NATS_E2E_PAIR_ID", "default"
    ).strip()

    database_dir = Path(tempfile.mkdtemp(prefix="story4-mc-e2e-"))
    store = ChatStore(database_dir / "story4.db")
    store.initialize()
    seed_chat_id = f"story4-pre-handshake-owner-{uuid.uuid4().hex}"
    store.upsert_chat({"id": seed_chat_id, "title": "Story 4 pre-handshake seed"})
    store.replace_turns(seed_chat_id, [{"question": "", "answer_md": "pre-handshake watermark seed"}])
    seed_message_id = store.resolve_canonical_message_by_turn(
        seed_chat_id, role="assistant", turn_index=0
    )
    seed = store.commit_message_notification_fact(
        pair_id=pair_id,
        domain="events",
        chat_id=seed_chat_id,
        message_id=seed_message_id,
        notification_kind="assistant_final",
        text="ignored test input",
        chat_title="ignored test input",
    )

    connection = await nats.connect(endpoint, token=token)

    def history_chat(chat: dict) -> dict:
        return {**chat, "chat_id": chat["id"], "running": False,
                "request_kind": "", "current": False, "active": False,
                "turns": [{**turn, "answer": turn.get("answer_md", "")}
                          for turn in chat.get("turns", [])]}

    def history_list() -> tuple[int, dict]:
        return 200, {"accepted": True, "chats": [history_chat(chat)
                                                for chat in store.list_chat_summaries()]}

    def history_read(payload: dict) -> tuple[int, dict]:
        chat = store.load_chat(str(payload.get("chat_id") or ""))
        if chat is None:
            return 404, {"accepted": False, "error": "chat_not_found"}
        return 200, {"accepted": True, "chat": history_chat(chat),
                     "has_more": False, "oldest_cursor": ""}

    transport = RemoteNatsTransport(
        pair_id=pair_id,
        token=token,
        jetstream=connection.jetstream(),
        durable_store=store,
        on_history_list=history_list,
        on_history_read=history_read,
    )
    await transport.initialize_streams()
    published = await transport.drain_outbox()

    fixture_facts: dict[str, dict] = {}

    async def handle_command(message: object) -> None:
        data = getattr(message, "data", b"")
        try:
            payload = json.loads(bytes(data).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if str(payload.get("type") or "").lower() == "story4_notification_fixture":
            fixture_id = str(payload.get("fixture_id") or "").strip()
            chat_id = str(payload.get("chat_id") or "").strip()
            title = str(payload.get("title") or "").strip()
            text = str(payload.get("text") or "").strip()
            if not fixture_id or not chat_id or not title or not text:
                return
            fact = fixture_facts.get(fixture_id)
            kind = prepare_fixture_title(store, payload, fact)
            if kind is None:
                return
            if fact is None:
                turns = store.load_turns(chat_id)
                turn_index = len(turns)
                store.replace_turns_from(chat_id, [{
                    "question": text if kind == "user_message" else "",
                    "answer_md": text if kind == "assistant_final" else "",
                }], start_index=turn_index)
                message_id = store.resolve_canonical_message_by_turn(
                    chat_id, role="user" if kind == "user_message" else "assistant", turn_index=turn_index
                )
                fact = store.commit_message_notification_fact(
                    pair_id=pair_id,
                    domain="events",
                    chat_id=chat_id,
                    message_id=message_id,
                    notification_kind=kind,
                    # Deliberately non-authoritative inputs: production store
                    # derivation must source both fields from canonical rows.
                    text="ignored fixture transport text",
                    chat_title="ignored fixture transport title",
                )
                fixture_facts[fixture_id] = fact
                await transport.drain_outbox()
            elif payload.get("replay") is True:
                replay = store.commit_message_notification_fact(
                    pair_id=pair_id, domain="events", chat_id=chat_id,
                    message_id=fact["body"]["message_id"], notification_kind=fact["kind"],
                    text=text, chat_title=title,
                )
                assert replay == fact, "committed notification envelope changed on replay"
                await connection.publish(
                    transport.subjects.events,
                    json.dumps(fact, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                )
                await connection.flush()
            print(
                "STORY4_MC_EVIDENCE authoritative_fixture "
                f"fixture={fixture_id} event={fact['event_id']} sequence={fact['sync_sequence']} replay={payload.get('replay') is True}",
                flush=True,
            )
            return
        await transport.handle_command(payload)
        if str(payload.get("type") or "").lower() == "hello":
            print(
                "STORY4_MC_EVIDENCE handshake "
                f"device={payload.get('device_id')} session={payload.get('session_id')}",
                flush=True,
            )

    subscription = await connection.subscribe(
        transport.subjects.commands,
        cb=handle_command,
    )
    await connection.flush()
    print(
        "STORY4_MC_EVIDENCE ready "
        f"pair={pair_id} seed_event={seed['event_id']} "
        f"high_sync_sequence={seed['sync_sequence']} published={published} "
        f"database={database_dir}",
        flush=True,
    )

    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await subscription.unsubscribe()
        await connection.drain()
        print(f"STORY4_MC_EVIDENCE stopped at={time.time()}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
