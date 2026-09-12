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
    transport = RemoteNatsTransport(
        pair_id=pair_id,
        token=token,
        jetstream=connection.jetstream(),
        durable_store=store,
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
            if fact is None:
                store.upsert_chat({"id": chat_id, "title": title})
                store.replace_turns(chat_id, [{"question": "", "answer_md": text}])
                message_id = store.resolve_canonical_message_by_turn(
                    chat_id, role="assistant", turn_index=0
                )
                fact = store.commit_message_notification_fact(
                    pair_id=pair_id,
                    domain="events",
                    chat_id=chat_id,
                    message_id=message_id,
                    notification_kind="assistant_final",
                    # Deliberately non-authoritative inputs: production store
                    # derivation must source both fields from canonical rows.
                    text="ignored fixture transport text",
                    chat_title="ignored fixture transport title",
                )
                fixture_facts[fixture_id] = fact
                await transport.drain_outbox()
            elif payload.get("replay") is True:
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
