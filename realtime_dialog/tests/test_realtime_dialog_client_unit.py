import asyncio
import gzip
import json
import uuid

import config
import realtime_dialog_client


class FakeWS:
    def __init__(self):
        self.response_headers = {"X-Tt-Logid": "fake-logid"}
        self._recv_count = 0
        self.sent_packets = []

    async def send(self, data):
        self.sent_packets.append(data)

    async def recv(self):
        self._recv_count += 1
        return b"fake-bytes"


def _extract_start_session_payload(packet: bytes):
    cursor = 4  # protocol header
    event = int.from_bytes(packet[cursor:cursor + 4], "big")
    assert event == 100
    cursor += 4
    sid_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    cursor += sid_len
    payload_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    payload = packet[cursor:cursor + payload_len]
    return json.loads(gzip.decompress(payload).decode("utf-8"))


def _extract_session_event_payload(packet: bytes):
    cursor = 4  # protocol header
    event = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    sid_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    sid = packet[cursor:cursor + sid_len].decode("utf-8")
    cursor += sid_len
    payload_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    payload = packet[cursor:cursor + payload_len]
    return event, sid, json.loads(gzip.decompress(payload).decode("utf-8"))


def test_connect_raises_on_start_session_error(monkeypatch):
    fake_ws = FakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    parse_results = [
        {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
        {"code": 45000001, "payload_msg": {"error": "invalid app key"}},
    ]

    def fake_parse_response(_):
        return parse_results.pop(0)

    monkeypatch.setattr(realtime_dialog_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(realtime_dialog_client.protocol, "parse_response", fake_parse_response)

    client = realtime_dialog_client.RealtimeDialogClient(
        config.ws_connect_config,
        str(uuid.uuid4()),
        output_audio_format="pcm_s16le",
        mod="audio",
        recv_timeout=10,
    )

    try:
        asyncio.run(client.connect())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "StartSession failed" in str(exc)

    assert raised


def test_start_session_uses_selected_speaker_and_speed(monkeypatch):
    fake_ws = FakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    parse_results = [
        {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
        {"message_type": "SERVER_FULL_RESPONSE", "event": 150, "payload_msg": {}},
    ]

    def fake_parse_response(_):
        return parse_results.pop(0)

    monkeypatch.setattr(realtime_dialog_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(realtime_dialog_client.protocol, "parse_response", fake_parse_response)

    client = realtime_dialog_client.RealtimeDialogClient(
        config.ws_connect_config,
        str(uuid.uuid4()),
        output_audio_format="pcm_s16le",
        mod="audio",
        recv_timeout=30,
        tts_speaker="zh_female_vv_jupiter_bigtts",
        tts_speed_ratio=1.7,
    )
    asyncio.run(client.connect())

    # packet[0] is StartConnection, packet[1] is StartSession
    request_payload = _extract_start_session_payload(fake_ws.sent_packets[1])
    assert request_payload["tts"]["speaker"] == "zh_female_vv_jupiter_bigtts"
    assert request_payload["tts"]["audio_params"]["speech_rate"] == config.speed_ratio_to_speech_rate(1.7)
    assert request_payload["tts"]["speech_rate"] == config.speed_ratio_to_speech_rate(1.7)
    assert request_payload["dialog"]["extra"]["recv_timeout"] == 30


def test_start_session_respects_explicit_speech_rate(monkeypatch):
    fake_ws = FakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    parse_results = [
        {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
        {"message_type": "SERVER_FULL_RESPONSE", "event": 150, "payload_msg": {}},
    ]

    def fake_parse_response(_):
        return parse_results.pop(0)

    monkeypatch.setattr(realtime_dialog_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(realtime_dialog_client.protocol, "parse_response", fake_parse_response)

    client = realtime_dialog_client.RealtimeDialogClient(
        config.ws_connect_config,
        str(uuid.uuid4()),
        output_audio_format="pcm_s16le",
        mod="audio",
        recv_timeout=30,
        tts_speaker="zh_female_vv_jupiter_bigtts",
        tts_speed_ratio=1.7,
        tts_speech_rate=-10,
    )
    asyncio.run(client.connect())

    request_payload = _extract_start_session_payload(fake_ws.sent_packets[1])
    assert request_payload["tts"]["audio_params"]["speech_rate"] == -10
    assert request_payload["tts"]["speech_rate"] == -10


def test_update_session_tts_sends_live_update_event(monkeypatch):
    fake_ws = FakeWS()

    async def fake_connect(*args, **kwargs):
        return fake_ws

    parse_results = [
        {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
        {"message_type": "SERVER_FULL_RESPONSE", "event": 150, "payload_msg": {}},
    ]

    def fake_parse_response(_):
        return parse_results.pop(0)

    monkeypatch.setattr(realtime_dialog_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(realtime_dialog_client.protocol, "parse_response", fake_parse_response)

    sid = str(uuid.uuid4())
    client = realtime_dialog_client.RealtimeDialogClient(
        config.ws_connect_config,
        sid,
        output_audio_format="pcm_s16le",
        mod="audio",
        recv_timeout=30,
        tts_speaker="zh_female_vv_jupiter_bigtts",
        tts_speed_ratio=1.0,
    )
    asyncio.run(client.connect())
    asyncio.run(client.update_session_tts("zh_male_yunzhou_jupiter_bigtts", speed_ratio=1.0, speech_rate=35))

    event, sent_sid, payload = _extract_session_event_payload(fake_ws.sent_packets[-1])
    assert event == 101
    assert sent_sid == sid
    assert payload["tts"]["speaker"] == "zh_male_yunzhou_jupiter_bigtts"
    assert payload["tts"]["audio_params"]["speech_rate"] == 35
    assert payload["tts"]["speech_rate"] == 35


def test_connect_uses_legacy_extra_headers_when_additional_headers_is_unsupported(monkeypatch):
    fake_ws = FakeWS()
    seen = {"calls": []}

    async def fake_connect(*args, **kwargs):
        seen["calls"].append(kwargs)
        if "additional_headers" in kwargs:
            raise TypeError("unexpected keyword argument 'additional_headers'")
        return fake_ws

    parse_results = [
        {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
        {"message_type": "SERVER_FULL_RESPONSE", "event": 150, "payload_msg": {}},
    ]

    def fake_parse_response(_):
        return parse_results.pop(0)

    monkeypatch.setattr(realtime_dialog_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(realtime_dialog_client.protocol, "parse_response", fake_parse_response)

    client = realtime_dialog_client.RealtimeDialogClient(
        config.ws_connect_config,
        str(uuid.uuid4()),
        output_audio_format="pcm_s16le",
        mod="audio",
        recv_timeout=10,
    )

    asyncio.run(client.connect())

    assert "additional_headers" in seen["calls"][0]
    assert "extra_headers" in seen["calls"][1]
