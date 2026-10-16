import asyncio
import json
import secrets
import socket
import sys
import tempfile
from pathlib import Path

import nats
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy
from chat_store import ChatStore
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from remote_nats_protocol import rpc_reply_subject


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def verify(directory, config):
    store = ChatStore(directory / 'chat.db')
    store.initialize()
    store.upsert_chat({'id': 'chat', 'title': 'Fixture'})
    store.replace_turns('chat', [{'question': 'fixture', 'answer_md': 'reply', 'request_status': 'done'}])
    transport = RemoteNatsTransport(pair_id='fixture', token=config.token,
        desktop_password=config.desktop_password, durable_store=store)
    mobile = None
    proxy_server = None
    proxy_tasks = set()
    wire_counts = {"mobile_to_broker": 0, "broker_to_mobile": 0}
    async def proxy(reader, writer):
        task = asyncio.current_task()
        proxy_tasks.add(task)
        remote_writer = None
        try:
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", config.port)
            async def copy(source, target, direction):
                while data := await source.read(65536):
                    wire_counts[direction] += len(data)
                    target.write(data)
                    await target.drain()
                target.close()
            await asyncio.gather(copy(reader, remote_writer, "mobile_to_broker"),
                                 copy(remote_reader, writer, "broker_to_mobile"))
        finally:
            writer.close()
            if remote_writer is not None:
                remote_writer.close()
            proxy_tasks.discard(task)
    errors = []
    async def error_cb(error):
        errors.append(type(error).__name__)
    try:
        await transport.start(f'nats://127.0.0.1:{config.port}')
        admin = transport._nats_client
        js = admin.jetstream()
        # Old retained data exists, but a new client never subscribes to it.
        async def seed(worker):
            for i in range(worker, 100000, 32):
                await js.publish('zgwd.fixture.events', json.dumps({'type': 'obsolete', 'i': i}).encode())
        await asyncio.gather(*(seed(i) for i in range(32)))
        await js.add_consumer('ZGWD_EVENTS_fixture', ConsumerConfig(durable_name='old-backlog',
            deliver_policy=DeliverPolicy.BY_START_SEQUENCE, opt_start_seq=60001,
            ack_policy=AckPolicy.EXPLICIT, filter_subject='zgwd.fixture.events'))
        old_before = await js.stream_info('ZGWD_EVENTS_fixture')
        old_consumer = await js.consumer_info('ZGWD_EVENTS_fixture', 'old-backlog')
        assert old_before.state.messages == 100000 and old_consumer.num_pending == 40000
        proxy_server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        mobile = await nats.connect(f'nats://127.0.0.1:{proxy_port}', user='mobile',
            password=config.token, error_cb=error_cb, max_reconnect_attempts=0)
        denied = False
        try:
            await mobile.jetstream(timeout=0.5).update_stream(name='ZGWD_NOTIFY_fixture',
                subjects=['zgwd.fixture.notify'], max_msgs=-1)
        except Exception:
            denied = True
        assert denied, 'mobile unexpectedly has stream mutation permission'
        policy = (await js.stream_info('ZGWD_NOTIFY_fixture')).config
        assert policy.max_age == 604800 and policy.max_msgs == 20000 and policy.max_bytes == 33554432
        epoch = ''
        device, session = 'readonly-fixture-device', 'fixture-session'
        byte_counts = {'received_payload': 0, 'sent_payload': 0}
        async def rpc(kind, number, body=None):
            nonlocal epoch
            rid = f'fixture-request-{number}'
            reply = rpc_reply_subject('fixture', device, session, rid)
            future = asyncio.get_running_loop().create_future()
            async def receive(msg):
                byte_counts['received_payload'] += len(msg.data)
                if not future.done(): future.set_result(json.loads(msg.data))
            sub = await mobile.subscribe(reply, cb=receive)
            try:
                await mobile.flush()
                payload = {'id': rid, 'request_id': rid, 'type': kind, 'chat_id': '',
                    'device_id': device, 'session_id': session, 'reply_subject': reply, 'body': body or {}}
                if kind == 'hello': payload['protocol_versions'] = [2, 1]
                else: payload.update(protocol_version=2, epoch=epoch)
                data = json.dumps(payload).encode()
                byte_counts['sent_payload'] += len(data)
                await mobile.publish('zgwd.fixture.rpc', data)
                response = await asyncio.wait_for(future, 5)
                assert response['status'] == 200, (kind, response['status'], response.get('body', {}).get('error'))
                epoch = response['body'].get('epoch', epoch)
                return response['body']
            finally:
                await sub.unsubscribe()
        hello = await rpc('hello', 0)
        assert 'rpc-inbox-v1' in hello['capabilities'] and 'notification-sync-v1' in hello['capabilities']
        for number in range(1, 1001):
            await rpc('readiness', number)
        snapshot = await rpc('notification_sync', 1001, {'baseline': 0,
            'incarnation': transport.notification_incarnation})
        assert any(row.get('kind') == 'assistant_final' for row in snapshot['records'])
        await transport.drain_outbox()
        for _ in range(30):
            notify = await js.stream_info('ZGWD_NOTIFY_fixture')
            if notify.state.messages == 1: break
            await asyncio.sleep(.1)
        assert notify.state.messages == 1, notify.state.messages
        final = json.loads((await js.get_msg('ZGWD_NOTIFY_fixture', seq=notify.state.last_seq)).data)
        assert final['kind'] == 'assistant_final' and len(json.dumps(final).encode()) < 2048
        assert 'text' not in final['body'] and final['projection_version'] == 1
        await transport.drain_outbox()
        assert (await js.stream_info('ZGWD_NOTIFY_fixture')).state.messages == 1
        old_after = await js.stream_info('ZGWD_EVENTS_fixture')
        command_info = await js.stream_info('ZGWD_COMMANDS_fixture')
        assert old_after.state.messages == 100000 and command_info.state.messages == 0
        assert (await js.consumer_info('ZGWD_EVENTS_fixture', 'old-backlog')).num_pending == 40000
        print(json.dumps({'result': 'PASS', 'legacy_retained': 100000, 'legacy_pending_unchanged': 40000,
            'rpc_count': 1002, 'rpc_persisted': 0, 'notify_count': 1, 'mobile_stream_update_denied': denied,
            'notification_policy': {'max_age': policy.max_age, 'max_msgs': policy.max_msgs, 'max_bytes': policy.max_bytes},
            'application_bytes': byte_counts, 'transport_wire_bytes': wire_counts,
            'wire_scope': 'actual isolated TCP NATS stream bytes; excludes IP/TCP headers and public TLS',
            'broker_old_bytes': old_after.state.bytes, 'broker_notify_bytes': notify.state.bytes,
            'expected_permission_errors': errors}), flush=True)
    finally:
        if mobile is not None: await mobile.close()
        if proxy_server is not None:
            proxy_server.close()
            await proxy_server.wait_closed()
        for task in list(proxy_tasks):
            task.cancel()
        if proxy_tasks:
            await asyncio.gather(*list(proxy_tasks), return_exceptions=True)
        await transport._close_async()


def test_real_broker_storage_acl_and_legacy_backlog(tmp_path):
    directory = tmp_path
    config = NatsRuntimeConfig(directory, secrets.token_urlsafe(24), host='127.0.0.1',
        port=free_port(), websocket_port=free_port(), role_permissions=True, pair_id='fixture')
    runtime = NatsServerProcess(config, bundled_dir=Path(__file__).resolve().parents[1] / 'tools' / 'nats-server')
    try:
        runtime.start()
        asyncio.run(verify(directory, config))
    finally:
        runtime.stop()
