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


@pytest.mark.parametrize('version,mode,expected,expected_version', [
    (None, 'never', 'auto-review', 1),
    (None, 'on-request', 'auto-review', 1),
    (None, None, 'auto-review', 1),
    (0, 'never', 'auto-review', 1),
    (1, 'never', 'never', 1),
    (1, 'on-request', 'on-request', 1),
    (1, 'auto-review', 'auto-review', 1),
    (3, 'never', 'never', 3),
    (1, ['invalid'], 'auto-review', 1),
])
def test_one_time_default_migration_preserves_restored_state(approval_frame, version, mode, expected, expected_version):
    frame = approval_frame
    frame._save_state()
    state = json.loads(frame.state_path.read_text(encoding='utf-8'))
    state['codex_approval_policy'] = mode
    if version is None:
        state.pop('codex_approval_defaults_version', None)
    else:
        state['codex_approval_defaults_version'] = version
    frame.state_path.write_text(json.dumps(state), encoding='utf-8')
    frame._load_state()
    assert frame.codex_approval_policy == expected
    assert frame.codex_approval_defaults_version == expected_version
    frame._save_state()
    saved = json.loads(frame.state_path.read_text(encoding='utf-8'))
    assert saved['codex_approval_defaults_version'] == expected_version
    assert saved['codex_approval_policy'] == expected
    assert saved['active_chat_id'] == state['active_chat_id']
    frame._load_state()
    assert frame.codex_approval_policy == expected


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


@pytest.mark.parametrize('policy', ['never', 'on-request', 'auto-review'])
def test_menu_policy_is_saved_for_future_threads_without_touching_live_turn(approval_frame, monkeypatch, policy):
    frame = approval_frame
    real_dialog = main.CodexUserInputDialog
    frame.active_codex_turn_active = True
    frame.active_codex_turn_id = 'live-never-turn'
    observed = []
    modal_selection(monkeypatch, policy, observed=observed)
    item = frame.GetMenuBar().FindItemById(int(frame._codex_approval_policy_menu_id))
    assert item.GetItemLabelText() == 'Codex 执行审批'
    frame.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, int(frame._codex_approval_policy_menu_id)))
    assert frame.codex_approval_policy == policy
    assert json.loads(frame.state_path.read_text(encoding='utf-8'))['codex_approval_policy'] == policy
    assert '访问权限独立设置' in observed[0]['question']
    assert [item['value'] for item in observed[0]['options']] == ['never', 'on-request', 'auto-review']
    assert frame.active_codex_turn_active and frame.active_codex_turn_id == 'live-never-turn'
    frame.codex_approval_policy = 'never'
    frame._load_state()
    assert frame.codex_approval_policy == policy
    # Reopen the actual dialog and verify its initial selection, then cancel.
    def reopen(parent, questions):
        dialog = real_dialog(parent, questions)
        def cancel():
            control = dialog._controls[0]
            assert control['values'][control['radio'].GetSelection()] == policy
            control['radio'].SetSelection((control['radio'].GetSelection() + 1) % 3)
            dialog.EndModal(wx.ID_CANCEL)
        wx.CallAfter(cancel)
        return dialog
    monkeypatch.setattr(main, 'CodexUserInputDialog', reopen)
    before = frame.state_path.read_bytes()
    frame._show_codex_approval_policy()
    assert frame.codex_approval_policy == policy
    assert frame.state_path.read_bytes() == before
    saved = json.loads(frame.state_path.read_text(encoding='utf-8'))
    saved['codex_approval_policy'] = 'invalid'
    frame.state_path.write_text(json.dumps(saved), encoding='utf-8')
    frame._load_state()
    assert frame.codex_approval_policy == 'auto-review'
    frame._save_state()
    assert json.loads(frame.state_path.read_text(encoding='utf-8'))['codex_approval_policy'] == 'auto-review'


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


@pytest.mark.parametrize("mode,expected", [("never", "danger-full-access"), ("on-request", "danger-full-access"), ("auto-review", "workspace-write")])
def test_missing_sandbox_migrates_once_and_persists_independently(approval_frame, mode, expected):
    frame = approval_frame
    frame._save_state()
    state = json.loads(frame.state_path.read_text(encoding="utf-8"))
    state.update(codex_approval_policy=mode, codex_approval_defaults_version=1)
    state.pop("codex_sandbox", None)
    frame.state_path.write_text(json.dumps(state), encoding="utf-8")
    frame._load_state()
    assert frame.codex_sandbox == expected
    frame.codex_sandbox = "danger-full-access"
    frame.codex_approval_policy = "auto-review"
    frame._save_state()
    frame.codex_sandbox = "read-only"
    frame._load_state()
    assert frame.codex_sandbox == "danger-full-access"
    assert frame.codex_approval_policy == "auto-review"


def test_sandbox_menu_and_resume_cache_apply_new_selection(approval_frame, monkeypatch):
    frame = approval_frame
    frame.codex_approval_policy = "auto-review"
    frame.active_codex_turn_active = True
    frame.active_codex_turn_id = "live-turn"
    modal_selection(monkeypatch, "danger-full-access")
    frame.ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, int(frame._codex_sandbox_menu_id)))
    assert frame.codex_sandbox == "danger-full-access"
    assert frame.codex_approval_policy == "auto-review"
    assert frame.active_codex_turn_id == "live-turn"
    calls = []
    class Client:
        def resume_thread(self, thread_id, **kwargs):
            calls.append(kwargs)
    client = Client()
    frame._ensure_codex_thread_resumed(client, "old-thread")
    frame._ensure_codex_thread_resumed(client, "old-thread")
    frame.codex_sandbox = "workspace-write"
    frame._ensure_codex_thread_resumed(client, "old-thread")
    assert [call["sandbox"] for call in calls] == ["danger-full-access", "workspace-write"]
    state = json.loads(frame.state_path.read_text(encoding="utf-8"))
    state["codex_sandbox"] = "unknown"
    frame.state_path.write_text(json.dumps(state), encoding="utf-8")
    frame._load_state()
    assert frame.codex_sandbox == "workspace-write"
    frame._save_state()
    assert json.loads(frame.state_path.read_text(encoding="utf-8"))["codex_sandbox"] == "workspace-write"


def test_thread_configuration_ack_is_bound_to_thread_and_rejects_stale_generation(approval_frame):
    frame = approval_frame
    frame.active_chat_id = frame.current_chat_id = "configuration-chat"
    frame.active_session_turns = [{"model": main.DEFAULT_CODEX_MODEL, "codex_context_generation": 3}]
    chat = {"id": "configuration-chat", "codex_thread_id": "thread", "codex_context_generation": 3,
            "turns": frame.active_session_turns}
    frame._current_chat_state = chat
    configuration = {"sandbox": {"type": "dangerFullAccess"}, "approvalPolicy": "on-request"}
    payload = {"thread_id": "thread", "turn_id": "turn", "turn_idx": 0,
               "context_generation": 3, "thread_configuration": configuration}
    frame._apply_codex_worker_turn_started_ack("configuration-chat", payload)
    assert chat["codex_thread_configuration"] == configuration
    assert chat["codex_thread_configuration_thread_id"] == "thread"
    status = frame._build_codex_status_markdown(None, chat, main.DEFAULT_CODEX_MODEL)
    assert "dangerFullAccess" in status and "最近确认线程配置" in status
    frame._apply_codex_worker_turn_started_ack("configuration-chat", {**payload,
        "context_generation": 2, "thread_configuration": {"sandbox": "stale"}})
    assert chat["codex_thread_configuration"] == configuration
    chat["codex_thread_id"] = "different-thread"
    assert "未确认（未取得原生响应）" in frame._build_codex_status_markdown(None, chat, main.DEFAULT_CODEX_MODEL)
