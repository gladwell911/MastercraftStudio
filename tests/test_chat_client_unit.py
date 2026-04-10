import json

import pytest
import requests

import chat_client
from chat_client import ChatClient


class _DummyStreamResponse:
    def __init__(self, *, status_code=200, lines=None, json_data=None, text=""):
        self.status_code = status_code
        self._lines = lines or []
        self._json_data = json_data
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self, decode_unicode=False):
        for item in self._lines:
            yield item

    def json(self):
        if self._json_data is not None:
            return self._json_data
        raise ValueError("no json")


def test_build_messages_with_history_context():
    client = ChatClient(api_key="test")
    history = [
        {"question": "Q1", "answer_md": "A1"},
        {"question": "Q2", "answer_md": "A2"},
    ]

    msgs = client._build_messages("Q3", history_turns=history)

    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "Q1"}
    assert msgs[2] == {"role": "assistant", "content": "A1"}
    assert msgs[3] == {"role": "user", "content": "Q2"}
    assert msgs[4] == {"role": "assistant", "content": "A2"}
    assert msgs[5] == {"role": "user", "content": "Q3"}


def test_model_404_no_endpoints_detected():
    err = "请求失败：HTTP 404。错误信息：No endpoints found for deepseek/deepseek-r1-0528-qwen3-8b."
    assert ChatClient.is_no_endpoint_error(err, model="deepseek/deepseek-r1-0528-qwen3-8b")
    assert not ChatClient.is_no_endpoint_error("请求失败：HTTP 500。", model="deepseek/deepseek-r1-0528-qwen3-8b")


def test_first_choice_handles_empty_or_missing_choices():
    assert ChatClient._first_choice({}) == {}
    assert ChatClient._first_choice({"choices": []}) == {}
    assert ChatClient._first_choice({"choices": [None]}) == {}
    assert ChatClient._first_choice({"choices": [{"message": {"content": "ok"}}]}) == {"message": {"content": "ok"}}


def test_doubao_model_detection_and_mapping():
    assert chat_client.is_doubao_model("doubao-2.0-pro")
    assert chat_client.is_doubao_model("doubao-2.0-lite")
    assert chat_client.is_doubao_model("doubao-2.0-mini")
    assert not chat_client.is_doubao_model("openai/gpt-5.2")
    assert chat_client.resolve_doubao_model("doubao-2.0-pro") == "doubao-seed-2-0-pro-260215"
    assert chat_client.resolve_doubao_model("doubao-2.0-lite") == "doubao-seed-2-0-lite-260215"
    assert chat_client.resolve_doubao_model("doubao-2.0-mini") == "doubao-seed-2-0-mini-260215"


def test_doubao_stream_chat_uses_ark_request_and_skips_web_detection(monkeypatch):
    client = ChatClient(api_key="ignored", model="doubao-2.0-pro")
    seen = {}
    deltas = []
    json_module = json

    def fail_should_use_web(_text):
        raise AssertionError("doubao models should not call _should_use_web")

    def fake_post(url, headers=None, json=None, timeout=None, stream=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        seen["timeout"] = timeout
        seen["stream"] = stream
        payload1 = {"choices": [{"delta": {"content": "你"}}]}
        payload2 = {"choices": [{"delta": {"content": "好"}}]}
        return _DummyStreamResponse(
            lines=[
                f"data: {json_module.dumps(payload1)}".encode("utf-8"),
                f"data: {json_module.dumps(payload2)}".encode("utf-8"),
                b"data: [DONE]",
            ]
        )

    monkeypatch.setattr(client, "_should_use_web", fail_should_use_web)
    monkeypatch.setattr(chat_client.requests, "post", fake_post)

    out = client.stream_chat("测试问题", deltas.append, history_turns=[{"question": "Q1", "answer_md": "A1"}])

    assert out == "你好"
    assert deltas == ["你", "好"]
    assert seen["url"] == f"{chat_client.DOUBAO_BASE_URL}{chat_client.CHAT_COMPLETIONS_PATH}"
    assert seen["headers"]["Authorization"] == f"Bearer {chat_client.DOUBAO_API_KEY}"
    assert seen["json"]["model"] == "doubao-seed-2-0-pro-260215"
    assert seen["json"]["messages"][1] == {"role": "user", "content": "Q1"}
    assert seen["json"]["messages"][-1] == {"role": "user", "content": "测试问题"}
    assert seen["stream"] is True


def test_doubao_stream_chat_raises_for_unauthorized(monkeypatch):
    client = ChatClient(api_key="ignored", model="doubao-2.0-lite")
    monkeypatch.setattr(
        chat_client.requests,
        "post",
        lambda *args, **kwargs: _DummyStreamResponse(status_code=401, json_data={"error": {"message": "bad key"}}),
    )

    with pytest.raises(RuntimeError, match="401 未授权。请检查豆包 API Key"):
        client.stream_chat("测试", lambda _delta: None)


def test_doubao_stream_chat_raises_for_timeout(monkeypatch):
    client = ChatClient(api_key="ignored", model="doubao-2.0-mini")

    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(chat_client.requests, "post", raise_timeout)

    with pytest.raises(RuntimeError, match="豆包请求超时"):
        client.stream_chat("测试", lambda _delta: None)


def test_doubao_stream_chat_raises_for_network_error(monkeypatch):
    client = ChatClient(api_key="ignored", model="doubao-2.0-mini")

    def raise_error(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(chat_client.requests, "post", raise_error)

    with pytest.raises(RuntimeError, match="豆包网络请求失败"):
        client.stream_chat("测试", lambda _delta: None)


def test_doubao_stream_chat_raises_for_empty_response(monkeypatch):
    client = ChatClient(api_key="ignored", model="doubao-2.0-mini")
    monkeypatch.setattr(chat_client.requests, "post", lambda *args, **kwargs: _DummyStreamResponse(lines=[b"data: [DONE]"]))

    with pytest.raises(RuntimeError, match="豆包未返回任何内容"):
        client.stream_chat("测试", lambda _delta: None)
