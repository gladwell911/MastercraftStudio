import threading

import pytest

from speech_input import MODE_DIRECT, MODE_OPTIMIZE, VoiceInputController
from realtime_asr import RealtimeAsrClient
from voice_fakes import VoiceRig, wait_until


@pytest.fixture
def rig(monkeypatch):
    return VoiceRig(monkeypatch)


@pytest.fixture
def controller(rig):
    seen = {'states': [], 'results': [], 'errors': [], 'stop': []}
    c = VoiceInputController(seen['states'].append, lambda *args: seen['results'].append(args),
                             seen['errors'].append, lambda: seen['stop'].append(True))
    yield c, seen
    rig.open_gate.set()
    c.shutdown()
    assert c._shutdown_done.wait(3)
    if c._audio_thread:
        c._audio_thread.join(2)
        assert not c._audio_thread.is_alive()
    rig.assert_closed()


def test_local_ready_precedes_connection_and_early_stop_preserves_all_pcm(controller, rig):
    c, seen = controller
    rig.tail = b'C' * 6400
    c.start_recording()
    wait_until(lambda: c.state == 'recording')
    assert seen['states'] == ['开始录音']
    assert not rig.sockets  # Connection remains deliberately blocked.
    rig.frame(b'B' * 6400)
    c.stop_and_transcribe()
    c.stop_and_transcribe()
    assert seen['stop'] == [True]
    assert rig.stopped.wait(2)
    assert not seen['results']
    rig.connect.set()
    wait_until(lambda: c.state == 'idle')
    assert seen['results'] == [(rig.text, MODE_DIRECT)]
    assert not seen['errors']
    assert [n < 0 for n, _ in rig.packets] == [False, False, False, True]
    assert rig.packets[0][1][44:] == rig.first
    assert rig.packets[1][1] == b'B' * 6400
    assert rig.packets[2][1] == rig.tail
    assert rig.packets[3][1] == b''
    assert sum(data.startswith(b'RIFF') for _, data in rig.packets) == 1


@pytest.mark.parametrize('mode', [MODE_DIRECT, MODE_OPTIMIZE])
def test_latest_text_and_identical_utterances_in_distinct_generations(controller, rig, mode, monkeypatch):
    c, seen = controller
    rig.interim_texts = ['opening', 'opening phrase']
    updates = []
    stream_text = c._stream_text
    def observe(session, text):
        stream_text(session, text)
        updates.append((session.generation, text))
    monkeypatch.setattr(c, '_stream_text', observe)
    rig.connect.set()
    for index in range(2):
        c.start_recording(mode)
        wait_until(lambda: c.state == 'recording')
        generation = c.generation
        wait_until(lambda: len(updates) == index * 3 + 2)
        assert updates[-2:] == [(generation, text) for text in rig.interim_texts]
        assert seen['results'] == [(rig.text, mode)] * index
        c.stop_and_transcribe()
        wait_until(lambda: c.state == 'idle')
        assert updates[-1] == (generation, rig.text)
        assert seen['results'] == [(rig.text, mode)] * (index + 1)
    assert seen['results'] == [(rig.text, mode), (rig.text, mode)]
    assert not seen['errors']


@pytest.mark.parametrize('phase', ['starting', 'recording', 'finishing'])
def test_cancel_in_every_phase_discards_and_converges(controller, rig, phase):
    c, seen = controller
    if phase == 'starting':
        rig.open_gate.clear()
    c.start_recording()
    if phase != 'starting':
        wait_until(lambda: c.state == 'recording')
    if phase == 'finishing':
        c.stop_and_transcribe()
    c.cancel()
    rig.open_gate.set()
    rig.connect.set()
    wait_until(lambda: c.state == 'idle')
    assert not seen['errors'] and not seen['results']
    if phase == 'starting':
        assert not seen['states']


@pytest.mark.parametrize('fault', ['no_frame', 'overflow', 'full', 'connect', 'send', 'early_final'])
def test_failures_are_atomic_and_report_once(controller, rig, fault, monkeypatch):
    c, seen = controller
    if fault == 'no_frame':
        rig.first = b''
        monkeypatch.setattr(c, 'FIRST_FRAME_TIMEOUT', .03)
    if fault == 'connect':
        rig.fail_connect = True
    if fault == 'early_final':
        rig.early_final = True
    c.start_recording()
    if fault != 'no_frame':
        wait_until(lambda: c.state == 'recording')
    if fault == 'overflow':
        rig.frame(b'bad', status=True)
    if fault == 'full':
        for _ in range(100):
            rig.frame(b'B' * 6400)
    if fault == 'send':
        rig.fail_send = True
    if fault in ('connect', 'send', 'early_final'):
        rig.connect.set()
    wait_until(lambda: c.state == 'idle')
    assert len(seen['errors']) == 1
    assert not seen['results']


def test_queued_ready_and_result_rechecked_after_cancel_and_new_generation(rig):
    events, seen = [], []
    c = VoiceInputController(lambda text: seen.append(text), lambda *a: seen.append(a),
                             seen.append, dispatch=events.append)
    try:
        c.start_recording()
        wait_until(lambda: c.state == 'recording')
        c.cancel()
        wait_until(lambda: c.state == 'idle')
        for event in events:
            event()
        assert seen == []
        events.clear()
        rig.connect.set()
        c.start_recording()
        wait_until(lambda: c.state == 'recording')
        c.stop_and_transcribe()
        wait_until(lambda: c.state == 'idle')
        c.start_recording()
        for event in events:
            event()
        assert seen == []
    finally:
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)


def test_100_lifecycles_return_resources_to_baseline(controller, rig):
    c, seen = controller
    rig.connect.set()
    for index in range(100):
        c.start_recording()
        wait_until(lambda: c.state == 'recording')
        if index % 2:
            c.cancel()
        else:
            c.stop_and_transcribe()
        wait_until(lambda: c.state == 'idle')
        rig.assert_closed()
    assert len(seen['results']) == 50
    assert not seen['errors']
    assert len([t for t in threading.enumerate() if t is c._audio_thread]) == 1
    async def tasks():
        import asyncio
        return len(asyncio.all_tasks())
    import asyncio
    assert asyncio.run_coroutine_threadsafe(tasks(), c._asr_loop).result(2) == 1


@pytest.mark.parametrize('phase', ['connect', 'drain', 'final'])
def test_deadlines_terminate_and_cleanup_without_partial_result(controller, rig, monkeypatch, phase):
    c, seen = controller
    if phase == 'connect':
        monkeypatch.setattr(RealtimeAsrClient, 'CONNECT_TIMEOUT', .04)
    elif phase == 'drain':
        monkeypatch.setattr(RealtimeAsrClient, 'DRAIN_TIMEOUT', .04)
        rig.send_gate.clear()
        rig.connect.set()
    else:
        monkeypatch.setattr(RealtimeAsrClient, 'FINAL_TIMEOUT', .04)
        rig.final_gate.clear()
        rig.connect.set()
    c.start_recording()
    wait_until(lambda: c.state == 'recording')
    original_deadline_start = c._session.client.started_at
    c.stop_and_transcribe()
    assert c._session.client.started_at == original_deadline_start
    wait_until(lambda: c.state == 'idle')
    assert len(seen['errors']) == 1 and not seen['results']
    assert '超时' in seen['errors'][0]
    rig.assert_closed()


def test_cleanup_failure_is_observable_and_prevents_success(controller, rig):
    c, seen = controller
    rig.connect.set()
    c.start_recording()
    wait_until(lambda: c.state == 'recording' and bool(rig.sessions))
    session = rig.sessions[-1]
    async def fail_close():
        session.closed = True
        raise OSError('controlled cleanup failure')
    session.close = fail_close
    c.stop_and_transcribe()
    wait_until(lambda: c.state == 'idle')
    assert c._session.client.cleanup_error
    assert len(seen['errors']) == 1 and not seen['results']


@pytest.mark.parametrize('fail_send', [False, True])
def test_final_during_terminal_send_waits_for_success(controller, rig, fail_send):
    import asyncio
    c, seen = controller
    rig.connect.set()
    rig.terminal_return_gate.clear()
    rig.fail_terminal = fail_send
    c.start_recording()
    wait_until(lambda: c.state == 'recording')
    c.stop_and_transcribe()
    assert rig.final_delivered.wait(2)
    # Keep send pending while the loop processes the delivered final, reproducing
    # the transport whose EOF reaches the peer 30ms before send_bytes returns.
    asyncio.run_coroutine_threadsafe(asyncio.sleep(.03), c._asr_loop).result(1)
    client = c._session.client
    assert not client.terminal_sent and not client.final_received
    assert c.state == 'finishing' and not seen['results'] and not seen['errors']
    rig.terminal_return_gate.set()
    wait_until(lambda: c.state == 'idle')
    if fail_send:
        assert len(seen['errors']) == 1 and not seen['results']
    else:
        assert seen['results'] == [(rig.text, MODE_DIRECT)] and not seen['errors']


def test_failure_wakeups_cannot_converge_while_close_is_pending(controller, rig):
    import asyncio
    c, seen = controller
    rig.connect.set()
    rig.close_gate.clear()
    try:
        c.start_recording()
        wait_until(lambda: c.state == 'recording' and bool(rig.sockets))
        session = c._session
        rig.frame(b'bad', status=True)
        assert rig.close_entered.wait(2)
        wait_until(session.audio_done.is_set)
        for _ in range(4):
            session.buffer.seal()  # deliberately deliver the same failure again
            asyncio.run_coroutine_threadsafe(asyncio.sleep(.005), c._asr_loop).result(1)
        assert not session.network_done and not session.converged
        assert not session.client._cleanup_task.done()
        assert c.state == 'cancelling' and not rig.sockets[0].closed
        assert len(seen['errors']) == 1 and not seen['results']
    finally:
        rig.close_gate.set()
    wait_until(lambda: c.state == 'idle')
    assert session.network_done and session.converged
    rig.assert_closed()


def test_diagnostic_record_tracks_queued_result_delivery(rig):
    events, results = [], []
    c = VoiceInputController(lambda *_: None, lambda *a: results.append(a),
                             lambda *_: None, dispatch=events.append)
    try:
        rig.connect.set()
        c.start_recording()
        wait_until(lambda: c.state == 'recording')
        c.stop_and_transcribe()
        wait_until(lambda: c.diagnostics and 'result_posted' in c.diagnostics[-1][1])
        record = c.diagnostics[-1][1]
        assert 'result_gui' not in record
        for event in events:
            event()
        assert results == [(rig.text, MODE_DIRECT)]
        assert record['result_gui'] >= record['result_posted'] >= record['cleanup']
        assert all(isinstance(value, float) for value in record.values())
    finally:
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)


@pytest.mark.parametrize('fault', ['eof', 'error', 'start', 'stop', 'close'])
def test_transport_and_device_failures_report_once_and_recover(controller, rig, fault):
    c, seen = controller
    if fault in ('eof', 'error'):
        rig.receive_fault = fault
    else:
        rig.device_error = fault
    c.start_recording()
    if fault != 'start':
        wait_until(lambda: c.state == 'recording')
        if fault in ('stop', 'close'):
            c.stop_and_transcribe()
        else:
            rig.connect.set()
    wait_until(lambda: c.state == 'idle')
    assert len(seen['errors']) == 1 and not seen['results']
    assert ('麦克风' if fault in ('start', 'stop', 'close') else '识别服务') in seen['errors'][0]
    rig.assert_closed()
    rig.receive_fault = rig.device_error = None
    rig.connect.set()
    c.start_recording()
    wait_until(lambda: c.state == 'recording')
    c.stop_and_transcribe()
    wait_until(lambda: c.state == 'idle')
    assert len(seen['errors']) == 1 and seen['results'] == [(rig.text, MODE_DIRECT)]


@pytest.mark.parametrize('fault', ['failed', 'uncompleted'])
def test_unreclaimed_network_close_is_observable_without_claiming_closed(controller, rig, monkeypatch, fault):
    import asyncio
    c, seen = controller
    rig.connect.set()
    if fault == 'failed':
        rig.fail_ws_close = True
    else:
        rig.close_gate.clear()
        monkeypatch.setattr(RealtimeAsrClient, 'CLEANUP_TIMEOUT', .04)
    c.start_recording()
    wait_until(lambda: c.state == 'recording')
    c.stop_and_transcribe()
    wait_until(lambda: c.state == 'idle')
    client = c._session.client
    assert client._cleanup_task.done() and client.cleanup_error
    assert not rig.sockets[-1].closed and rig.sessions[-1].closed
    assert len(seen['errors']) == 1 and not seen['results']
    # Explicitly reclaim the intentionally open fake on its network owner loop
    # after proving the observable failure, before the common fixture teardown.
    rig.fail_ws_close = False
    rig.close_gate.set()
    asyncio.run_coroutine_threadsafe(rig.sockets[-1].close(), c._asr_loop).result(1)
