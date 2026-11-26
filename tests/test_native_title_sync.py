import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_client import CodexAppServerClient
from codex_history import CodexHistoryReader, codex_history_account
from kimi_history import KimiHistoryReader, kimi_history_account, kimi_history_storage
from kimi_runtime import child_environment
from kimi_server_client import KimiServerClient, KimiServerError
from remote_nats import RemoteNatsTransport
from test_kimi_runtime_usage_e2e import gated_reply, loopback_kimi_home


@pytest.mark.skipif(not os.getenv('MC_TEST_CODEX_NATIVE'), reason='requires Codex 0.171.0')
def test_codex_rename_is_read_from_independent_app_server(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_BIN', os.environ['MC_TEST_CODEX_NATIVE'])
    home = tmp_path / 'codex'
    home.mkdir()
    (home / 'config.toml').write_text('model_provider="fixture"\n[model_providers.fixture]\n'
                                     'name="Fixture"\nbase_url="http://127.0.0.1:1/v1"\nwire_api="responses"\n')
    writer = CodexAppServerClient(native_home=home, use_native_config=True)
    reader = CodexHistoryReader(home, False, 'default', lambda: [])
    try:
        sid = writer.start_thread(str(tmp_path))['thread']['id']
        writer.request('thread/name/set', {'threadId': sid, 'name': 'Before'})
        result = reader.rename_session(sid, str(tmp_path), codex_history_account(writer), '桌面确认')
        assert result == '桌面确认'
        assert writer.read_thread(sid, include_turns=False)['thread']['name'] == '桌面确认'
    finally:
        reader.client.close()
        writer.close()


@pytest.mark.skipif(os.getenv('MC_KIMI_RUNTIME_TEST') != '1', reason='requires isolated Kimi Code')
def test_kimi_title_write_is_read_by_independent_native_selector(loopback_kimi_home, frame, wait_for_wx, monkeypatch):
    workspace, _ = loopback_kimi_home
    client = KimiServerClient()
    reader = KimiHistoryReader(client, 'default', lambda _: True)
    try:
        client.start(timeout=10)
        sid = client.create_session(cwd=str(workspace), title='Before')
        assert reader.rename_session(sid, str(workspace), kimi_history_storage(), kimi_history_account(), 'First') == 'First'
        frame._kimi_client = client
        assert frame._on_new_chat_clicked(None)
        chat_id = frame.active_chat_id
        frame._current_chat_state.update(
            model="kimi/main", kimi_session_id=sid, kimi_storage_id=kimi_history_storage(),
            native_cwd=str(workspace), native_account=kimi_history_account(),
        )
        frame.chat_store.upsert_chat(frame._slim_active_chat_state())
        remote = RemoteNatsTransport(pair_id="default", token="fixture", on_rename_chat=frame._remote_api_rename_chat)
        with ThreadPoolExecutor() as client_thread:
            response = client_thread.submit(
                remote._route_command, {"type": "rename_chat", "chat_id": chat_id, "title": "手机确认"},
            )
            wait_for_wx(response.done, "remote rename result", timeout=20)
            status, body = response.result()
        assert status == 200, body
        assert frame._current_chat_state["title"] == "手机确认"
        output = subprocess.run(
            [os.environ['KIMI_BIN'], 'session', 'list', '--json'], cwd=workspace,
            env=child_environment(), check=True, capture_output=True, encoding='utf-8', timeout=20,
        ).stdout
        session, = [row for row in json.loads(output) if row['id'] == sid]
        assert session['title'] == '手机确认'

        def lost(*_):
            raise KimiServerError("Synthetic lost request", result_unknown=True)

        monkeypatch.setattr(client, "set_session_title", lost)
        with ThreadPoolExecutor() as client_thread:
            response = client_thread.submit(
                remote._route_command, {"type": "rename_chat", "chat_id": chat_id, "title": "未确认标题"},
            )
            wait_for_wx(response.done, "unknown rename result", timeout=20)
            status, body = response.result()
        assert status == 503, body
        assert frame._current_chat_state["title"] == "手机确认"
    finally:
        client.close()
