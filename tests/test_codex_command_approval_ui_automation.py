import json

import pytest
import wx

import main
from test_codex_ui_responsiveness_automation import _track_ui_timers_for_test


@pytest.fixture
def approval_frame(request, monkeypatch):
    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue('frame')
    yield frame
    cleanup(frame)


def modal_selection(monkeypatch, value, *, close=False, mutation=None, observed=None):
    original = main.CodexUserInputDialog
    def create(parent, questions):
        if observed is not None:
            observed.extend(questions)
        dialog = original(parent, questions)
        def choose():
            control = dialog._controls[0]
            if value in control['values']:
                control['radio'].SetSelection(control['values'].index(value))
            if mutation:
                mutation()
            dialog.EndModal(wx.ID_CANCEL if close else wx.ID_OK)
        wx.CallAfter(choose)
        return dialog
    monkeypatch.setattr(main, 'CodexUserInputDialog', create)


def test_menu_policy_is_saved_for_future_threads_without_touching_live_turn(approval_frame, monkeypatch):
    frame = approval_frame
    frame.active_codex_turn_active = True
    frame.active_codex_turn_id = 'live-never-turn'
    modal_selection(monkeypatch, 'on-request')
    item = frame.GetMenuBar().FindItemById(int(frame._codex_approval_policy_menu_id))
    assert item.GetItemLabelText() == 'Codex 执行审批'
    frame.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, int(frame._codex_approval_policy_menu_id)))
    assert frame.codex_approval_policy == 'on-request'
    assert json.loads(frame.state_path.read_text(encoding='utf-8'))['codex_approval_policy'] == 'on-request'
    assert frame.active_codex_turn_active and frame.active_codex_turn_id == 'live-never-turn'
    frame.codex_approval_policy = 'never'
    frame._load_state()
    assert frame.codex_approval_policy == 'on-request'
    saved = json.loads(frame.state_path.read_text(encoding='utf-8'))
    saved['codex_approval_policy'] = 'invalid'
    frame.state_path.write_text(json.dumps(saved), encoding='utf-8')
    frame._load_state()
    assert frame.codex_approval_policy == 'never'


@pytest.mark.parametrize('value,close,changed,expected', [('accept', False, '', 'accept'),
    ('decline', False, '', 'decline'), ('accept', True, '', 'decline'),
    ('accept', False, 'chat', 'decline'), ('accept', False, 'clear', 'decline'),
    ('accept', False, 'client', 'decline')])
def test_native_command_dialog_keeps_owner_and_close_declines(approval_frame, monkeypatch, value, close, changed, expected):
    frame = approval_frame
    replies = []
    class Client:
        def reply_command_approval(self, **payload):
            replies.append(payload)
    client = Client()
    frame.active_chat_id = frame.current_chat_id = 'approval-chat'
    turn = {'model': main.DEFAULT_CODEX_MODEL, 'request_status': 'running',
            'codex_thread_id': 'thread', 'codex_turn_id': 'turn', 'codex_context_generation': 3}
    frame._current_chat_state = {'id': 'approval-chat', 'model': main.DEFAULT_CODEX_MODEL,
                                'codex_context_generation': 3, 'turns': [turn]}
    frame._codex_clients['approval-chat'] = client
    observed = []
    def mutate():
        if changed == 'chat':
            frame.current_chat_id = frame.active_chat_id = 'other'
        elif changed == 'clear':
            frame._current_chat_state['codex_context_generation'] = 4
        elif changed == 'client':
            frame._codex_clients['approval-chat'] = Client()
    modal_selection(monkeypatch, value, close=close, observed=observed, mutation=mutate)
    frame._handle_codex_request_dialog({'chat_id': 'approval-chat', 'model': main.DEFAULT_CODEX_MODEL,
        'thread_id': 'thread', 'turn_id': 'turn', 'turn_idx': 0, 'context_generation': 3,
        'request_id': 17, 'method': 'item/commandExecution/requestApproval',
        'params': {'command': 'example-command', 'cwd': 'C:/example', 'reason': 'explicit reason',
                   'availableDecisions': ['accept', 'acceptForSession', 'decline']}})
    assert replies[0]['decision'] == expected
    assert replies[0]['request_id'] == 17
    question = observed[0]
    assert all(part in question['question'] for part in ['example-command', 'C:/example', 'explicit reason'])
    assert [option['value'] for option in question['options']] == ['decline', 'accept']
    assert 'codex_pending_request' not in frame._current_chat_state


def test_command_server_request_entry_rejects_retired_client_then_prompts_current(approval_frame, monkeypatch, wx_app):
    frame = approval_frame
    replies = []
    class Client:
        def reply_command_approval(self, **payload):
            replies.append(payload)
    client, retired = Client(), Client()
    frame.active_chat_id = frame.current_chat_id = 'event-owner'
    turn = {'model': main.DEFAULT_CODEX_MODEL, 'request_status': 'running',
            'codex_thread_id': 'thread', 'codex_turn_id': 'turn', 'codex_context_generation': 3}
    frame.active_session_turns = [turn]
    frame._current_chat_state = {'id': 'event-owner', 'model': main.DEFAULT_CODEX_MODEL,
        'codex_thread_id': 'thread', 'codex_turn_id': 'turn', 'codex_context_generation': 3, 'turns': [turn]}
    frame._codex_clients['event-owner'] = client
    observed = []
    modal_selection(monkeypatch, 'accept', observed=observed)
    message = {'type': 'event', 'payload': {'chat_id': 'event-owner', 'model': main.DEFAULT_CODEX_MODEL,
        'turn_idx': 0, 'context_generation': 3, 'event': {'type': 'server_request',
        'method': 'item/commandExecution/requestApproval', 'request_id': 19,
        'thread_id': 'thread', 'turn_id': 'turn', 'params': {'command': 'local-test', 'cwd': 'C:/test',
        'reason': 'test', 'availableDecisions': ['accept', 'decline']}}}}
    frame._on_codex_worker_message('event-owner', message, retired)
    wx_app.Yield()
    assert observed == [] and replies == []
    frame._on_codex_worker_message('event-owner', message, client)
    wx_app.Yield()
    assert len(observed) == 1 and replies[0]['decision'] == 'accept'
    assert replies[0]['request_id'] == 19
    assert 'codex_pending_request' not in frame._current_chat_state
    assert not frame._current_chat_state.get('execution_steps')
