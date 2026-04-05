import feishu_bridge


def test_parse_text_message_content_reads_json_text():
    assert feishu_bridge.parse_text_message_content('{"text":"hello"}') == "hello"
    assert feishu_bridge.parse_text_message_content("plain text") == "plain text"


def test_prefix_helpers_add_and_strip():
    assert feishu_bridge.add_feishu_message_prefix("hello") == "computer_message hello"
    assert feishu_bridge.add_feishu_message_prefix("computer_message hello") == "computer_message hello"
    assert feishu_bridge.strip_feishu_message_prefix("computer_message hello") == "hello"
    assert feishu_bridge.strip_feishu_message_prefix("hello") == "hello"


def test_parse_remote_user_input_reply_supports_option_index():
    params = {
        "questions": [
            {
                "id": "q1",
                "header": "问题",
                "question": "请选择",
                "options": [{"label": "A"}, {"label": "B"}],
            }
        ]
    }

    answers, error = feishu_bridge.parse_remote_user_input_reply(params, "2")

    assert error == ""
    assert answers == {"q1": ["B"]}


def test_parse_remote_approval_reply_supports_keywords():
    assert feishu_bridge.parse_remote_approval_reply("item/commandExecution/requestApproval", "1") == "accept"
    assert (
        feishu_bridge.parse_remote_approval_reply("item/commandExecution/requestApproval", "允许本会话")
        == "acceptForSession"
    )
    assert feishu_bridge.parse_remote_approval_reply("item/commandExecution/requestApproval", "4") == "cancel"


def test_from_env_uses_built_in_test_credentials(monkeypatch):
    monkeypatch.delenv("FEISHU_BOT_ENABLED", raising=False)
    monkeypatch.delenv("FEISHU_BOT_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_BOT_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_ALLOWED_CHAT_IDS", raising=False)

    bridge = feishu_bridge.FeishuBotBridge.from_env(lambda _message: None)

    assert bridge is not None
    assert bridge.app_id == feishu_bridge.DEFAULT_FEISHU_BOT_APP_ID
    assert bridge.app_secret == feishu_bridge.DEFAULT_FEISHU_BOT_APP_SECRET
    assert bridge.allowed_chat_ids == {feishu_bridge.DEFAULT_FEISHU_CHAT_ID}
