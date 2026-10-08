from chat_store import ChatStore
from scripts.nats_e2e_desktop_harness import CrossClientHarnessState


def test_competitor_completion_satisfies_canonical_notification_contract(tmp_path, monkeypatch):
    monkeypatch.setenv('E2E_IMMEDIATE_RECENCY', '1')
    store = ChatStore(tmp_path / 'harness.db')
    store.initialize()
    state = CrossClientHarnessState(
        durable_store=store, pair_id='laptop', seed_title='laptop',
        codex_reply='laptop final', kimi_reply='kimi final',
    )
    code, body = state.message({
        'chat_id': 'chat-e2e-newer', 'text': 'QA release background final',
        'model': 'codex/main',
    })
    assert code == 200 and body['accepted']
    assert state.observed_messages[-1]['reply'] == 'laptop final'
    expected = store.readable_answer('chat-e2e-newer', turn_index=0)
    assert expected is not None
    _, history = state.history_read({'chat_id': 'chat-e2e-newer'})
    _, snapshot = state.state({'chat_id': 'chat-e2e-newer'})
    for turn in (history['chat']['turns'][0], snapshot['turns'][0]):
        assert {key: turn[key] for key in expected} == expected
    assert snapshot['read_state'] == store.get_chat_read_state('chat-e2e-newer', pair_id='laptop')
