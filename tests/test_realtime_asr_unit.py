import asyncio
import types

from realtime_asr import AsrResponse, RealtimeAsrClient, extract_text


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

    import realtime_asr

    monkeypatch.setattr(realtime_asr, "aiohttp", types.SimpleNamespace(WSMsgType=types.SimpleNamespace(BINARY="binary")))
    monkeypatch.setattr(realtime_asr.Protocol, "parse_response", staticmethod(lambda _data: next(responses)))

    asyncio.run(client._receiver())

    assert seen == ["今天", "今天天气", "今天天气不错", "今天天气不错"]
    assert errors == []
