import pytest

import main


def _prepare_remote_ingress(frame, monkeypatch):
    frame.active_chat_id = "remote-chat"
    frame.current_chat_id = "remote-chat"
    frame.active_session_turns = []
    frame._current_chat_state.update(
        {
            "id": "remote-chat",
            "model": main.DEFAULT_MODEL_ID,
            "turns": frame.active_session_turns,
        }
    )
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize(
    ("model", "worker_name"),
    [
        ("codex/main", "_start_codex_worker_for_turn"),
        ("kimi/main", "_start_kimi_worker_for_turn"),
        ("claudecode/opus", "_start_claudecode_worker_for_turn"),
    ],
)
def test_remote_ws_cli_models_use_their_dedicated_worker(
    frame, monkeypatch, model, worker_name
):
    """Remote requests must not fall through to the OpenRouter worker."""
    _prepare_remote_ingress(frame, monkeypatch)
    started = []
    for candidate in (
        "_start_codex_worker_for_turn",
        "_start_kimi_worker_for_turn",
        "_start_claudecode_worker_for_turn",
    ):
        if candidate == worker_name:
            monkeypatch.setattr(
                frame,
                candidate,
                lambda *args, name=candidate: started.append((name, args)),
            )
        else:
            monkeypatch.setattr(
                frame,
                candidate,
                lambda *_args, name=candidate: pytest.fail(
                    f"{model} must not start {name}"
                ),
            )
    monkeypatch.setattr(
        main,
        "openrouter_api_key_for_app",
        lambda: pytest.fail("CLI model must not read the OpenRouter API key"),
    )

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "remote-chat",
            "text": "remote question",
            "model": model,
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body == {
        "accepted": True,
        "message": "",
        "chat_id": "remote-chat",
        "model": model,
    }
    assert len(started) == 1
    started_name, args = started[0]
    assert started_name == worker_name
    assert args[0] == "remote-chat"
    assert args[2] == "remote question"
    assert args[-1] == model
    assert frame.active_session_turns[-1]["model"] == model


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-5.2",
        "anthropic/claude-sonnet-4.6",
    ],
)
def test_remote_ws_openrouter_model_keeps_generic_worker(frame, monkeypatch, model):
    """Remote non-CLI models must keep using the generic OpenRouter worker."""
    _prepare_remote_ingress(frame, monkeypatch)
    started = []
    monkeypatch.setattr(main, "openrouter_api_key_for_app", lambda: "test-key")
    for worker_name in (
        "_start_codex_worker_for_turn",
        "_start_kimi_worker_for_turn",
        "_start_claudecode_worker_for_turn",
    ):
        monkeypatch.setattr(
            frame,
            worker_name,
            lambda *_args, name=worker_name: pytest.fail(
                f"OpenRouter model must not start {name}"
            ),
        )

    class _RecordingThread:
        def __init__(self, *, target=None, args=(), **kwargs):
            started.append((target, args, kwargs))

        def start(self):
            pass

    with monkeypatch.context() as ingress_patch:
        ingress_patch.setattr(main.threading, "Thread", _RecordingThread)
        status, body = frame._remote_api_message_ui(
            {
                "chat_id": "remote-chat",
                "text": "remote question",
                "model": model,
                "model_change_mode": "explicit",
            }
        )

    assert status == 200
    assert body["accepted"] is True
    assert body["model"] == model
    assert len(started) == 1
    target, args, kwargs = started[0]
    assert getattr(target, "__self__", None) is frame
    assert getattr(target, "__name__", "") == "_worker"
    assert args[0] == "test-key"
    assert args[2] == "remote question"
    assert args[3] == model
    assert args[5] == "remote-chat"
    assert kwargs["daemon"] is True


def test_remote_ws_kimi_message_does_not_enter_another_chats_claude_client(
    frame, monkeypatch
):
    _prepare_remote_ingress(frame, monkeypatch)
    claude_inputs = []
    active_claude_client = type(
        "_ActiveClaudeClient",
        (),
        {"send_user_input": lambda _self, text: claude_inputs.append(text)},
    )()
    frame._set_active_claudecode_client(active_claude_client, "claude-chat")
    started = []
    monkeypatch.setattr(
        frame,
        "_start_kimi_worker_for_turn",
        lambda *args: started.append(args),
    )
    monkeypatch.setattr(
        frame,
        "_start_claudecode_worker_for_turn",
        lambda *_args: pytest.fail("Kimi request must not start a Claude worker"),
    )
    monkeypatch.setattr(
        main,
        "openrouter_api_key_for_app",
        lambda: pytest.fail("Kimi request must not read the OpenRouter API key"),
    )

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "remote-chat",
            "text": "question for kimi",
            "model": "kimi/main",
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert claude_inputs == []
    assert len(started) == 1
    assert started[0][0] == "remote-chat"
    assert started[0][2] == "question for kimi"
    assert frame.active_session_turns[-1]["question"] == "question for kimi"
    assert frame.active_session_turns[-1]["model"] == "kimi/main"


def test_remote_ws_same_chat_message_continues_active_claude_client(
    frame, monkeypatch
):
    _prepare_remote_ingress(frame, monkeypatch)
    claude_inputs = []
    active_claude_client = type(
        "_ActiveClaudeClient",
        (),
        {"send_user_input": lambda _self, text: claude_inputs.append(text)},
    )()
    frame._set_active_claudecode_client(active_claude_client, "remote-chat")
    monkeypatch.setattr(
        frame,
        "_start_claudecode_worker_for_turn",
        lambda *_args: pytest.fail("continuation must not start another Claude worker"),
    )

    status, body = frame._remote_api_message_ui(
        {
            "chat_id": "remote-chat",
            "text": "continue claude",
            "model": "claudecode/opus",
            "model_change_mode": "explicit",
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert claude_inputs == ["continue claude"]
    assert frame.active_session_turns == []


@pytest.mark.parametrize("fails", [False, True])
def test_claudecode_worker_reports_dynamic_model_on_completion(
    frame, monkeypatch, fails
):
    completed = []

    class _ImmediateThread:
        def __init__(self, *, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    class _ClaudeClient:
        last_context_usage = None

        def __init__(self, *args, **kwargs):
            pass

        def stream_chat(self, *args, **kwargs):
            assert frame._active_claudecode_client is self
            assert frame._active_claudecode_chat_id == "remote-chat"
            if fails:
                raise RuntimeError("claude failed")
            return "claude answer", "session-new"

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main, "ClaudeCodeClient", _ClaudeClient)
    monkeypatch.setattr(
        main,
        "wx_call_after_if_alive",
        lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )
    monkeypatch.setattr(frame, "_on_done", lambda *args: completed.append(args))
    frame.active_chat_id = "remote-chat"
    frame.current_chat_id = "remote-chat"

    frame._start_claudecode_worker_for_turn(
        "remote-chat",
        7,
        "remote question",
        "session-old",
        "claudecode/opus",
    )

    assert len(completed) == 1
    assert completed[0][0] == 7
    assert completed[0][1] == ("" if fails else "claude answer")
    assert completed[0][2] == ("claude failed" if fails else "")
    assert completed[0][3] == "claudecode/opus"
    assert completed[0][5] == "remote-chat"
    assert frame._active_claudecode_client is None
    assert frame._active_claudecode_chat_id == ""


def test_claudecode_worker_cleanup_does_not_clear_replacement_client(
    frame, monkeypatch
):
    replacement_client = object()

    class _ImmediateThread:
        def __init__(self, *, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    class _ClaudeClient:
        last_context_usage = None

        def __init__(self, *args, **kwargs):
            pass

        def stream_chat(self, *args, **kwargs):
            frame._set_active_claudecode_client(replacement_client, "new-chat")
            return "claude answer", "session-new"

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main, "ClaudeCodeClient", _ClaudeClient)
    monkeypatch.setattr(main, "wx_call_after_if_alive", lambda *_args, **_kwargs: None)
    frame.active_chat_id = "remote-chat"
    frame.current_chat_id = "remote-chat"

    frame._start_claudecode_worker_for_turn(
        "remote-chat",
        7,
        "remote question",
        "session-old",
        "claudecode/opus",
    )

    assert frame._active_claudecode_client is replacement_client
    assert frame._active_claudecode_chat_id == "new-chat"
