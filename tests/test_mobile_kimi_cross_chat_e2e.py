import pytest

import main
from remote_nats import RemoteNatsTransport


class _RecordingClaudeClient:
    def __init__(self):
        self.inputs = []

    def send_user_input(self, text):
        self.inputs.append(text)


def _prepare_remote_chat(frame, monkeypatch):
    frame.active_chat_id = "mobile-target-chat"
    frame.current_chat_id = "mobile-target-chat"
    frame.active_session_turns = []
    frame._current_chat_state.update(
        {
            "id": "mobile-target-chat",
            "model": main.DEFAULT_MODEL_ID,
            "turns": frame.active_session_turns,
        }
    )
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)


def _mobile_transport(frame):
    return RemoteNatsTransport(
        pair_id="qa-mobile-desktop",
        token="qa-token",
        on_message=frame._remote_api_message_ui,
    )


def test_e2e_mobile_nats_kimi_message_isolated_from_other_chat_claude(
    frame, monkeypatch
):
    _prepare_remote_chat(frame, monkeypatch)
    claude_client = _RecordingClaudeClient()
    frame._set_active_claudecode_client(claude_client, "claude-running-chat")
    kimi_workers = []
    monkeypatch.setattr(
        frame,
        "_start_kimi_worker_for_turn",
        lambda *args: kimi_workers.append(args),
    )
    monkeypatch.setattr(
        frame,
        "_start_codex_worker_for_turn",
        lambda *_args: pytest.fail("Kimi message must not start Codex"),
    )
    monkeypatch.setattr(
        frame,
        "_start_claudecode_worker_for_turn",
        lambda *_args: pytest.fail("Kimi message must not start Claude"),
    )
    monkeypatch.setattr(
        main,
        "openrouter_api_key_for_app",
        lambda: pytest.fail("Kimi message must not read the OpenRouter key"),
    )

    status, body = _mobile_transport(frame)._route_command(
        {
            "id": "mobile-message-1",
            "type": "message",
            "chat_id": "mobile-target-chat",
            "text": "来自手机端的 Kimi 问题",
            "model": "kimi/main",
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body == {
        "accepted": True,
        "message": "",
        "chat_id": "mobile-target-chat",
        "model": "kimi/main",
    }
    assert claude_client.inputs == []
    assert frame._active_claudecode_client is claude_client
    assert frame._active_claudecode_chat_id == "claude-running-chat"
    assert len(kimi_workers) == 1
    assert kimi_workers[0][0] == "mobile-target-chat"
    assert kimi_workers[0][2] == "来自手机端的 Kimi 问题"
    assert kimi_workers[0][-1] == "kimi/main"
    assert frame.active_session_turns[-1]["question"] == "来自手机端的 Kimi 问题"
    assert frame.active_session_turns[-1]["model"] == "kimi/main"


def test_e2e_mobile_nats_same_chat_claude_message_continues_active_client(
    frame, monkeypatch
):
    _prepare_remote_chat(frame, monkeypatch)
    claude_client = _RecordingClaudeClient()
    frame._set_active_claudecode_client(claude_client, "mobile-target-chat")
    monkeypatch.setattr(
        frame,
        "_start_claudecode_worker_for_turn",
        lambda *_args: pytest.fail("Claude continuation must not start another worker"),
    )

    status, body = _mobile_transport(frame)._route_command(
        {
            "id": "mobile-message-2",
            "type": "message",
            "chat_id": "mobile-target-chat",
            "text": "继续当前 Claude 会话",
            "model": "claudecode/opus",
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"] == "mobile-target-chat"
    assert body["model"] == "claudecode/opus"
    assert claude_client.inputs == ["继续当前 Claude 会话"]
    assert frame.active_session_turns == []


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            {
                "id": "mobile-empty",
                "type": "message",
                "chat_id": "mobile-target-chat",
                "text": "   ",
                "model": "kimi/main",
                "model_change_mode": "explicit",
            },
            "empty_text",
        ),
        (
            {
                "id": "mobile-invalid-model",
                "type": "message",
                "chat_id": "mobile-target-chat",
                "text": "不能发送给未知模型",
                "model": "not-a-real-model",
                "model_change_mode": "explicit",
            },
            "invalid_explicit_model",
        ),
    ],
)
def test_api_mobile_nats_message_rejects_invalid_request_before_client_or_worker(
    frame, monkeypatch, payload, expected_error
):
    _prepare_remote_chat(frame, monkeypatch)
    claude_client = _RecordingClaudeClient()
    frame._set_active_claudecode_client(claude_client, "mobile-target-chat")
    for worker_name in (
        "_start_kimi_worker_for_turn",
        "_start_codex_worker_for_turn",
        "_start_claudecode_worker_for_turn",
    ):
        monkeypatch.setattr(
            frame,
            worker_name,
            lambda *_args, name=worker_name: pytest.fail(
                f"invalid request must not start {name}"
            ),
        )

    status, body = _mobile_transport(frame)._route_command(payload)

    assert status == 400
    assert body["accepted"] is False
    assert body["error"] == expected_error
    assert claude_client.inputs == []
    assert frame.active_session_turns == []
