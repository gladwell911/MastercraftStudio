import asyncio
import types

from realtime_asr import AsrResponse, RealtimeAsrClient, extract_text
from realtime_asr import PcmBuffer


def test_fifo_exact_byte_boundary_and_irreversible_failure():
    buffer = PcmBuffer()
    for _ in range(100):
        assert buffer.append(b'x' * 6400)
    assert buffer.size == buffer.MAX_BYTES
    assert not buffer.append(b'x')
    assert buffer.failure
    assert buffer.size == buffer.MAX_BYTES  # no silent drop or extra byte
    assert not buffer.append(b'y')
    buffer.discard()
    assert buffer.size == 0 and buffer.failure


def test_fifo_rejects_oversized_frame_and_seal_is_final():
    buffer = PcmBuffer()
    assert not buffer.append(b'x' * 6401)
    assert buffer.failure
    buffer = PcmBuffer()
    assert buffer.append(b'x')
    buffer.seal()
    assert not buffer.append(b'y')
    assert list(buffer.frames) == [b'x']


def test_fifo_wakeups_are_coalesced_even_when_loop_is_blocked():
    callbacks = []
    loop = types.SimpleNamespace(call_soon_threadsafe=callbacks.append)
    buffer = PcmBuffer()
    buffer.bind(loop, None, None)
    for _ in range(1000):
        buffer.append(b'x')
    buffer.fail('overflow')
    assert len(callbacks) == 1
    callbacks.pop()()
    buffer.seal()
    assert len(callbacks) == 1


def test_extract_text_prefers_full_sentence_over_fragments():
    payload = {
        "result": {
            "text": "今天天气怎么样？",
            "utterances": [
                {"text": "今天"},
                {"text": "天气"},
                {"text": "怎么样？"},
            ],
        }
    }
    out = extract_text(payload)
    assert out == "今天天气怎么样？"


def test_extract_text_keeps_distinct_sentences():
    payload = {
        "segments": [
            {"text": "今天天气怎么样？"},
            {"text": "明天会下雨吗？"},
        ]
    }
    out = extract_text(payload)
    assert "今天天气怎么样？" in out
    assert "明天会下雨吗？" in out


def test_receiver_emits_each_text_packet_without_client_side_dedupe(monkeypatch):
    seen = []
    errors = []
    client = RealtimeAsrClient(
        on_text=lambda text: seen.append(text),
        on_error=lambda msg: errors.append(msg),
        ws_url="ws://test",
    )

    class _FakeWs:
        def __init__(self, messages):
            self._messages = list(messages)

        def __aiter__(self):
            self._iter = iter(self._messages)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    responses = iter(
        [
            AsrResponse(payload_msg={"result": [{"text": "今天", "utterances": [{"text": "今天", "definite": False}]}]}),
            AsrResponse(payload_msg={"result": [{"text": "今天天气", "utterances": [{"text": "今天天气", "definite": False}]}]}),
            AsrResponse(payload_msg={"result": [{"text": "今天天气不错", "utterances": [{"text": "今天天气不错", "definite": True, "end_time": 1200}]}]}),
            AsrResponse(
                is_last_package=True,
                payload_msg={"result": [{"text": "今天天气不错", "utterances": [{"text": "今天天气不错", "definite": True, "end_time": 1200}]}]},
            ),
        ]
    )
    messages = [types.SimpleNamespace(type="binary", data=b"x") for _ in range(4)]
    client._ws = _FakeWs(messages)
    client.buffer.seal()
    client.terminal_sent = True

    import realtime_asr

    monkeypatch.setattr(realtime_asr, "aiohttp", types.SimpleNamespace(WSMsgType=types.SimpleNamespace(BINARY="binary")))
    monkeypatch.setattr(realtime_asr.Protocol, "parse_response", staticmethod(lambda _data: next(responses)))

    asyncio.run(client._receiver())

    assert seen == ["今天", "今天天气", "今天天气不错", "今天天气不错"]
    assert errors == []
