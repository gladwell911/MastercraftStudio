import pytest

import main


@pytest.mark.parametrize(
    ("model", "worker_name"),
    [
        ("codex/main", "_start_codex_worker_for_turn"),
        ("kimi/main", "_start_kimi_worker_for_turn"),
        ("claudecode/default", "_start_claudecode_worker_for_turn"),
    ],
)
def test_remote_ws_cli_models_use_their_dedicated_worker(
    frame, monkeypatch, model, worker_name
):
    """Remote requests must not fall through to the OpenRouter worker."""
    started = []
    monkeypatch.setattr(
        frame,
        worker_name,
        lambda *args: started.append(args),
    )
    monkeypatch.setattr(
        frame,
        "_play_send_sound",
        lambda: None,
    )
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args: None)

    class _UnexpectedGenericWorker:
        def __init__(self, *, target=None, **kwargs):
            if (
                getattr(target, "__self__", None) is frame
                and getattr(target, "__name__", "") == "_worker"
            ):
                pytest.fail("CLI model must not start the generic OpenRouter worker")

        def start(self):
            pass

    # Scope this global shim to the submission itself; wx may create unrelated
    # helper threads while the fixture is being torn down.
    with monkeypatch.context() as submission_patch:
        submission_patch.setattr(main.threading, "Thread", _UnexpectedGenericWorker)
        ok, message = frame._submit_question(
            "remote question",
            source="remote-ws",
            model=model,
            chat_id="remote-chat",
        )

    assert (ok, message) == (True, "")
    assert len(started) == 1
    assert started[0][0] == "remote-chat"
    assert started[0][2] == "remote question"
    assert frame.active_session_turns[-1]["model"] == model
