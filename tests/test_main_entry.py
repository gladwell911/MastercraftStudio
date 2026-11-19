import json
import main
import pytest
import subprocess
import sys
import threading
import time
from pathlib import Path


@pytest.mark.parametrize(
    "entry_args",
    [
        pytest.param(["main.py"], id="script"),
        pytest.param(["-m", "main"], id="module"),
        pytest.param(["-mmain"], id="compact-module"),
        pytest.param(
            ["-c", "import runpy; runpy.run_path('main.py', run_name='__main__')"],
            id="debugger",
        ),
    ],
)
def test_worker_entry_responds_to_ping(entry_args):
    request_id = "entry-ping"
    completed = subprocess.run(
        [sys.executable, *entry_args, "--codex-worker"],
        cwd=Path(__file__).resolve().parents[1],
        input=json.dumps({"type": "ping", "id": request_id}) + "\n",
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [event["type"] for event in events] == ["ready", "pong"]
    assert events[1]["id"] == request_id


def test_secondary_entry_preserves_original_application_binding():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import runpy

import main
import mc_frame_bindings

assert mc_frame_bindings.app is main
runpy.run_path(main.__file__, run_name='__main__')
assert mc_frame_bindings.app is main
print('binding preserved')
""",
            "--codex-worker",
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "binding preserved"


def test_app_shows_missing_onedrive_error_before_creating_frame(monkeypatch):
    class Checker:
        def IsAnotherRunning(self):
            return False

    messages = []
    monkeypatch.setattr(main.wx, "SingleInstanceChecker", lambda _name: Checker())
    monkeypatch.setattr(main, "resolve_common_commands_path", lambda: (_ for _ in ()).throw(RuntimeError("OneDrive 缺失")))
    monkeypatch.setattr(main.wx, "MessageBox", lambda *args: messages.append(args))
    monkeypatch.setattr(main, "ChatFrame", lambda: pytest.fail("frame must not start"))
    assert main.ChatApp.OnInit(type("FakeApp", (), {})()) is False
    assert messages[0][0] == "OneDrive 缺失"


def test_model_combo_order_prioritizes_cli_models(frame):
    choices = [frame.model_combo.GetString(idx) for idx in range(frame.model_combo.GetCount())]

    assert choices[:6] == [
        "codex",
        "codex gpt5.4 medium",
        "codex gpt5.3spark high",
        "claudeCode",
        "Kimi Code",
        "openclaw",
    ]


def test_chat_frame_initializes_incremental_list_models(frame):
    assert frame.history_list_model.control is frame.history_list
    assert frame.answer_list_model.control is frame.answer_list
    assert frame.execution_list_model.control is frame.execution_list
    assert frame.notes_notebook_list_model.control is frame.notes_notebook_list
    assert frame.notes_entry_list_model.control is frame.notes_entry_list


def test_global_ctrl_emit_deduplicates_same_side_within_window(monkeypatch):
    seen = []
    hook = main.GlobalCtrlTapHook(lambda combo_used, side: seen.append((combo_used, side)))
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    hook._emit_ctrl_keyup(False, "left")
    hook._emit_ctrl_keyup(True, "left")
    assert seen == [(False, "left")]


def test_global_ctrl_poller_release_enabled_when_hook_missing():
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = None
    assert hook._should_use_poller_release()


def test_global_ctrl_poller_release_disabled_when_hook_recent(monkeypatch):
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = object()
    hook._using_fallback = False
    hook._last_hook_event_at = 100.0
    hook._hook_stale_seconds = 0.45
    monkeypatch.setattr(main.time, "monotonic", lambda: 100.2)
    assert hook._should_use_poller_release()


def test_global_ctrl_poller_release_enabled_when_hook_stale(monkeypatch):
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = object()
    hook._using_fallback = False
    hook._last_hook_event_at = 100.0
    hook._hook_stale_seconds = 0.45
    monkeypatch.setattr(main.time, "monotonic", lambda: 101.0)
    assert hook._should_use_poller_release()


def test_global_ctrl_poller_release_enabled_even_when_hook_is_recent():
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = object()
    hook._using_fallback = False
    hook._last_hook_event_at = time.monotonic()
    assert hook._should_use_poller_release()


def test_global_ctrl_poller_detects_left_release_without_waiting_for_hook_staleness(monkeypatch):
    seen = []
    hook = main.GlobalCtrlTapHook(lambda combo_used, side: seen.append((combo_used, side)))
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    hook._running = True
    hook._hook = object()
    hook._using_fallback = False

    hook._process_poller_state(left_down=True, right_down=False)
    hook._process_poller_state(left_down=False, right_down=False)

    assert seen == [(False, "left")]


def test_large_legacy_app_state_migrates_to_chat_store_and_slims_json(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    legacy = {
        "selected_model_id": "codex/main",
        "active_chat_id": "chat-active",
        "active_session_turns": [{"question": "active", "answer_md": "answer"}],
        "archived_chats": [
            {"id": "chat-old", "title": "old", "turns": [{"question": "q", "answer_md": "a" * 10000}]}
        ],
    }
    (tmp_path / "app_state.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    f = main.ChatFrame()
    try:
        assert f.chat_store.load_turns("chat-old")[0]["question"] == "q"
        data = json.loads((tmp_path / "app_state.json").read_text(encoding="utf-8"))
        assert "archived_chats" not in data
        assert "active_session_turns" not in data
        assert (tmp_path / "app_state.json").stat().st_size < 200_000
        assert list(tmp_path.glob("app_state.json.bak.*"))
    finally:
        f.Destroy()


def test_bind_events_registers_both_hotkey_ids():
    frame_bind_calls = []
    control_bindings = {}

    class _Ctrl:
        def __init__(self, name):
            self.name = name

        def Bind(self, event, *_args, **_kwargs):
            control_bindings.setdefault(self.name, []).append(event)
            return None

    class _DummyFrame:
        def __init__(self):
            self.send_button = _Ctrl("send_button")
            self.new_chat_button = _Ctrl("new_chat_button")
            self.notes_button = _Ctrl("notes_button")
            self.model_combo = _Ctrl("model_combo")
            self.codex_speed_combo = _Ctrl("codex_speed_combo")
            self.input_edit = _Ctrl("input_edit")
            self.answer_list = _Ctrl("answer_list")
            self.execution_list = _Ctrl("execution_list")
            self.history_list = _Ctrl("history_list")
            self.notes_search_ctrl = _Ctrl("notes_search_ctrl")
            self.notes_search_button = _Ctrl("notes_search_button")
            self.notes_clear_search_button = _Ctrl("notes_clear_search_button")
            self.notes_notebook_list = _Ctrl("notes_notebook_list")
            self.notes_new_notebook_button = _Ctrl("notes_new_notebook_button")
            self.notes_rename_notebook_button = _Ctrl("notes_rename_notebook_button")
            self.notes_delete_notebook_button = _Ctrl("notes_delete_notebook_button")
            self.notes_back_button = _Ctrl("notes_back_button")
            self.notes_entry_list = _Ctrl("notes_entry_list")
            self.notes_new_entry_button = _Ctrl("notes_new_entry_button")
            self.notes_edit_entry_button = _Ctrl("notes_edit_entry_button")
            self.notes_delete_entry_button = _Ctrl("notes_delete_entry_button")
            self.notes_pin_entry_button = _Ctrl("notes_pin_entry_button")
            self.notes_bottom_entry_button = _Ctrl("notes_bottom_entry_button")
            self.notes_import_file_button = _Ctrl("notes_import_file_button")
            self.notes_import_clipboard_button = _Ctrl("notes_import_clipboard_button")
            self.notes_editor = _Ctrl("notes_editor")
            self.notes_save_button = _Ctrl("notes_save_button")
            self.notes_cancel_button = _Ctrl("notes_cancel_button")
            self._chat_navigation_left_id = 1
            self._chat_navigation_right_id = 2

        def Bind(self, _event, _handler, id=None):
            frame_bind_calls.append(id)

        def SetAcceleratorTable(self, *_args, **_kwargs):
            return None

        def _on_send_clicked(self, *_args):
            return None

        def _on_new_chat_clicked(self, *_args):
            return None

        def _on_open_realtime_call_settings(self, *_args):
            return None

        def _on_model_changed(self, *_args):
            return None

        def _on_codex_speed_changed(self, *_args):
            return None

        def _on_input_key_down(self, *_args):
            return None

        def _on_input_key_up(self, *_args):
            return None

        def _on_char_hook(self, *_args):
            return None

        def _on_show_sync_tray_state(self, *_args):
            return None

        def _on_close(self, *_args):
            return None

        def _on_global_hotkey(self, *_args):
            return None

        def _on_frame_key_down(self, *_args):
            return None

        def _on_generic_key_down(self, *_args):
            return None

        def _on_any_key_down_escape_minimize(self, *_args):
            return None

        def _on_answer_key_down(self, *_args):
            return None

        def _on_answer_char(self, *_args):
            return None

        def _on_answer_activate(self, *_args):
            return None

        def _on_execution_key_down(self, *_args):
            return None

        def _on_execution_char(self, *_args):
            return None

        def _on_execution_activate(self, *_args):
            return None

        def _activate_selected_history(self, *_args):
            return None

        def _on_history_key_down(self, *_args):
            return None

        def _on_history_char(self, *_args):
            return None

        def _on_history_selected(self, *_args):
            return None

        def _on_history_context(self, *_args):
            return None

        def _on_notes_key_down(self, *_args):
            return None

        def _on_notes_context(self, *_args):
            return None

        def _on_notes_notebook_selected(self, *_args):
            return None

        def _on_notes_entry_selected(self, *_args):
            return None

        def _on_notes_editor_changed(self, *_args):
            return None

    dummy = _DummyFrame()
    main.ChatFrame._bind_events(dummy)

    assert main.HOTKEY_ID_SHOW in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT2 in frame_bind_calls
    assert main.wx.EVT_KEY_UP in control_bindings["input_edit"]
    assert main.wx.EVT_KEY_UP in control_bindings["send_button"]
    assert main.wx.EVT_KEY_UP in control_bindings["new_chat_button"]
    assert main.wx.EVT_KEY_DOWN in control_bindings["new_chat_button"]
    assert main.wx.EVT_CHAR_HOOK in control_bindings["new_chat_button"]
    assert main.wx.EVT_CHAR in control_bindings["new_chat_button"]
    assert main.wx.EVT_KEY_UP in control_bindings["model_combo"]
    assert main.wx.EVT_KEY_UP in control_bindings["answer_list"]
    assert main.wx.EVT_KEY_UP in control_bindings["execution_list"]
    assert main.wx.EVT_KEY_UP in control_bindings["history_list"]
    assert main.wx.EVT_KEY_DOWN in control_bindings["execution_list"]
    assert main.wx.EVT_CHAR in control_bindings["execution_list"]
    assert main.wx.EVT_LISTBOX_DCLICK in control_bindings["execution_list"]


def test_remove_trailing_punctuation():
    assert main.remove_trailing_punctuation("你好！！！") == "你好"
    assert main.remove_trailing_punctuation("hello...") == "hello"
    assert main.remove_trailing_punctuation("。！？") == ""
    assert main.remove_trailing_punctuation("正常文本") == "正常文本"


def test_resolve_chat_target_returns_current_chat_for_blank_id(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"

    chat, is_current = frame._resolve_chat_target("")

    assert chat is frame._current_chat_state
    assert is_current is True


def test_resolve_current_model_uses_combo_selection(frame):
    frame.selected_model = "openai/gpt-5.2"
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528")
    assert frame._resolve_current_model() == "deepseek/deepseek-r1-0528"


def test_model_ids_contains_new_models():
    assert "stepfun/step-3.5-flash" in main.MODEL_IDS
    assert "meta-llama/llama-3.1-8b-instruct" in main.MODEL_IDS
    assert "z-ai/glm-4.5-air" in main.MODEL_IDS
    assert "deepseek/deepseek-r1-0528-qwen3-8b" in main.MODEL_IDS
    assert "qwen/qwen3-8b" in main.MODEL_IDS
    assert "doubao-2.0-pro" in main.MODEL_IDS
    assert "doubao-2.0-lite" in main.MODEL_IDS
    assert "doubao-2.0-mini" in main.MODEL_IDS
    assert "doubao-2.0-pro" in main.VISIBLE_MODEL_IDS
    assert "doubao-2.0-lite" in main.VISIBLE_MODEL_IDS
    assert "doubao-2.0-mini" in main.VISIBLE_MODEL_IDS


def test_model_combo_shows_display_names(frame):
    choices = list(frame.model_combo.GetItems())
    assert "openclaw" in choices
    assert "codex" in choices
    assert "codex gpt5.4 medium" in choices
    assert "codex gpt5.3spark high" in choices
    assert "claudeCode" in choices
    assert "openclaw/main" not in choices
    assert "codex/main" not in choices
    assert "codex/gpt-5.4-medium" not in choices
    assert "codex/gpt-5.3-codex-spark-high" not in choices
    assert "claudecode/default" not in choices


def test_resolve_current_model_uses_cached_value_off_main_thread(frame, monkeypatch):
    frame.selected_model = "codex/main"

    class _ForbiddenCombo:
        def GetStringSelection(self):
            raise AssertionError("background thread should not touch combobox")

        def GetSelection(self):
            raise AssertionError("background thread should not touch combobox")

        def GetValue(self):
            raise AssertionError("background thread should not touch combobox")

    monkeypatch.setattr(frame, "model_combo", _ForbiddenCombo())

    result = {}

    def _worker():
        result["value"] = frame._resolve_current_model()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert result["value"] == "codex/main"


def test_sanitize_optimized_text_dedupes_near_duplicate_lines():
    src = "今天天气很好。\n今天天气很好！\n今天天气很好\n"
    out = main.sanitize_optimized_text(src)
    lines = [x for x in out.split("\n") if x.strip()]
    assert len(lines) == 1
    assert "今天天气很好" in lines[0]


def test_close_not_vetoed_while_running(frame):
    frame.is_running = True
    calls = {"skip": 0, "veto": 0}

    class CloseEvent:
        def Skip(self):
            calls["skip"] += 1

        def Veto(self):
            calls["veto"] += 1

    frame._on_close(CloseEvent())
    assert calls["skip"] == 1
    assert calls["veto"] == 0


def test_packaging_name_defaults_to_mc():
    spec_text = Path("zgwd.spec").read_text(encoding="utf-8")
    assert spec_text.count("name='mc'") == 2


def test_package_script_rejects_admin_shell_before_running_pyinstaller():
    script_text = Path("package_mc.ps1").read_text(encoding="utf-8")
    assert "WindowsBuiltInRole" in script_text
    assert "Administrator" in script_text
    assert "pyinstaller" in script_text.lower()


def test_package_script_has_valid_powershell_syntax():
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[void][System.Management.Automation.Language.Parser]::ParseFile('package_mc.ps1',[ref]$null,[ref]$errors); $errors.Count",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "0"


def test_wx_call_later_if_alive_skips_when_top_window_handle_is_invalid(monkeypatch):
    class _TopWindow:
        def IsBeingDeleted(self):
            return False

        def GetHandle(self):
            return 0

    class _App:
        def GetTopWindow(self):
            return _TopWindow()

    called = {"later": False}
    monkeypatch.setattr(main.wx, "GetApp", lambda: _App())
    monkeypatch.setattr(main.wx, "CallLater", lambda *args, **kwargs: called.__setitem__("later", True))

    result = main.wx_call_later_if_alive(120, lambda: None)

    assert result is None
    assert called["later"] is False


def test_wx_call_after_if_alive_skips_when_top_window_handle_is_invalid(monkeypatch):
    class _TopWindow:
        def IsBeingDeleted(self):
            return False

        def GetHandle(self):
            return 0

    class _App:
        def GetTopWindow(self):
            return _TopWindow()

    called = {"after": False}
    monkeypatch.setattr(main.wx, "GetApp", lambda: _App())
    monkeypatch.setattr(main.wx, "CallAfter", lambda *args, **kwargs: called.__setitem__("after", True))

    result = main.wx_call_after_if_alive(lambda: None)

    assert result is False
    assert called["after"] is False


def test_idle_ui_refresh_delay_is_one_second():
    assert main.IDLE_UI_REFRESH_DELAY_MS == 1000


def test_slow_ui_operation_records_only_above_threshold(frame, monkeypatch):
    frame.ui_perf_slow_threshold_ms = 20.0
    samples = iter([10.0, 10.01, 20.0, 20.05])
    monkeypatch.setattr(main.time, "perf_counter", lambda: next(samples))

    with frame._measure_ui_operation("fast-path"):
        pass
    with frame._measure_ui_operation("slow-path"):
        pass

    assert [sample["name"] for sample in frame.ui_perf_slow_samples] == ["slow-path"]
    assert frame.ui_perf_slow_samples[0]["elapsed_ms"] == pytest.approx(50.0)


def test_input_text_change_is_bound_to_slow_operation_monitor(frame, monkeypatch):
    calls = []
    original_bind = frame.input_edit.Bind

    def capture_bind(event_type, handler, *args, **kwargs):
        if event_type == main.wx.EVT_TEXT:
            calls.append(handler)
        return original_bind(event_type, handler, *args, **kwargs)

    monkeypatch.setattr(frame.input_edit, "Bind", capture_bind)
    frame._bind_events()

    assert calls
    assert getattr(calls[-1], "_ui_perf_name", "") == "input_text"
