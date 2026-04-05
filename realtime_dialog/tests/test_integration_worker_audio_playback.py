import asyncio

from dialog_worker import DialogWorker


class _FakeOutputStream:
    def __init__(self):
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)

    def stop_stream(self):
        return None

    def close(self):
        return None


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


class _FakeClient:
    def __init__(self, **_kwargs):
        self.logid = "integration-logid"
        self._responses: asyncio.Queue = asyncio.Queue()
        self._ws_open = True
        self.session_id = "integration-session"

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
        return None

    async def finish_session(self):
        self._ws_open = False

    async def finish_connection(self):
        return None

    async def close(self):
        self._ws_open = False


def test_integration_worker_connect_receives_and_plays_audio():
    events = []
    fake_audio = _FakeAudio()
    fake_client = _FakeClient()

    async def run_case():
        worker = DialogWorker(
            on_event=lambda et, payload=None: events.append((et, payload or {})),
            audio_factory=lambda: fake_audio,
            client_factory=lambda **kwargs: fake_client,
        )
        await worker._connect()

        await fake_client._responses.put(
            {"message_type": "SERVER_FULL_RESPONSE", "event": 351, "payload_msg": {"text": "你好"}}
        )
        await fake_client._responses.put({"message_type": "SERVER_ACK", "payload_msg": b"\x01\x02\x03\x04"})

        deadline = asyncio.get_running_loop().time() + 1.5
        while (asyncio.get_running_loop().time() < deadline) and (
            not fake_audio.output_stream.writes or fake_audio.output_stream.writes[-1] != b"\x01\x02\x03\x04"
        ):
            await asyncio.sleep(0.02)

        worker.shutting_down = True
        if worker.recv_task:
            worker.recv_task.cancel()
            try:
                await worker.recv_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        worker.player_running = False
        worker.audio_queue.put(None)
        if worker.player_thread:
            worker.player_thread.join(timeout=1)

        assert len(fake_audio.output_stream.writes) >= 2
        assert fake_audio.output_stream.writes[0] == b"\x00" * 5760
        assert fake_audio.output_stream.writes[-1] == b"\x01\x02\x03\x04"

    asyncio.run(run_case())

    statuses = [payload.get("message") for et, payload in events if et == "status"]
    assert "Connected" in statuses
    assert "Connected, waiting for remote audio" in statuses
    assert "Received remote audio" in statuses
    assert "Playing audio" in statuses
    assert ("server_text", {"text": "你好"}) in events
