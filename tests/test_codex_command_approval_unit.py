import io

import pytest

from codex_client import CodexEvent
from codex_worker_process import CodexWorkerRuntime
from codex_worker_protocol import make_ui_request
from test_codex_worker_process import FakeCodexClient


@pytest.mark.parametrize('policy,expected', [(None, 'never'), ('invalid', 'never'), ('on-request', 'on-request')])
@pytest.mark.parametrize('resume', [False, True])
def test_command_policy_applies_only_to_future_thread_start_or_resume(policy, expected, resume):
    created = []
    runtime = CodexWorkerRuntime(lambda callback, model: created.append(FakeCodexClient(callback, model)) or created[-1], io.StringIO())
    payload = {'chat_id': 'chat', 'model': 'codex/main', 'turn_idx': 0,
               'question': 'hello', 'approval_policy': policy}
    if resume:
        payload['thread_id'] = 'existing-thread'
    runtime.handle_message(make_ui_request('start', 'start_turn', payload))
    client = created[0]
    args = client.resumed_threads[0][1] if resume else client.started_threads[0]
    assert args['approval_policy'] == expected
    assert client.replies == []
    runtime.close()


def command_runtime(decisions=None, method='item/commandExecution/requestApproval'):
    created = []
    runtime = CodexWorkerRuntime(lambda callback, model: created.append(FakeCodexClient(callback, model)) or created[-1], io.StringIO())
    runtime.handle_message(make_ui_request('start', 'start_turn', {'chat_id': 'chat', 'model': 'codex/main',
        'turn_idx': 0, 'context_generation': 3, 'question': 'hello'}))
    client = created[0]
    event = CodexEvent(type='server_request', method=method, request_id=7,
        thread_id='thread-1', turn_id='turn-1', params={'availableDecisions': decisions})
    client.on_event(event)
    payload = {'chat_id': 'chat', 'model': 'codex/main', 'request_id': 7,
        'thread_id': 'thread-1', 'turn_id': 'turn-1', 'turn_idx': 0,
        'context_generation': 3, 'decision': 'accept'}
    return runtime, client, event, payload


@pytest.mark.parametrize('decision', ['accept', 'decline'])
def test_command_reply_is_once_and_keeps_native_request_id(decision):
    runtime, client, event, payload = command_runtime(['accept', 'decline'])
    payload['decision'] = decision
    runtime.handle_message(make_ui_request('reply', 'reply_command_approval', payload))
    runtime.handle_message(make_ui_request('duplicate', 'reply_command_approval', payload))
    client.on_event(event)
    runtime.handle_message(make_ui_request('replayed', 'reply_command_approval', payload))
    assert client.replies == [(7, decision)]
    runtime.close()


@pytest.mark.parametrize('field,bad', [('chat_id', 'other'), ('model', 'other'), ('thread_id', 'other'),
    ('turn_id', 'other'), ('turn_idx', 1), ('context_generation', 4), ('request_id', 8)])
def test_command_reply_rejects_wrong_owner(field, bad):
    runtime, client, _, payload = command_runtime()
    payload[field] = bad
    runtime.handle_message(make_ui_request('reply', 'reply_command_approval', payload))
    assert client.replies == []
    runtime.close()


def test_command_reply_rejects_stale_scope_client_and_noncommand():
    for stale in ('scope', 'active', 'client', 'method', 'unsupported'):
        runtime, client, _, payload = command_runtime(['decline'] if stale == 'unsupported' else None,
            'item/tool/requestUserInput' if stale == 'method' else 'item/commandExecution/requestApproval')
        if stale == 'scope':
            runtime._thread_turn_scopes[('chat', 'codex/main', 'thread-1')] = (1, 4)
        if stale == 'active':
            runtime._active_command_owners[('chat', 'codex/main')] = ('new-thread', 'new-turn', 0, 4)
        if stale == 'client':
            runtime._clients[('chat', 'codex/main')] = FakeCodexClient()
        runtime.handle_message(make_ui_request('reply', 'reply_command_approval', payload))
        assert client.replies == []
        runtime.close()


def test_command_decision_serialization_preserves_native_id_and_owner(monkeypatch):
    from codex_worker_client import CodexWorkerClient
    from codex_client import CodexAppServerClient
    worker = CodexWorkerClient()
    messages = []
    monkeypatch.setattr(worker, '_send_message', messages.append)
    worker.reply_command_approval(chat_id='chat', model='codex/main', request_id=7,
        thread_id='thread', turn_id='turn', turn_idx=0, context_generation=3, decision='decline')
    assert messages[0]['type'] == 'reply_command_approval'
    assert messages[0]['payload']['decision'] == 'decline'
    assert messages[0]['payload']['request_id'] == 7
    client = CodexAppServerClient()
    native = []
    monkeypatch.setattr(client, '_send_json', native.append)
    client.respond_command_approval(7, 'accept')
    assert native == [{'id': 7, 'result': {'decision': 'accept'}}]
