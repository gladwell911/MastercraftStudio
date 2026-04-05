import asyncio

from dialog_worker import DialogWorker


class _FakeOutputStream:
    def __init__(self):
        self.writes = []
        self.stop_calls = 0
        self.closed = False

    def write(self, payload):
        self.writes.append(payload)

    def stop_stream(self):
        self.stop_calls += 1

    def close(self):
        self.closed = True


class _FakeAudio:
    def __init__(self):
        self.output_stream = _FakeOutputStream()

    def open(self, **kwargs):
        if kwargs.get("output"):
            return self.output_stream
        raise RuntimeError("input not expected in this test")

    def get_default_output_device_info(self):
        return {"index": 5, "name": "Integration Speaker"}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": f"Device-{index}"}

    def terminate(self):
        return None


class _ScriptedClient:
    def __init__(self):
        self.logid = "integration-logid"
        self._responses: asyncio.Queue = asyncio.Queue()
        self._ws_open = True
        self.session_id = "integration-session"
        self.say_hello_calls = 0
        self.finish_session_calls = 0
        self.finish_connection_calls = 0

    async def connect(self):
        return None

    def is_ws_open(self):
        return self._ws_open

    async def receive_server_response(self):
        item = await self._responses.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def say_hello(self):
        self.say_hello_calls += 1

    async def finish_session(self):
        self.finish_session_calls += 1
        self._ws_open = False

    async def finish_connection(self):
        self.finish_connection_calls += 1

    async def close(self):
        self._ws_open = False


def test_integration_worker_first_connection_greets_then_finishes():
    events = []
    fake_audio = _FakeAudio()
    fake_client = _ScriptedClient()

    async def run_case():
        worker = DialogWorker(
            on_event=lambda et, payload=None: events.append((et, payload or {})),
            audio_factory=lambda: fake_audio,
            client_factory=lambda **kwargs: fake_client,
        )
        await worker._connect()
        await worker._play_greeting()
        worker._handle_server_response({"message_type": "SERVER_FULL_RESPONSE", "event": 359, "payload_msg": {}})
        worker.interrupt_and_shutdown()

        assert fake_client.say_hello_calls == 1

    asyncio.run(run_case())

    assert ("greeting_finished", {"event": 359}) in events
    statuses = [payload.get("message") for et, payload in events if et == "status"]
    assert "Connected" in statuses
    assert "Greeting" in statuses


def test_integration_worker_interrupt_ignores_late_audio_packets():
    fake_audio = _FakeAudio()
    fake_client = _ScriptedClient()

    async def run_case():
        worker = DialogWorker(
            on_event=lambda *_args, **_kwargs: None,
            audio_factory=lambda: fake_audio,
            client_factory=lambda **kwargs: fake_client,
        )
        await worker._connect()
        worker._handle_server_response({"message_type": "SERVER_ACK", "payload_msg": b"\x01\x02"})
        await asyncio.sleep(0.05)
        worker.interrupt_and_shutdown()
        worker._handle_server_response({"message_type": "SERVER_ACK", "payload_msg": b"\x03\x04"})

        await asyncio.sleep(0.05)

        assert fake_audio.output_stream.writes[0] == b"\x00" * 5760
        assert b"\x03\x04" not in fake_audio.output_stream.writes
        assert fake_audio.output_stream.stop_calls == 1
        assert fake_audio.output_stream.closed is True

    asyncio.run(run_case())
