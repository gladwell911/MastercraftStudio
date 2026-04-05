from chat_client import ChatClient


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
