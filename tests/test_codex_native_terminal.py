import os
from pathlib import Path

import pytest
import websocket


@pytest.mark.skipif(not os.getenv('MC_TEST_CODEX_NATIVE'), reason='requires an isolated Codex 0.171.0 runtime')
def test_native_terminal_endpoint_rejects_uncredentialed_client_and_resumes_empty_thread(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_BIN', os.environ['MC_TEST_CODEX_NATIVE'])
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    from codex_native_terminal import CodexTerminalClient

    (tmp_path / 'config.toml').write_text('[features]\nplugins=false\n', encoding='utf-8')
    with_client = CodexTerminalClient(native_home=tmp_path, use_native_config=True)
    try:
        endpoint = with_client.terminal_endpoint()
        with pytest.raises(websocket.WebSocketBadStatusException) as denied:
            websocket.create_connection(endpoint['url'], suppress_origin=True, timeout=3)
        assert denied.value.status_code in {401, 403}
        created = with_client.start_thread(str(tmp_path))['thread']['id']
        with_client.request('thread/name/set', {'threadId': created, 'name': 'Synthetic test'})
        from remote_machine_config import _dpapi_unprotect
        import base64
        client_token = _dpapi_unprotect(base64.b64decode(endpoint['credential'])).decode('ascii')
        peer = websocket.create_connection(endpoint['url'], suppress_origin=True, timeout=3,
                                         header={'Authorization': f'Bearer {client_token}'})
        try:
            import json
            peer.send(json.dumps({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'test_peer', 'version': '1'}}}))
            assert 'result' in json.loads(peer.recv())
            peer.send(json.dumps({'method': 'initialized'}))
            peer.send(json.dumps({'id': 2, 'method': 'thread/resume', 'params': {'threadId': created}}))
            while (reply := json.loads(peer.recv())).get('id') != 2:
                pass
            assert reply['result']['thread']['id'] == created
        finally:
            peer.close()
    finally:
        with_client.close()


@pytest.mark.skipif(not os.getenv('MC_TEST_CODEX_NATIVE'), reason='requires an isolated Codex 0.171.0 runtime')
def test_native_tui_turn_is_visible_to_mc_and_remote_reader_before_completion(frame, monkeypatch, tmp_path, wait_for_wx):
    import concurrent.futures
    import json
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from winpty import PtyProcess

    import main
    from codex_client import DEFAULT_CODEX_MODEL
    from codex_worker_client import CodexWorkerClient
    from remote_nats import RemoteNatsTransport
    from shared_terminal import TerminalClient, TerminalServer
    from shared_terminal_command import _codex_tui_command
    from shared_terminal_routes import dispatch_terminal

    release = threading.Event()
    consumed_answer = threading.Event()
    text = '\u9694\u79bb\u539f\u751f\u7ec8\u7aef\u56de\u7b54\u3002\u672b\u5c3e\u6807\u8bc6 NATIVE_TAIL_04'
    item = {'id': 'msg_fixture', 'type': 'message', 'status': 'completed', 'role': 'assistant',
            'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    response = {'id': 'resp_fixture', 'object': 'response', 'status': 'completed', 'output': [item],
                'usage': {'input_tokens': 2, 'output_tokens': 2, 'total_tokens': 4,
                          'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()

            def emit(kind, **payload):
                event = json.dumps(dict(payload, type=kind))
                self.wfile.write(f'event: {kind}\ndata: {event}\n\n'.encode())
                self.wfile.flush()

            serialized_input = json.dumps(payload.get('input', []))
            for part in payload.get('input', []):
                if (part.get('type') == 'function_call_output' and part.get('call_id') == 'call_mc_test_input'
                        and 'Blue' in str(part.get('output') or '')):
                    consumed_answer.set()
            tool = None
            if 'Synthetic command approval' in serialized_input and 'call_mc_test_shell' not in serialized_input:
                tool = {'id': 'fc_mc_test_shell', 'type': 'function_call', 'status': 'completed',
                        'name': 'exec_command', 'call_id': 'call_mc_test_shell', 'arguments': json.dumps({
                            'cmd': 'Write-Output MC_APPROVAL_TEST_OUTPUT',
                            'sandbox_permissions': 'require_escalated',
                            'justification': 'Print an isolated test marker.',
                        })}
            elif 'Synthetic structured question' in serialized_input and 'call_mc_test_input' not in serialized_input:
                tool = {'id': 'fc_mc_test_input', 'type': 'function_call', 'status': 'completed',
                        'name': 'request_user_input', 'call_id': 'call_mc_test_input', 'arguments': json.dumps({
                            'questions': [{'id': 'color', 'header': 'Color', 'question': 'Which color?', 'options': [
                                {'label': 'Blue', 'description': 'Use blue.'},
                                {'label': 'Red', 'description': 'Use red.'},
                            ]}],
                        })}
            if tool is not None:
                emit('response.created', response=dict(response, output=[], status='in_progress'))
                emit('response.output_item.added', item=dict(tool, arguments='', status='in_progress'), output_index=0)
                emit('response.output_item.done', item=tool, output_index=0)
                emit('response.completed', response=dict(response, output=[tool]))
                return

            emit('response.created', response=dict(response, output=[], status='in_progress'))
            emit('response.output_item.added', item=dict(item, content=[], status='in_progress'), output_index=0)
            emit('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=text)
            if not release.wait(25):
                return
            try:
                emit('response.output_item.done', item=item, output_index=0)
                emit('response.completed', response=response)
            except ConnectionError:
                # A native interrupt can close the fixture response before release.
                pass

    work = tmp_path / 'work'
    work.mkdir()
    home = tmp_path / 'profile' / '.codex'
    home.mkdir(parents=True)
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setenv('CODEX_BIN', os.environ['MC_TEST_CODEX_NATIVE'])
    monkeypatch.setenv('USERPROFILE', str(home.parent))
    monkeypatch.setenv('MC_LOCAL_FAKE_MODEL_KEY', 'loopback-fixture')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('PYTHONPATH', str(project))

    def worker_factory(**options):
        def process(args):
            return subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        return CodexWorkerClient(**options, process_factory=process)

    monkeypatch.setattr(main, 'CodexWorkerClient', worker_factory)
    monkeypatch.setattr(frame, '_workspace_dir_for_codex', lambda: str(work))
    monkeypatch.setattr(frame, '_play_finish_sound', lambda: None)
    owner = 'terminal-owner'
    frame.chat_store.upsert_chat({'id': owner, 'title': 'Isolated Codex TUI', 'model': DEFAULT_CODEX_MODEL})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame.codex_approval_policy = 'on-request'
    frame.codex_sandbox = 'workspace-write'
    remote = RemoteNatsTransport(pair_id='default', token='synthetic', on_history_read=frame._remote_api_history_read_ui,
                                 on_message=frame._remote_api_message_ui, on_state=frame._remote_api_state_ui,
                                 on_reply_request=frame._remote_api_reply_request_ui,
                                 on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret='synthetic'))
    with ThreadingHTTPServer(('127.0.0.1', 0), Model) as http:
        threading.Thread(target=http.serve_forever, daemon=True).start()
        (home / 'config.toml').write_text('approval_policy="on-request"\nsandbox_mode="workspace-write"\n'
            'approvals_reviewer="user"\nmodel="gpt-5.4"\nmodel_provider="fixture"\n'
            '[features]\nplugins=false\ndefault_mode_request_user_input=true\n[model_providers.fixture]\n'
            f'name="Fixture"\nbase_url="http://127.0.0.1:{http.server_port}/v1"\n'
            'env_key="MC_LOCAL_FAKE_MODEL_KEY"\nwire_api="responses"\n', encoding='utf-8')
        pty = None
        try:
            with TerminalServer(tmp_path / 'endpoint.json', lambda op, body: dispatch_terminal(frame, op, body)) as server:
                client = TerminalClient.from_endpoint(server.endpoint)
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    def request(op, payload):
                        result = pool.submit(client.request, op, payload, timeout=40)
                        wait_for_wx(result.done, op, timeout=30)
                        return result.result()

                    code, listing = request('list', {'provider': 'codex'})
                    assert code == 200 and listing['chats'][0]['chat_id'] == owner
                    scope = {'provider': 'codex', 'chat_id': owner, 'session_id': ''}
                    assert request('open_native', {**scope, 'session_id': 'wrong'})[0] == 409
                    code, result = request('open_native', scope)
                    assert code == 200, result.get('message')
                    def spawn_tui(prompt):
                        command, env = _codex_tui_command(result, prompt)
                        terminal = PtyProcess.spawn(command, cwd=result['cwd'], env=env)

                        def discard_rendered_output():
                            try:
                                while terminal.isalive():
                                    terminal.read(4096)
                            except (EOFError, OSError):
                                pass

                        threading.Thread(target=discard_rendered_output, daemon=True).start()
                        return terminal

                    pty = spawn_tui('Synthetic TUI question')

                    def observed_turn():
                        return (frame._find_archived_chat(owner).get('turns') or [{}])[-1]

                    wait_for_wx(lambda: bool(observed_turn().get('codex_turn_id')), 'terminal turn in MC', timeout=10)
                    turn_id = observed_turn()['codex_turn_id']
                    assert observed_turn()['codex_thread_id'] == result['thread_id']
                    assert observed_turn()['question'] == 'Synthetic TUI question'

                    def remote_has_step():
                        status, page = remote._route_command({'type': 'execution_page_v3', 'chat_id': owner})
                        return status == 200 and any('NATIVE_TAIL_04' in row['list_text'] for row in page['rows'])

                    wait_for_wx(remote_has_step, 'RC execution projection before Codex completion')
                    frame.chat_store.notification_sync(pair_id="default", device_id="private-device",
                                                       baseline=0, incarnation="test", secret="test")
                    assert not frame.chat_store.pending_outbox(pair_id="default", lane="notification")
                    assert observed_turn()['request_status'] in {'pending', 'running'}
                    release.set()
                    wait_for_wx(lambda: observed_turn()['request_status'] == 'done', 'terminal final in MC')
                    assert observed_turn()['codex_turn_id'] == turn_id
                    assert frame._flush_execution_step_persists_sync()
                    frame._flush_codex_background_updates()
                    status, body = remote._route_command({'type': 'history_read', 'chat_id': owner})
                    assert status == 200 and body['chat']['turns'][-1]['answer'] == text
                    wait_for_wx(lambda: frame.chat_store.pending_outbox(pair_id="default", lane="notification"),
                                "the durable Codex notification")
                    fact, = frame.chat_store.pending_outbox(pair_id="default", lane="notification")
                    assert body['chat']['turns'][-1]['message_id'] in fact["event_id"]
                    release.clear()
                    pty.close(force=True)
                    pty = spawn_tui('Second native question')
                    wait_for_wx(lambda: observed_turn().get('codex_turn_id') != turn_id, 'next native turn')
                    next_turn_id = observed_turn()['codex_turn_id']
                    status, body = remote._route_command({'type': 'state', 'chat_id': owner})
                    assert status == 200 and body['native_turn_id'] == next_turn_id
                    command_body = {'type': 'message', 'chat_id': owner, 'text': '/stop', 'model': DEFAULT_CODEX_MODEL}
                    assert remote._route_command({**command_body, 'expected_native_turn_id': turn_id})[0] == 409
                    assert remote._route_command({**command_body, 'expected_native_turn_id': next_turn_id})[0] == 200
                    wait_for_wx(lambda: observed_turn()['request_status'] not in {'pending', 'running'},
                                'Codex stop resolves the same native turn')
                    assert observed_turn()['codex_turn_id'] == next_turn_id
                    assert observed_turn()['request_status'] == 'cancelled'
                    assert remote._route_command({**command_body, 'expected_native_turn_id': next_turn_id})[0] == 409
                    release.set()
                    pty.close(force=True)
                    pty = spawn_tui('Synthetic command approval')

                    def pending_approval():
                        return remote._route_command({'type': 'state', 'chat_id': owner})[1].get('pending_request')

                    wait_for_wx(pending_approval, 'Codex TUI command approval on RC')
                    request = pending_approval()
                    assert request['body']['method'] == 'item/commandExecution/requestApproval'
                    interaction_id = request['body']['interaction_id']
                    decision = {'type': 'reply_request', 'chat_id': owner, 'text': '1',
                                'interaction_id': interaction_id}
                    assert remote._route_command({**decision, 'chat_id': 'different-chat'})[0] == 400
                    assert remote._route_command({**decision, 'interaction_id': 'stale-value'})[0] == 400
                    assert remote._route_command(decision)[0] == 200
                    assert remote._route_command(decision)[0] == 400
                    wait_for_wx(lambda: pending_approval() is None and observed_turn()['request_status'] == 'done',
                                'terminal approval and final reply')
                    assert remote._route_command(decision)[0] == 400
                    def approval_output_is_available():
                        status, projection = remote._route_command({'type': 'execution_page_v3', 'chat_id': owner})
                        assert status == 200 or projection.get("error") == "EXECUTION_SOURCE_PENDING", projection
                        return any('MC_APPROVAL_TEST_OUTPUT' in row.get('detail_text', '').splitlines()
                                   for row in projection.get('rows', []))

                    wait_for_wx(approval_output_is_available, 'completed terminal approval output')
                    pty.close(force=True)
                    pty = spawn_tui('Synthetic structured question')
                    wait_for_wx(pending_approval, 'Codex TUI structured question on RC')
                    request = pending_approval()
                    assert request['request_kind'] == 'user_input'
                    assert request['body']['params']['questions'][0]['id'] == 'color'
                    answer = {'type': 'reply_request', 'chat_id': owner, 'text': 'Blue',
                              'interaction_id': request['body']['interaction_id']}
                    assert remote._route_command(answer)[0] == 200
                    wait_for_wx(lambda: pending_approval() is None and observed_turn()['request_status'] == 'done',
                                'structurally answered native turn')
                    assert consumed_answer.is_set()
                    assert remote._route_command(answer)[0] == 400
        finally:
            release.set()
            if pty is not None:
                pty.close(force=True)
            for worker in frame._codex_clients.values():
                worker.close()
            http.shutdown()

@pytest.mark.skipif(not os.getenv('MC_TEST_CODEX_NATIVE'), reason='requires an isolated Codex 0.171.0 runtime')
@pytest.mark.parametrize('finish_offline', [False, True])
def test_native_observer_recovers_same_turn_after_socket_loss(tmp_path, monkeypatch, finish_offline):
    import base64
    import io
    import json
    import queue
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from codex_client import CodexAppServerClient
    from codex_native_terminal import CodexTerminalClient
    import codex_worker_process
    from codex_worker_protocol import decode_worker_line, make_ui_request
    from remote_machine_config import _dpapi_unprotect

    text = '\u539f\u751f\u89c2\u5bdf\u91cd\u8fde\u540e\u7684\u5b8c\u6574\u6b63\u6587 OBSERVER_FINAL_04'
    item = {'id': 'observer-answer', 'type': 'message', 'status': 'completed', 'role': 'assistant',
            'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    response = {'id': 'observer-response', 'object': 'response', 'status': 'completed', 'output': [item],
                'usage': {'input_tokens': 2, 'output_tokens': 2, 'total_tokens': 4,
                          'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}
    release = threading.Event()
    allow_connect = threading.Event()
    prompts = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            prompts.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()

            def emit(kind, **payload):
                event = json.dumps(dict(payload, type=kind))
                self.wfile.write(f'event: {kind}\ndata: {event}\n\n'.encode())
                self.wfile.flush()

            emit('response.created', response=dict(response, output=[], status='in_progress'))
            emit('response.output_item.added', item=dict(item, content=[], status='in_progress'), output_index=0)
            emit('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=text[:5])
            if release.wait(20):
                emit('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=text[5:])
                emit('response.output_item.done', item=item, output_index=0)
                emit('response.completed', response=response)

    monkeypatch.setenv('CODEX_BIN', os.environ['MC_TEST_CODEX_NATIVE'])
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('MC_OBSERVER_FIXTURE_KEY', 'synthetic')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'protocol.allow')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'never')
    home = tmp_path / 'codex'
    home.mkdir()
    work = tmp_path / 'work'
    work.mkdir()
    stream = queue.Queue()
    events = []
    output = io.StringIO()
    worker = codex_worker_process.CodexWorkerRuntime(output=output)
    client = None

    def terminal_client(**options):
        nonlocal client
        worker_event = options.pop('on_event')

        def on_event(event):
            worker_event(event)
            stream.put(event)

        client = CodexTerminalClient(on_event=on_event, **options)
        return client

    monkeypatch.setattr(codex_worker_process, 'CodexTerminalClient', terminal_client)

    def messages(kind):
        return [packet['payload'] for line in output.getvalue().splitlines()
                if (packet := decode_worker_line(line))['type'] == kind]

    def wait_for(predicate):
        deadline = time.monotonic() + 7
        while True:
            try:
                event = stream.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                pytest.fail('Missing Codex observer event')
            events.append(event)
            if predicate(event):
                return event

    with ThreadingHTTPServer(('127.0.0.1', 0), Model) as http:
        threading.Thread(target=http.serve_forever, daemon=True).start()
        (home / 'config.toml').write_text('model="gpt-5.4"\nmodel_provider="fixture"\n[model_providers.fixture]\n'
            f'name="Fixture"\nbase_url="http://127.0.0.1:{http.server_port}/v1"\n'
            'env_key="MC_OBSERVER_FIXTURE_KEY"\nwire_api="responses"\n[features]\nplugins=false\n', encoding='utf-8')
        peer = None
        try:
            scope = {'chat_id': 'observer-test-chat', 'context_generation': 4,
                     'cwd': str(work), 'native_home': str(home), 'native_user_home': True}
            worker.handle_message(make_ui_request('open', 'open_terminal', scope))
            endpoint, = messages('terminal_endpoint')
            assert 'error' not in endpoint, endpoint.get('error')
            thread_id = endpoint['thread_id']

            def reopen(**options):
                request = dict(scope, thread_id=thread_id, require_existing_terminal=True)
                request.update(options)
                worker.handle_message(make_ui_request('reopen', 'open_terminal', request))
                return messages('terminal_endpoint')[-1]
            token = _dpapi_unprotect(base64.b64decode(endpoint['credential'])).decode('ascii')
            peer = websocket.create_connection(endpoint['url'], suppress_origin=True, timeout=8,
                                               header={'Authorization': f'Bearer {token}'})

            def rpc(identity, method, params):
                peer.send(json.dumps({'id': identity, 'method': method, 'params': params}))
                while (packet := json.loads(peer.recv())).get('id') != identity:
                    pass
                assert 'result' in packet, packet.get('error')
                return packet['result']

            rpc(1, 'initialize', {'clientInfo': {'name': 'observer-test-peer', 'version': '1'},
                                  'capabilities': {'experimentalApi': True}})
            peer.send(json.dumps({'method': 'initialized'}))
            rpc(2, 'thread/resume', {'threadId': thread_id})
            turn_id = rpc(3, 'turn/start', {'threadId': thread_id, 'input': [
                {'type': 'text', 'text': 'Synthetic observer question', 'text_elements': []},
            ]})['turn']['id']
            first_delta = wait_for(lambda event: event.turn_id == turn_id and event.type == 'agent_message_delta')
            assert first_delta.text == text[:5]
            claimed_turn, = messages('terminal_turn')
            assert claimed_turn['question'] == 'Synthetic observer question'
            owner = {'chat_id': scope['chat_id'], 'context_generation': 4, 'turn_idx': 0,
                     'thread_id': thread_id, 'turn_id': turn_id}
            worker.handle_message(make_ui_request('bind', 'bind_terminal_turn', owner))

            def projected(kind):
                return [row for row in messages('event')
                        if row['event']['type'] == kind and row['event']['turn_id'] == turn_id]

            initial, = projected('agent_message_delta')
            assert initial['event']['item_id'] == first_delta.item_id
            assert initial['turn_idx'] == 0 and initial['context_generation'] == 4
            connect = websocket.create_connection

            def paused_reconnect(*args, **kwargs):
                if not allow_connect.is_set():
                    raise OSError('Test observer socket is still unavailable')
                return connect(*args, **kwargs)

            monkeypatch.setattr(websocket, 'create_connection', paused_reconnect)
            client._socket.shutdown()
            wait_for(lambda event: event.type == 'shared_transport_lost')
            assert 'error' in reopen(context_generation=3)

            if not finish_offline:
                allow_connect.set()
                # Successful re-open verifies the existing bound transport is ready for live notifications.
                assert reopen()['url'] == endpoint['url']
                assert client.read_thread(thread_id)['thread']['turns'][-1]['status'] == 'inProgress'
            release.set()
            while True:
                packet = json.loads(peer.recv())
                if packet.get('method') == 'turn/completed' and packet['params']['turn']['id'] == turn_id:
                    break

            allow_connect.set()
            completion = wait_for(lambda event: event.turn_id == turn_id and event.type == 'turn_completed')
            assert completion.thread_id == thread_id and completion.status == 'completed'
            if finish_offline:
                answer = next(row for row in completion.data['items'] if row['id'] == first_delta.item_id)
                assert (answer['type'], answer['text'], answer['phase']) == ('agentMessage', text, 'final_answer')
            else:
                assert any(event.turn_id == turn_id and event.item_id == first_delta.item_id and event.text == text
                           for event in events if event.type == 'item_completed')

            # A second loss after completion must not re-emit that terminal or launch a model turn.
            allow_connect.clear()
            client._socket.shutdown()
            wait_for(lambda event: event.type == 'shared_transport_lost')
            allow_connect.set()
            assert reopen()['url'] == endpoint['url']
            while not stream.empty():
                events.append(stream.get_nowait())
            assert sum(event.turn_id == turn_id and event.type == 'turn_completed' for event in events) == 1
            answers = [row for row in projected('item_completed') if row['event']['item_id'] == first_delta.item_id]
            assert answers and all(row['event']['text'] == text and row['event']['data']['codex_terminal_snapshot']
                                   and row['turn_idx'] == 0 and row['context_generation'] == 4 for row in answers)
            if finish_offline:
                assert len(answers) == 1
            final, = projected('turn_completed')
            assert len(messages('terminal_turn')) == 1
            assert final['event']['thread_id'] == thread_id and final['event']['status'] == 'completed'
            assert final['completion_owners'] == [{'turn_idx': 0, 'context_generation': 4}]
            assert len(prompts) == 1
            if finish_offline:
                peer.close()
                peer = None
                worker.close()
                output.seek(0)
                output.truncate()

                # Simulate a metadata-only initial resume of the existing
                # rollout. A later full read must leave this history alone.
                configure = CodexAppServerClient._request_thread_configuration

                def resume_metadata_only(self, method, params):
                    if method == 'thread/resume':
                        params = dict(params, excludeTurns=True)
                    return configure(self, method, params)

                monkeypatch.setattr(CodexAppServerClient, '_request_thread_configuration', resume_metadata_only)
                retired_client = client
                assert 'error' in reopen()
                assert client is retired_client
                imported = reopen(require_existing_terminal=False)
                assert imported.get('thread_id') == thread_id, imported.get('error')
                assert client.read_thread(thread_id)['thread']['turns'][-1]['id'] == turn_id
                while not stream.empty():
                    stream.get_nowait()
                client._socket.shutdown()
                wait_for(lambda event: event.type == 'shared_transport_lost')
                assert reopen()['url'] == imported['url']
                assert not messages('terminal_turn')
                assert not projected('turn_completed')
                assert len(prompts) == 1
                app_server = client._proc
                app_server.terminate()
                app_server.wait(timeout=5)
                wait_for(lambda event: event.type == 'shared_transport_lost')
                wait_for(lambda event: event.type == 'shared_host_exited')
                exited, = messages('terminal_host_exited')
                assert exited['thread_id'] == thread_id
                assert exited['terminal_host_id'] == imported['terminal_host_id']
                assert 'error' in reopen()
                assert client._proc is app_server
                reopened = reopen(require_existing_terminal=False)
                assert reopened.get('thread_id') == thread_id, reopened.get('error')
                assert reopened['terminal_host_id'] != imported['terminal_host_id']
                assert client.read_thread(thread_id)['thread']['turns'][-1]['id'] == turn_id
                assert len(prompts) == 1
                assert not messages('terminal_turn')
                assert not projected('turn_completed')
        finally:
            allow_connect.set()
            release.set()
            if peer is not None:
                peer.close()
            worker.close()
            http.shutdown()


def test_confirmed_terminal_host_exit_preserves_pending_codex_turn_as_unknown(frame, tmp_path):
    from remote_nats import RemoteNatsTransport

    owner, thread, turn_id = "codex-host-owner", "original-thread", "observed-turn"
    chat = {
        "id": owner, "title": "Shared", "model": "codex/main", "native_cwd": str(tmp_path),
        "codex_thread_id": thread, "codex_terminal_thread": thread, "codex_terminal_host_id": "current",
        "codex_turn_id": turn_id, "codex_turn_active": True, "codex_context_generation": 3,
        "turns": [{"question": "Pending", "answer_md": "Partial answer", "model": "codex/main",
                   "request_status": "pending", "codex_thread_id": thread, "codex_turn_id": turn_id,
                   "codex_context_generation": 3, "terminal_notification_pair": "default",
                   "terminal_final_pending": True}],
    }
    frame.archived_chats.append(chat)
    frame.chat_store.upsert_chat(chat)
    frame.chat_store.replace_turns(owner, chat["turns"])
    remote = RemoteNatsTransport(pair_id="default", token="fixture", on_state=frame._remote_api_state_ui)
    state = lambda: remote._route_command({"type": "state", "chat_id": owner})[1]

    def worker_exit(host):
        frame._on_codex_worker_message(owner, {"type": "terminal_host_exited", "payload": {
            "chat_id": owner, "model": "codex/main", "thread_id": thread, "turn_id": turn_id,
            "turn_idx": 0, "context_generation": 3, "terminal_host_id": host,
        }})

    assert state()["native_turn_id"] == turn_id
    worker_exit("obsolete")
    assert state()["native_turn_id"] == turn_id
    worker_exit("current")
    assert state()["status"] == "idle" and not state().get("pending_request")
    status, scope = frame._prepare_codex_terminal_open({"chat_id": owner, "session_id": thread})
    assert status == 200 and scope["thread_id"] == thread and not scope["require_existing_terminal"]
    frame._flush_codex_background_updates()
    saved, = frame.chat_store.load_turns(owner)
    assert saved["request_status"] == "unknown"
    assert saved["codex_turn_id"] == turn_id and not saved["answer_md"]
    assert saved["request_recoverable"] is False
