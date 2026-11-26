import os
from pathlib import Path

import pytest
import websocket


@pytest.mark.skipif(not os.getenv('MC_TEST_CODEX_NATIVE'), reason='requires an isolated Codex 0.171.0 runtime')
def test_native_terminal_endpoint_rejects_uncredentialed_client_and_resumes_empty_thread(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_BIN', os.environ['MC_TEST_CODEX_NATIVE'])
    from codex_native_terminal import CodexTerminalClient

    with_client = CodexTerminalClient()
    with_client._codex_home_dir = tmp_path
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
            if 'Synthetic command approval' in serialized_input and 'call_mc_test_shell' not in serialized_input:
                tool = {'id': 'fc_mc_test_shell', 'type': 'function_call', 'status': 'completed',
                        'name': 'exec_command', 'call_id': 'call_mc_test_shell', 'arguments': json.dumps({
                            'cmd': 'Write-Output MC_APPROVAL_TEST_OUTPUT',
                            'sandbox_permissions': 'require_escalated',
                            'justification': 'Print an isolated test marker.',
                        })}
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
            'approvals_reviewer="user"\nmodel="gpt-5.4"\nmodel_provider="fixture"\n[model_providers.fixture]\n'
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
                    assert observed_turn()['request_status'] in {'pending', 'running'}
                    release.set()
                    wait_for_wx(lambda: observed_turn()['request_status'] == 'done', 'terminal final in MC')
                    assert observed_turn()['codex_turn_id'] == turn_id
                    status, body = remote._route_command({'type': 'history_read', 'chat_id': owner})
                    assert status == 200 and body['chat']['turns'][-1]['answer'] == text
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
                    status, projection = remote._route_command({'type': 'execution_page_v3', 'chat_id': owner})
                    assert status == 200
                    assert any('MC_APPROVAL_TEST_OUTPUT' in row.get('detail_text', '') for row in projection['rows']), projection['rows']
        finally:
            release.set()
            if pty is not None:
                pty.close(force=True)
            for worker in frame._codex_clients.values():
                worker.close()
            http.shutdown()









