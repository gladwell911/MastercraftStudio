import chat_client
from chat_client import ChatClient


class _DummyResponse:
    def __init__(self, *, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def test_extract_title_text_returns_first_non_empty_line():
    data = {
        "choices": [
            {
                "message": {
                    "content": "美食推荐\n这里是不应保留的第二行",
                }
            }
        ]
    }

    assert ChatClient._extract_title_text(data) == "美食推荐"


def test_generate_chat_title_uses_shared_prompt(monkeypatch):
    client = ChatClient(api_key="test-key")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["payload"] = json
        return _DummyResponse(
            status_code=200,
            json_data={"choices": [{"message": {"content": "美食推荐\n补充说明"}}]},
        )

    monkeypatch.setattr(chat_client.requests, "post", fake_post)

    assert client.generate_chat_title("用户需要介绍好吃的") == "美食推荐"
    assert seen["payload"]["messages"][0]["content"] == chat_client.TITLE_SYSTEM_PROMPT
