import ctypes
import copy
import asyncio
import json
import os
import platform
import re
import socket
import subprocess
import shutil
import threading
import time
import uuid
import webbrowser
import winsound
import sys
from ctypes import wintypes
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit

import markdown
import wx
import wx.adv
from aiohttp import ClientSession, ClientTimeout, WSMsgType

from chat_store import ChatStore
from chat_client import ChatClient, DEFAULT_MODEL
from claudecode_client import ClaudeCodeClient, DEFAULT_CLAUDECODE_MODEL, is_claudecode_model
from cli_agent_manager import get_default_cli_agent_manager
from codex_client import (
    CodexAppServerClient,
    CodexEvent,
    DEFAULT_CODEX_MODEL,
    codex_model_label_for_model,
    is_codex_model,
    read_codex_cli_model_label,
)
from context_usage import (
    context_usage_from_dict,
    estimate_turns_tokens,
    format_context_usage_label,
)
from notes_import import import_note_entries_from_clipboard, import_note_entries_from_file
from notes_backup import export_notes_backup, restore_notes_backup
from notes_projection import DesktopNotesProjection
from notes_store import NotesStore
from notes_sync import NotesSyncService
from notes_ui import DesktopNotesController
from openclaw_client import (
    DEFAULT_OPENCLAW_AGENT,
    DEFAULT_OPENCLAW_SESSION_KEY,
    OpenClawClient,
    OpenClawSyncEvent,
    is_openclaw_model,
    load_session_pointer,
    load_session_pointer_by_session_id,
    normalize_openclaw_text,
    read_session_events,
    resolve_openclaw_sessions_dir,
)
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from realtime_call import (
    DEFAULT_REALTIME_CALL_ROLE,
    DEFAULT_REALTIME_CALL_SPEECH_RATE,
    RealtimeCallController,
    RealtimeCallSettings,
)
from speech_input import MODE_DIRECT, MODE_OPTIMIZE, VoiceInputController
from zdsr_tts import ZDSRTTSClient

REQUESTING_TEXT = "正在请求..."
EMPTY_CURRENT_CHAT_TITLE = "心聊天"
APP_STATE_FILE = "app_state.json"
APP_WINDOW_TITLE = "神匠工坊"
MAX_RECOVERY_ATTEMPTS = 3
CODEX_BACKGROUND_FLUSH_DELAY_MS = 250
CODEX_UI_EVENT_BATCH_SIZE = 25
CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE = 5
CODEX_UI_EVENT_BATCH_DELAY_MS = 10
ANSWER_LIST_DEFAULT_VISIBLE_ROWS = 100
ANSWER_LIST_EXPAND_ROWS = 100
EXECUTION_LIST_DEFAULT_VISIBLE_ROWS = 100
EXECUTION_LIST_EXPAND_ROWS = 100
REMOTE_STATE_DEFAULT_TURN_LIMIT = 100
NOTES_ENTRY_LABEL_MAX_CHARS = 160
CONTEXT_USAGE_SYNC_TURN_LIMIT = 120
HOTKEY_ID_SHOW = 0xA112
HOTKEY_ID_REALTIME_CALL = 0xA113
HOTKEY_ID_REALTIME_CALL_ALT = 0xA114
HOTKEY_ID_REALTIME_CALL_ALT2 = 0xA115
WM_QUIT = 0x0012

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_CHAR = 0x0102
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_MENU = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
DEFAULT_REMOTE_CONTROL_TOKEN = "h9k2m7p4q8x1z6v3t5n9c2r7d4s8j1f6"
DEFAULT_REMOTE_CONTROL_DOMAIN = "wss://rc.tingyou.cc/nats"
DEFAULT_REMOTE_NATS_PORT = 4222
REMOTE_NATS_PORT_FALLBACKS = (
    4622,
    4822,
    4223,
    4224,
    4522,
)
DEFAULT_REMOTE_NATS_WEBSOCKET_PORT = 18080
REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS = (
    18081,
    18082,
    10080,
    28080,
    28081,
)
DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT = 18080
DEFAULT_REMOTE_NATS_CLOUDFLARED_URL = "wss://rc.tingyou.cc/nats"
REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS = 5
VK_PROCESSKEY = 0xE5
VK_PACKET = 0xE7
VK_V = 0x56
VK_OEM_5 = 0xDC
VK_OEM_102 = 0xE2
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_VALUECHANGE = 0x800E
OBJID_CLIENT = -4
CHILDID_SELF = 0

MODEL_IDS = [
    "codex/main",
    "codex/gpt-5.4-medium",
    "codex/gpt-5.3-codex-spark-high",
    "claudecode/default",
    "openclaw/main",
    "stepfun/step-3.5-flash",
    "meta-llama/llama-3.1-8b-instruct",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-pro-image-preview",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-opus-4.5",
    "z-ai/glm-4.5-air",
    "qwen/qwen3.5-plus-02-15",
    "qwen/qwen3-8b",
    "minimax/minimax-m2.5",
    "minimax/minimax-m2-her",
    "z-ai/glm-5",
    "moonshotai/kimi-k2.5",
    "openai/gpt-5.2",
    "openai/gpt-5.2-codex",
    "openai/gpt-5.2-chat",
    "openai/gpt-5.2-pro",
    "bytedance-seed/seed-1.6",
    "doubao-2.0-pro",
    "doubao-2.0-lite",
    "doubao-2.0-mini",
    "xiaomi/mimo-v2-flash",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-r1-0528-qwen3-8b",
]
HIDDEN_MODEL_IDS = {
    "stepfun/step-3.5-flash",
    "meta-llama/llama-3.1-8b-instruct",
    "z-ai/glm-4.5-air",
    "qwen/qwen3-8b",
    "openai/gpt-5.2-codex",
    "openai/gpt-5.2-pro",
    "deepseek/deepseek-r1-0528-qwen3-8b",
}
VISIBLE_MODEL_IDS = [m for m in MODEL_IDS if m not in HIDDEN_MODEL_IDS]
DEFAULT_MODEL_ID = DEFAULT_CODEX_MODEL
STARTUP_DEFAULT_MODEL_ID = DEFAULT_MODEL_ID
APP_DIR_NAME = "神匠工坊"
VOICE_OPTIMIZE_MODEL = "openai/gpt-5.2-chat"
VOICE_OPTIMIZE_PROMPT = (
    "请对以下文本进行整理与优化，保持原意不变。要求：\n"
    "- 仅对所给文字进行优化整理，不对文字中的问题进行回答\n"
    "- 语言简洁、逻辑清晰、表达准确\n"
    "- 删除语气词、口头禅、赘述与重复信息\n"
    "- 不使用 Markdown 格式\n"
    "- 不添加额外标点符号或表情符号\n"
    "- 仅保留纯文字内容与自然段落结构\n"
    "- 不要空行\n"
    "- 不新增事实、不引入推测"
)


def _wx_target_is_alive(target) -> bool:
    if target is None:
        return False
    try:
        if hasattr(target, "IsBeingDeleted") and target.IsBeingDeleted():
            return False
        if hasattr(target, "GetHandle") and not target.GetHandle():
            return False
        return True
    except Exception:
        return False


def _wx_app_allows_ui_timers() -> bool:
    app = wx.GetApp()
    if app is None:
        return False
    try:
        top_window = app.GetTopWindow() if hasattr(app, "GetTopWindow") else None
    except Exception:
        return False
    if top_window is None:
        return True
    return _wx_target_is_alive(top_window)


def wx_call_after_if_alive(func, *args, **kwargs) -> bool:
    if not _wx_app_allows_ui_timers():
        return False
    target = getattr(func, "__self__", None)
    if target is not None and not _wx_target_is_alive(target):
        return False
    try:
        wx.CallAfter(func, *args, **kwargs)
        return True
    except Exception:
        return False


def wx_call_later_if_alive(delay_ms: int, func, *args, **kwargs):
    if not _wx_app_allows_ui_timers():
        return None
    target = getattr(func, "__self__", None)
    if target is not None and not _wx_target_is_alive(target):
        return None
    try:
        timer = wx.CallLater(delay_ms, func, *args, **kwargs)
        return timer if timer is not None else True
    except Exception:
        return None


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def resolve_app_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir.parent / "history"
    return Path(__file__).resolve().parent / "dist" / "history"


_CHAT_TITLE_RULES_CACHE: dict | None = None


def shared_chat_title_rules_path() -> Path:
    current = Path(__file__).resolve()
    seen: set[Path] = set()
    for base in [current.parent, *current.parents, Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        base = Path(base).resolve()
        if base in seen:
            continue
        seen.add(base)
        candidate = base / "rc" / "assets" / "chat_title_rules.json"
        if candidate.exists():
            return candidate
    return current.parents[2] / "rc" / "assets" / "chat_title_rules.json"


def load_chat_title_rules(path: Path | None = None, *, refresh: bool = False) -> dict:
    global _CHAT_TITLE_RULES_CACHE
    target = Path(path) if path is not None else shared_chat_title_rules_path()
    if _CHAT_TITLE_RULES_CACHE is not None and not refresh and path is None:
        return _CHAT_TITLE_RULES_CACHE
    data = json.loads(target.read_text(encoding="utf-8"))
    rules = {
        "leading_phrases": [str(item) for item in data.get("leading_phrases", []) if str(item).strip()],
        "action_prefixes": [str(item) for item in data.get("action_prefixes", []) if str(item).strip()],
        "what_is_prefixes": [str(item) for item in data.get("what_is_prefixes", []) if str(item).strip()],
        "question_suffixes": [str(item) for item in data.get("question_suffixes", []) if str(item).strip()],
        "trailing_punctuation": [str(item) for item in data.get("trailing_punctuation", []) if str(item).strip()],
    }
    if path is None:
        _CHAT_TITLE_RULES_CACHE = rules
    return rules


def normalize_remote_nats_endpoint(value: str, *, default_scheme: str = "wss") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"{default_scheme}://{raw}"
    elif raw.startswith("http://"):
        raw = "ws://" + raw[len("http://"):]
    elif raw.startswith("https://"):
        raw = "wss://" + raw[len("https://"):]
    parts = urlsplit(raw)
    scheme = parts.scheme or default_scheme
    hostname = (parts.hostname or "").strip()
    if not hostname:
        return ""
    port = parts.port if parts.port and parts.port > 0 else None
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = (parts.path or "").rstrip("/")
    if scheme == "nats":
        path = ""
    elif not path:
        path = "/nats"
    elif path != "/nats":
        path = "/nats" if path.endswith("/nats") else f"{path}/nats"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def normalize_remote_ws_endpoint(value: str, *, default_scheme: str = "wss") -> str:
    return normalize_remote_nats_endpoint(value, default_scheme=default_scheme)


def is_loopback_remote_host(value: str) -> bool:
    host = str(value or "").strip().lower()
    return host in {"", "127.0.0.1", "localhost", "::1"}


def model_display_name(model_id: str) -> str:
    """Convert model ID to display name."""
    if not model_id:
        return ""
    if model_id == "codex/gpt-5.4-medium":
        return "codex gpt5.4 medium"
    if model_id == "codex/gpt-5.3-codex-spark-high":
        return "codex gpt5.3spark high"
    if model_id.startswith("codex/"):
        return "codex"
    if model_id.startswith("claudecode/"):
        return "claudeCode"
    if model_id.startswith("openclaw/"):
        return "openclaw"
    return model_id


def model_id_from_display_name(display_name: str) -> str:
    """Convert display name to model ID."""
    if not display_name:
        return ""
    if display_name == "codex":
        return "codex/main"
    if display_name == "codex gpt5.4 medium":
        return "codex/gpt-5.4-medium"
    if display_name in {"codex gpt5.3spark high", "codex gpt5.3spark heigh"}:
        return "codex/gpt-5.3-codex-spark-high"
    if display_name == "claudeCode":
        return "claudecode/default"
    if display_name == "openclaw":
        return "openclaw/main"
    return display_name


def is_cli_filtered_model(model_id: str) -> bool:
    return is_codex_model(model_id) or is_claudecode_model(model_id) or is_openclaw_model(model_id)


def is_visible_model_id(model_id: str) -> bool:
    return str(model_id or "").strip() in VISIBLE_MODEL_IDS


def md_to_plain(md_text: str) -> str:
    if not md_text:
        return ""
    html = markdown.markdown(md_text, extensions=["extra", "fenced_code", "tables", "sane_lists"])
    st = _Stripper()
    st.feed(html)
    return unescape(st.text()).strip()


def remove_emojis(text: str) -> str:
    # Remove common emoji/pictograph unicode ranges (works with surrogate pairs too).
    out = []
    for ch in text:
        cp = ord(ch)
        is_emoji = (
            0x1F1E6 <= cp <= 0x1F1FF
            or 0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or cp in (0x200D, 0xFE0F)  # ZWJ and emoji variation selector
            or 0xD800 <= cp <= 0xDFFF  # surrogate pairs on narrow builds
        )
        if not is_emoji:
            out.append(ch)
    return "".join(out)


def remove_trailing_punctuation(text: str) -> str:
    if not text:
        return ""
    trailing = ".,!?;:，。！？；：、…~～'\"“”‘’（）()[]【】<>《》"
    return text.strip().rstrip(trailing).strip()


def sanitize_optimized_text(text: str) -> str:
    if not text:
        return ""
    def _normalize_for_dedupe(line: str) -> str:
        # Keep CJK/alnum only so punctuation-only differences are treated as duplicates.
        kept = []
        for ch in line:
            cp = ord(ch)
            if ch.isalnum() or (0x4E00 <= cp <= 0x9FFF):
                kept.append(ch.lower())
        return "".join(kept)

    lines = str(text).replace("\r", "\n").split("\n")
    cleaned_lines = []
    seen_norm = set()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # Strip common markdown prefixes.
        for pfx in ("### ", "## ", "# ", "- ", "* ", "+ ", "> "):
            if line.startswith(pfx):
                line = line[len(pfx):].strip()
                break
        # Strip ordered-list markdown like "1. ".
        if len(line) > 3 and line[0].isdigit() and line[1:3] == ". ":
            line = line[3:].strip()
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = remove_emojis(line)
        if line:
            norm = _normalize_for_dedupe(line)
            if norm and norm in seen_norm:
                continue
            if norm:
                seen_norm.add(norm)
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class GlobalCtrlTapHook:
    def __init__(self, on_ctrl_keyup: Callable[[bool, str], None], on_error: Callable[[str], None] | None = None):
        self.on_ctrl_keyup = on_ctrl_keyup
        self.on_error = on_error
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._hook_proc = None
        self._fallback_thread = None
        self._running = False
        self._ctrl_down = False
        self._combo_used = False
        self._active_ctrl_side = "left"
        self._using_fallback = False
        self._fallback_notice_sent = False
        self._last_hook_event_at = 0.0
        self._hook_stale_seconds = 0.45
        self._emit_lock = threading.Lock()
        self._last_emit_at_by_side: dict[str, float] = {"left": 0.0, "right": 0.0}
        self._poller_left_down = False
        self._poller_right_down = False
        self._backend_state = "hook+poller"

    @staticmethod
    def _should_mark_combo_key(vk: int) -> bool:
        # Ignore modifier/IME pseudo keys to avoid false combo detection in some custom controls.
        if vk in {
            0,
            0xFF,
            VK_CONTROL,
            VK_LCONTROL,
            VK_RCONTROL,
            VK_SHIFT,
            VK_LSHIFT,
            VK_RSHIFT,
            VK_MENU,
            VK_LMENU,
            VK_RMENU,
            VK_LWIN,
            VK_RWIN,
            VK_PROCESSKEY,
            VK_PACKET,
        }:
            return False
        return True

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._fallback_thread = threading.Thread(target=self._run_fallback_poller, daemon=True)
        self._fallback_thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            if self._thread_id:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._fallback_thread and self._fallback_thread.is_alive():
            self._fallback_thread.join(timeout=1.0)
        self._thread = None
        self._fallback_thread = None
        self._thread_id = 0
        self._poller_left_down = False
        self._poller_right_down = False

    def _thread_main(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def _low_level_proc(n_code, w_param, l_param):
            if n_code == 0:
                self._last_hook_event_at = time.monotonic()
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                msg = int(w_param)
                vk = int(kb.vkCode)
                is_ctrl = vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL)
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if is_ctrl:
                        self._ctrl_down = True
                        self._active_ctrl_side = "right" if vk == VK_RCONTROL else "left"
                    elif self._ctrl_down and self._should_mark_combo_key(vk):
                        self._combo_used = True
                elif msg in (WM_KEYUP, WM_SYSKEYUP) and is_ctrl:
                    combo = self._combo_used
                    if vk == VK_RCONTROL:
                        side = "right"
                    elif vk == VK_LCONTROL:
                        side = "left"
                    else:
                        # Some keyboards report VK_CONTROL on key-up; keep side from key-down.
                        side = self._active_ctrl_side
                    self._ctrl_down = False
                    self._combo_used = False
                    self._emit_ctrl_keyup(combo, side)
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._hook_proc = HOOKPROC(_low_level_proc)
        h_inst = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, h_inst, 0)
        if not self._hook:
            err = ctypes.get_last_error()
            if self.on_error:
                wx_call_after_if_alive(self.on_error, f"全局语音热键不可用（Windows Hook 安装失败，错误码 {err}）")
            self._using_fallback = True
            self._backend_state = "poller_only"
            return
        self._last_hook_event_at = time.monotonic()

        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == -1:
                    if self.on_error:
                        wx_call_after_if_alive(self.on_error, "全局语音热键不可用（消息循环异常）")
                    break
                if ret == 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._hook_proc = None
            self._thread_id = 0

    def _run_fallback_poller(self) -> None:
        # Fallback for environments where low-level hook is blocked or unreliable.
        user32 = ctypes.windll.user32
        try:
            while self._running:
                left_down = bool(user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)
                right_down = bool(user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000)
                if (not self._hook or self._using_fallback) and (not self._fallback_notice_sent) and self.on_error:
                    self._fallback_notice_sent = True
                    wx_call_after_if_alive(self.on_error, "全局语音热键进入兼容模式（轮询）")
                self._process_poller_state(left_down=left_down, right_down=right_down)
                time.sleep(0.015)
        finally:
            self._using_fallback = False

    def _should_use_poller_release(self) -> bool:
        return bool(self._running)

    def _process_poller_state(self, *, left_down: bool, right_down: bool) -> None:
        should_emit = self._should_use_poller_release()
        prev_left_down = bool(self._poller_left_down)
        prev_right_down = bool(self._poller_right_down)
        self._poller_left_down = bool(left_down)
        self._poller_right_down = bool(right_down)
        if not should_emit:
            return
        if prev_left_down and (not left_down):
            self._emit_ctrl_keyup(False, "left")
        if prev_right_down and (not right_down):
            self._emit_ctrl_keyup(False, "right")

    def _emit_ctrl_keyup(self, combo_used: bool, side: str) -> None:
        side_key = "right" if side == "right" else "left"
        now = time.monotonic()
        with self._emit_lock:
            last = self._last_emit_at_by_side.get(side_key, 0.0)
            # Deduplicate hook/poller double delivery for the same key-up.
            if (now - last) < 0.06:
                return
            self._last_emit_at_by_side[side_key] = now
        wx_call_after_if_alive(self.on_ctrl_keyup, combo_used, side_key)

    @property
    def backend_state(self) -> str:
        if self._using_fallback or not self._hook:
            return "poller_only"
        return self._backend_state


class RenameDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, initial_text: str):
        super().__init__(parent, title="重命名聊天", size=(420, 180))
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(panel, label="请输入新的聊天标题："), 0, wx.ALL, 10)
        self.input = wx.TextCtrl(panel, value=initial_text)
        root.Add(self.input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(root)
        btn_ok.SetDefault()

    def get_value(self) -> str:
        return self.input.GetValue().strip()


class RealtimeCallSettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, role: str, speech_rate: int):
        super().__init__(parent, title="语音通话设置", size=(520, 360))
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(wx.StaticText(panel, label="角色设定："), 0, wx.ALL, 10)
        self.role_edit = wx.TextCtrl(panel, value=role, style=wx.TE_MULTILINE, size=(-1, 170))
        self.role_edit.SetHint("输入实时语音通话的角色设定")
        root.Add(self.role_edit, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        speed_row = wx.BoxSizer(wx.HORIZONTAL)
        speed_row.Add(wx.StaticText(panel, label="语速："), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.speed_input = wx.SpinCtrl(panel, min=-50, max=100, initial=int(min(max(int(speech_rate), -50), 100)))
        speed_row.Add(self.speed_input, 0, wx.RIGHT, 12)
        speed_row.Add(wx.StaticText(panel, label="范围 -50 到 100，数值越大语速越快"), 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(speed_row, 0, wx.EXPAND | wx.ALL, 10)

        root.Add(wx.StaticText(panel, label="全局快捷键：Ctrl+\\ 开始或结束实时语音通话"), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)
        btn_ok.SetDefault()
        self.role_edit.SetFocus()

    def get_settings(self) -> RealtimeCallSettings:
        return RealtimeCallSettings(
            role=self.role_edit.GetValue().strip() or DEFAULT_REALTIME_CALL_ROLE,
            speech_rate=self.speed_input.GetValue(),
        ).normalized()


class CodexUserInputDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, questions: list[dict]):
        super().__init__(parent, title="Codex 输入", size=(620, 420))
        self._controls: list[dict] = []
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        scroll = wx.BoxSizer(wx.VERTICAL)

        for question in questions or []:
            if not isinstance(question, dict):
                continue
            block = wx.StaticBoxSizer(wx.StaticBox(panel, label=str(question.get("header") or "问题")), wx.VERTICAL)
            block.Add(wx.StaticText(panel, label=str(question.get("question") or "")), 0, wx.ALL, 6)
            options = question.get("options") if isinstance(question.get("options"), list) else []
            option_labels: list[str] = []
            option_values: list[str] = []
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label") or "").strip()
                value = str(opt.get("value") or label).strip()
                if label:
                    option_labels.append(label)
                    option_values.append(value or label)
            if not option_labels:
                option_labels = [""]
                option_values = [""]
            radio = wx.RadioBox(panel, choices=option_labels, majorDimension=1, style=wx.RA_SPECIFY_ROWS)
            block.Add(radio, 0, wx.EXPAND | wx.ALL, 6)
            self._controls.append(
                {
                    "id": str(question.get("id") or ""),
                    "radio": radio,
                    "values": option_values,
                }
            )
            scroll.Add(block, 0, wx.EXPAND | wx.ALL, 8)

        root.Add(scroll, 1, wx.EXPAND | wx.ALL, 8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(root)
        btn_ok.SetDefault()

    def get_answers(self) -> dict[str, list[str]]:
        answers: dict[str, list[str]] = {}
        for control in self._controls:
            qid = str(control.get("id") or "").strip()
            radio = control.get("radio")
            values = control.get("values") if isinstance(control.get("values"), list) else []
            if not qid or radio is None:
                continue
            idx = int(radio.GetSelection())
            if idx < 0:
                continue
            if idx < len(values):
                answers[qid] = [str(values[idx] or "").strip()]
            else:
                answers[qid] = [str(radio.GetStringSelection() or "").strip()]
        return answers


class ChatFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=APP_WINDOW_TITLE, size=(1200, 800))
        self.CreateStatusBar(1)
        self.SetStatusText("就绪")

        self.app_data_dir = resolve_app_data_dir()
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.app_data_dir / APP_STATE_FILE
        self.detail_pages_dir = self.app_data_dir / "detail_pages"
        self.detail_pages_dir.mkdir(parents=True, exist_ok=True)
        self.chat_uploads_dir = self.app_data_dir / "chat_uploads"
        self.chat_uploads_dir.mkdir(parents=True, exist_ok=True)
        self.notes_db_path = self.app_data_dir / "notes.db"
        self.notes_device_id = f"desktop-{platform.node().strip().lower() or 'local'}"
        self.notes_store = NotesStore(self.notes_db_path, device_id=self.notes_device_id)
        self.notes_store.initialize()
        self.chat_db_path = self.app_data_dir / "chat_history.db"
        self.chat_store = ChatStore(self.chat_db_path)
        self.chat_store.initialize()
        self._chat_store_enabled = True
        self.notes_projection = DesktopNotesProjection(self.notes_store)
        self._notes_ui_thread_id = threading.get_ident()
        self.notes_sync = NotesSyncService(
            self.notes_store,
            broadcaster=self._on_notes_sync_push_result,
            on_remote_ops_applied=self._on_notes_remote_ops_applied_safe,
            on_status_changed=self._on_notes_sync_status_changed_safe,
        )
        self._notes_sync_worker_lock = threading.Lock()
        self._notes_sync_worker_scheduled = False
        self._listbox_label_cache: dict[int, tuple[str, ...]] = {}
        self._configure_notes_couchdb_sync_from_env()
        self._current_notes_state = {}
        self.notes_sync_hint = ""
        self.send_sound = self._resolve_sound_path("send")
        self.reply_sound = self._resolve_sound_path("reply")
        self.voice_begin_sound = self._resolve_sound_path("inputBegin")
        self.voice_end_sound = self._resolve_sound_path("inputEnd")
        self.voice_wrong_sound = self._resolve_sound_path("inputWrong")
        self._zdsr_tts = ZDSRTTSClient()
        self._cli_agent_manager = get_default_cli_agent_manager()
        self.selected_model = STARTUP_DEFAULT_MODEL_ID

        self.archived_chats = []
        self.active_session_turns = []
        self.active_session_started_at = 0.0
        self.active_chat_id = ""
        self.active_openclaw_session_key = DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = ""
        self.active_openclaw_session_file = ""
        self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = 0.0
        self.codex_answer_english_filter_enabled = False
        self.active_codex_thread_id = ""
        self.active_codex_turn_id = ""
        self.active_codex_turn_active = False
        self.active_codex_pending_prompt = ""
        self.active_codex_pending_request = None
        self.active_codex_request_queue = []
        self.active_codex_thread_flags = []
        self.active_codex_latest_assistant_text = ""
        self.active_codex_latest_assistant_phase = ""
        self.active_claudecode_session_id = ""
        self._active_claudecode_client = None
        self._codex_clients: dict[str, CodexAppServerClient] = {}
        self._remote_nats_process = None
        self._remote_nats_transport = None
        self._managed_cloudflared_process = None
        self._remote_nats_websocket_port = DEFAULT_REMOTE_NATS_WEBSOCKET_PORT
        self._codex_background_flush_scheduled = False
        self._codex_background_flush_dirty = False
        self._pending_codex_ui_events: list[tuple[str, CodexEvent]] = []
        self._codex_ui_event_lock = threading.Lock()
        self._codex_ui_event_flush_scheduled = False
        self._codex_ui_event_drain_timer = None
        self._codex_ui_batch_depth = 0
        self._execution_list_deferred_repaint = False
        self._execution_list_deferred_select_latest = False
        self._pending_execution_step_persists = []
        self._execution_step_persist_lock = threading.Lock()
        self._execution_step_persist_scheduled = False
        self._execution_step_persist_worker_running = False
        self._chat_turn_dirty_from: dict[str, int] = {}
        self._remote_turn_payload_cache: dict[int, tuple[tuple, dict]] = {}
        self._context_usage_estimate_lock = threading.Lock()
        self._context_usage_estimate_keys: set[tuple[str, int]] = set()
        self._alt_menu_armed = False
        self._alt_menu_suppressed = False
        self.active_project_folder = ""
        self.view_mode = "active"
        self.view_history_id = None
        self._current_chat_state = {}
        self.current_chat_id = None
        self.remote_control_token = DEFAULT_REMOTE_CONTROL_TOKEN
        self.remote_control_domain = ""
        self.remote_control_host = "127.0.0.1"
        self.remote_control_port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        self.remote_control_autostart = True
        self.remote_control_runtime_mode = "local"
        self.remote_control_runtime_bind = ""
        self.remote_control_runtime_url = ""
        self.remote_control_runtime_status = {
            "local_listener_ready": False,
            "public_ws_ready": False,
            "last_remote_error": "",
            "published_url": "",
        }
        self.remote_nats_runtime_url = ""
        self.remote_nats_runtime_status = {
            "enabled": False,
            "tcp_url": "",
            "websocket_url": "",
            "cloudflared_url": "",
            "last_error": "",
        }

        self.history_ids = []
        self.answer_meta = []
        self.execution_meta = []
        self._execution_delta_buffer = {}
        self.answer_visible_row_limit = ANSWER_LIST_DEFAULT_VISIBLE_ROWS
        self.answer_total_content_rows = 0
        self.execution_visible_row_limit = EXECUTION_LIST_DEFAULT_VISIBLE_ROWS
        self.execution_total_content_rows = 0
        self.is_running = False
        self._active_request_count = 0
        self.active_turn_idx = -1
        self._active_answer_row_index = -1
        self.input_hint_state = "输入"
        self._answer_committed_buffer = ""
        self._answer_redirect_timer = None
        self._pending_input_attachments = []
        self._pending_context_usage_by_turn = {}
        self._openclaw_sync_thread = None
        self._openclaw_sync_stop = threading.Event()
        self._openclaw_sync_lock = threading.Lock()
        self._is_in_tray = False
        self._tray_icon = None
        self._show_hotkey_registered = False
        self._realtime_call_hotkey_registered_ids = set()
        self.global_ctrl_backend_status = "hook+poller"
        self.voice_screen_reader_status = {
            "last_text": "",
            "last_success": None,
            "last_error": "",
        }
        self.realtime_call_role = DEFAULT_REALTIME_CALL_ROLE
        self.realtime_call_speech_rate = DEFAULT_REALTIME_CALL_SPEECH_RATE
        self._voice_input = VoiceInputController(
            on_state_change=lambda text: wx_call_after_if_alive(self._on_voice_state, text),
            on_result=lambda text, mode: wx_call_after_if_alive(self._on_voice_result, text, mode),
            on_error=lambda msg: wx_call_after_if_alive(self._on_voice_error, msg),
            on_stop_recording=lambda: wx_call_after_if_alive(self._on_voice_stop_recording),
        )
        self._chat_navigation_left_id = wx.NewIdRef()
        self._chat_navigation_right_id = wx.NewIdRef()
        self._realtime_call = RealtimeCallController(
            settings=RealtimeCallSettings(role=self.realtime_call_role, speech_rate=self.realtime_call_speech_rate),
            on_status=lambda message: wx_call_after_if_alive(self._on_realtime_call_status, message),
            on_error=lambda message: wx_call_after_if_alive(self._on_realtime_call_error, message),
            on_active_change=lambda active: wx_call_after_if_alive(self._on_realtime_call_active_changed, active),
        )
        self._global_ctrl_hook = GlobalCtrlTapHook(self._on_global_ctrl_keyup, self._on_global_ctrl_hook_error)

        self._build_ui()
        self.notes_controller = DesktopNotesController(self, self.notes_store)
        self._bind_events()
        self._register_global_hotkey()
        self._global_ctrl_hook.start()
        self._migrate_legacy_state_if_needed()
        self._migrate_legacy_chat_json_if_needed()
        self._load_state()
        self._realtime_call.update_settings(
            RealtimeCallSettings(role=self.realtime_call_role, speech_rate=self.realtime_call_speech_rate)
        )
        self._initialize_remote_control_settings()
        wx_call_after_if_alive(self._realtime_call.prepare)
        if not getattr(self, "_chat_store_enabled", False):
            self._merge_legacy_archived_chats()
        self._schedule_remote_nats_autostart()
        self._start_claudecode_remote_nats_runtime_if_configured()
        self._refresh_openclaw_sync_lifecycle(force_replay=not bool(self.active_openclaw_session_file))
        if self.active_session_turns:
            last_model = ""
            for turn in reversed(self.active_session_turns):
                last_model = str(turn.get("model") or "").strip()
                if last_model:
                    break
            resolved_last_model = model_id_from_display_name(last_model)
            if is_visible_model_id(last_model):
                self.selected_model = last_model
            elif is_visible_model_id(resolved_last_model):
                self.selected_model = resolved_last_model
            elif not is_visible_model_id(self.selected_model):
                self.selected_model = STARTUP_DEFAULT_MODEL_ID
        else:
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        self.model_combo.SetValue(model_display_name(self.selected_model))
        self._refresh_history()
        self._render_answer_list()
        self._set_input_hint_idle()
        wx_call_after_if_alive(self.input_edit.SetFocus)

    def _build_ui(self):
        frame_panel = wx.Panel(self)
        frame_root = wx.BoxSizer(wx.VERTICAL)
        self.chat_root_panel = wx.Panel(frame_panel)
        frame_root.Add(self.chat_root_panel, 1, wx.EXPAND)
        frame_panel.SetSizer(frame_root)

        panel = self.chat_root_panel
        root = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(panel, label="历史聊天："), 0, wx.LEFT | wx.TOP, 10)
        self.history_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.history_list.SetName("历史聊天")
        left.Add(self.history_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self._notes_search_query = ""

        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(wx.StaticText(panel, label="输入："), 0, wx.LEFT | wx.TOP, 10)
        self.input_edit = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 140))
        self.input_edit.SetName("输入")
        self.input_edit.SetToolTip("Enter发送，Ctrl+Enter换行，Alt+S发送，Alt+N新聊天")
        right.Add(self.input_edit, 0, wx.EXPAND | wx.ALL, 10)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.send_button = wx.Button(panel, label="发送(&S)")
        self.new_chat_button = wx.Button(panel, label="新聊天(&N)")
        row.Add(self.send_button, 0, wx.RIGHT, 8)
        row.Add(self.new_chat_button, 0, wx.RIGHT, 16)
        row.Add(wx.StaticText(panel, label="模型："), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        model_choices = []
        for choice in VISIBLE_MODEL_IDS:
            display_choice = model_display_name(choice)
            if display_choice not in model_choices:
                model_choices.append(display_choice)
        self.model_combo = wx.ComboBox(panel, choices=model_choices, style=wx.CB_READONLY, size=(320, -1))
        row.Add(self.model_combo, 0, wx.ALIGN_CENTER_VERTICAL)
        right.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.detail_title_label = wx.StaticText(panel, label="回答：")
        right.Add(self.detail_title_label, 0, wx.LEFT, 10)
        self.answer_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.answer_list.SetName("回答列表")
        right.Add(self.answer_list, 1, wx.EXPAND | wx.ALL, 10)
        self.execution_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.execution_list.SetName("执行过程列表")
        right.Add(self.execution_list, 1, wx.EXPAND | wx.ALL, 10)
        root.Add(right, 2, wx.EXPAND)
        panel.SetSizer(root)

        self._notes_notebook_ids = []
        self._notes_entry_ids = []
        self.notes_list_panel = wx.Panel(panel)
        list_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.notes_section_label = wx.StaticText(self.notes_list_panel, label="笔记")
        list_panel_sizer.Add(self.notes_section_label, 0, wx.BOTTOM, 6)
        self.notes_notebook_list = wx.ListBox(self.notes_list_panel, style=wx.LB_SINGLE)
        self.notes_notebook_list.SetName("笔记")
        self.notes_notebook_list.SetToolTip("笔记")
        list_panel_sizer.Add(self.notes_notebook_list, 1, wx.EXPAND)
        self.notes_list_panel.SetSizer(list_panel_sizer)
        left.Add(self.notes_list_panel, 1, wx.EXPAND | wx.ALL, 10)
        root.Add(left, 1, wx.EXPAND)

        self.notes_detail_panel = wx.Panel(panel)
        detail_sizer = wx.BoxSizer(wx.VERTICAL)
        detail_header = wx.BoxSizer(wx.HORIZONTAL)
        self.notes_detail_title = wx.StaticText(self.notes_detail_panel, label="笔记详情")
        detail_header.Add(self.notes_detail_title, 1, wx.ALIGN_CENTER_VERTICAL)
        detail_sizer.Add(detail_header, 0, wx.EXPAND | wx.ALL, 8)
        self.notes_entry_list = wx.ListBox(self.notes_detail_panel, style=wx.LB_SINGLE)
        self.notes_entry_list.SetName("笔记条目列表")
        detail_sizer.Add(self.notes_entry_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.notes_detail_panel.SetSizer(detail_sizer)

        self.notes_edit_panel = wx.Panel(panel)
        edit_sizer = wx.BoxSizer(wx.VERTICAL)
        self.notes_edit_title = wx.StaticText(self.notes_edit_panel, label="笔记")
        edit_sizer.Add(self.notes_edit_title, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self.notes_editor = wx.TextCtrl(self.notes_edit_panel, style=wx.TE_MULTILINE)
        self.notes_editor.SetName("笔记")
        self.notes_editor.SetToolTip("笔记")
        edit_sizer.Add(self.notes_editor, 1, wx.EXPAND | wx.ALL, 10)
        self.notes_edit_panel.SetSizer(edit_sizer)
        self.notes_content_label = wx.StaticText(panel, label="笔记：")
        right.Add(self.notes_content_label, 0, wx.LEFT, 10)
        right.Add(self.notes_detail_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        right.Add(self.notes_edit_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.new_chat_button.MoveAfterInTabOrder(self.input_edit)
        self.model_combo.MoveAfterInTabOrder(self.new_chat_button)
        self.send_button.MoveAfterInTabOrder(self.model_combo)
        self.notes_list_panel.MoveAfterInTabOrder(self.send_button)
        self.history_list.MoveAfterInTabOrder(self.notes_list_panel)
        self.answer_list.MoveAfterInTabOrder(self.history_list)
        self.execution_list.MoveAfterInTabOrder(self.answer_list)
        self.chat_root_panel.SetSizer(root)

        self.root_tab_order = []
        self.chat_tab_order = []
        self.notes_tab_order = []
        self._notes_rebuild_tab_order()
        self._sync_notes_ui()

    def _schedule_remote_nats_autostart(self) -> None:
        if not self.remote_control_autostart:
            return

        def _worker() -> None:
            try:
                self._start_remote_nats_runtime_if_configured(ensure_connectivity=True)
            except Exception as exc:
                wx_call_after_if_alive(self.SetStatusText, f"远程 NATS 启动失败：{exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _bind_events(self):
        self.send_button.Bind(wx.EVT_BUTTON, self._on_send_clicked)
        self.new_chat_button.Bind(wx.EVT_BUTTON, self._on_new_chat_clicked)
        self.model_combo.Bind(wx.EVT_COMBOBOX, self._on_model_changed)
        self.input_edit.Bind(wx.EVT_KEY_DOWN, self._on_input_key_down)
        self.input_edit.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.send_button.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.new_chat_button.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.model_combo.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.answer_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.history_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_SHOW, self._on_show_sync_tray_state)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_SHOW)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL_ALT)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL_ALT2)
        self.Bind(wx.EVT_KEY_DOWN, self._on_frame_key_down)

        self.answer_list.Bind(wx.EVT_KEY_DOWN, self._on_answer_key_down)
        self.answer_list.Bind(wx.EVT_CHAR, self._on_answer_char)
        self.answer_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_answer_activate)
        self.execution_list.Bind(wx.EVT_KEY_DOWN, self._on_execution_key_down)
        self.execution_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.execution_list.Bind(wx.EVT_CHAR, self._on_execution_char)
        self.execution_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_execution_activate)
        self.history_list.Bind(wx.EVT_KEY_DOWN, self._on_history_key_down)
        self.history_list.Bind(wx.EVT_CHAR, self._on_history_char)
        self.history_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._activate_selected_history())
        self.history_list.Bind(wx.EVT_LISTBOX, self._on_history_selected)
        self.history_list.Bind(wx.EVT_CONTEXT_MENU, self._on_history_context)
        self.model_combo.Bind(wx.EVT_KEY_DOWN, self._on_generic_key_down)
        self.send_button.Bind(wx.EVT_KEY_DOWN, self._on_generic_key_down)
        self.new_chat_button.Bind(wx.EVT_KEY_DOWN, self._on_generic_key_down)
        self.Bind(wx.EVT_MENU, lambda _evt: self._navigate_history_chats(-1), id=int(self._chat_navigation_left_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._navigate_history_chats(1), id=int(self._chat_navigation_right_id))
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_LEFT, int(self._chat_navigation_left_id)),
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_RIGHT, int(self._chat_navigation_right_id)),
                ]
            )
        )

        if hasattr(self, "notes_notebook_list"):
            self.notes_notebook_list.Bind(wx.EVT_LISTBOX, self._on_notes_notebook_selected)
            self.notes_notebook_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._notes_open_selected_notebook())
            self.notes_notebook_list.Bind(wx.EVT_KEY_DOWN, self._on_notes_key_down)
            self.notes_notebook_list.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)
            self.notes_entry_list.Bind(wx.EVT_LISTBOX, self._on_notes_entry_selected)
            self.notes_entry_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._notes_edit_entry())
            self.notes_entry_list.Bind(wx.EVT_KEY_DOWN, self._on_notes_key_down)
            self.notes_entry_list.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)
            self.notes_editor.Bind(wx.EVT_TEXT, self._on_notes_editor_changed)
            self.notes_editor.Bind(wx.EVT_KEY_DOWN, self._on_notes_key_down)
            self.notes_editor.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)

    def _resolve_sound_path(self, name: str):
        base = os.path.dirname(os.path.abspath(__file__))
        for n in (f"sound\\{name}", f"sound\\{name}.wav", name, f"{name}.wav", "Alarm02", "Alarm02.wav"):
            p = os.path.join(base, n)
            if os.path.isfile(p):
                return p
        sound_dir = os.path.join(base, "sound")
        if os.path.isdir(sound_dir):
            token = str(name or "").strip().lower()
            suffix_match = None
            contains_match = None
            for entry in os.listdir(sound_dir):
                stem, ext = os.path.splitext(entry)
                if ext.lower() not in {".wav", ".mp3", ".m4a"}:
                    continue
                stem_lower = stem.lower()
                full_path = os.path.join(sound_dir, entry)
                if stem_lower == token:
                    return full_path
                if stem_lower.endswith(token):
                    suffix_match = suffix_match or full_path
                if token and token in stem_lower:
                    contains_match = contains_match or full_path
            if suffix_match:
                return suffix_match
            if contains_match:
                return contains_match
        return None

    def _set_input_hint_idle(self):
        self.input_hint_state = "输入"
        self.input_edit.SetHint("输入")

    def _set_input_hint_sent(self):
        self.input_hint_state = "已发送"
        self.input_edit.SetHint("已发送")

    def _build_question_detail_html(self, question: str) -> str:
        q_html = markdown.markdown(remove_emojis(question), extensions=["extra", "fenced_code", "tables", "sane_lists"])
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>问题详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;}"
            "</style></head><body>"
            "<h2>问题详情</h2>"
            f"{q_html}"
            "</body></html>"
        )

    def _build_answer_detail_html(self, answer_md: str) -> str:
        a_html = markdown.markdown(remove_emojis(answer_md), extensions=["extra", "fenced_code", "tables", "sane_lists"])
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>回答详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;}"
            "</style></head><body>"
            "<h2>回答详情</h2>"
            f"{a_html}"
            "</body></html>"
        )

    def _ensure_question_detail_page(self, turn: dict, turn_idx: int) -> Path:
        created = int(float(turn.get("created_at") or time.time()))
        file_name = f"question_{created}_{turn_idx}_{uuid.uuid4().hex[:8]}.html"
        page_path = self.detail_pages_dir / file_name
        html = self._build_question_detail_html(str(turn.get("question") or ""))
        page_path.write_text(html, encoding="utf-8")
        turn["question_detail_page_path"] = str(page_path)
        return page_path

    def _ensure_answer_detail_page(self, turn: dict, turn_idx: int) -> Path:
        created = int(float(turn.get("created_at") or time.time()))
        file_name = f"answer_{created}_{turn_idx}_{uuid.uuid4().hex[:8]}.html"
        page_path = self.detail_pages_dir / file_name
        html = self._build_answer_detail_html(str(turn.get("answer_md") or ""), str(turn.get("model") or ""))
        page_path.write_text(html, encoding="utf-8")
        turn["answer_detail_page_path"] = str(page_path)
        return page_path

    def _ensure_execution_detail_page(self, step: dict, step_idx: int) -> Path:
        created = int(float(step.get("created_at") or time.time()))
        existing_path_raw = str(step.get("detail_page_path") or "").strip()
        page_path = None
        if existing_path_raw:
            try:
                existing_path = Path(existing_path_raw)
                if existing_path.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                    page_path = existing_path
            except Exception:
                page_path = None
        if page_path is None:
            file_name = f"execution_{created}_{step_idx}_{uuid.uuid4().hex[:8]}.html"
            page_path = self.detail_pages_dir / file_name
            if existing_path_raw:
                try:
                    old_path = Path(existing_path_raw)
                    if old_path.exists() and old_path.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                        old_path.unlink(missing_ok=True)
                except Exception:
                    pass
        title = str(
            step.get("list_text")
            or step.get("title")
            or step.get("step")
            or step.get("message")
            or step.get("event_type")
            or f"执行步骤 {step_idx + 1}"
        ).strip()
        detail_text = str(
            step.get("detail_text")
            or step.get("message")
            or step.get("step")
            or step.get("title")
            or step.get("text")
            or step.get("content")
            or step.get("description")
            or ""
        )
        detail_html = escape(detail_text).replace("\r\n", "\n").replace("\r", "\n")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>执行过程详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            ".meta{color:#475569;margin-bottom:12px;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;white-space:pre-wrap;word-break:break-word;}"
            "</style></head><body>"
            "<h2>执行过程详情</h2>"
            f"<div class='meta'>{escape(title)}</div>"
            f"<pre>{detail_html}</pre>"
            "</body></html>"
        )
        page_path.write_text(html, encoding="utf-8")
        step["detail_page_path"] = str(page_path)
        return page_path

    def _open_local_webpage(self, page_path: Path) -> None:
        try:
            os.startfile(str(page_path))  # type: ignore[attr-defined]
            return
        except Exception:
            webbrowser.open(page_path.resolve().as_uri())

    def _show_ok_dialog(self, message: str, title: str = "提示") -> None:
        # 使用系统消息框样式，提升读屏自动朗读稳定性；同时将按钮改为中文“确定”。
        dlg = wx.MessageDialog(self, message, title, wx.OK | wx.ICON_INFORMATION)
        ok_btn = dlg.FindWindow(wx.ID_OK)
        if ok_btn and isinstance(ok_btn, wx.Button):
            ok_btn.SetLabel("确定")
            ok_btn.SetDefault()
            ok_btn.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def _migrate_legacy_state_if_needed(self) -> None:
        if self.state_path.exists():
            return
        appdata = os.getenv("APPDATA", "").strip()
        legacy_candidates = [
            (Path(appdata) / APP_DIR_NAME / APP_STATE_FILE) if appdata else None,
            Path(__file__).resolve().parent / APP_STATE_FILE,
            Path(sys.executable).resolve().parent / APP_STATE_FILE,
            Path.cwd() / APP_STATE_FILE,
        ]
        for old in legacy_candidates:
            if old is None:
                continue
            try:
                if old.exists():
                    shutil.copy2(old, self.state_path)
                    return
            except Exception:
                continue

    def _migrate_legacy_chat_json_if_needed(self) -> None:
        store = getattr(self, "chat_store", None)
        if store is None or store.get_meta("legacy_json_migration_complete") == "1":
            return
        if not self.state_path.exists():
            store.set_meta("legacy_json_migration_complete", "1")
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        chats: list[dict] = []
        active_chat = data.get("active_chat") if isinstance(data.get("active_chat"), dict) else {}
        active_id = str(data.get("active_chat_id") or active_chat.get("id") or "").strip()
        active_turns = data.get("active_session_turns")
        if not isinstance(active_turns, list):
            active_turns = active_chat.get("turns") if isinstance(active_chat.get("turns"), list) else []
        if active_id and active_turns:
            active = copy.deepcopy(active_chat)
            active["id"] = active_id
            active["turns"] = active_turns
            chats.append(active)

        archived = data.get("archived_chats")
        if not isinstance(archived, list):
            archived = data.get("chats")
        if isinstance(archived, list):
            chats.extend(copy.deepcopy(chat) for chat in archived if isinstance(chat, dict))

        if not chats:
            store.set_meta("legacy_json_migration_complete", "1")
            return

        for chat in chats:
            if not isinstance(chat, dict):
                continue
            self._normalize_archived_chat(chat)
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id:
                continue
            store.upsert_chat(chat)
            if isinstance(chat.get("turns"), list):
                store.replace_turns(chat_id, chat.get("turns") or [])
            if isinstance(chat.get("execution_steps"), list):
                store.replace_execution_steps(chat_id, chat.get("execution_steps") or [])

        backup = self.state_path.with_name(f"{self.state_path.name}.bak.{int(time.time())}")
        try:
            shutil.copy2(self.state_path, backup)
        except Exception:
            pass
        for key in ("archived_chats", "chats", "active_session_turns"):
            data.pop(key, None)
        if isinstance(data.get("active_chat"), dict):
            data["active_chat"].pop("turns", None)
            data["active_chat"].pop("execution_steps", None)
        try:
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return
        store.set_meta("legacy_json_migration_complete", "1")

    def _legacy_state_paths(self) -> list[Path]:
        appdata = os.getenv("APPDATA", "").strip()
        runtime_dir = Path(__file__).resolve().parent
        exe_dir = Path(sys.executable).resolve().parent
        paths: list[Path] = [
            self.state_path,
            runtime_dir / APP_STATE_FILE,
            runtime_dir / "dist" / "history" / APP_STATE_FILE,
            exe_dir / APP_STATE_FILE,
            exe_dir / "_internal" / "dist" / "history" / APP_STATE_FILE,
            Path.cwd() / APP_STATE_FILE,
        ]
        if appdata:
            paths.append(Path(appdata) / APP_DIR_NAME / APP_STATE_FILE)
        # De-duplicate while preserving order.
        uniq: list[Path] = []
        seen = set()
        for p in paths:
            k = str(p.resolve()) if p.exists() else str(p)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)
        return uniq

    def _merge_legacy_archived_chats(self) -> None:
        merged_by_id: dict[str, dict] = {}

        def _score(chat: dict) -> tuple[int, float]:
            turns = chat.get("turns") or []
            ts = float(chat.get("updated_at") or chat.get("created_at") or 0.0)
            return (len(turns), ts)

        for chat in self.archived_chats:
            cid = str(chat.get("id") or "").strip()
            if not cid:
                continue
            merged_by_id[cid] = chat

        changed = False
        for p in self._legacy_state_paths():
            if p == self.state_path or (not p.exists()):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            archived = data.get("archived_chats")
            if not isinstance(archived, list):
                archived = data.get("chats")
            if not isinstance(archived, list):
                continue
            for chat in archived:
                if not isinstance(chat, dict):
                    continue
                self._normalize_archived_chat(chat)
                cid = str(chat.get("id") or "").strip()
                if not cid:
                    continue
                old = merged_by_id.get(cid)
                if old is None:
                    merged_by_id[cid] = chat
                    changed = True
                    continue
                if _score(chat) > _score(old):
                    merged_by_id[cid] = chat
                    changed = True

        if changed:
            self.archived_chats = list(merged_by_id.values())
            self._sort_archived_chats()
            self._defer_chat_state_save()

    def _slim_active_chat_state(self) -> dict:
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        now = time.time()
        title_manual = state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        slim = {
            "id": str(state.get("id") or self.active_chat_id or self.current_chat_id or "").strip(),
            "title": str(state.get("title") or EMPTY_CURRENT_CHAT_TITLE),
            "title_manual": title_manual,
            "title_source": str(state.get("title_source") or ("manual" if title_manual else "default")),
            "title_updated_at": float(state.get("title_updated_at") or state.get("updated_at") or now),
            "title_revision": int(state.get("title_revision") or 1),
            "model": str(state.get("model") or self.selected_model or DEFAULT_MODEL_ID),
            "created_at": float(state.get("created_at") or self.active_session_started_at or now),
            "updated_at": float(state.get("updated_at") or now),
            "detail_panel_mode": self._detail_panel_mode() if hasattr(self, "answer_list") else "answers",
        }
        if "context_usage" in state:
            slim["context_usage"] = copy.deepcopy(state.get("context_usage"))
        return slim

    def _persist_chat_history_to_store(self) -> None:
        store = getattr(self, "chat_store", None)
        if store is None:
            return
        # Execution steps are persisted incrementally as Codex events arrive.
        # Rewriting them on every UI-thread state save stalls keyboard response
        # once a command-heavy turn has hundreds of steps.
        if self.active_chat_id or self.current_chat_id or self.active_session_turns:
            active = self._slim_active_chat_state()
            active_id = str(active.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
            if active_id:
                active["id"] = active_id
                active["turns"] = self.active_session_turns if isinstance(self.active_session_turns, list) else []
                state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
                active_steps = state.get("execution_steps") if isinstance(state.get("execution_steps"), list) else []
                active["execution_steps"] = active_steps
                store.upsert_chat(active)
                self._persist_dirty_chat_turns(store, active_id, active["turns"])
        for chat in self.archived_chats:
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id:
                continue
            store.upsert_chat(chat)
            if isinstance(chat.get("turns"), list):
                self._persist_dirty_chat_turns(store, chat_id, chat.get("turns") or [])

    def _mark_chat_turns_dirty(self, chat_id: str | None = None, start_index: int = 0) -> None:
        normalized = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        if not normalized:
            return
        self._invalidate_remote_state_cache()
        try:
            index = max(0, int(start_index))
        except Exception:
            index = 0
        previous = self._chat_turn_dirty_from.get(normalized)
        self._chat_turn_dirty_from[normalized] = index if previous is None else min(previous, index)

    def _persist_dirty_chat_turns(self, store, chat_id: str, turns: list[dict]) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        dirty_from = self._chat_turn_dirty_from.get(normalized)
        if dirty_from is None:
            count_turns = getattr(store, "count_turns", None)
            if callable(count_turns):
                try:
                    if int(count_turns(normalized) or 0) == 0 and turns:
                        dirty_from = 0
                except Exception:
                    dirty_from = None
        if dirty_from is None:
            return
        start_index = max(0, min(int(dirty_from), len(turns or [])))
        replace_from = getattr(store, "replace_turns_from", None)
        if callable(replace_from):
            replace_from(normalized, list(turns or [])[start_index:], start_index=start_index)
        else:
            store.replace_turns(normalized, turns or [])
        self._chat_turn_dirty_from.pop(normalized, None)

    def _chat_summary_by_id(self, chat_id: str) -> dict | None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return None
        for chat in self.archived_chats:
            if isinstance(chat, dict) and str(chat.get("id") or "").strip() == normalized:
                return chat
        return None

    def _hydrate_chat_from_store(self, chat: dict | None, *, include_execution_steps: bool = True) -> dict | None:
        if not isinstance(chat, dict):
            return None
        if not getattr(self, "_chat_store_enabled", False):
            return chat
        if isinstance(chat.get("turns"), list) and (
            not include_execution_steps or isinstance(chat.get("execution_steps"), list)
        ):
            return chat
        chat_id = str(chat.get("id") or "").strip()
        store = getattr(self, "chat_store", None)
        if not chat_id or store is None:
            return chat
        loaded = store.load_chat(chat_id, include_execution_steps=include_execution_steps)
        if not isinstance(loaded, dict):
            return chat
        for idx, existing in enumerate(self.archived_chats):
            if isinstance(existing, dict) and str(existing.get("id") or "").strip() == chat_id:
                self.archived_chats[idx] = loaded
                break
        return loaded

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.selected_model = str(data.get("selected_model_id") or STARTUP_DEFAULT_MODEL_ID)
        if not is_visible_model_id(self.selected_model):
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        self.codex_answer_english_filter_enabled = bool(data.get("codex_answer_english_filter_enabled", False))

        use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
        if use_chat_store and getattr(self, "chat_store", None) is not None:
            self.archived_chats = self.chat_store.list_chat_summaries()
        else:
            archived = data.get("archived_chats")
            if isinstance(archived, list):
                self.archived_chats = archived
            else:
                legacy = data.get("chats")
                if isinstance(legacy, list):
                    self.archived_chats = legacy
        changed = False
        if not use_chat_store:
            for chat in self.archived_chats:
                if isinstance(chat, dict) and self._normalize_archived_chat(chat):
                    changed = True

        active_turns = data.get("active_session_turns")
        if isinstance(active_turns, list) and not use_chat_store:
            self.active_session_turns = active_turns
        active_chat = data.get("active_chat")
        if isinstance(active_chat, dict):
            self._current_chat_state = active_chat
            if self._normalize_detail_panel_fields(self._current_chat_state):
                changed = True
            if not isinstance(active_turns, list):
                chat_turns = active_chat.get("turns")
                if isinstance(chat_turns, list):
                    self.active_session_turns = chat_turns
            self.active_chat_id = str(active_chat.get("id") or self.active_chat_id or "").strip()
        self.active_chat_id = str(data.get("active_chat_id") or self.active_chat_id or "").strip()
        if use_chat_store and getattr(self, "chat_store", None) is not None:
            if self.active_chat_id:
                self.active_session_turns = self.chat_store.load_turns(self.active_chat_id)
                summary = self._chat_summary_by_id(self.active_chat_id) or {}
                current = self._current_chat_state if isinstance(self._current_chat_state, dict) else {}
                merged = copy.deepcopy(summary)
                merged.update(current)
                merged["id"] = self.active_chat_id
                merged["turns"] = self.active_session_turns
                self._current_chat_state = merged
            else:
                self.active_session_turns = []
        self.active_openclaw_session_key = str(data.get("active_openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = str(data.get("active_openclaw_session_id") or "").strip()
        self.active_openclaw_session_file = str(data.get("active_openclaw_session_file") or "").strip()
        try:
            self.active_openclaw_sync_offset = max(int(data.get("active_openclaw_sync_offset") or 0), 0)
        except Exception:
            self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = str(data.get("active_openclaw_last_event_id") or "").strip()
        try:
            self.active_openclaw_last_synced_at = float(data.get("active_openclaw_last_synced_at") or 0.0)
        except Exception:
            self.active_openclaw_last_synced_at = 0.0
        self.active_codex_thread_id = str(data.get("active_codex_thread_id") or "").strip()
        self.active_codex_turn_id = str(data.get("active_codex_turn_id") or "").strip()
        self.active_codex_turn_active = bool(data.get("active_codex_turn_active", False))
        self.active_codex_pending_prompt = str(data.get("active_codex_pending_prompt") or "").strip()
        pending_request = data.get("active_codex_pending_request")
        self.active_codex_pending_request = pending_request if isinstance(pending_request, dict) else None
        request_queue = data.get("active_codex_request_queue")
        self.active_codex_request_queue = request_queue if isinstance(request_queue, list) else []
        thread_flags = data.get("active_codex_thread_flags")
        self.active_codex_thread_flags = thread_flags if isinstance(thread_flags, list) else []
        self.active_codex_latest_assistant_text = str(data.get("active_codex_latest_assistant_text") or "").strip()
        self.active_codex_latest_assistant_phase = str(data.get("active_codex_latest_assistant_phase") or "").strip()
        self.active_claudecode_session_id = str(data.get("active_claudecode_session_id") or "").strip()
        self.active_session_started_at = float(data.get("active_session_started_at") or 0.0)
        self.realtime_call_role = str(data.get("realtime_call_role") or DEFAULT_REALTIME_CALL_ROLE).strip() or DEFAULT_REALTIME_CALL_ROLE
        try:
            self.realtime_call_speech_rate = int(min(max(int(data.get("realtime_call_speech_rate", DEFAULT_REALTIME_CALL_SPEECH_RATE)), -50), 100))
        except Exception:
            self.realtime_call_speech_rate = DEFAULT_REALTIME_CALL_SPEECH_RATE
        self.remote_control_token = str(data.get("remote_control_token") or self.remote_control_token or "").strip()
        self.remote_control_domain = str(data.get("remote_control_domain") or self.remote_control_domain or "").strip()
        self.remote_control_host = str(data.get("remote_control_host") or self.remote_control_host or "127.0.0.1").strip() or "127.0.0.1"
        try:
            self.remote_control_port = int(
                data.get("remote_control_port")
                or self.remote_control_port
                or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            )
        except Exception:
            self.remote_control_port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        self.remote_control_autostart = bool(data.get("remote_control_autostart", self.remote_control_autostart))
        notes_state = data.get("notes_ui_state")
        if isinstance(notes_state, dict):
            self._current_notes_state = notes_state
        if not is_visible_model_id(self.selected_model):
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        if self.active_session_turns and not self.active_chat_id:
            self.active_chat_id = str(uuid.uuid4())
        if self.active_session_turns and not self.active_openclaw_session_id:
            if any(is_openclaw_model(str(turn.get("model") or "")) for turn in self.active_session_turns):
                self.active_openclaw_session_id = self._make_openclaw_session_id(self.active_chat_id)
        if self.active_chat_id and not self.current_chat_id:
            self.current_chat_id = self.active_chat_id
        if self.active_chat_id and not self._current_chat_state.get("id"):
            self._current_chat_state["id"] = self.active_chat_id
        if isinstance(self._current_chat_state, dict):
            self._current_chat_state["turns"] = self.active_session_turns
            if self._normalize_detail_panel_fields(self._current_chat_state):
                changed = True
        if hasattr(self, "notes_controller"):
            self.notes_controller.restore_state(self._current_notes_state)
            self._notes_refresh_ui()
        self._sort_archived_chats()
        if changed:
            self._defer_chat_state_save()

    def _save_state(self, *, persist_chat_history: bool = True, capture_notes_editor: bool = False):
        if hasattr(self, "notes_controller"):
            capture = getattr(self.notes_controller, "capture_editor_state", None)
            if capture_notes_editor and callable(capture):
                capture()
            to_state = getattr(self.notes_controller, "to_state_dict", None)
            if callable(to_state):
                try:
                    self._current_notes_state = to_state(capture_editor=bool(capture_notes_editor))
                except TypeError:
                    self._current_notes_state = to_state()
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
        if use_chat_store and persist_chat_history:
            self._persist_chat_history_to_store()
        active_chat = (
            self._slim_active_chat_state()
            if use_chat_store
            else (
                copy.deepcopy(self._current_chat_state)
                if isinstance(getattr(self, "_current_chat_state", None), dict)
                else {
                    "id": self.active_chat_id or self.current_chat_id or "",
                    "turns": copy.deepcopy(self.active_session_turns),
                }
            )
        )
        data = {
            "selected_model_id": self.selected_model,
            "active_chat": active_chat,
            "active_chat_id": self.active_chat_id,
            "active_openclaw_session_key": self.active_openclaw_session_key,
            "active_openclaw_session_id": self.active_openclaw_session_id,
            "active_openclaw_session_file": self.active_openclaw_session_file,
            "active_openclaw_sync_offset": self.active_openclaw_sync_offset,
            "active_openclaw_last_event_id": self.active_openclaw_last_event_id,
            "active_openclaw_last_synced_at": self.active_openclaw_last_synced_at,
            "active_codex_thread_id": self.active_codex_thread_id,
            "active_codex_turn_id": self.active_codex_turn_id,
            "active_codex_turn_active": self.active_codex_turn_active,
            "active_codex_pending_prompt": self.active_codex_pending_prompt,
            "active_codex_pending_request": self.active_codex_pending_request,
            "active_codex_request_queue": self.active_codex_request_queue,
            "active_codex_thread_flags": self.active_codex_thread_flags,
            "active_codex_latest_assistant_text": self.active_codex_latest_assistant_text,
            "active_codex_latest_assistant_phase": self.active_codex_latest_assistant_phase,
            "active_claudecode_session_id": self.active_claudecode_session_id,
            "codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled,
            "active_session_started_at": self.active_session_started_at,
            "realtime_call_role": self.realtime_call_role,
            "realtime_call_speech_rate": self.realtime_call_speech_rate,
            "remote_control_token": self.remote_control_token,
            "remote_control_domain": self.remote_control_domain,
            "remote_control_host": self.remote_control_host,
            "remote_control_port": self.remote_control_port,
            "remote_control_autostart": self.remote_control_autostart,
            "notes_ui_state": self._current_notes_state,
        }
        if not use_chat_store:
            data["archived_chats"] = self.archived_chats
            data["active_session_turns"] = self.active_session_turns
        try:
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _read_remote_control_setting(self, *names: str, default: str = "") -> str:
        for name in names:
            value = str(os.getenv(name) or "").strip()
            if value:
                return value
        return default

    def _has_remote_control_env(self, *names: str) -> bool:
        for name in names:
            if str(os.getenv(name) or "").strip():
                return True
        return False

    def _read_remote_control_bool_setting(self, *names: str, default: bool = True) -> bool:
        raw = self._read_remote_control_setting(*names, default="")
        if not raw:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    def _initialize_remote_control_settings(self) -> None:
        changed = False
        has_domain_env = self._has_remote_control_env(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
        )
        has_local_binding_env = self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
        )
        has_autostart_env = self._has_remote_control_env(
            "REMOTE_CONTROL_AUTOSTART",
            "CLAUDECODE_REMOTE_CONTROL_AUTOSTART",
        )

        token = self._read_remote_control_setting(
            "REMOTE_CONTROL_TOKEN",
            "CLAUDECODE_REMOTE_CONTROL_TOKEN",
            default=DEFAULT_REMOTE_CONTROL_TOKEN,
        )
        if not token:
            token = DEFAULT_REMOTE_CONTROL_TOKEN
        if token != self.remote_control_token:
            self.remote_control_token = token
            changed = True

        domain = self._read_remote_control_setting(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
            default=self.remote_control_domain,
        )
        if not has_domain_env and has_local_binding_env:
            domain = ""
        if domain:
            domain = normalize_remote_nats_endpoint(domain, default_scheme="wss")
        if (
            not domain
            and not has_local_binding_env
        ):
            domain = DEFAULT_REMOTE_CONTROL_DOMAIN
        if domain != self.remote_control_domain:
            self.remote_control_domain = domain
            changed = True

        fixed_domain_mode = bool(domain)
        host = self._read_remote_control_setting(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            default=self.remote_control_host or "127.0.0.1",
        ).strip() or "127.0.0.1"
        if not has_domain_env and has_local_binding_env and not self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
        ):
            host = "127.0.0.1"
        if fixed_domain_mode and is_loopback_remote_host(host):
            host = "0.0.0.0"
        if host != self.remote_control_host:
            self.remote_control_host = host
            changed = True

        port_text = self._read_remote_control_setting(
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
            default=str(self.remote_control_port or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT),
        ).strip() or str(DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        try:
            port = int(port_text)
        except Exception:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if fixed_domain_mode:
            if port <= 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port < 0:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port == 0 and port_text != "0":
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port != self.remote_control_port:
            self.remote_control_port = port
            changed = True

        autostart = self._read_remote_control_bool_setting(
            "REMOTE_CONTROL_AUTOSTART",
            "CLAUDECODE_REMOTE_CONTROL_AUTOSTART",
            default=self.remote_control_autostart,
        )
        if fixed_domain_mode and not has_autostart_env:
            autostart = True
        if autostart != self.remote_control_autostart:
            self.remote_control_autostart = autostart
            changed = True

        if changed:
            self._defer_chat_state_save()

    def _remote_runtime_config(self) -> dict:
        has_domain_env = self._has_remote_control_env(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
        )
        has_local_binding_env = self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
        )
        domain = normalize_remote_nats_endpoint(
            self._read_remote_control_setting(
                "REMOTE_CONTROL_DOMAIN",
                "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
                default=self.remote_control_domain,
            ),
            default_scheme="wss",
        )
        if not has_domain_env and has_local_binding_env:
            domain = ""
        if (
            not domain
            and not has_local_binding_env
        ):
            domain = DEFAULT_REMOTE_CONTROL_DOMAIN
        fixed_domain_mode = bool(domain)
        host = self._read_remote_control_setting(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            default=self.remote_control_host or "127.0.0.1",
        ).strip() or "127.0.0.1"
        if not has_domain_env and has_local_binding_env and not self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
        ):
            host = "127.0.0.1"
        port_text = self._read_remote_control_setting(
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
            default=str(self.remote_control_port or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT),
        ).strip() or str(DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        try:
            port = int(port_text)
        except Exception:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if fixed_domain_mode:
            if is_loopback_remote_host(host):
                host = "0.0.0.0"
            if port <= 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            published_base = domain
            publish_port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        else:
            if port < 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            publish_port = port if port > 0 else DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            published_host = "127.0.0.1" if host == "0.0.0.0" else host
            published_base = normalize_remote_nats_endpoint(
                f"ws://{published_host}:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats",
                default_scheme="ws",
            )
        return {
            "fixed_domain_mode": fixed_domain_mode,
            "host": host,
            "port": port,
            "published_base": published_base,
            "published_bind": f"ws://{host}:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats",
        }

    def _is_timestamp_like_archive_title(self, title: str) -> bool:
        text = str(title or "").strip()
        if not text:
            return False
        if re.fullmatch(r"\d{9,}(?:\.\d+)?", text):
            return True
        normalized = re.sub(r"\s+", " ", text)
        patterns = [
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?: \d{1,2}:\d{2}(?::\d{2})?)?",
            r"\d{1,2}[-/]\d{1,2}(?: \d{1,2}:\d{2}(?::\d{2})?)?",
            r"\d{1,2}:\d{2}(?::\d{2})?",
        ]
        return any(re.fullmatch(p, normalized) for p in patterns)

    @staticmethod
    def _normalize_detail_panel_fields(chat: dict) -> bool:
        changed = False
        detail_panel_mode = str(chat.get("detail_panel_mode") or "").strip()
        if detail_panel_mode not in {"answers", "execution"}:
            chat["detail_panel_mode"] = "answers"
            changed = True
        if not isinstance(chat.get("execution_steps"), list):
            chat["execution_steps"] = []
            changed = True
        return changed

    def _normalize_archived_chat(self, chat: dict) -> bool:
        changed = self._normalize_detail_panel_fields(chat)
        title_manual = chat.get("title_manual")
        if not isinstance(title_manual, bool):
            title_manual = bool(title_manual)
            chat["title_manual"] = title_manual
            changed = True
        title_source = str(chat.get("title_source") or "").strip() or (
            "manual" if title_manual else "default"
        )
        if chat.get("title_source") != title_source:
            chat["title_source"] = title_source
            changed = True
        title_updated_at = float(chat.get("title_updated_at") or chat.get("updated_at") or chat.get("created_at") or 0.0)
        if float(chat.get("title_updated_at") or 0.0) != title_updated_at:
            chat["title_updated_at"] = title_updated_at
            changed = True
        title_revision = int(chat.get("title_revision") or 1)
        if int(chat.get("title_revision") or 0) != title_revision:
            chat["title_revision"] = title_revision
            changed = True
        if not isinstance(chat.get("pinned"), bool):
            chat["pinned"] = bool(chat.get("pinned"))
            changed = True
        title = str(chat.get("title") or "").strip()
        if (not title_manual) and ((not title) or self._is_timestamp_like_archive_title(title)):
            chat["title"] = self._summarize_last_turn_locally(chat.get("turns") or [])
            changed = True
        return changed

    @staticmethod
    def _title_source_question(turns) -> str:
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            question = str(turn.get("question") or "").strip()
            if question:
                return question
        return ""

    @staticmethod
    def _compact_first_question_title(text: str, max_length: int = 18) -> str:
        rules = load_chat_title_rules()
        normalized = str(text or "").replace("\r", " ").replace("\n", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()

        def _prefix_pattern(values: list[str]):
            if not values:
                return None
            return re.compile(rf"^(?:{'|'.join(re.escape(value) for value in values)})\s*")

        def _suffix_pattern(values: list[str]):
            if not values:
                return None
            return re.compile(rf"(?:{'|'.join(re.escape(value) for value in values)})[？?]*$")

        leading_pattern = _prefix_pattern(rules["leading_phrases"])
        if leading_pattern:
            normalized = leading_pattern.sub("", normalized)
        action_pattern = _prefix_pattern(rules["action_prefixes"])
        if action_pattern:
            normalized = action_pattern.sub("", normalized)
        what_is_pattern = _prefix_pattern(rules["what_is_prefixes"])
        if what_is_pattern:
            normalized = what_is_pattern.sub("", normalized)
        question_pattern = _suffix_pattern(rules["question_suffixes"])
        if question_pattern:
            normalized = question_pattern.sub("", normalized)
        if rules["trailing_punctuation"]:
            normalized = re.sub(
                rf"[{''.join(re.escape(value) for value in rules['trailing_punctuation'])}]+$",
                "",
                normalized,
            )
        normalized = normalized.strip()
        semantic_patterns = (
            (("好吃", "美食", "餐厅", "菜谱", "吃什么"), "美食推荐"),
            (("旅游", "景点", "行程", "攻略"), "旅行攻略"),
            (("简历", "求职", "面试"), "求职准备"),
            (("自动化", "测试"), "自动化测试"),
            (("笔记", "同步"), "笔记同步"),
        )
        for keywords, replacement in semantic_patterns:
            if any(keyword in normalized for keyword in keywords):
                normalized = replacement
                break
        return normalized[:max_length].strip() if normalized else ""

    @staticmethod
    def _is_default_chat_title(title: str) -> bool:
        text = str(title or "").strip()
        return (
            text == "新聊天"
            or text == EMPTY_CURRENT_CHAT_TITLE
            or bool(re.fullmatch(rf"{re.escape(EMPTY_CURRENT_CHAT_TITLE)}\d+", text))
        )

    def _next_default_chat_title(self) -> str:
        existing_titles = {
            str(chat.get("title") or "").strip()
            for chat in self.archived_chats
            if isinstance(chat, dict)
        }
        current_title = str((self._current_chat_state or {}).get("title") or "").strip()
        if current_title:
            existing_titles.add(current_title)
        if EMPTY_CURRENT_CHAT_TITLE not in existing_titles:
            return EMPTY_CURRENT_CHAT_TITLE
        index = 1
        while f"{EMPTY_CURRENT_CHAT_TITLE}{index}" in existing_titles:
            index += 1
        return f"{EMPTY_CURRENT_CHAT_TITLE}{index}"

    def _bump_chat_title_revision(self, chat: dict, source: str, updated_at: float | None = None, title: str | None = None) -> None:
        timestamp = float(updated_at or time.time())
        next_title = str(title if title is not None else chat.get("title") or "").strip() or EMPTY_CURRENT_CHAT_TITLE
        chat["title"] = next_title
        chat["title_manual"] = source == "manual"
        chat["title_source"] = source
        chat["title_updated_at"] = timestamp
        chat["title_revision"] = int(chat.get("title_revision") or 0) + 1

    @staticmethod
    def _title_source_priority(source: str) -> int:
        normalized = str(source or "").strip().lower()
        if normalized == "manual":
            return 2
        if normalized == "auto":
            return 1
        return 0

    def _generate_first_question_title(self, question: str) -> str:
        prompt = str(question or "").strip()
        if not prompt:
            return ""
        for _ in range(3):
            try:
                client = ChatClient(
                    api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
                    model="doubao-2.0-mini",
                    timeout=15,
                )
                title = client.generate_chat_title(prompt).strip()[:40]
            except Exception:
                title = ""
            if title:
                compact = self._compact_first_question_title(title, 12)
                return compact or title
        return ""

    def _apply_generated_first_question_title(self, chat_id: str, question: str, title: str) -> None:
        resolved_chat_id = str(chat_id or "").strip()
        if not resolved_chat_id or not title:
            return
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id}:
            chat = self._current_chat_state
        else:
            chat = self._find_archived_chat(resolved_chat_id)
        if not isinstance(chat, dict):
            return
        if bool(chat.get("title_manual")) or str(chat.get("title_source") or "").strip() == "manual":
            return
        current_title = str(chat.get("title") or "").strip()
        current_source = str(chat.get("title_source") or "").strip()
        if (not self._is_default_chat_title(current_title)) and current_source != "auto":
            return
        if not self._compact_first_question_title(question, 120):
            return
        self._bump_chat_title_revision(chat, "auto", title=title)
        self._defer_chat_state_save()
        self._refresh_history(resolved_chat_id)
        self._push_remote_history_changed(resolved_chat_id)

    def _schedule_first_question_auto_title(self, chat_id: str, question: str) -> None:
        resolved_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        normalized_question = str(question or "").strip()
        if not resolved_chat_id or not normalized_question:
            return
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id}:
            chat = self._current_chat_state
        else:
            chat = self._find_archived_chat(resolved_chat_id)
        if not isinstance(chat, dict):
            return
        if bool(chat.get("title_manual")) or str(chat.get("title_source") or "").strip() == "manual":
            return
        if not self._is_default_chat_title(str(chat.get("title") or "")):
            return
        immediate_title = self._compact_first_question_title(normalized_question, 12)
        if immediate_title:
            self._apply_generated_first_question_title(
                resolved_chat_id,
                normalized_question,
                immediate_title,
            )
        if chat.get("first_question_auto_title_scheduled"):
            return
        chat["first_question_auto_title_scheduled"] = True

        def _worker() -> None:
            title = self._generate_first_question_title(normalized_question)
            if not title:
                return
            wx_call_after_if_alive(
                self._apply_generated_first_question_title,
                resolved_chat_id,
                normalized_question,
                title,
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _sort_archived_chats(self):
        self.archived_chats.sort(
            key=lambda c: (
                0 if c.get("pinned") else 1,
                -float(c.get("updated_at") or c.get("created_at") or 0.0),
                -float(c.get("created_at") or 0.0),
            )
        )

    def _current_history_id(self) -> str:
        return str(
            self.active_chat_id
            or self.current_chat_id
            or (self._current_chat_state.get("id") if isinstance(self._current_chat_state, dict) else "")
            or ""
        ).strip()

    def _current_history_title(self) -> str:
        title = str((self._current_chat_state or {}).get("title") or "").strip()
        title_manual = bool((self._current_chat_state or {}).get("title_manual"))
        if title_manual and title:
            return title
        if self._is_default_chat_title(title):
            return EMPTY_CURRENT_CHAT_TITLE
        return title or EMPTY_CURRENT_CHAT_TITLE

    def _refresh_history(self, keep_id=None):
        self._sort_archived_chats()
        labels = []
        ids = []
        current_id = self._current_history_id()
        target = keep_id if keep_id is not None else self.view_history_id
        if current_id:
            labels.append(self._current_history_title())
            ids.append(current_id)
        for c in self.archived_chats:
            chat_id = str(c.get("id") or "")
            if current_id and chat_id == current_id:
                continue
            title = str(c.get("title") or "新聊天")
            if self._is_default_chat_title(title):
                title = EMPTY_CURRENT_CHAT_TITLE
            disp = f"[置顶] {title}" if c.get("pinned") else title
            labels.append(disp)
            ids.append(chat_id)
        selected_idx = None
        if target in ids:
            selected_idx = ids.index(target)
        elif labels:
            selected_idx = 0
        changed = self._replace_listbox_items_if_changed(self.history_list, labels, selected_idx)
        self.history_ids = ids
        if not changed and selected_idx is not None:
            try:
                if self.history_list.GetSelection() != selected_idx:
                    self.history_list.SetSelection(selected_idx)
            except Exception:
                pass
        if changed:
            self._request_listbox_repaint(self.history_list)

    def _get_view_turns(self):
        if self.view_mode == "history":
            chat = self._hydrate_chat_from_store(
                self._find_archived_chat(self.view_history_id),
                include_execution_steps=False,
            )
            if chat:
                return chat.get("turns") or []
            return []
        return self.active_session_turns

    def _attachment_label(self, attachment: dict, *, incoming: bool = False) -> str:
        name = str((attachment or {}).get("name") or Path(str((attachment or {}).get("path") or "")).name or "未知附件").strip()
        is_image = str((attachment or {}).get("kind") or "").strip() == "image"
        if incoming:
            kind = "图片" if is_image else "文件"
            return f"CLI 发来{kind}：{name}"
        success = str((attachment or {}).get("status") or "").strip() == "success"
        if is_image:
            return "图片上传成功" if success else "图片上传失败"
        return f"{name} {'上传成功' if success else '上传失败'}"

    def _turn_attachment_rows(self, turn_idx: int, attachments: list[dict], *, incoming: bool = False) -> list[tuple[str, tuple]]:
        rows: list[tuple[str, tuple]] = []
        for attachment in attachments or []:
            label = self._attachment_label(attachment, incoming=incoming)
            open_path = str((attachment or {}).get("open_path") or (attachment or {}).get("path") or "").strip()
            rows.append((label, ("attachment", turn_idx, label, open_path)))
        return rows

    def _append_turn_attachment_rows(self, turn_idx: int, attachments: list[dict], *, incoming: bool = False) -> None:
        for label, meta in self._turn_attachment_rows(turn_idx, attachments, incoming=incoming):
            self.answer_list.Append(label)
            self.answer_meta.append(meta)

    def _input_attachment_marker_text(self, attachments: list[dict]) -> str:
        names = [str((item or {}).get("name") or "").strip() for item in attachments or [] if str((item or {}).get("name") or "").strip()]
        if not names:
            return ""
        return f"【附件】{'、'.join(names)}"

    def _strip_attachment_markers(self, text: str) -> str:
        lines = []
        for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if line.startswith("【附件】"):
                continue
            lines.append(raw_line)
        return "\n".join(lines).strip()

    def _normalize_attachment_kind(self, path: str) -> str:
        suffix = Path(str(path or "")).suffix.lower()
        return "image" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"} else "file"

    def _normalize_outgoing_attachments(self, attachments: list[dict]) -> tuple[list[dict], list[dict]]:
        success = []
        failed = []
        for item in attachments or []:
            path = str((item or {}).get("path") or "").strip()
            name = str((item or {}).get("name") or Path(path).name or "未知附件").strip()
            exists = bool(path) and Path(path).is_file()
            normalized = {
                "name": name,
                "path": path,
                "kind": str((item or {}).get("kind") or self._normalize_attachment_kind(path)).strip() or "file",
                "direction": "outgoing",
                "status": "success" if exists else "failed",
                "open_path": path if exists else "",
            }
            (success if exists else failed).append(normalized)
        return success, failed

    def _record_received_attachment(self, turn: dict, attachment: dict) -> bool:
        if not isinstance(turn, dict) or not isinstance(attachment, dict):
            return False
        attachments = turn.setdefault("received_attachments", [])
        if not isinstance(attachments, list):
            attachments = []
            turn["received_attachments"] = attachments
        open_path = str(attachment.get("open_path") or attachment.get("path") or "").strip()
        for existing in attachments:
            if str((existing or {}).get("open_path") or (existing or {}).get("path") or "").strip() == open_path:
                return False
        attachments.append(attachment)
        return True

    def _extract_existing_file_attachments_from_text(self, text: str, source: str) -> list[dict]:
        matches = []
        for candidate in re.findall(r"(?:[A-Za-z]:[\\/][^\s<>\|\?\*\"']+|/[^\s<>\|\?\*\"']+)", str(text or "")):
            path = str(candidate or "").strip().rstrip(".,;:)]}")
            if not path:
                continue
            try:
                file_path = Path(path)
            except Exception:
                continue
            if not file_path.is_file():
                continue
            matches.append(
                {
                    "name": file_path.name,
                    "path": str(file_path),
                    "kind": self._normalize_attachment_kind(str(file_path)),
                    "direction": "incoming",
                    "status": "success",
                    "open_path": str(file_path),
                    "source": source,
                }
            )
        deduped = []
        seen = set()
        for item in matches:
            key = str(item.get("open_path") or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _summarize_attachment_send_text(self, success: list[dict], failed: list[dict]) -> str:
        outgoing = success + failed
        if not outgoing:
            return ""
        names = "、".join(str(item.get("name") or "").strip() for item in outgoing if str(item.get("name") or "").strip())
        has_only_one_image = len(outgoing) == 1 and str(outgoing[0].get("kind") or "") == "image"
        suffix = "图片" if has_only_one_image else "文件"
        status = "已成功上传" if success and not failed else ("上传失败" if failed and not success else "已部分上传")
        return f"{names} {suffix}{status}".strip()

    def _build_cli_attachment_context(self, attachments: list[dict]) -> str:
        lines = []
        for item in attachments or []:
            path = str((item or {}).get("path") or "").strip()
            if not path:
                continue
            kind = "图片" if str((item or {}).get("kind") or "") == "image" else "文件"
            lines.append(f"- {kind}：{path}")
        if not lines:
            return ""
        return "请结合以下本地附件继续处理：\n" + "\n".join(lines)

    def _build_codex_input_items(self, question: str, attachments: list[dict]) -> list[dict]:
        items = []
        text = str(question or "").strip()
        file_context = self._build_cli_attachment_context([item for item in attachments or [] if str((item or {}).get("kind") or "") != "image"])
        if text and file_context:
            items.append({"type": "text", "text": f"{text}\n\n{file_context}"})
        elif text:
            items.append({"type": "text", "text": text})
        elif file_context:
            items.append({"type": "text", "text": file_context})
        for item in attachments or []:
            if str((item or {}).get("kind") or "") == "image":
                path = str((item or {}).get("path") or "").strip()
                if path:
                    items.append({"type": "localImage", "path": path})
        return items or [{"type": "text", "text": ""}]

    @staticmethod
    def _parse_codex_local_command(question: str) -> dict | None:
        text = str(question or "").strip()
        if not text.startswith("/") or text == "/":
            return None
        body = text[1:]
        parts = body.split(None, 1)
        name = parts[0].strip().lower()
        if not name:
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9:_\.-]*", name):
            return None
        args = parts[1].strip() if len(parts) > 1 else ""
        return {"raw": text, "name": name, "args": args}

    @staticmethod
    def _codex_local_command_name(question: str) -> str:
        command = ChatFrame._parse_codex_local_command(question)
        return str(command.get("name") or "") if command else ""

    @staticmethod
    def _codex_supported_local_commands() -> tuple[tuple[str, str], ...]:
        return (
            ("status", "显示账号、模型、沙箱、线程、上下文和额度状态"),
            ("help", "列出本程序支持的 Codex 斜杠命令"),
            ("compact", "请求 Codex 压缩当前线程上下文"),
            ("model", "不带参数显示当前模型；带 Codex 模型名时切换模型"),
            ("new", "开始一个新的本地聊天"),
            ("clear", "清除当前聊天关联的 Codex 线程状态"),
            ("stop", "中断当前活跃 Codex turn"),
        )

    def _build_codex_help_markdown(self) -> str:
        lines = [
            "## Codex 斜杠命令",
            "",
            "本程序会识别所有以 `/` 开头的 Codex 输入。以下命令已在桌面端实现：",
            "",
        ]
        lines.extend(f"- `/{name}`：{description}" for name, description in self._codex_supported_local_commands())
        lines.extend(
            [
                "",
                "其他 `/...` 命令会被识别为 Codex 斜杠命令，但当前桌面端暂不支持，不会被当成普通聊天发送。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_codex_unsupported_command_markdown(command: str, args: str = "") -> str:
        suffix = f" {args}" if str(args or "").strip() else ""
        return (
            "## Codex 斜杠命令暂不支持\n\n"
            f"`/{command}{suffix}` 已被识别为 Codex 斜杠命令，但当前桌面端还没有对应实现。\n\n"
            "为避免误操作，这条输入不会作为普通聊天内容发送给 Codex。"
        )

    @staticmethod
    def _format_codex_account_status(account_resp: dict) -> str:
        account = account_resp.get("account") if isinstance(account_resp, dict) else None
        if not isinstance(account, dict):
            return "未登录或账号不可用"
        account_type = str(account.get("type") or "").strip()
        if account_type == "chatgpt":
            email = str(account.get("email") or "").strip()
            plan = str(account.get("planType") or "").strip()
            suffix = f"，{plan}" if plan else ""
            return f"ChatGPT：{email or '未知账号'}{suffix}"
        if account_type:
            return account_type
        return "未知"

    @staticmethod
    def _format_codex_rate_limit_status(rate_limits_resp: dict) -> str:
        if not isinstance(rate_limits_resp, dict):
            return "未知"
        limits = rate_limits_resp.get("rateLimits")
        if not isinstance(limits, dict):
            return "未知"
        name = str(limits.get("limitName") or limits.get("limitId") or "Codex").strip()
        primary = limits.get("primary") if isinstance(limits.get("primary"), dict) else {}
        percent = primary.get("percentUsed") if isinstance(primary, dict) else None
        if isinstance(percent, (int, float)):
            return f"{name}：{percent:.0f}% 已用"
        return name or "未知"

    @staticmethod
    def _codex_thread_status_from_response(thread_resp: dict) -> tuple[str, list[str]]:
        thread = thread_resp.get("thread") if isinstance(thread_resp, dict) else {}
        status = thread.get("status") if isinstance(thread, dict) and isinstance(thread.get("status"), dict) else {}
        return str(status.get("type") or "").strip(), list(status.get("activeFlags") or [])

    def _codex_thread_id_for_chat(self, chat: dict | None) -> str:
        thread_id = str((chat or {}).get("codex_thread_id") or "").strip()
        if thread_id:
            return thread_id
        if chat is self._current_chat_state:
            return str(self.active_codex_thread_id or "").strip()
        return ""

    def _codex_turn_id_for_chat(self, chat: dict | None) -> str:
        turn_id = str((chat or {}).get("codex_turn_id") or "").strip()
        if turn_id:
            return turn_id
        if chat is self._current_chat_state:
            return str(self.active_codex_turn_id or "").strip()
        return ""

    def _build_codex_status_markdown(self, client, chat: dict, model: str) -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        turn_id = self._codex_turn_id_for_chat(chat)
        flags = list((chat or {}).get("codex_thread_flags") or self.active_codex_thread_flags or [])
        thread_status = "active" if flags else ("idle" if thread_id else "notLoaded")
        account_status = "未查询"
        rate_limit_status = "未查询"
        if client is not None:
            try:
                if hasattr(client, "read_thread") and thread_id:
                    thread_status, flags = self._codex_thread_status_from_response(client.read_thread(thread_id, include_turns=False))
            except Exception as exc:
                thread_status = f"查询失败：{exc}"
            try:
                if hasattr(client, "read_account"):
                    account_status = self._format_codex_account_status(client.read_account(refresh_token=False))
            except Exception as exc:
                account_status = f"查询失败：{exc}"
            try:
                if hasattr(client, "read_rate_limits"):
                    rate_limit_status = self._format_codex_rate_limit_status(client.read_rate_limits())
            except Exception as exc:
                rate_limit_status = f"查询失败：{exc}"
        usage = format_context_usage_label(context_usage_from_dict((chat or {}).get("context_usage")))
        flags_text = "、".join(str(item) for item in flags) if flags else "无"
        lines = [
            "## Codex 状态",
            "",
            f"- 模型：{model_display_name(model) or model or DEFAULT_CODEX_MODEL}",
            f"- 工作目录：{self._workspace_dir_for_codex()}",
            "- 审批策略：never",
            "- 沙箱：danger-full-access",
            f"- 线程：{thread_id or '未创建'}",
            f"- 当前轮次：{turn_id or '无'}",
            f"- 运行状态：{thread_status or '未知'}",
            f"- 活跃标记：{flags_text}",
            f"- 上下文：{usage}",
            f"- 账号：{account_status}",
            f"- 额度：{rate_limit_status}",
        ]
        return "\n".join(lines)

    def _active_chat_context_usage(self):
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        usage = (chat or {}).get("context_usage") if isinstance(chat, dict) else None
        normalized = context_usage_from_dict(usage)
        if normalized is not None:
            return normalized
        return None

    def _pending_context_usage_for_chat(self, chat: dict | None, turn_idx: int):
        if not isinstance(chat, dict):
            return None
        pending = self._pending_context_usage_by_turn.get(self._context_usage_pending_key_from_chat(chat, turn_idx))
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        model = ""
        if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict):
            model = str(turns[turn_idx].get("model") or "").strip()
        if not self._pending_context_usage_matches_model(pending, model):
            return None
        return context_usage_from_dict(pending) if isinstance(pending, dict) else None

    def _pending_context_usage_matches_model(self, pending, model: str) -> bool:
        if not isinstance(pending, dict):
            return False
        source = str(pending.get("source") or "").strip()
        model_text = str(model or "").strip()
        if is_codex_model(model_text):
            return source == "codex"
        if is_openclaw_model(model_text):
            return source == "openclaw"
        if is_claudecode_model(model_text):
            return source == "claudecode"
        return source not in {"codex", "openclaw", "claudecode"}

    def _is_authoritative_context_usage_model(self, model: str) -> bool:
        text = str(model or "").strip()
        return is_openclaw_model(text) or is_codex_model(text) or is_claudecode_model(text)

    def _turns_require_authoritative_context_usage(self, turns: list[dict]) -> bool:
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                return self._is_authoritative_context_usage_model(model)
        return False

    def _history_context_fallback_model(self, chat, turns) -> str:
        if isinstance(chat, dict):
            model = str(chat.get("model") or "").strip()
            if model:
                return model
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                return model
        return str(self.selected_model or "").strip() or DEFAULT_MODEL_ID

    def _active_chat_current_model(self) -> str:
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        turns = self._get_view_turns()
        if turns and isinstance(chat, dict):
            pending = self._pending_context_usage_for_chat(chat, len(turns) - 1)
            if pending is not None and str(pending.model or "").strip():
                return str(pending.model or "").strip()
        usage = (chat or {}).get("context_usage") if isinstance(chat, dict) else None
        normalized = context_usage_from_dict(usage)
        if normalized is not None and str(normalized.model or "").strip():
            return str(normalized.model or "").strip()
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                configured_model = codex_model_label_for_model(model)
                if configured_model:
                    return configured_model
                if is_codex_model(model):
                    configured = read_codex_cli_model_label(self._workspace_dir_for_codex())
                    if configured:
                        return configured
                return model
        if isinstance(chat, dict):
            model = str(chat.get("model") or "").strip()
            if model:
                configured_model = codex_model_label_for_model(model)
                if configured_model:
                    return configured_model
                if is_codex_model(model):
                    configured = read_codex_cli_model_label(self._workspace_dir_for_codex())
                    if configured:
                        return configured
                return model
        selected = str(self.selected_model or "").strip() or DEFAULT_MODEL_ID
        configured_model = codex_model_label_for_model(selected)
        if configured_model:
            return configured_model
        if is_codex_model(selected):
            configured = read_codex_cli_model_label(self._workspace_dir_for_codex())
            if configured:
                return configured
        return selected

    def _append_context_usage_row(self, rows: list[str] | None = None, metas: list[tuple] | None = None) -> None:
        label = format_context_usage_label(self._active_chat_context_usage())
        meta = ("context_usage", -1, label, "")
        if rows is not None and metas is not None:
            rows.append(label)
            metas.append(meta)
            return
        self.answer_list.Append(label)
        self.answer_meta.append(meta)

    def _append_current_model_row(self, rows: list[str] | None = None, metas: list[tuple] | None = None) -> None:
        return

    def _refresh_context_usage_header_rows(self) -> bool:
        if not hasattr(self, "answer_list"):
            return False
        handled = False
        changed = False
        labels = {
            "context_usage": format_context_usage_label(self._active_chat_context_usage()),
        }
        for row, meta in enumerate(list(self.answer_meta)):
            if row >= self.answer_list.GetCount():
                break
            kind = meta[0] if meta else ""
            if kind not in labels:
                continue
            handled = True
            label = labels[kind]
            current = ""
            try:
                current = self.answer_list.GetString(row)
            except Exception:
                current = ""
            if current == label and len(meta) >= 3 and meta[2] == label:
                continue
            try:
                self.answer_list.SetString(row, label)
            except Exception:
                continue
            self.answer_meta[row] = (kind, -1, label, "")
            changed = True
        if changed:
            self._request_listbox_repaint(self.answer_list)
        return handled

    @staticmethod
    def _context_usage_payload_changed(previous, current) -> bool:
        return context_usage_from_dict(previous) != context_usage_from_dict(current)

    def _set_pending_context_usage_for_turn(self, chat: dict, turn_idx: int, usage) -> bool:
        key = self._context_usage_pending_key_from_chat(chat, turn_idx)
        previous = self._pending_context_usage_by_turn.get(key)
        if not self._context_usage_payload_changed(previous, usage):
            return False
        self._pending_context_usage_by_turn[key] = usage
        return True

    def _reset_answer_visible_row_limit(self) -> None:
        self.answer_visible_row_limit = ANSWER_LIST_DEFAULT_VISIBLE_ROWS

    def _reset_execution_visible_row_limit(self) -> None:
        self.execution_visible_row_limit = EXECUTION_LIST_DEFAULT_VISIBLE_ROWS

    def _show_more_answer_rows(self) -> None:
        current_limit = int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0)
        self.answer_visible_row_limit = max(ANSWER_LIST_DEFAULT_VISIBLE_ROWS, current_limit) + ANSWER_LIST_EXPAND_ROWS
        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")

    def _show_more_execution_rows(self) -> None:
        current_limit = int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0)
        self.execution_visible_row_limit = max(EXECUTION_LIST_DEFAULT_VISIBLE_ROWS, current_limit) + EXECUTION_LIST_EXPAND_ROWS
        self._render_execution_list()

    def _apply_answer_row_limit(self, header_count: int, *, force_has_more: bool = False) -> None:
        rows = [self.answer_list.GetString(idx) for idx in range(self.answer_list.GetCount())]
        metas = list(self.answer_meta)
        rows, metas, active_idx = self._answer_rows_with_limit(
            rows,
            metas,
            header_count,
            active_meta=metas[self._active_answer_row_index] if 0 <= self._active_answer_row_index < len(metas) else None,
            force_has_more=force_has_more,
        )
        self.answer_list.Clear()
        self.answer_meta = []
        self._active_answer_row_index = active_idx
        for row_text, meta in zip(rows, metas):
            self.answer_list.Append(row_text)
            self.answer_meta.append(meta)

    def _answer_rows_with_limit(
        self,
        rows: list[str],
        metas: list[tuple],
        header_count: int,
        *,
        active_meta: tuple | None = None,
        force_has_more: bool = False,
    ) -> tuple[list[str], list[tuple], int]:
        header_count = max(0, min(int(header_count or 0), len(rows), len(metas)))
        header_rows = rows[:header_count]
        header_metas = metas[:header_count]
        content_rows = rows[header_count:]
        content_metas = metas[header_count:]
        self.answer_total_content_rows = len(content_rows)
        limit = max(ANSWER_LIST_DEFAULT_VISIBLE_ROWS, int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0))
        self.answer_visible_row_limit = limit
        has_more = force_has_more or len(content_rows) > limit
        if len(content_rows) > limit:
            content_rows = content_rows[-limit:]
            content_metas = content_metas[-limit:]
        limited_rows: list[str] = []
        limited_metas: list[tuple] = []
        if has_more:
            limited_rows.append("更多")
            limited_metas.append(("more", -1, "更多", ""))
        for row_text, meta in zip(header_rows + content_rows, header_metas + content_metas):
            limited_rows.append(row_text)
            limited_metas.append(meta)
        active_idx = -1
        if active_meta is not None:
            for idx, meta in enumerate(limited_metas):
                if meta == active_meta:
                    active_idx = idx
                    break
        return limited_rows, limited_metas, active_idx

    def _refresh_answer_list_preserving_selection(self, refresh_execution: bool = True) -> None:
        selected_meta = None
        idx = self.answer_list.GetSelection() if hasattr(self, "answer_list") else wx.NOT_FOUND
        if idx != wx.NOT_FOUND and 0 <= idx < len(self.answer_meta):
            selected_meta = self.answer_meta[idx]
        self._render_answer_list_compat(refresh_execution=refresh_execution)
        if selected_meta is None:
            return
        for new_idx, meta in enumerate(self.answer_meta):
            if selected_meta[0] in {"context_usage", "current_model"}:
                matched = meta[0] == "context_usage"
                if selected_meta[0] == "current_model":
                    matched = meta[0] == "current_model"
            else:
                matched = meta == selected_meta
            if matched:
                self.answer_list.SetSelection(new_idx)
                break

    def _render_answer_list_compat(self, refresh_execution: bool = True) -> None:
        if refresh_execution:
            self._render_answer_list()
            return
        self._render_answer_list(refresh_execution=False)

    def _render_answer_list(self, refresh_execution: bool = True):
        mode = self._apply_detail_panel_mode()
        rows: list[str] = []
        metas: list[tuple] = []
        self._active_answer_row_index = -1
        self._append_context_usage_row(rows, metas)
        self._append_current_model_row(rows, metas)
        header_count = len(metas)
        turns = self._get_view_turns()
        if not turns:
            self.answer_total_content_rows = 1
            rows.append("暂无对话内容")
            metas.append(("info", -1, "", ""))
            selected_idx = 0 if rows else None
            changed = self._replace_listbox_items_if_changed(self.answer_list, rows, selected_idx)
            self.answer_meta = metas
            if changed:
                self._request_listbox_repaint(self.answer_list)
            if mode == "execution" and refresh_execution:
                self._render_execution_list()
            return
        limit = max(
            ANSWER_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "answer_visible_row_limit", ANSWER_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        turn_offset = 0
        force_has_more = False
        visible_turns = turns
        if len(turns) > limit:
            turn_offset = len(turns) - limit
            visible_turns = turns[turn_offset:]
            force_has_more = True
        for local_i, t in enumerate(visible_turns):
            i = turn_offset + local_i
            q = str(t.get("question") or "")
            a_md, a = self._turn_answer_markdown(t)
            attachments = t.get("attachments") if isinstance(t.get("attachments"), list) else []
            received_attachments = t.get("received_attachments") if isinstance(t.get("received_attachments"), list) else []
            suppress_empty_answer_row = bool(t.get("suppress_empty_answer_row")) and (not str(a_md or "").strip())
            attachment_only_summary = bool(attachments) and suppress_empty_answer_row and any(
                token in q for token in ("上传成功", "已成功上传", "上传失败", "已部分上传")
            )
            show_pending_placeholder = (a_md != REQUESTING_TEXT) and (not suppress_empty_answer_row)
            show_user_rows = bool(attachments) or not (
                (is_openclaw_model(str(t.get("model") or "")) or is_codex_model(str(t.get("model") or "")) or is_claudecode_model(str(t.get("model") or "")))
                and (not q.strip())
                and bool(a_md.strip())
            )
            should_show_user_label = show_user_rows and ((q.strip() and not attachment_only_summary) or bool(attachments))
            if should_show_user_label:
                rows.append("我")
                metas.append(("user", i, "我", ""))
                if q.strip() and not attachment_only_summary:
                    rows.append(q)
                    metas.append(("question", i, q, ""))
            if attachments:
                for label, meta in self._turn_attachment_rows(i, attachments, incoming=False):
                    rows.append(label)
                    metas.append(meta)
            if show_pending_placeholder:
                rows.append("小诸葛")
                metas.append(("ai", i, "小诸葛", ""))
                rows.append(a)
                metas.append(("answer", i, a, a_md))
                for label, meta in self._turn_attachment_rows(i, received_attachments, incoming=True):
                    rows.append(label)
                    metas.append(meta)
                if self.view_mode == "active" and i == self.active_turn_idx:
                    self._active_answer_row_index = len(rows) - 1
            elif received_attachments:
                for label, meta in self._turn_attachment_rows(i, received_attachments, incoming=True):
                    rows.append(label)
                    metas.append(meta)
        active_meta = metas[self._active_answer_row_index] if 0 <= self._active_answer_row_index < len(metas) else None
        rows, metas, active_idx = self._answer_rows_with_limit(rows, metas, header_count, active_meta=active_meta, force_has_more=force_has_more)
        selected_idx = self.answer_list.GetSelection()
        if selected_idx == wx.NOT_FOUND:
            selected_idx = len(rows) - 1 if rows else None
        elif rows:
            selected_idx = max(0, min(int(selected_idx), len(rows) - 1))
        else:
            selected_idx = None
        changed = self._replace_listbox_items_if_changed(self.answer_list, rows, selected_idx)
        self.answer_meta = metas
        self._active_answer_row_index = active_idx
        if changed:
            self._request_listbox_repaint(self.answer_list)
        if mode == "execution" and refresh_execution:
            self._render_execution_list()

    def _request_listbox_repaint(self, *controls) -> None:
        for control in controls:
            if control is None:
                continue
            try:
                control.Refresh()
            except Exception:
                pass

    def _is_foreground_window(self) -> bool:
        return self.IsActive()

    def _can_focus_completion_result(self) -> bool:
        # 只在窗口本来就在前台且未最小化时，才允许把焦点移到最新回答。
        if not self._is_foreground_window() or self.IsIconized():
            return False
        try:
            focus = wx.Window.FindFocus()
        except Exception:
            focus = None
        if focus is None:
            return True
        allowed = {
            ctrl
            for ctrl in (
                getattr(self, "input_edit", None),
                getattr(self, "answer_list", None),
                getattr(self, "send_button", None),
            )
            if ctrl is not None
        }
        return focus in allowed

    def _find_answer_row_index(self, turn_idx: int) -> int:
        for row, meta in enumerate(self.answer_meta):
            if meta[0] == "answer" and meta[1] == turn_idx:
                return row
        return -1

    def _update_active_answer_row(self, turn_idx: int) -> bool:
        if self.view_mode != "active":
            return False
        row = self._active_answer_row_index
        if row < 0 or row >= self.answer_list.GetCount():
            row = self._find_answer_row_index(turn_idx)
            self._active_answer_row_index = row
        if row < 0:
            return False
        if not (0 <= turn_idx < len(self.active_session_turns)):
            return False
        answer_md = str(self.active_session_turns[turn_idx].get("answer_md") or "")
        text = REQUESTING_TEXT if answer_md == REQUESTING_TEXT else remove_emojis(md_to_plain(answer_md))
        item_type, idx, current_meta_text, current_meta_md = self.answer_meta[row]
        if current_meta_text == text and current_meta_md == answer_md:
            return True
        current_text = ""
        try:
            current_text = self.answer_list.GetString(row)
        except Exception:
            current_text = ""
        if current_text == text and current_meta_md == answer_md:
            return True
        self.answer_list.SetString(row, text)
        self.answer_meta[row] = (item_type, idx, text, answer_md)
        self._request_listbox_repaint(self.answer_list)
        return True

    def _on_model_changed(self, _):
        return

    def _resolve_current_model(self) -> str:
        # Always honor the model currently shown in combobox for each send.
        if threading.current_thread() is not threading.main_thread():
            cached = str(self.selected_model or "").strip()
            if cached in MODEL_IDS:
                return cached
            resolved_cached = model_id_from_display_name(cached)
            if resolved_cached in MODEL_IDS:
                return resolved_cached
            return DEFAULT_MODEL_ID
        current = (self.model_combo.GetStringSelection() or "").strip()
        if current in MODEL_IDS:
            return current
        resolved = model_id_from_display_name(current)
        if resolved in MODEL_IDS:
            return resolved
        try:
            sel = self.model_combo.GetSelection()
            if sel != wx.NOT_FOUND:
                current = (self.model_combo.GetString(sel) or "").strip()
                if current in MODEL_IDS:
                    return current
                resolved = model_id_from_display_name(current)
                if resolved in MODEL_IDS or is_codex_model(resolved) or is_claudecode_model(resolved) or is_openclaw_model(resolved):
                    return resolved
        except Exception:
            pass
        current = (self.model_combo.GetValue() or "").strip()
        if current in MODEL_IDS:
            return current
        resolved = model_id_from_display_name(current)
        if resolved in MODEL_IDS or is_codex_model(resolved) or is_claudecode_model(resolved) or is_openclaw_model(resolved):
            return resolved
        if self.selected_model in MODEL_IDS and not is_visible_model_id(self.selected_model):
            return self.selected_model
        if self.selected_model in MODEL_IDS:
            return self.selected_model
        resolved = model_id_from_display_name(self.selected_model)
        if resolved in MODEL_IDS or is_codex_model(resolved) or is_claudecode_model(resolved) or is_openclaw_model(resolved):
            return resolved
        return DEFAULT_MODEL_ID

    def _detail_panel_mode(self) -> str:
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        if self.view_mode == "history":
            viewed_chat = self._find_archived_chat(self.view_history_id)
            if isinstance(viewed_chat, dict):
                state = viewed_chat
        mode = str((state or {}).get("detail_panel_mode") or "").strip()
        return "execution" if mode == "execution" else "answers"

    def _current_detail_tab_target(self):
        if self._detail_panel_mode() == "execution" and hasattr(self, "execution_list"):
            return self.execution_list
        return self.answer_list if hasattr(self, "answer_list") else None

    def _current_execution_steps(self) -> list:
        if self.view_mode == "history":
            chat = self._hydrate_chat_from_store(self._find_archived_chat(self.view_history_id))
            if isinstance(chat, dict):
                steps = chat.get("execution_steps")
                if isinstance(steps, list):
                    return steps
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        steps = state.get("execution_steps")
        if not isinstance(steps, list):
            return []
        if self.view_mode == "active" and any(isinstance(step, dict) and "turn_idx" in step for step in steps):
            active_idx = int(getattr(self, "active_turn_idx", -1) or -1)
            return [
                step
                for step in steps
                if not isinstance(step, dict)
                or ("turn_idx" not in step)
                or self._safe_int(step.get("turn_idx"), -1) == active_idx
            ]
        return steps

    def _current_execution_steps_for_render(self) -> tuple[int, list]:
        limit = max(
            EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        chat_id = self._visible_execution_chat_id()
        store = getattr(self, "chat_store", None)
        if (
            getattr(self, "_chat_store_enabled", False)
            and store is not None
            and chat_id
            and hasattr(store, "load_recent_execution_steps")
        ):
            turn_idx = None
            if self.view_mode == "active":
                active_idx = int(getattr(self, "active_turn_idx", -1) or -1)
                turn_idx = active_idx if active_idx >= 0 else None
            try:
                total, rows = store.load_recent_execution_steps(chat_id, turn_idx=turn_idx, limit=limit)
                if total > 0 or rows:
                    return total, rows
            except Exception:
                pass
        rows = list(self._current_execution_steps())
        return len(rows), rows[-limit:]

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _visible_execution_chat_id(self) -> str:
        if self.view_mode == "history":
            viewed_id = str(self.view_history_id or "").strip()
            if viewed_id:
                return viewed_id
        return str(self.active_chat_id or self.current_chat_id or "").strip()

    def _flush_relevant_execution_deltas_for_switch(self) -> bool:
        flushed = False
        visible_chat_id = self._visible_execution_chat_id()
        if visible_chat_id:
            flushed = self._flush_all_execution_deltas_for_chat(visible_chat_id) or flushed
        active_chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if active_chat_id and active_chat_id != visible_chat_id:
            flushed = self._flush_all_execution_deltas_for_chat(active_chat_id) or flushed
        return flushed

    def _chat_state_for_execution_steps(self, chat_id: str) -> dict | None:
        resolved_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            if not isinstance(getattr(self, "_current_chat_state", None), dict):
                self._current_chat_state = {}
            return self._current_chat_state
        chat = self._hydrate_chat_from_store(self._find_archived_chat(resolved_chat_id))
        return chat if isinstance(chat, dict) else None

    @staticmethod
    def _event_turn_id(event: CodexEvent) -> str:
        return str(getattr(event, "turn_id", "") or "").strip()

    @staticmethod
    def _event_thread_id(event: CodexEvent) -> str:
        return str(getattr(event, "thread_id", "") or "").strip()

    def _event_turn_index(self, turns: list, event: CodexEvent) -> int:
        turn_id = self._event_turn_id(event)
        if turn_id:
            for idx, turn in enumerate(turns or []):
                if not isinstance(turn, dict):
                    continue
                for key in ("turn_id", "codex_turn_id", "id"):
                    if str(turn.get(key) or "").strip() == turn_id:
                        return idx
        return len(turns) - 1 if isinstance(turns, list) and turns else -1

    def _known_codex_turn_ids_for_chat(self, chat: dict | None) -> set[str]:
        ids: set[str] = set()
        if isinstance(chat, dict):
            for key in ("codex_turn_id", "turn_id"):
                value = str(chat.get(key) or "").strip()
                if value:
                    ids.add(value)
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                for key in ("codex_turn_id", "turn_id", "id"):
                    value = str(turn.get(key) or "").strip()
                    if value:
                        ids.add(value)
        if chat is getattr(self, "_current_chat_state", None):
            value = str(getattr(self, "active_codex_turn_id", "") or "").strip()
            if value:
                ids.add(value)
        return ids

    def _codex_event_turn_is_compatible_with_chat(self, chat: dict | None, event: CodexEvent) -> bool:
        turn_id = self._event_turn_id(event)
        known_turn_ids = self._known_codex_turn_ids_for_chat(chat)
        if not known_turn_ids:
            return True
        if not turn_id:
            return not self._codex_event_requires_known_turn(event)
        return turn_id in known_turn_ids

    @staticmethod
    def _codex_event_requires_known_turn(event: CodexEvent) -> bool:
        event_type = str(getattr(event, "type", "") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        if event_type in {"subagent_result", "turn_completed"}:
            return True
        if event_type == "item_completed" and (phase == "final_answer" or status == "imageView"):
            return True
        return False

    def _known_codex_event_chat_id(self, event: CodexEvent) -> str:
        turn_id = self._event_turn_id(event)
        thread_id = self._event_thread_id(event)
        candidates = []
        active_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if active_id:
            candidates.append(
                {
                    "id": active_id,
                    "codex_thread_id": str(getattr(self, "active_codex_thread_id", "") or "").strip(),
                    "codex_turn_id": str(getattr(self, "active_codex_turn_id", "") or "").strip(),
                    "turns": self.active_session_turns if isinstance(self.active_session_turns, list) else [],
                }
            )
        if isinstance(getattr(self, "_current_chat_state", None), dict):
            candidates.append(self._current_chat_state)
        candidates.extend(chat for chat in (self.archived_chats or []) if isinstance(chat, dict))

        if turn_id:
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                if str(chat.get("codex_turn_id") or "").strip() == turn_id:
                    return chat_id
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("codex_turn_id") or turn.get("turn_id") or turn.get("id") or "").strip() == turn_id:
                        return chat_id

        if thread_id:
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                if str(chat.get("codex_thread_id") or "").strip() == thread_id:
                    return chat_id
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("codex_thread_id") or "").strip() == thread_id:
                        return chat_id

        return ""

    def _resolve_codex_event_chat_id(self, event: CodexEvent) -> str:
        known_chat_id = self._known_codex_event_chat_id(event)
        if known_chat_id:
            return known_chat_id
        return str(self.active_chat_id or self.current_chat_id or "").strip()

    @staticmethod
    def _codex_execution_step_fallback(event: CodexEvent) -> str:
        for key in ("status", "phase", "text"):
            value = str(getattr(event, key, "") or "").strip()
            if value:
                return value
        return "步骤"

    @staticmethod
    def _codex_item_paths_text(item: dict) -> str:
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        paths = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or change.get("filePath") or change.get("targetPath") or "").strip()
            if path and path not in paths:
                paths.append(path)
        if paths:
            return ", ".join(paths[:3]) + (" 等" if len(paths) > 3 else "")
        for key in ("path", "filePath", "targetPath"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""

    def _codex_item_summary_text(self, event: CodexEvent) -> str:
        item = event.data if isinstance(event.data, dict) else {}
        item_type = str(item.get("type") or getattr(event, "status", "") or "").strip()
        title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        command = str(item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        text = str(item.get("text") or getattr(event, "text", "") or "").strip()
        phase = str(item.get("phase") or getattr(event, "phase", "") or "").strip()
        exit_code = item.get("exitCode")
        paths_text = self._codex_item_paths_text(item)

        if item_type == "commandExecution":
            parts = []
            if title:
                parts.append(title)
            if command:
                parts.append(f"命令：{command}")
            if exit_code not in (None, ""):
                parts.append(f"退出码：{exit_code}")
            if text and text not in {title, command}:
                parts.append(text)
            return " | ".join(parts)

        if item_type == "fileChange":
            if paths_text:
                return f"修改文件 {paths_text}"
            if title:
                return title

        if item_type == "agentMessage":
            if phase and text:
                return f"{phase}：{text}"
            if text:
                return text

        parts = []
        if title:
            parts.append(title)
        if paths_text and paths_text not in title:
            parts.append(paths_text)
        if text and text not in {title, paths_text}:
            parts.append(text)
        if phase and phase not in text:
            parts.append(f"阶段：{phase}")
        return " | ".join([part for part in parts if part])

    def _execution_display_kind(self, event: CodexEvent) -> str:
        display_kind = str(getattr(event, "display_kind", "") or "").strip()
        if display_kind:
            return display_kind
        event_type = str(getattr(event, "type", "") or "").strip()
        subtype = str(getattr(event, "subtype", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        item = event.data if isinstance(event.data, dict) else {}
        item_type = str(item.get("type") or "").strip()
        if event_type == "plan_updated":
            return "plan"
        if event_type == "stderr":
            return "error"
        if event_type == "diff_updated":
            return "diff"
        if event_type == "server_request":
            return "user_input"
        if event_type in ("turn_started", "turn_completed"):
            return "status"
        if subtype == "commandExecution" or status == "commandExecution" or item_type == "commandExecution":
            return "command"
        if "command" in {subtype, status, item_type}:
            return "command"
        return ""

    def _execution_detail_text_from_event(self, event: CodexEvent) -> str:
        event_type = str(getattr(event, "type", "") or "").strip()
        text = str(getattr(event, "text", "") or "").strip()
        raw_text = str(getattr(event, "raw_text", "") or "").rstrip()
        title = str(getattr(event, "title", "") or "").strip()
        command = str(getattr(event, "command", "") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        subtype = str(getattr(event, "subtype", "") or "").strip()
        item = event.data if isinstance(event.data, dict) else {}
        if not title:
            title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        if not command:
            command = str(item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        if event_type == "diff_updated":
            return "已生成代码变更"
        if event_type == "turn_started":
            return raw_text or text or "开始处理本轮请求"
        if event_type == "turn_completed":
            return raw_text or text or "本轮处理结束"
        if event_type == "server_request":
            return raw_text or text or "等待用户输入"
        if event_type == "plan_updated":
            return raw_text or text or "计划更新"
        if event_type == "stderr":
            return raw_text or text or "错误输出"
        if event_type == "item_completed" and phase == "final_answer":
            return raw_text or text or "已生成最终回答"
        if raw_text:
            return raw_text
        if event_type in ("item_started", "item_completed"):
            prefix = "开始执行：" if event_type == "item_started" else "完成执行："
            summary = self._codex_item_summary_text(event)
            if command:
                header = title or summary or self._codex_execution_step_fallback(event)
                lines = [f"{prefix}{header}"]
                lines.append(f"命令：{command}")
                exit_code = getattr(event, "exit_code", None)
                if exit_code in (None, ""):
                    exit_code = item.get("exitCode")
                if exit_code not in (None, "") and event_type == "item_completed":
                    lines.append(f"退出码：{exit_code}")
                if text and text not in {header, command}:
                    lines.append(text)
                return "\n".join(line for line in lines if str(line or "").strip())
            if summary:
                return f"{prefix}{summary}"
            fallback = self._codex_execution_step_fallback(event)
            return f"{prefix}{fallback}" if fallback else ""
        if text:
            return text
        if title:
            return title
        if command:
            return command
        for fallback in (phase, status, subtype):
            if fallback:
                return fallback
        return ""

    @staticmethod
    def _execution_list_text_from_detail(detail: str, kind: str) -> str:
        single_line = re.sub(r"\s+", " ", str(detail or "").strip())
        if not single_line:
            return ""
        prefix_map = {
            "command": "命令：",
            "error": "错误：",
            "plan": "计划：",
        }
        prefix = prefix_map.get(str(kind or "").strip(), "")
        if prefix and not single_line.startswith(prefix):
            return f"{prefix}{single_line}"
        return single_line

    @staticmethod
    def _execution_command_list_text(event_type: str, title: str, command: str, exit_code, fallback_text: str = "") -> str:
        parts = []
        normalized_type = str(event_type or "").strip()
        if normalized_type == "item_started":
            parts.append("开始执行")
        elif normalized_type == "item_completed":
            parts.append("完成执行")
        title_text = str(title or "").strip()
        command_text = str(command or "").strip()
        fallback = str(fallback_text or "").strip()
        if title_text:
            parts.append(title_text)
        if command_text:
            parts.append(command_text)
        if not title_text and not command_text and fallback:
            parts.append(fallback)
        if normalized_type == "item_completed" and exit_code not in (None, ""):
            parts.append(f"退出码：{exit_code}")
        summary = " ".join(parts).strip()
        return f"命令：{summary}" if summary else "命令：commandExecution"

    def _build_execution_entry(self, event: CodexEvent) -> dict | None:
        if not isinstance(event, CodexEvent):
            return None
        detail_text = self._execution_detail_text_from_event(event)
        if not detail_text:
            return None
        display_kind = self._execution_display_kind(event)
        if display_kind == "error":
            detail_text = self._sanitize_execution_error_text(detail_text)
            if not detail_text:
                return None
        item = event.data if isinstance(event.data, dict) else {}
        title = str(getattr(event, "title", "") or item.get("title") or item.get("name") or item.get("label") or "").strip()
        command = str(getattr(event, "command", "") or item.get("command") or item.get("commandLine") or item.get("cmd") or "").strip()
        exit_code = getattr(event, "exit_code", None)
        if exit_code in (None, ""):
            exit_code = item.get("exitCode")
        try:
            exit_code = int(exit_code) if exit_code not in (None, "") else None
        except (TypeError, ValueError):
            exit_code = None
        subtype = str(getattr(event, "subtype", "") or item.get("type") or "").strip()
        event_type = str(getattr(event, "type", "") or "").strip()
        phase = str(getattr(event, "phase", "") or "").strip()
        command_fallback = subtype or str(getattr(event, "status", "") or "").strip() or event_type
        if event_type == "diff_updated":
            list_text = "已生成代码变更"
        elif event_type == "item_completed" and phase == "final_answer":
            list_text = "已生成最终回答"
        elif display_kind == "command":
            list_text = self._execution_command_list_text(event_type, title, command, exit_code, command_fallback)
        else:
            list_text = self._execution_list_text_from_detail(detail_text, display_kind)
        return {
            "event_type": event_type,
            "display_kind": display_kind,
            "subtype": subtype,
            "list_text": list_text,
            "detail_text": detail_text,
            "raw_text": str(getattr(event, "raw_text", "") or ""),
            "text": str(getattr(event, "text", "") or ""),
            "title": title,
            "command": command,
            "exit_code": exit_code,
            "phase": phase,
            "status": str(getattr(event, "status", "") or "").strip(),
            "thread_id": self._event_thread_id(event),
            "turn_id": self._event_turn_id(event),
            "item_id": str(getattr(event, "item_id", "") or "").strip(),
            "created_at": time.time(),
        }

    def _append_execution_step_to_chat(self, chat_id: str, step_text: str, *, save_state: bool = True) -> bool:
        text = str(step_text or "").strip()
        if not text:
            return False
        return self._append_execution_entry_to_chat(chat_id, {"step": text}, save_state=save_state)

    def _visible_execution_chat_state(self) -> dict | None:
        if self._detail_panel_mode() != "execution":
            return None
        if self.view_mode == "history":
            chat = self._find_archived_chat(self.view_history_id)
            return chat if isinstance(chat, dict) else None
        state = getattr(self, "_current_chat_state", None)
        return state if isinstance(state, dict) else None

    def _append_visible_execution_entry(self, target_chat: dict, step_idx: int, step) -> bool:
        if not isinstance(target_chat, dict) or not hasattr(self, "execution_list"):
            return False
        if self._visible_execution_chat_state() is not target_chat:
            return False
        if self.view_mode == "active" and isinstance(step, dict) and "turn_idx" in step:
            if self._safe_int(step.get("turn_idx"), -1) != int(getattr(self, "active_turn_idx", -1) or -1):
                return False
        if not self._should_show_execution_step(step):
            return False
        if bool(getattr(self, "_execution_list_pending_turn_reset", False)):
            self._execution_list_pending_turn_reset = False
            try:
                self.execution_list.Clear()
            except Exception:
                return False
            self.execution_meta = []
        meta = self._execution_meta_tuple(step_idx, step)
        row_text = str(meta[2] or "").strip()
        if not row_text:
            return False
        if (
            self.execution_list.GetCount() == 1
            and len(self.execution_meta) == 1
            and self.execution_meta[0][0] == "info"
        ):
            try:
                self.execution_list.Delete(0)
            except Exception:
                return False
            self.execution_meta = []
        limit = max(
            EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        visible_execution_rows = sum(1 for item in self.execution_meta if item[0] == "execution")
        has_more_row = bool(self.execution_meta and self.execution_meta[0][0] == "more")
        if has_more_row:
            self.execution_list.Append(row_text)
            self.execution_meta.append(meta)
            if visible_execution_rows >= limit:
                try:
                    self.execution_list.Delete(1)
                    del self.execution_meta[1]
                except Exception:
                    self._rebuild_execution_list_from_state()
            if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
                self._execution_list_deferred_repaint = True
                self._execution_list_deferred_select_latest = True
            else:
                try:
                    self.execution_list.SetSelection(self.execution_list.GetCount() - 1)
                except Exception:
                    pass
                self._request_listbox_repaint(self.execution_list)
            return True
        if visible_execution_rows >= limit:
            try:
                self.execution_list.Insert("更多", 0)
                self.execution_meta.insert(0, ("more", -1, "更多", ""))
                self.execution_list.Append(row_text)
                self.execution_meta.append(meta)
                self.execution_list.Delete(1)
                del self.execution_meta[1]
            except Exception:
                self._rebuild_execution_list_from_state()
            if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
                self._execution_list_deferred_repaint = True
                self._execution_list_deferred_select_latest = True
            else:
                try:
                    self.execution_list.SetSelection(self.execution_list.GetCount() - 1)
                except Exception:
                    pass
                self._request_listbox_repaint(self.execution_list)
            return True
        self.execution_list.Append(row_text)
        self.execution_meta.append(meta)
        if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
            self._execution_list_deferred_repaint = True
            self._execution_list_deferred_select_latest = True
            return True
        try:
            self.execution_list.SetSelection(self.execution_list.GetCount() - 1)
        except Exception:
            pass
        self._request_listbox_repaint(self.execution_list)
        return True

    def _flush_deferred_execution_list_updates(self) -> None:
        if not bool(getattr(self, "_execution_list_deferred_repaint", False)):
            return
        self._execution_list_deferred_repaint = False
        should_select_latest = bool(getattr(self, "_execution_list_deferred_select_latest", False))
        self._execution_list_deferred_select_latest = False
        if should_select_latest and hasattr(self, "execution_list") and self.execution_list.GetCount() > 0:
            try:
                should_select_latest = not self.execution_list.HasFocus()
            except Exception:
                should_select_latest = True
        if should_select_latest and hasattr(self, "execution_list") and self.execution_list.GetCount() > 0:
            try:
                self.execution_list.SetSelection(self.execution_list.GetCount() - 1)
            except Exception:
                pass
        self._request_listbox_repaint(self.execution_list)

    @staticmethod
    def _execution_step_detail_text(step) -> str:
        if not isinstance(step, dict):
            return str(step or "").strip()
        return str(
            step.get("detail_text")
            or step.get("message")
            or step.get("step")
            or step.get("title")
            or step.get("text")
            or step.get("content")
            or step.get("description")
            or ""
        ).strip()

    @staticmethod
    def _normalize_execution_text_for_compare(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    @staticmethod
    def _execution_texts_are_near_duplicate(previous_text: str, next_text: str) -> bool:
        left = str(previous_text or "").strip()
        right = str(next_text or "").strip()
        if not left or not right:
            return False
        if left == right:
            return True
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if len(shorter) < 16:
            return False
        return longer.startswith(shorter) or longer.endswith(shorter)

    def _execution_entries_should_dedupe(self, previous_step, next_step) -> bool:
        if not isinstance(previous_step, dict) or not isinstance(next_step, dict):
            return False
        previous_kind = str(previous_step.get("display_kind") or "").strip()
        next_kind = str(next_step.get("display_kind") or "").strip()
        if previous_kind != "commentary" or next_kind != "commentary":
            return False
        previous_detail = self._normalize_execution_text_for_compare(self._execution_step_detail_text(previous_step))
        next_detail = self._normalize_execution_text_for_compare(self._execution_step_detail_text(next_step))
        if not previous_detail or not next_detail:
            return False
        return self._execution_texts_are_near_duplicate(previous_detail, next_detail)

    def _remote_execution_entry_payload(self, chat_id: str, entry: dict) -> dict:
        if not isinstance(entry, dict):
            entry = {}
        title = str(entry.get("list_text") or entry.get("step") or entry.get("text") or "").strip()
        detail = str(entry.get("detail_text") or entry.get("detail") or title).strip()
        kind = str(entry.get("display_kind") or entry.get("event_type") or "info").strip() or "info"
        event_id = str(entry.get("event_id") or "").strip() or f"evt-{uuid.uuid4().hex[:8]}"
        try:
            ts = float(entry.get("ts") or entry.get("created_at") or time.time())
        except (TypeError, ValueError):
            ts = time.time()
        return {
            "type": "execution_entry",
            "chat_id": str(chat_id or ""),
            "event_id": event_id,
            "kind": kind,
            "title": title,
            "detail": detail,
            "ts": ts,
        }

    def _append_execution_entry_to_chat(self, chat_id: str, entry: dict, *, save_state: bool = True) -> bool:
        if not isinstance(entry, dict):
            return False
        self._invalidate_remote_state_cache()
        target_chat = self._chat_state_for_execution_steps(chat_id)
        if not isinstance(target_chat, dict):
            return False
        if "turn_idx" not in entry and target_chat is getattr(self, "_current_chat_state", None):
            active_idx = int(getattr(self, "active_turn_idx", -1) or -1)
            if active_idx >= 0:
                entry = dict(entry)
                entry["turn_idx"] = active_idx
        steps = target_chat.get("execution_steps")
        if not isinstance(steps, list):
            steps = []
            target_chat["execution_steps"] = steps
        if steps and self._execution_entries_should_dedupe(steps[-1], entry):
            return False
        steps.append(copy.deepcopy(entry))
        resolved_chat_id = str(chat_id or target_chat.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        if resolved_chat_id:
            self._persist_execution_step_or_queue(resolved_chat_id, steps[-1])
        self._prune_cached_execution_steps_for_turn(target_chat, steps[-1])
        steps = target_chat.get("execution_steps")
        if not isinstance(steps, list):
            steps = []
            target_chat["execution_steps"] = steps
        if resolved_chat_id:
            self._broadcast_remote_event(self._remote_execution_entry_payload(resolved_chat_id, steps[-1]))
        self._append_visible_execution_entry(target_chat, len(steps) - 1, steps[-1])
        if save_state:
            self._defer_chat_state_save()
        return True

    def _persist_execution_step_or_queue(self, chat_id: str, step: dict) -> None:
        if not getattr(self, "_chat_store_enabled", False) or getattr(self, "chat_store", None) is None:
            return
        if not chat_id or not isinstance(step, dict):
            return
        if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) <= 0:
            self.chat_store.append_execution_step(chat_id, step)
            return
        self._queue_execution_step_persist(chat_id, step)

    def _queue_execution_step_persist(self, chat_id: str, step: dict) -> None:
        with self._execution_step_persist_lock:
            self._pending_execution_step_persists.append((str(chat_id or ""), copy.deepcopy(step)))
            if self._execution_step_persist_scheduled or self._execution_step_persist_worker_running:
                return
            self._execution_step_persist_scheduled = True
        if int(getattr(self, "_codex_ui_batch_depth", 0) or 0) > 0:
            return
        self._start_execution_step_persist_worker()

    def _start_execution_step_persist_worker(self) -> None:
        with self._execution_step_persist_lock:
            self._execution_step_persist_scheduled = False
            if self._execution_step_persist_worker_running or not self._pending_execution_step_persists:
                return
            self._execution_step_persist_worker_running = True
        threading.Thread(target=self._execution_step_persist_worker, daemon=True).start()

    def _execution_step_persist_worker(self) -> None:
        try:
            while True:
                with self._execution_step_persist_lock:
                    batch = list(self._pending_execution_step_persists)
                    self._pending_execution_step_persists.clear()
                    if not batch:
                        self._execution_step_persist_worker_running = False
                        return
                store = getattr(self, "chat_store", None)
                if store is None:
                    continue
                for chat_id, step in batch:
                    try:
                        store.append_execution_step(chat_id, step)
                    except Exception:
                        continue
        finally:
            with self._execution_step_persist_lock:
                if not self._pending_execution_step_persists:
                    self._execution_step_persist_worker_running = False

    def _flush_execution_step_persists_sync(self) -> None:
        with self._execution_step_persist_lock:
            batch = list(self._pending_execution_step_persists)
            self._pending_execution_step_persists.clear()
            self._execution_step_persist_scheduled = False
        store = getattr(self, "chat_store", None)
        if store is None:
            return
        for chat_id, step in batch:
            try:
                store.append_execution_step(chat_id, step)
            except Exception:
                continue

    def _prune_cached_execution_steps_for_turn(self, chat: dict, latest_step: dict) -> None:
        if not isinstance(chat, dict) or not isinstance(latest_step, dict):
            return
        turn_idx = latest_step.get("turn_idx")
        if turn_idx is None:
            return
        steps = chat.get("execution_steps")
        if not isinstance(steps, list):
            return
        store = getattr(self, "chat_store", None)
        limit = getattr(store, "max_execution_steps_per_turn", 500)
        try:
            limit = int(limit)
        except Exception:
            limit = 500
        if limit <= 0:
            return
        matching_indexes = [
            idx
            for idx, step in enumerate(steps)
            if isinstance(step, dict) and self._safe_int(step.get("turn_idx"), -1) == self._safe_int(turn_idx, -2)
        ]
        overflow = len(matching_indexes) - limit
        if overflow <= 0:
            return
        remove = set(matching_indexes[:overflow])
        chat["execution_steps"] = [step for idx, step in enumerate(steps) if idx not in remove]

    def _buffer_execution_delta(self, chat_id: str, event: CodexEvent) -> None:
        if not isinstance(event, CodexEvent):
            return
        key = (str(chat_id or ""), self._event_turn_id(event), str(getattr(event, "item_id", "") or "").strip())
        state = self._execution_delta_buffer.setdefault(key, {"parts": [], "event": event, "last_event_at": 0.0})
        state["parts"].append(str(getattr(event, "text", "") or getattr(event, "raw_text", "") or ""))
        state["event"] = event
        state["last_event_at"] = time.time()

    def _flush_execution_delta(self, chat_id: str, turn_id: str | None = None, item_id: str | None = None) -> bool:
        flushed = False
        normalized_chat_id = str(chat_id or "")
        normalized_turn_id = None if turn_id is None else str(turn_id or "").strip()
        normalized_item_id = None if item_id is None else str(item_id or "").strip()
        for key in list(self._execution_delta_buffer.keys()):
            buf_chat_id, buf_turn_id, buf_item_id = key
            if buf_chat_id != normalized_chat_id:
                continue
            if normalized_turn_id is not None and buf_turn_id != normalized_turn_id:
                continue
            if normalized_item_id is not None and buf_item_id != normalized_item_id:
                continue
            state = self._execution_delta_buffer.pop(key, None)
            if not isinstance(state, dict):
                continue
            text = "".join(str(part or "") for part in (state.get("parts") or [])).strip()
            base_event = state.get("event")
            if not text or not isinstance(base_event, CodexEvent):
                continue
            merged_event = CodexEvent(
                type="agent_message_delta",
                thread_id=self._event_thread_id(base_event),
                turn_id=self._event_turn_id(base_event),
                item_id=str(getattr(base_event, "item_id", "") or "").strip(),
                text=text,
                raw_text=text,
                phase=str(getattr(base_event, "phase", "") or "").strip(),
                status=str(getattr(base_event, "status", "") or "").strip(),
                subtype=str(getattr(base_event, "subtype", "") or "").strip() or "agentMessageDelta",
                display_kind="commentary",
            )
            entry = self._build_execution_entry(merged_event)
            if entry and self._append_execution_entry_to_chat(chat_id, entry, save_state=False):
                flushed = True
        return flushed

    def _flush_all_execution_deltas_for_chat(self, chat_id: str) -> bool:
        return self._flush_execution_delta(chat_id)

    def _refresh_visible_history_chat(self, chat_id: str) -> None:
        if self.view_mode != "history":
            return
        if str(self.view_history_id or "").strip() != str(chat_id or "").strip():
            return
        self._refresh_history(chat_id)
        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")

    def _execution_meta_tuple(self, step_idx: int, step) -> tuple:
        if isinstance(step, dict):
            detail_text = self._execution_step_detail_text(step)
            display_kind = str(step.get("display_kind") or "").strip()
            if display_kind == "error":
                detail_text = self._sanitize_execution_error_text(detail_text)
            list_text = str(step.get("list_text") or "").strip()
            if display_kind == "error":
                list_text = self._execution_list_text_from_detail(detail_text, display_kind)
            if not list_text:
                if display_kind == "command":
                    fallback_text = (
                        str(step.get("subtype") or "").strip()
                        or str(step.get("status") or "").strip()
                        or str(step.get("event_type") or "").strip()
                    )
                    list_text = self._execution_command_list_text(
                        str(step.get("event_type") or "").strip(),
                        str(step.get("title") or "").strip(),
                        str(step.get("command") or "").strip(),
                        step.get("exit_code"),
                        fallback_text,
                    )
                else:
                    list_text = self._execution_list_text_from_detail(detail_text, display_kind)
            return ("execution", step_idx, list_text, detail_text)
        text = str(step or "").strip()
        return ("execution", step_idx, text, text)

    def _execution_step_text(self, step) -> str:
        return self._execution_meta_tuple(-1, step)[2]

    @staticmethod
    def _strip_ansi_control_sequences(text: str) -> str:
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))

    @staticmethod
    def _is_noisy_execution_error_line(line: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(line or "").strip()).lower()
        if not normalized:
            return True
        if (
            " warn " in f" {normalized} "
            and "codex_core_" in normalized
            and "ignoring interface" in normalized
        ):
            return True
        return False

    @staticmethod
    def _sanitize_execution_error_text(detail_text: str) -> str:
        cleaned_text = ChatFrame._strip_ansi_control_sequences(detail_text)
        lines = [str(line or "").strip() for line in str(cleaned_text or "").replace("\r", "").split("\n")]
        kept = []
        noise_prefixes = (
            "Output:",
            "At line:",
            "+ ",
            "+ ~",
            "CategoryInfo:",
            "FullyQualifiedErrorId:",
        )
        noise_exact = {"错误：Output:", "Output:"}
        noise_sentence_prefixes = (
            "deprecation:",
            "warning:",
            "runtimewarning:",
        )
        for line in lines:
            if not line:
                continue
            normalized_line = line.removeprefix("错误：").strip()
            if ChatFrame._is_noisy_execution_error_line(normalized_line):
                continue
            if line in noise_exact or normalized_line in noise_exact:
                continue
            if any(line.startswith(prefix) for prefix in noise_prefixes):
                continue
            if any(normalized_line.startswith(prefix) for prefix in noise_prefixes):
                continue
            if any(normalized_line.lower().startswith(prefix) for prefix in noise_sentence_prefixes):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _should_show_execution_step(self, step) -> bool:
        if not isinstance(step, dict):
            return bool(str(step or "").strip())
        display_kind = str(step.get("display_kind") or "").strip()
        event_type = str(step.get("event_type") or "").strip()
        phase = str(step.get("phase") or "").strip()
        list_text = str(step.get("list_text") or "").strip()
        detail_text = self._execution_step_detail_text(step)
        if display_kind == "error":
            return False
        hidden_status_texts = {"开始处理本轮请求", "本轮处理结束", "active", "idle"}
        normalized_detail_text = self._normalize_execution_text_for_compare(detail_text)
        normalized_list_text = self._normalize_execution_text_for_compare(list_text)
        if normalized_detail_text in {
            "开始执行：阶段：commentary",
            "完成执行：阶段：commentary",
            "开始执行：阶段：final_answer",
            "完成执行：阶段：final_answer",
        }:
            return False
        if normalized_list_text in {
            "开始执行：阶段：commentary",
            "完成执行：阶段：commentary",
            "开始执行：阶段：final_answer",
            "完成执行：阶段：final_answer",
        }:
            return False

        if event_type in {"turn_started", "turn_completed"} and (detail_text in hidden_status_texts or list_text in hidden_status_texts):
            return False
        if event_type == "item_completed" and phase == "final_answer":
            return False
        if display_kind == "command":
            return False
        if display_kind == "status" and (detail_text in hidden_status_texts or list_text in hidden_status_texts):
            return False
        if list_text in {"active", "idle"} or detail_text in {"active", "idle"}:
            return False
        if display_kind in {"commentary", "plan", "error", "user_input"}:
            return bool(list_text or detail_text)
        if event_type in {"agent_message_delta", "plan_updated", "stderr", "server_request"}:
            return bool(list_text or detail_text)
        if display_kind:
            return False
        return bool(list_text or detail_text)

    def _rebuild_execution_list_from_state(self) -> None:
        self._execution_list_pending_turn_reset = False
        total_steps, steps = self._current_execution_steps_for_render()
        visible_items = []
        for idx, step in enumerate(steps):
            if not self._should_show_execution_step(step):
                continue
            meta = self._execution_meta_tuple(idx, step)
            row_text = str(meta[2] or "").strip()
            if not row_text:
                continue
            visible_items.append((row_text, meta))
        self.execution_total_content_rows = len(visible_items)
        limit = max(
            EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
            int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0),
        )
        self.execution_visible_row_limit = limit
        has_more = total_steps > len(steps) or len(visible_items) > limit
        rows: list[str] = []
        metas: list[tuple] = []
        if has_more:
            visible_items = visible_items[-limit:]
            rows.append("更多")
            metas.append(("more", -1, "更多", ""))
        if not visible_items:
            rows.append("暂无执行过程")
            metas.append(("info", -1, "", ""))
            changed = self._replace_listbox_items_if_changed(self.execution_list, rows, 0)
            self.execution_meta = metas
            if changed:
                self._request_listbox_repaint(self.execution_list)
            return
        for row_text, meta in visible_items:
            rows.append(row_text)
            metas.append(meta)
        selected_idx = self.execution_list.GetSelection()
        if selected_idx == wx.NOT_FOUND:
            selected_idx = 0
        elif rows:
            selected_idx = max(0, min(int(selected_idx), len(rows) - 1))
        else:
            selected_idx = None
        changed = self._replace_listbox_items_if_changed(self.execution_list, rows, selected_idx)
        self.execution_meta = metas
        if changed:
            self._request_listbox_repaint(self.execution_list)

    def _render_execution_list(self) -> None:
        if not hasattr(self, "execution_list"):
            return
        self._rebuild_execution_list_from_state()

    def _reset_current_turn_execution_view(self) -> None:
        self._reset_execution_visible_row_limit()
        if hasattr(self, "execution_list"):
            if self._detail_panel_mode() == "execution" and self.execution_list.GetCount() > 0:
                self._execution_list_pending_turn_reset = True
                return
            self.execution_list.Clear()
            self.execution_meta = []
            self.execution_list.Append("暂无执行过程")
            self.execution_meta.append(("info", -1, "", ""))
            try:
                self.execution_list.SetSelection(0)
            except Exception:
                pass
            self._request_listbox_repaint(self.execution_list)

    def _apply_detail_panel_mode(self, mode: str | None = None, refresh_execution: bool = False) -> str:
        previous_mode = self._detail_panel_mode()
        normalized = "execution" if str(mode or self._detail_panel_mode()).strip() == "execution" else "answers"
        if not isinstance(getattr(self, "_current_chat_state", None), dict):
            self._current_chat_state = {}
        if self.view_mode != "history":
            self._current_chat_state["detail_panel_mode"] = normalized
            if not isinstance(self._current_chat_state.get("execution_steps"), list):
                self._current_chat_state["execution_steps"] = []
        show_answers = normalized != "execution"
        show_execution = normalized == "execution"
        if mode is None and not refresh_execution and previous_mode == normalized:
            visible_matches = True
            if hasattr(self, "answer_list"):
                try:
                    visible_matches = visible_matches and bool(self.answer_list.IsShown()) == show_answers
                except Exception:
                    visible_matches = False
            if hasattr(self, "execution_list"):
                try:
                    visible_matches = visible_matches and bool(self.execution_list.IsShown()) == show_execution
                except Exception:
                    visible_matches = False
            label_matches = True
            if hasattr(self, "detail_title_label"):
                try:
                    label_matches = self.detail_title_label.GetLabel() == ("执行过程：" if normalized == "execution" else "回答：")
                except Exception:
                    label_matches = False
            if visible_matches and label_matches:
                return normalized
        if hasattr(self, "detail_title_label"):
            try:
                self.detail_title_label.SetLabel("执行过程：" if normalized == "execution" else "回答：")
            except Exception:
                pass
        answer_has_focus = False
        execution_has_focus = False
        try:
            answer_has_focus = bool(hasattr(self, "answer_list") and self.answer_list.HasFocus())
        except Exception:
            answer_has_focus = False
        try:
            execution_has_focus = bool(hasattr(self, "execution_list") and self.execution_list.HasFocus())
        except Exception:
            execution_has_focus = False
        if hasattr(self, "answer_list"):
            try:
                self.answer_list.Show(show_answers)
            except Exception:
                pass
        if hasattr(self, "execution_list"):
            try:
                self.execution_list.Show(show_execution)
            except Exception:
                pass
        if normalized == "execution" and answer_has_focus and hasattr(self, "execution_list"):
            try:
                self.execution_list.SetFocus()
            except Exception:
                pass
        elif normalized == "answers" and execution_has_focus and hasattr(self, "answer_list"):
            try:
                self.answer_list.SetFocus()
            except Exception:
                pass
        if normalized == "execution" and (refresh_execution or previous_mode != "execution"):
            self._flush_all_execution_deltas_for_chat(self._visible_execution_chat_id())
            self._render_execution_list()
        self._notes_rebuild_tab_order()
        try:
            self.Layout()
        except Exception:
            pass
        return normalized

    def _focus_latest_execution_item(self) -> bool:
        if not hasattr(self, "execution_list"):
            return False
        count = self.execution_list.GetCount()
        if count <= 0:
            return False
        try:
            self.execution_list.SetSelection(count - 1)
            if not self.execution_list.HasFocus():
                self.execution_list.SetFocus()
        except Exception:
            return False
        return True

    def _toggle_detail_panel_mode(self, *, focus_detail: bool = False) -> str:
        next_mode = "answers" if self._detail_panel_mode() == "execution" else "execution"
        self._apply_detail_panel_mode(next_mode, refresh_execution=True)
        if focus_detail:
            if next_mode == "execution":
                self._focus_latest_execution_item()
            elif hasattr(self, "answer_list"):
                self._focus_latest_answer()
        self._save_state()
        return next_mode

    def _workspace_dir_for_codex(self) -> str:
        return str(Path(__file__).resolve().parent)

    def _ensure_active_chat_id(self) -> str:
        if not self.active_chat_id:
            self.active_chat_id = str(self.current_chat_id or "").strip() or str(uuid.uuid4())
        return self.active_chat_id

    def _make_openclaw_session_id(self, chat_id: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_-]", "-", str(chat_id or "").strip())
        clean = clean.strip("-") or uuid.uuid4().hex
        return f"zgwd-{clean}"

    def _ensure_active_openclaw_session_id(self) -> str:
        if not self.active_openclaw_session_id:
            self.active_openclaw_session_id = self._make_openclaw_session_id(self._ensure_active_chat_id())
        return self.active_openclaw_session_id

    def _openclaw_session_id_for_active_chat(self) -> str:
        session_id = self._ensure_active_openclaw_session_id()
        if isinstance(self._current_chat_state, dict):
            self._current_chat_state["openclaw_session_key"] = self.active_openclaw_session_key
            self._current_chat_state["openclaw_session_id"] = session_id
        return session_id

    def _has_openclaw_turns(self, turns: list[dict] | None = None) -> bool:
        for turn in turns or self.active_session_turns:
            if is_openclaw_model(str(turn.get("model") or "")):
                return True
        return False

    def _is_openclaw_sync_target_active(self) -> bool:
        return bool(
            self.active_openclaw_session_id
            or self.active_openclaw_session_file
            or self._has_openclaw_turns()
        )

    def _has_archived_openclaw_sync_targets(self) -> bool:
        for chat in self.archived_chats:
            if not isinstance(chat, dict):
                continue
            if str(chat.get("openclaw_session_id") or chat.get("openclaw_session_file") or "").strip():
                return True
            turns = chat.get("turns")
            if isinstance(turns, list) and self._has_openclaw_turns(turns):
                return True
        return False

    def _reset_openclaw_sync_progress(self, clear_session_file: bool = False) -> None:
        self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = 0.0
        if clear_session_file:
            self.active_openclaw_session_file = ""

    def _seek_openclaw_sync_to_current_tail(self) -> None:
        session_file = str(self.active_openclaw_session_file or "").strip()
        if not session_file:
            sessions_json = resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json"
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is not None:
                self.active_openclaw_session_id = str(pointer.session_id or self.active_openclaw_session_id).strip()
                session_file = str(pointer.session_file or "").strip()
                self.active_openclaw_session_file = session_file
        offset = 0
        if session_file:
            try:
                offset = Path(session_file).stat().st_size
            except Exception:
                offset = 0
        self.active_openclaw_sync_offset = offset
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = time.time()

    def _resolve_active_openclaw_session_pointer(self, sessions_json: Path):
        session_id = str(self.active_openclaw_session_id or "").strip()
        if session_id:
            pointer = load_session_pointer_by_session_id(sessions_json, session_id)
            if pointer is not None:
                return pointer
        if not session_id:
            return load_session_pointer(sessions_json, self.active_openclaw_session_key or DEFAULT_OPENCLAW_SESSION_KEY)
        return None

    def _refresh_openclaw_sync_lifecycle(self, force_replay: bool = False) -> None:
        if not self._is_openclaw_sync_target_active() and not self._has_archived_openclaw_sync_targets():
            self._stop_openclaw_sync()
            return
        if force_replay:
            self._reset_openclaw_sync_progress(clear_session_file=True)
        self._start_openclaw_sync()

    def _start_openclaw_sync(self) -> None:
        thread = self._openclaw_sync_thread
        if thread and thread.is_alive():
            return
        self._openclaw_sync_stop.clear()
        self._openclaw_sync_thread = threading.Thread(target=self._openclaw_sync_loop, daemon=True)
        self._openclaw_sync_thread.start()

    def _stop_openclaw_sync(self) -> None:
        self._openclaw_sync_stop.set()
        thread = self._openclaw_sync_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._openclaw_sync_thread = None

    def _openclaw_sync_loop(self) -> None:
        while not self._openclaw_sync_stop.is_set():
            try:
                self._sync_openclaw_once()
            except Exception:
                pass
            if self._openclaw_sync_stop.wait(1.0):
                break

    def _sync_openclaw_once(self) -> None:
        sessions_json = resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json"
        if self._is_openclaw_sync_target_active():
            self._sync_active_openclaw_once(sessions_json)
        self._sync_archived_openclaw_targets_once(sessions_json)

    def _sync_active_openclaw_once(self, sessions_json: Path) -> None:
        pointer = None
        stored_session_file = str(self.active_openclaw_session_file or "").strip()
        session_file = stored_session_file
        session_id = str(self.active_openclaw_session_id or "").strip()
        updated_at = 0.0
        if session_file and (not Path(session_file).exists()):
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is not None:
                session_file = str(pointer.session_file or "").strip()
                session_id = str(pointer.session_id or session_id).strip()
                updated_at = float(pointer.updated_at or 0.0)
        if not session_file:
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is None:
                return
            session_file = str(pointer.session_file or "").strip()
            session_id = str(pointer.session_id or session_id).strip()
            updated_at = float(pointer.updated_at or 0.0)
        if not session_file:
            return

        with self._openclaw_sync_lock:
            previous_file = self.active_openclaw_session_file
            previous_session_id = self.active_openclaw_session_id
            previous_offset = int(self.active_openclaw_sync_offset or 0)
        file_changed = previous_file != session_file
        session_changed = bool(session_id and previous_session_id and (previous_session_id != session_id))
        needs_replay = file_changed or session_changed
        offset = 0 if needs_replay else previous_offset
        new_offset, events = read_session_events(session_file, offset=offset)
        if not events and not needs_replay and new_offset == previous_offset:
            return
        wx_call_after_if_alive(
            self._apply_openclaw_sync_batch,
                {
                    "session_id": session_id,
                    "session_file": session_file,
                    "offset": new_offset,
                    "updated_at": updated_at,
                    "previous_file": previous_file,
                    "file_changed": file_changed,
                    "session_changed": session_changed,
                },
                events,
        )

    def _sync_archived_openclaw_targets_once(self, sessions_json: Path) -> None:
        for chat in list(self.archived_chats):
            if not isinstance(chat, dict):
                continue
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            session_id = str(chat.get("openclaw_session_id") or "").strip()
            session_file = str(chat.get("openclaw_session_file") or "").strip()
            if not session_id and not session_file and not self._has_openclaw_turns(turns):
                continue
            if not session_id:
                chat_id = str(chat.get("source_chat_id") or chat.get("id") or "").strip()
                session_id = self._make_openclaw_session_id(chat_id)
                chat["openclaw_session_id"] = session_id
            updated_at = 0.0
            if session_file and not Path(session_file).exists():
                session_file = ""
            if not session_file:
                pointer = load_session_pointer_by_session_id(sessions_json, session_id)
                if pointer is None:
                    continue
                session_file = str(pointer.session_file or "").strip()
                session_id = str(pointer.session_id or session_id).strip()
                updated_at = float(pointer.updated_at or 0.0)
            if not session_file:
                continue
            try:
                previous_offset = max(int(chat.get("openclaw_sync_offset") or 0), 0)
            except Exception:
                previous_offset = 0
            previous_file = str(chat.get("openclaw_session_file") or "").strip()
            previous_session_id = str(chat.get("openclaw_session_id") or "").strip()
            file_changed = previous_file != session_file
            session_changed = bool(session_id and previous_session_id and previous_session_id != session_id)
            offset = 0 if (file_changed or session_changed) else previous_offset
            new_offset, events = read_session_events(session_file, offset=offset)
            if not events and not file_changed and not session_changed and new_offset == previous_offset:
                continue
            wx_call_after_if_alive(
                self._apply_openclaw_sync_batch,
                {
                    "chat_id": str(chat.get("id") or "").strip(),
                    "session_id": session_id,
                    "session_file": session_file,
                    "offset": new_offset,
                    "updated_at": updated_at,
                    "previous_file": previous_file,
                    "file_changed": file_changed,
                    "session_changed": session_changed,
                },
                events,
            )

    def _apply_openclaw_sync_batch(self, sync_state: dict, events: list[OpenClawSyncEvent]) -> None:
        changed = False
        had_input_events = bool(events)
        chat_id = str(sync_state.get("chat_id") or "").strip()
        active_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip(), ""}
        is_active_target = chat_id in active_ids
        target_chat = self._current_chat_state if is_active_target else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_turns = self.active_session_turns if is_active_target else target_chat.get("turns")
        if not isinstance(target_turns, list):
            target_turns = []
            target_chat["turns"] = target_turns
        events = self._filter_openclaw_initial_sync_events(sync_state, events, target_turns)
        events = self._filter_openclaw_events_for_local_turns(events, target_turns)
        file_changed = bool(sync_state.get("file_changed"))
        session_changed = bool(sync_state.get("session_changed"))
        current_offset = int(self.active_openclaw_sync_offset or 0) if is_active_target else int(target_chat.get("openclaw_sync_offset") or 0)
        next_offset = int(sync_state.get("offset") or current_offset or 0)
        if not events and not file_changed and not session_changed:
            if next_offset != current_offset:
                target_chat["openclaw_sync_offset"] = next_offset
                if is_active_target:
                    self.active_openclaw_sync_offset = next_offset
            return
        if not had_input_events and not file_changed and not session_changed and next_offset == current_offset:
            return
        had_prior_sync = bool(
            self.active_openclaw_last_synced_at or self.active_openclaw_session_file
        ) if is_active_target else bool(target_chat.get("openclaw_last_synced_at") or target_chat.get("openclaw_session_file"))
        assistant_changed = False
        if file_changed or session_changed:
            if is_active_target:
                self.active_openclaw_session_file = str(sync_state.get("session_file") or "").strip()
                self.active_openclaw_sync_offset = 0
                self.active_openclaw_last_event_id = ""
            target_chat["openclaw_session_file"] = str(sync_state.get("session_file") or "").strip()
            target_chat["openclaw_sync_offset"] = 0
            target_chat["openclaw_last_event_id"] = ""
        session_id = str(sync_state.get("session_id") or "").strip()
        if session_id:
            target_chat["openclaw_session_id"] = session_id
            if is_active_target:
                self.active_openclaw_session_id = session_id
        session_file = str(sync_state.get("session_file") or target_chat.get("openclaw_session_file") or "").strip()
        target_chat["openclaw_session_file"] = session_file
        if is_active_target:
            self.active_openclaw_session_file = session_file
        for event in events:
            result = self._apply_openclaw_sync_event(event, target_turns, chat_id if chat_id else self.active_chat_id, is_active_target)
            if result == "visible":
                changed = True
                if event.role == "assistant":
                    assistant_changed = True
            if result:
                self._mark_chat_turns_dirty(chat_id if chat_id else self.active_chat_id, 0)
        sync_offset = next_offset
        target_chat["openclaw_sync_offset"] = sync_offset
        target_chat["openclaw_last_synced_at"] = time.time()
        if is_active_target:
            self.active_openclaw_sync_offset = sync_offset
            self.active_openclaw_last_synced_at = target_chat["openclaw_last_synced_at"]
        if events:
            last_event_id = str(events[-1].event_id or target_chat.get("openclaw_last_event_id") or "")
            target_chat["openclaw_last_event_id"] = last_event_id
            if is_active_target:
                self.active_openclaw_last_event_id = last_event_id
        if changed:
            should_render = assistant_changed
            target_chat["updated_at"] = time.time()
            if is_active_target and self.view_mode == "active" and should_render:
                self._render_answer_list()
            self.SetStatusText("已同步 OpenClaw 主会话")
            if is_active_target and assistant_changed and had_prior_sync:
                self._play_finish_sound()
        self._save_state()

    def _filter_openclaw_initial_sync_events(
        self,
        sync_state: dict,
        events: list[OpenClawSyncEvent],
        target_turns: list[dict],
    ) -> list[OpenClawSyncEvent]:
        if not events or not bool(sync_state.get("file_changed")):
            return events
        previous_file = str(sync_state.get("previous_file") or "").strip()
        if previous_file:
            return events
        local_created_times = []
        for turn in target_turns:
            if not isinstance(turn, dict):
                continue
            if not is_openclaw_model(str(turn.get("model") or "")):
                continue
            if str(turn.get("origin") or turn.get("question_origin") or "").strip() == "openclaw-sync":
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at > 0:
                local_created_times.append(created_at)
        if not local_created_times:
            return events
        cutoff = min(local_created_times) - 30.0
        return [event for event in events if float(event.timestamp or 0.0) >= cutoff]

    def _filter_openclaw_events_for_local_turns(
        self,
        events: list[OpenClawSyncEvent],
        target_turns: list[dict],
    ) -> list[OpenClawSyncEvent]:
        if not events:
            return events
        local_turn_indexes = [
            idx
            for idx, turn in enumerate(target_turns)
            if self._is_local_openclaw_turn(turn)
        ]
        if not local_turn_indexes:
            return events
        has_user_events = any(event.role == "user" for event in events)
        filtered: list[OpenClawSyncEvent] = []
        awaiting_answer_idx: int | None = None
        for event in events:
            if event.role == "user":
                match_idx = self._matching_local_openclaw_user_turn(event, target_turns, local_turn_indexes)
                if match_idx is None:
                    awaiting_answer_idx = None
                    continue
                filtered.append(event)
                awaiting_answer_idx = match_idx
                continue
            if event.role != "assistant":
                continue
            metadata_idx = self._matching_local_openclaw_assistant_metadata_turn(event, target_turns, local_turn_indexes)
            if metadata_idx is not None:
                filtered.append(event)
                continue
            if awaiting_answer_idx is not None and self._can_openclaw_turn_accept_assistant(target_turns[awaiting_answer_idx], event):
                filtered.append(event)
                awaiting_answer_idx = None
                continue
            if not has_user_events:
                match_idx = self._pending_local_openclaw_answer_turn(event, target_turns, local_turn_indexes)
                if match_idx is not None:
                    filtered.append(event)
        return filtered

    def _is_local_openclaw_turn(self, turn: dict) -> bool:
        if not isinstance(turn, dict):
            return False
        if not is_openclaw_model(str(turn.get("model") or "")):
            return False
        if not str(turn.get("question") or "").strip():
            return False
        origin = str(turn.get("origin") or turn.get("question_origin") or "").strip()
        return origin != "openclaw-sync"

    def _matching_local_openclaw_user_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        normalized = self._normalized_turn_text(event.text)
        event_ts = float(event.timestamp or time.time())
        for idx in local_turn_indexes:
            turn = target_turns[idx]
            if self._normalized_turn_text(str(turn.get("question") or "")) != normalized:
                continue
            existing_id = str(turn.get("question_external_event_id") or "").strip()
            if existing_id and existing_id != str(event.event_id or "").strip():
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and abs(created_at - event_ts) > 120:
                continue
            return idx
        return None

    def _matching_local_openclaw_assistant_metadata_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        normalized = self._normalized_turn_text(event.text)
        event_ts = float(event.timestamp or time.time())
        for idx in reversed(local_turn_indexes):
            turn = target_turns[idx]
            existing_id = str(turn.get("answer_external_event_id") or "").strip()
            if existing_id and existing_id != str(event.event_id or "").strip():
                continue
            answer = self._normalized_turn_text(str(turn.get("answer_md") or ""))
            if answer != normalized:
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and abs(created_at - event_ts) > 120:
                continue
            return idx
        return None

    def _pending_local_openclaw_answer_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        event_ts = float(event.timestamp or time.time())
        for idx in reversed(local_turn_indexes):
            turn = target_turns[idx]
            if not self._can_openclaw_turn_accept_assistant(turn, event):
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and event_ts < created_at - 30:
                continue
            return idx
        return None

    def _can_openclaw_turn_accept_assistant(self, turn: dict, event: OpenClawSyncEvent) -> bool:
        if not isinstance(turn, dict):
            return False
        existing_id = str(turn.get("answer_external_event_id") or "").strip()
        if existing_id and existing_id != str(event.event_id or "").strip():
            return False
        answer_md = str(turn.get("answer_md") or "")
        return answer_md in {"", REQUESTING_TEXT} or ((not existing_id) and answer_md.startswith("OpenClaw "))

    def _apply_openclaw_sync_event(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict] | None = None,
        chat_id: str = "",
        is_active_target: bool = True,
    ) -> str:
        turns = target_turns if isinstance(target_turns, list) else self.active_session_turns
        event_id = str(event.event_id or "").strip()
        if event_id and self._has_openclaw_event_id(event_id, turns):
            return ""
        text = remove_emojis(normalize_openclaw_text(event.text))
        if not text:
            return ""
        if event.role == "user":
            merged = self._merge_openclaw_user_event(text, event, turns)
            if merged:
                return merged
            turns.append(
                {
                    "question": text,
                    "answer_md": "",
                    "model": "openclaw/main",
                    "created_at": float(event.timestamp or time.time()),
                    "origin": "openclaw-sync",
                    "external_event_id": event_id,
                    "external_role": "user",
                    "external_timestamp": float(event.timestamp or time.time()),
                    "question_origin": "openclaw-sync",
                    "question_external_event_id": event_id,
                    "question_external_timestamp": float(event.timestamp or time.time()),
                }
            )
            self._apply_nonrecoverable_turn_metadata(turns[-1], "openclaw/main", text)
            if is_active_target:
                self.active_turn_idx = len(turns) - 1
            if len([turn for turn in turns if str((turn or {}).get("question") or "").strip()]) == 1:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id or self.current_chat_id, text)
            return "visible"

        merged = self._merge_openclaw_assistant_event(text, event, turns, is_active_target)
        if merged:
            return merged
        turns.append(
            {
                "question": "",
                "answer_md": text,
                "model": "openclaw/main",
                "created_at": float(event.timestamp or time.time()),
                "origin": "openclaw-sync",
                "external_event_id": event_id,
                "external_role": "assistant",
                "external_timestamp": float(event.timestamp or time.time()),
                "answer_origin": "openclaw-sync",
                "answer_external_event_id": event_id,
                "answer_external_timestamp": float(event.timestamp or time.time()),
            }
        )
        self._apply_nonrecoverable_turn_metadata(turns[-1], "openclaw/main", "")
        if is_active_target:
            self.active_turn_idx = len(turns) - 1
        return "visible"

    def _has_openclaw_event_id(self, event_id: str, turns: list[dict] | None = None) -> bool:
        eid = str(event_id or "").strip()
        if not eid:
            return False
        for turn in turns if isinstance(turns, list) else self.active_session_turns:
            if eid in {
                str(turn.get("external_event_id") or "").strip(),
                str(turn.get("question_external_event_id") or "").strip(),
                str(turn.get("answer_external_event_id") or "").strip(),
            }:
                return True
        return False

    def _normalized_turn_text(self, text: str) -> str:
        return normalize_openclaw_text(text)

    def _apply_nonrecoverable_turn_metadata(self, turn: dict, model: str, question: str) -> None:
        now = time.time()
        turn["request_status"] = "done"
        turn["request_model"] = str(model or "")
        turn["request_question"] = str(question or "")
        turn["request_started_at"] = now
        turn["request_last_attempt_at"] = now
        turn["request_attempt_count"] = 0
        turn["request_recoverable"] = False
        turn["request_recovery_mode"] = "retry"
        turn["request_resume_token"] = {}
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False

    def _merge_openclaw_user_event(self, text: str, event: OpenClawSyncEvent, turns: list[dict] | None = None) -> str:
        target_turns = turns if isinstance(turns, list) else self.active_session_turns
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or time.time())
        for idx in range(len(target_turns) - 1, -1, -1):
            turn = target_turns[idx]
            question = self._normalized_turn_text(str(turn.get("question") or ""))
            if not question:
                continue
            if question != normalized:
                continue
            existing_id = str(turn.get("question_external_event_id") or "").strip()
            created_at = float(turn.get("created_at") or 0.0)
            if existing_id == str(event.event_id or "").strip():
                return ""
            if existing_id:
                continue
            if abs(created_at - event_ts) > 30:
                continue
            turn["question_external_event_id"] = str(event.event_id or "").strip()
            turn["question_external_timestamp"] = event_ts
            turn["external_event_id"] = str(event.event_id or "").strip()
            turn["external_role"] = "user"
            turn["external_timestamp"] = event_ts
            if "question_origin" not in turn:
                turn["question_origin"] = str(turn.get("origin") or "local")
            if "request_status" not in turn:
                self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", str(turn.get("question") or text))
            return "metadata"
        return ""

    def _merge_openclaw_assistant_event(
        self,
        text: str,
        event: OpenClawSyncEvent,
        turns: list[dict] | None = None,
        is_active_target: bool = True,
    ) -> str:
        target_turns = turns if isinstance(turns, list) else self.active_session_turns
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or time.time())
        event_id = str(event.event_id or "").strip()
        for idx in range(len(target_turns) - 1, -1, -1):
            turn = target_turns[idx]
            answer_md = str(turn.get("answer_md") or "")
            answer = self._normalized_turn_text("" if answer_md == REQUESTING_TEXT else answer_md)
            existing_id = str(turn.get("answer_external_event_id") or "").strip()
            created_at = float(turn.get("created_at") or 0.0)
            if existing_id == event_id:
                return ""
            if answer_md in {"", REQUESTING_TEXT} or ((not existing_id) and answer_md.startswith("OpenClaw ")):
                turn["answer_md"] = text
                turn["answer_origin"] = "openclaw-sync"
                turn["answer_external_event_id"] = event_id
                turn["answer_external_timestamp"] = event_ts
                turn["external_event_id"] = event_id
                turn["external_role"] = "assistant"
                turn["external_timestamp"] = event_ts
                if not turn.get("created_at"):
                    turn["created_at"] = event_ts
                if "request_status" not in turn:
                    self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", "")
                if is_active_target:
                    self.active_turn_idx = idx
                return "visible"
            if answer == normalized and (not existing_id) and abs(created_at - event_ts) <= 60:
                turn["answer_external_event_id"] = event_id
                turn["answer_external_timestamp"] = event_ts
                turn["external_event_id"] = event_id
                turn["external_role"] = "assistant"
                turn["external_timestamp"] = event_ts
                if "request_status" not in turn:
                    self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", str(turn.get("question") or ""))
                if is_active_target:
                    self.active_turn_idx = idx
                return "metadata"
        return ""

    def _send_openclaw_hidden_message(self, text: str) -> None:
        message = str(text or "").strip()
        if not message:
            return
        model = self._resolve_current_model()
        if not is_openclaw_model(model):
            return
        self.selected_model = model
        self.model_combo.SetValue(model)
        self._ensure_active_chat_id()
        self._ensure_active_openclaw_session_id()
        if not self.active_session_started_at:
            self.active_session_started_at = time.time()
        self.is_running = True
        self.new_chat_button.Disable()
        self._set_input_hint_idle()
        self._save_state()
        self._refresh_openclaw_sync_lifecycle()
        t = threading.Thread(target=self._worker, args=("", -1, message, model), daemon=True)
        t.start()

    def _answer_markdown_for_output(self, answer_md: str, model: str = "") -> str:
        text = str(answer_md or "")
        if not text:
            return ""
        if self.codex_answer_english_filter_enabled and is_cli_filtered_model(model):
            text = re.sub(r"\[[^\]]+\]\([^)]+\)", "[文件路径]", text)
            text = re.sub(r"`[^`]*(?:[A-Za-z]:[\\/]|/)[^`]*`", "[文件路径]", text)
            text = re.sub(r"`[^`]*\.(?:py|md|txt|json|yaml|yml)[^`]*`", "[文件路径]", text)
            text = re.sub(r"(?<![\w/.-])(?:[A-Za-z]:[\\/][^\s\]\)]+|/[^\s\]\)]+)", "[文件路径]", text)
            text = re.sub(r"pytest\s+tests/[^\n]+", "pytest [文件路径]", text)
            text = re.sub(r"\btest_[A-Za-z0-9_]+\b", "[测试项]", text)
        return text

    def _turn_answer_markdown(self, turn: dict) -> tuple[str, str]:
        answer_md = str((turn or {}).get("answer_md") or "")
        model = str((turn or {}).get("model") or "")
        if answer_md == REQUESTING_TEXT:
            return answer_md, REQUESTING_TEXT
        if not answer_md.strip():
            status = str((turn or {}).get("request_status") or "").strip()
            if status == "failed":
                err = str((turn or {}).get("request_error") or "").strip()
                return "", err or "上次未完成回答恢复失败，可手动继续"
            return "", ""
        return answer_md, remove_emojis(md_to_plain(self._answer_markdown_for_output(answer_md, model)))

    def _build_answer_detail_html(self, answer_md: str, model: str = "") -> str:
        filtered = self._answer_markdown_for_output(answer_md, model)
        a_html = markdown.markdown(remove_emojis(filtered), extensions=["extra", "fenced_code", "tables", "sane_lists"])
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>回答详情</title>"
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:12px;line-height:1.6;}"
            "h2{margin:8px 0;}"
            "pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto;}"
            "</style></head><body>"
            "<h2>回答详情</h2>"
            f"{a_html}"
            "</body></html>"
        )

    def _mark_turn_request_pending(self, turn: dict, model: str, question: str) -> None:
        now = time.time()
        turn["request_status"] = "pending"
        turn["request_model"] = str(model or "")
        turn["request_question"] = str(question or "")
        turn["request_started_at"] = now
        turn["request_last_attempt_at"] = now
        turn["request_attempt_count"] = 1
        turn["request_recoverable"] = True
        turn["request_recovery_mode"] = "resume" if (is_codex_model(model) or is_claudecode_model(model)) else "retry"
        turn["request_resume_token"] = self._request_resume_token_for_model(model)
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False

    def _request_resume_token_for_model(self, model: str) -> dict:
        if is_codex_model(model):
            return {
                "thread_id": str(self.active_codex_thread_id or "").strip(),
                "turn_id": str(self.active_codex_turn_id or "").strip(),
            }
        if is_claudecode_model(model):
            return {"session_id": str(self.active_claudecode_session_id or "").strip()}
        return {}

    def _append_codex_local_turn(self, question: str) -> int:
        turn = {
            "question": str(question or ""),
            "answer_md": REQUESTING_TEXT,
            "model": DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
        self._mark_turn_request_pending(turn, DEFAULT_CODEX_MODEL, str(question or ""))
        return self.active_turn_idx

    def _merge_codex_final_answer(self, answer: str) -> bool:
        turn = {
            "question": "",
            "answer_md": str(answer or ""),
            "model": DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
        }
        self._apply_nonrecoverable_turn_metadata(turn, DEFAULT_CODEX_MODEL, "")
        turn["request_recovery_mode"] = "resume"
        turn["request_resume_token"] = {
            "thread_id": str(self.active_codex_thread_id or "").strip(),
            "turn_id": str(self.active_codex_turn_id or "").strip(),
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
        return True

    @staticmethod
    def _is_codex_subagent_result_answer(turn: dict) -> bool:
        return isinstance(turn, dict) and str(turn.get("answer_origin") or "").strip() == "codex-subagent-result"

    def _apply_codex_subagent_result_to_turn(self, turn: dict, text: str) -> bool:
        if not isinstance(turn, dict):
            return False
        result = str(text or "").strip()
        if not result:
            return False
        current = str(turn.get("answer_md") or "")
        if not current.strip() or current == REQUESTING_TEXT:
            turn["answer_md"] = result
        elif self._is_codex_subagent_result_answer(turn):
            if result in current:
                return False
            turn["answer_md"] = f"{current.rstrip()}\n\n{result}"
        else:
            return False
        turn["answer_origin"] = "codex-subagent-result"
        return True

    def _apply_codex_final_answer_to_turn(self, turn: dict, text: str) -> bool:
        if not isinstance(turn, dict):
            return False
        answer = str(text or "")
        current = str(turn.get("answer_md") or "")
        if not answer.strip() and self._is_codex_subagent_result_answer(turn) and current.strip():
            return False
        if current.strip() and current != REQUESTING_TEXT and not self._is_codex_subagent_result_answer(turn):
            return False
        turn["answer_md"] = answer
        if self._is_codex_subagent_result_answer(turn):
            turn.pop("answer_origin", None)
        return True

    @staticmethod
    def _codex_error_text(exc: Exception | str) -> str:
        return str(exc or "").strip().lower()

    def _is_codex_thread_missing_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "thread not found" in text or "unknown thread" in text

    def _is_codex_rollout_missing_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "no rollout found" in text

    def _is_codex_no_active_turn_error(self, exc: Exception | str) -> bool:
        text = self._codex_error_text(exc)
        return "no active turn to steer" in text

    def _build_codex_rollout_recovery_prompt(self, history_turns: list[dict], question: str) -> str:
        clean_question = str(question or "").strip()
        transcript_parts = []
        for turn in history_turns or []:
            if not isinstance(turn, dict):
                continue
            prior_question = str(turn.get("question") or "").strip()
            prior_answer = str(turn.get("answer_md") or "").strip()
            if prior_answer == REQUESTING_TEXT:
                prior_answer = ""
            if prior_question:
                transcript_parts.append(f"用户：{prior_question}")
            if prior_answer:
                transcript_parts.append(f"Codex：{prior_answer}")
        if not transcript_parts:
            return clean_question
        transcript = "\n".join(transcript_parts)
        return (
            "下面是当前聊天在本地保存的历史记录，请把它当作本次会话上下文继续：\n"
            f"{transcript}\n\n"
            "请基于以上上下文继续回答下面这个新问题：\n"
            f"{clean_question}"
        )

    def _codex_should_steer_turn(self, target_chat: dict, is_current_target: bool) -> bool:
        if not is_current_target:
            return False
        if not bool(target_chat.get("codex_turn_active")) and not self.active_codex_turn_active:
            return False
        pending_prompt = str(
            (target_chat.get("codex_pending_prompt") if isinstance(target_chat, dict) else "") or
            (self.active_codex_pending_prompt if is_current_target else "")
        ).strip()
        thread_flags = target_chat.get("codex_thread_flags") if isinstance(target_chat, dict) else []
        if is_current_target and not pending_prompt:
            thread_flags = self.active_codex_thread_flags
        return bool(pending_prompt or "waitingOnUserInput" in (thread_flags or []))

    def _mark_turn_request_done(self, turn: dict) -> None:
        turn["request_status"] = "done"
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False

    def _mark_turn_request_failed(self, turn: dict, error: str) -> None:
        turn["request_status"] = "failed"
        turn["request_error"] = str(error or "")
        turn["request_recovered_after_restart"] = False

    def _start_codex_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, model: str) -> None:
        def _worker() -> None:
            self._run_codex_turn_worker(chat_id, turn_idx, question, model, from_recovery=False)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_codex_local_command_worker_for_turn(self, chat_id: str, turn_idx: int, command: str, args: str, model: str) -> None:
        threading.Thread(
            target=self._run_codex_local_command_worker,
            args=(chat_id, turn_idx, command, args, model),
            daemon=True,
        ).start()

    def _run_codex_local_command_worker(self, chat_id: str, turn_idx: int, command: str, args: str, model: str) -> None:
        try:
            chat_id = str(chat_id or "").strip()
            current_ids = {
                str(self.active_chat_id or "").strip(),
                str(self.current_chat_id or "").strip(),
                str((self._current_chat_state or {}).get("id") or "").strip() if isinstance(getattr(self, "_current_chat_state", None), dict) else "",
                "",
            }
            is_current_target = chat_id in current_ids
            target_chat = self._current_chat_state if is_current_target else self._find_archived_chat(chat_id)
            if not isinstance(target_chat, dict):
                target_chat = self._current_chat_state if is_current_target else {}
            normalized_command = str(command or "").strip().lower()
            client = None
            if normalized_command in {"status", "compact", "stop"}:
                if is_current_target:
                    client = self._ensure_codex_client(model) if model != DEFAULT_CODEX_MODEL else self._ensure_codex_client()
                else:
                    client = self._get_or_create_codex_client(chat_id, model) if model != DEFAULT_CODEX_MODEL else self._get_or_create_codex_client(chat_id)
            if normalized_command == "status":
                answer = self._build_codex_status_markdown(client, target_chat, model)
            elif normalized_command == "help":
                answer = self._build_codex_help_markdown()
            elif normalized_command == "compact":
                answer = self._handle_codex_compact_command(client, target_chat)
            elif normalized_command == "model":
                answer = self._handle_codex_model_command(str(args or ""), target_chat)
            elif normalized_command == "new":
                answer = "## Codex 新聊天\n\n已请求开始新聊天。"
                self._call_after_if_alive(self._on_new_chat_clicked, None)
            elif normalized_command == "clear":
                answer = self._handle_codex_clear_command(target_chat)
            elif normalized_command == "stop":
                answer = self._handle_codex_stop_command(client, target_chat)
            else:
                answer = self._build_codex_unsupported_command_markdown(normalized_command or command, args)
            self._call_after_if_alive(self._on_done, turn_idx, answer, "", model, "", chat_id)
        except Exception as exc:
            self._call_after_if_alive(self._on_done, turn_idx, "", str(exc), model, "", chat_id)

    def _handle_codex_compact_command(self, client, chat: dict) -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        if not thread_id:
            return "## Codex 压缩\n\n当前聊天还没有 Codex 线程，无法执行 `/compact`。"
        if client is None or not hasattr(client, "compact_thread"):
            return "## Codex 压缩\n\n当前 Codex 客户端不支持 `thread/compact/start`。"
        client.compact_thread(thread_id)
        return f"## Codex 压缩\n\n已开始压缩当前 Codex 线程：`{thread_id}`。"

    def _handle_codex_model_command(self, args: str, chat: dict) -> str:
        requested = str(args or "").strip()
        if not requested:
            model = str((chat or {}).get("model") or self.selected_model or DEFAULT_CODEX_MODEL)
            return f"## Codex 模型\n\n当前模型：`{model_display_name(model) or model}`。"
        model_id = model_id_from_display_name(requested)
        if not is_codex_model(model_id):
            return f"## Codex 模型\n\n`{requested}` 不是本程序可用的 Codex 模型。"
        self.selected_model = model_id
        if isinstance(chat, dict):
            chat["model"] = model_id
        self._call_after_if_alive(self.model_combo.SetValue, model_display_name(model_id))
        self._save_state()
        return f"## Codex 模型\n\n已切换到：`{model_display_name(model_id) or model_id}`。"

    def _handle_codex_clear_command(self, chat: dict) -> str:
        if isinstance(chat, dict):
            chat["codex_thread_id"] = ""
            chat["codex_turn_id"] = ""
            chat["codex_turn_active"] = False
            chat["codex_pending_prompt"] = ""
            chat["codex_pending_request"] = None
            chat["codex_request_queue"] = []
            chat["codex_thread_flags"] = []
        if chat is self._current_chat_state:
            self.active_codex_thread_id = ""
            self.active_codex_turn_id = ""
            self.active_codex_turn_active = False
            self.active_codex_pending_prompt = ""
            self.active_codex_pending_request = None
            self.active_codex_thread_flags = []
        self._save_state()
        return "## Codex 清理\n\n已清除当前聊天关联的 Codex 线程状态。聊天记录不会被删除。"

    def _handle_codex_stop_command(self, client, chat: dict) -> str:
        thread_id = self._codex_thread_id_for_chat(chat)
        turn_id = self._codex_turn_id_for_chat(chat)
        if not thread_id or not turn_id:
            return "## Codex 中断\n\n当前没有可中断的 Codex turn。"
        if client is None or not hasattr(client, "interrupt_turn"):
            return "## Codex 中断\n\n当前 Codex 客户端不支持 `turn/interrupt`。"
        client.interrupt_turn(thread_id, turn_id)
        return f"## Codex 中断\n\n已请求中断 turn：`{turn_id}`。"

    def _ensure_codex_thread_resumed(self, client, thread_id: str) -> None:
        thread_value = str(thread_id or "").strip()
        if not thread_value or not hasattr(client, "resume_thread"):
            return
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            resumed = set()
            setattr(client, "_zgwd_resumed_thread_ids", resumed)
        if thread_value in resumed:
            return
        client.resume_thread(
            thread_value,
            approval_policy="never",
            sandbox="danger-full-access",
            personality="pragmatic",
            cwd=self._workspace_dir_for_codex(),
        )
        resumed.add(thread_value)

    @staticmethod
    def _remember_codex_thread_resumed(client, thread_id: str) -> None:
        thread_value = str(thread_id or "").strip()
        if not thread_value:
            return
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            resumed = set()
            setattr(client, "_zgwd_resumed_thread_ids", resumed)
        resumed.add(thread_value)

    @staticmethod
    def _forget_codex_thread_resume(client, thread_id: str) -> None:
        resumed = getattr(client, "_zgwd_resumed_thread_ids", None)
        if not isinstance(resumed, set):
            return
        resumed.discard(str(thread_id or "").strip())

    def _run_codex_turn_worker(self, chat_id: str, turn_idx: int, question: str, model: str, from_recovery: bool = False) -> None:
        is_current_target = chat_id in {self.active_chat_id, self.current_chat_id, ""}
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            if not is_current_target:
                return
            target_chat = self._current_chat_state
        thread_id = str(target_chat.get("codex_thread_id") or (self.active_codex_thread_id if is_current_target else "") or "").strip()
        turn_id = str(target_chat.get("codex_turn_id") or (self.active_codex_turn_id if is_current_target else "") or "").strip()
        recovery_context = bool(getattr(self, "_codex_recovery_context", False))
        use_shared_client = (not from_recovery) and (not recovery_context) and chat_id in {self.active_chat_id, self.current_chat_id, ""}
        if use_shared_client:
            client = self._ensure_codex_client(model) if model != DEFAULT_CODEX_MODEL else self._ensure_codex_client()
        else:
            client_chat_id = chat_id or self.active_chat_id or self.current_chat_id or ""
            client = self._get_or_create_codex_client(client_chat_id, model) if model != DEFAULT_CODEX_MODEL else self._get_or_create_codex_client(client_chat_id)

        def _sync_codex_thread_state(new_thread_id: str = "", new_turn_id: str = "", active: bool | None = None) -> None:
            if not isinstance(target_chat, dict):
                return
            if new_thread_id is not None:
                target_chat["codex_thread_id"] = str(new_thread_id or "").strip()
                if is_current_target:
                    self.active_codex_thread_id = str(new_thread_id or "").strip()
            if new_turn_id is not None:
                target_chat["codex_turn_id"] = str(new_turn_id or "").strip()
                if is_current_target:
                    self.active_codex_turn_id = str(new_turn_id or "").strip()
            if active is not None:
                target_chat["codex_turn_active"] = bool(active)
                if is_current_target:
                    self.active_codex_turn_active = bool(active)

        def _clear_stale_codex_thread_state() -> None:
            _sync_codex_thread_state("", "", active=False)
            target_chat["codex_pending_prompt"] = ""
            target_chat["codex_pending_request"] = None
            target_chat["codex_thread_flags"] = []
            if is_current_target:
                self.active_codex_pending_prompt = ""
                self.active_codex_pending_request = None
                self.active_codex_thread_flags = []

        def _start_new_thread() -> str:
            thread_resp = client.start_thread(
                cwd=self._workspace_dir_for_codex(),
                approval_policy="never",
                sandbox="danger-full-access",
                personality="pragmatic",
            )
            new_thread_id = str((thread_resp.get("thread") or {}).get("id") or "").strip()
            if not new_thread_id:
                raise RuntimeError("Codex app-server did not return a thread id.")
            _sync_codex_thread_state(new_thread_id, "", active=False)
            self._remember_codex_thread_resumed(client, new_thread_id)
            return new_thread_id

        def _start_turn_with_items(thread_value: str, items: list[dict]) -> dict:
            if hasattr(client, "start_turn_items"):
                return client.start_turn_items(thread_value, items)
            text = str((items or [{}])[0].get("text") or "") if items else ""
            return client.start_turn(thread_value, text)

        def _steer_turn_with_items(thread_value: str, expected_turn_id: str, items: list[dict]) -> dict:
            if hasattr(client, "steer_turn_items"):
                return client.steer_turn_items(thread_value, expected_turn_id, items)
            text = str((items or [{}])[0].get("text") or "") if items else ""
            return client.steer_turn(thread_value, expected_turn_id, text)

        def _send_turn(thread_value: str, steer: bool, items: list[dict]) -> dict:
            if steer:
                if not turn_id:
                    raise RuntimeError("Codex app-server cannot steer without an active turn id.")
                return _steer_turn_with_items(thread_value, turn_id, items)
            return _start_turn_with_items(thread_value, items)

        try:
            target_turns = self.active_session_turns if is_current_target else (target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else [])
            if not isinstance(target_turns, list) or turn_idx < 0 or turn_idx >= len(target_turns):
                if not is_current_target:
                    return
                target_turns = self.active_session_turns
            turn_attachments = []
            if 0 <= turn_idx < len(target_turns):
                maybe_attachments = target_turns[turn_idx].get("attachments") if isinstance(target_turns[turn_idx], dict) else []
                if isinstance(maybe_attachments, list):
                    turn_attachments = [item for item in maybe_attachments if str((item or {}).get("status") or "") == "success"]
            send_question = question
            input_items = self._build_codex_input_items(send_question, turn_attachments)
            if not thread_id:
                thread_id = _start_new_thread()
            else:
                try:
                    self._ensure_codex_thread_resumed(client, thread_id)
                except Exception as exc:
                    if self._is_codex_thread_missing_error(exc) or self._is_codex_rollout_missing_error(exc):
                        self._forget_codex_thread_resume(client, thread_id)
                        _clear_stale_codex_thread_state()
                        history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
                        send_question = self._build_codex_rollout_recovery_prompt(history_turns, question)
                        input_items = self._build_codex_input_items(send_question, turn_attachments)
                        thread_id = _start_new_thread()
                    else:
                        raise
            should_steer = self._codex_should_steer_turn(target_chat, is_current_target) and bool(turn_id)
            try:
                if should_steer:
                    turn_resp = _send_turn(thread_id, should_steer, input_items)
                else:
                    turn_resp = _start_turn_with_items(thread_id, input_items)
            except Exception as exc:
                if should_steer and self._is_codex_no_active_turn_error(exc):
                    should_steer = False
                    turn_resp = _start_turn_with_items(thread_id, input_items)
                elif self._is_codex_thread_missing_error(exc):
                    self._forget_codex_thread_resume(client, thread_id)
                    _clear_stale_codex_thread_state()
                    history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
                    send_question = self._build_codex_rollout_recovery_prompt(history_turns, question)
                    input_items = self._build_codex_input_items(send_question, turn_attachments)
                    thread_id = _start_new_thread()
                    should_steer = False
                    turn_resp = _start_turn_with_items(thread_id, input_items)
                else:
                    raise
            new_turn_id = str((turn_resp.get("turn") or turn_resp.get("turnId") or {}).get("id") if isinstance(turn_resp.get("turn"), dict) else (turn_resp.get("turnId") or "")).strip()
            if new_turn_id:
                _sync_codex_thread_state(thread_id, new_turn_id, active=True)
            else:
                _sync_codex_thread_state(thread_id, "", active=True)
            if is_current_target:
                self.active_codex_turn_active = True
                self.active_codex_pending_prompt = ""
                self.active_codex_pending_request = None
            turn = target_turns[turn_idx]
            turn["codex_thread_id"] = thread_id
            turn["codex_turn_id"] = new_turn_id
            turn["request_recovery_mode"] = "resume"
            turn["request_resume_token"] = {"thread_id": thread_id, "turn_id": new_turn_id}
            if (not from_recovery) and (not recovery_context):
                turn["request_status"] = "pending"
            self._save_state()
        except Exception as exc:
            self._call_after_if_alive(self._on_done, turn_idx, "", str(exc), model, "", chat_id)

    def _start_claudecode_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, session_id: str) -> None:
        def _worker() -> None:
            try:
                client = ClaudeCodeClient(full_auto=True, cli_manager=self._cli_agent_manager)

                def is_current_target() -> bool:
                    return str(chat_id or "").strip() in {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip(), ""}

                def target_chat_state() -> dict | None:
                    if is_current_target():
                        return self._current_chat_state
                    chat = self._find_archived_chat(chat_id)
                    return chat if isinstance(chat, dict) else None

                def sync_session_id(new_session_id: str) -> None:
                    value = str(new_session_id or "").strip()
                    if not value:
                        return
                    chat = target_chat_state()
                    if isinstance(chat, dict):
                        chat["claudecode_session_id"] = value
                    if is_current_target():
                        self.active_claudecode_session_id = value

                if is_current_target():
                    # 保存客户端引用，以便在用户发送消息时使用
                    self._active_claudecode_client = client

                def on_delta(delta):
                    wx_call_after_if_alive(self._on_delta, turn_idx, delta, chat_id)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request, parse_remote_user_input_reply

                    # 格式化请求消息
                    request_msg = format_remote_user_input_request(params)

                    # 显示请求消息
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n", chat_id)

                    # 显示交互式界面（如果有回调）
                    if is_current_target() and hasattr(self, '_show_claudecode_user_input'):
                        wx_call_after_if_alive(self._show_claudecode_user_input, turn_idx, params, client)

                    # 返回空字符串，让 Claude Code 等待 stdin
                    # 用户会通过发送消息来提供输入，该消息会被拦截并写入 stdin
                    return ""

                def on_approval(params: dict) -> str:
                    """处理批准请求"""
                    from claudecode_remote_protocol import format_remote_approval_request, parse_remote_approval_reply

                    # 格式化请求消息
                    request_msg = format_remote_approval_request(params)

                    # 显示请求消息
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n", chat_id)

                    # 显示交互式界面（如果有回调）
                    if is_current_target() and hasattr(self, '_show_claudecode_approval'):
                        wx_call_after_if_alive(self._show_claudecode_approval, turn_idx, params, client)

                    # 返回空字符串，让 Claude Code 等待 stdin
                    # 用户会通过发送消息来提供批准，该消息会被拦截并写入 stdin
                    return ""

                full_text, new_session_id = client.stream_chat(
                    question,
                    session_id=session_id,
                    on_delta=on_delta,
                    on_user_input=on_user_input,
                    on_approval=on_approval
                )
                if new_session_id:
                    sync_session_id(new_session_id)
                last_context_usage = getattr(client, "last_context_usage", None)
                if last_context_usage:
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
                wx_call_after_if_alive(self._on_done, turn_idx, full_text, "", DEFAULT_CLAUDECODE_MODEL, "", chat_id)
            except Exception as exc:
                error_msg = str(exc)
                wx_call_after_if_alive(self._on_done, turn_idx, "", error_msg, DEFAULT_CLAUDECODE_MODEL, "", chat_id)
            finally:
                # 清除客户端引用
                if self._active_claudecode_client is client:
                    self._active_claudecode_client = None

        threading.Thread(target=_worker, daemon=True).start()

    def _publish_remote_nats_event(self, payload: dict) -> None:
        transport = getattr(self, "_remote_nats_transport", None)
        if transport is None:
            return
        try:
            publish = getattr(transport, "publish_event_threadsafe", None)
            if callable(publish):
                publish(payload)
        except Exception:
            pass

    def _broadcast_remote_event(self, payload: dict) -> None:
        self._publish_remote_nats_event(payload)

    def _push_remote_status(self, status: str, request_kind: str = "") -> None:
        self._invalidate_remote_state_cache()
        payload = {
            "type": "status",
            "chat_id": self.active_chat_id or self.current_chat_id or "",
            "status": status,
            "request_kind": request_kind,
            "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
            "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
            "ts": time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_state(self, chat_id: str | None = None) -> None:
        resolved_chat_id = chat_id or self.active_chat_id or self.current_chat_id or ""
        status, body = self._remote_api_state_ui({"chat_id": resolved_chat_id})
        if status >= 400:
            return
        self._broadcast_remote_event(
            {
                "type": "state",
                "chat_id": resolved_chat_id,
                "body": body,
                "ts": time.time(),
            }
        )

    def _push_remote_final_answer(self, chat_id: str, text: str) -> None:
        resolved_chat_id = chat_id if chat_id not in {"", None} else self.current_chat_id
        if resolved_chat_id in {"", None}:
            resolved_chat_id = self.active_chat_id if self.active_chat_id not in {"", None} else None
        payload = {
            "type": "final_answer",
            "chat_id": resolved_chat_id,
            "text": str(text or ""),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_history_changed(self, chat_id: str | None = None) -> None:
        self._invalidate_remote_history_list_cache()
        self._invalidate_remote_state_cache()
        payload = {
            "type": "history_changed",
            "chat_id": str(chat_id or ""),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_notes_changed(self, cursor: str | None = None) -> None:
        self._invalidate_remote_notes_changes_cache()
        payload = {
            "type": "notes_changed",
            "cursor": str(cursor or self.notes_store.current_cursor() or "0"),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_notes_conflict(self, payload: dict | None = None) -> None:
        conflict = dict(payload or {})
        conflict.update(
            {
                "type": "notes_conflict",
                "event_id": f"evt-{uuid.uuid4().hex[:8]}",
                "ts": time.time(),
            }
        )
        self._broadcast_remote_event(conflict)

    def _push_remote_notes_sync_status(self, status: str | dict, *, cursor: str | None = None, message: str | None = None) -> None:
        payload = dict(status) if isinstance(status, dict) else {"status": str(status or "")}
        if cursor is not None:
            payload["cursor"] = str(cursor or "")
        if message is not None:
            payload["message"] = str(message or "")
        payload.update({"type": "notes_sync_status", "event_id": f"evt-{uuid.uuid4().hex[:8]}", "ts": time.time()})
        self._broadcast_remote_event(payload)

    def _run_remote_ui_route(self, callback, payload: dict | None = None) -> tuple[int, dict]:
        if threading.current_thread() is threading.main_thread():
            return callback(payload) if payload is not None else callback()
        done = threading.Event()
        result: dict[str, object] = {}

        def _invoke() -> None:
            try:
                result["value"] = callback(payload) if payload is not None else callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        if not self._call_after_if_alive(_invoke):
            return 503, {"accepted": False, "error": "ui_unavailable"}
        if not done.wait(15.0):
            return 503, {"accepted": False, "error": "ui_timeout"}
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        value = result.get("value")
        if isinstance(value, tuple) and len(value) == 2:
            return value  # type: ignore[return-value]
        return 500, {"accepted": False, "error": "invalid_route_result"}

    def _on_notes_sync_push_result(self, result: dict | None = None) -> None:
        result = dict(result or {})
        cursor = str(result.get("cursor") or self.notes_store.current_cursor() or "0")
        self._push_remote_notes_changed(cursor)
        if result.get("conflicts"):
            self._push_remote_notes_conflict({"conflicts": list(result.get("conflicts") or []), "cursor": cursor})
            self._push_remote_notes_sync_status({"status": "conflict"}, cursor=cursor, message="notes_conflict")
        else:
            self._push_remote_notes_sync_status({"status": "synced"}, cursor=cursor, message="notes_sync_status")

    def _remote_turn_payload(self, turn: dict) -> dict:
        question = str(turn.get("question") or "")
        answer_md = str(turn.get("answer_md") or "")
        model = str(turn.get("model") or "")
        signature = (
            question,
            answer_md,
            model,
            str(turn.get("request_status") or ""),
            str(turn.get("request_error") or ""),
        )
        cache = getattr(self, "_remote_turn_payload_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._remote_turn_payload_cache = cache
        cache_key = id(turn)
        cached = cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == signature:
            return dict(cached[1])
        answer = REQUESTING_TEXT if answer_md == REQUESTING_TEXT else remove_emojis(md_to_plain(self._answer_markdown_for_output(answer_md, model)))
        payload = {
            "question": question,
            "answer": answer,
            "model": model,
            "created_at": float(turn.get("created_at") or 0.0),
            "assistant_only": (not question.strip()) and bool(answer_md.strip()),
            "pending": str(turn.get("request_status") or "").strip() == "pending" or answer_md == REQUESTING_TEXT,
            "request_status": str(turn.get("request_status") or ""),
            "request_error": str(turn.get("request_error") or ""),
        }
        cache[cache_key] = (signature, dict(payload))
        return payload

    def _remote_chat_summary(self, chat: dict) -> dict:
        if not isinstance(chat, dict):
            return {
                "chat_id": "",
                "title": "新聊天",
                "model": DEFAULT_MODEL_ID,
                "created_at": 0.0,
                "updated_at": 0.0,
                "turn_count": 0,
                "running": False,
                "request_kind": "",
                "current": False,
                "active": False,
                "pinned": False,
                "detail_panel_mode": "answers",
            }
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        turn_count = len(turns) if turns else int(chat.get("turn_count") or 0)
        chat_id = str(chat.get("id") or "")
        title = str(chat.get("title") or "新聊天")
        model = str(chat.get("model") or DEFAULT_MODEL_ID)
        created_at = float(chat.get("created_at") or 0.0)
        updated_at = float(chat.get("updated_at") or created_at or time.time())
        pending_request = chat.get("codex_pending_request")
        request_kind = "user_input" if isinstance(pending_request, dict) and pending_request else ""
        current_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        return {
            "chat_id": chat_id,
            "title": title,
            "model": model,
            "created_at": created_at,
            "updated_at": updated_at,
            "title_source": str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")),
            "title_updated_at": float(chat.get("title_updated_at") or updated_at),
            "title_revision": int(chat.get("title_revision") or 1),
            "turn_count": turn_count,
            "running": False,
            "request_kind": request_kind,
            "current": bool(chat_id) and chat_id == current_id,
            "active": bool(chat_id) and chat_id == current_id,
            "pinned": bool(chat.get("pinned")),
            "detail_panel_mode": str(chat.get("detail_panel_mode") or "answers").strip() or "answers",
        }

    def _remote_execution_step_payload(self, chat: dict, *, include_execution_steps: bool = False) -> tuple[list, int]:
        steps = chat.get("execution_steps") if isinstance(chat.get("execution_steps"), list) else []
        if not include_execution_steps:
            return [], len(steps)
        return copy.deepcopy(steps), len(steps)

    def _remote_chat_snapshot(self, chat: dict, *, include_execution_steps: bool = False) -> dict:
        if not isinstance(chat, dict):
            return {
                "chat_id": "",
                "title": "新聊天",
                "model": DEFAULT_MODEL_ID,
                "created_at": 0.0,
                "updated_at": 0.0,
                "turn_count": 0,
                "running": False,
                "request_kind": "",
                "current": False,
                "active": False,
                "pinned": False,
                "detail_panel_mode": "answers",
                "execution_steps": [],
                "execution_step_count": 0,
                "turns": [],
            }
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        execution_steps, execution_step_count = self._remote_execution_step_payload(
            chat,
            include_execution_steps=include_execution_steps,
        )
        chat_id = str(chat.get("id") or "")
        title = str(chat.get("title") or "新聊天")
        model = str(chat.get("model") or DEFAULT_MODEL_ID)
        created_at = float(chat.get("created_at") or 0.0)
        updated_at = float(chat.get("updated_at") or created_at or time.time())
        pending_request = chat.get("codex_pending_request")
        request_kind = "user_input" if isinstance(pending_request, dict) and pending_request else ""
        current_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        return {
            "chat_id": chat_id,
            "title": title,
            "model": model,
            "created_at": created_at,
            "updated_at": updated_at,
            "title_source": str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")),
            "title_updated_at": float(chat.get("title_updated_at") or updated_at),
            "title_revision": int(chat.get("title_revision") or 1),
            "turn_count": len(turns),
            "running": False,
            "request_kind": request_kind,
            "current": bool(chat_id) and chat_id == current_id,
            "active": bool(chat_id) and chat_id == current_id,
            "pinned": bool(chat.get("pinned")),
            "detail_panel_mode": str(chat.get("detail_panel_mode") or "answers").strip() or "answers",
            "execution_steps": execution_steps,
            "execution_step_count": execution_step_count,
            "turns": [self._remote_turn_payload(turn) for turn in turns if isinstance(turn, dict)],
        }

    def _remote_chat_snapshot_page(self, chat: dict, payload: dict | None = None) -> tuple[dict, bool, str]:
        payload = payload or {}
        include_execution_steps = bool(payload.get("include_execution_steps"))
        raw_limit = payload.get("limit")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 0
        if limit <= 0:
            return self._remote_chat_snapshot(chat, include_execution_steps=include_execution_steps), False, ""
        raw_turns = chat.get("turns") if isinstance(chat, dict) and isinstance(chat.get("turns"), list) else []
        turns = [turn for turn in raw_turns if isinstance(turn, dict)]
        total = len(turns)
        before_raw = payload.get("before_turn_index")
        try:
            end = int(before_raw)
        except Exception:
            end = total
        end = max(0, min(end, total))
        start = max(0, end - limit)
        paged_chat = dict(chat) if isinstance(chat, dict) else {}
        paged_chat["turns"] = turns[start:end]
        snapshot = self._remote_chat_snapshot(paged_chat, include_execution_steps=include_execution_steps)
        snapshot["turn_count"] = total
        return snapshot, start > 0, (str(start) if start > 0 else "")

    def _remote_chat_snapshot_page_from_store(self, chat: dict, payload: dict | None = None) -> tuple[dict, bool, str]:
        payload = dict(payload or {})
        chat_id = str((chat or {}).get("id") or payload.get("chat_id") or "").strip()
        store = getattr(self, "chat_store", None)
        if not chat_id or store is None or not hasattr(store, "load_turns_page"):
            return self._remote_chat_snapshot_page(chat, payload)
        raw_limit = payload.get("limit")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 0
        if limit <= 0:
            limit = REMOTE_STATE_DEFAULT_TURN_LIMIT
        before_raw = payload.get("before_turn_index")
        try:
            before_turn_index = int(before_raw) if before_raw is not None else None
        except Exception:
            before_turn_index = None
        total, turns = store.load_turns_page(chat_id, limit=limit, before_turn_index=before_turn_index)
        end = before_turn_index if before_turn_index is not None else total
        end = max(0, min(int(end or 0), int(total or 0)))
        start = max(0, end - max(1, int(limit or 1)))
        paged_chat = dict(chat or {})
        paged_chat["id"] = chat_id
        paged_chat["turns"] = turns
        if bool(payload.get("include_execution_steps")) and "execution_steps" not in paged_chat:
            try:
                paged_chat["execution_steps"] = store.load_execution_steps(chat_id)
            except Exception:
                paged_chat["execution_steps"] = []
        snapshot = self._remote_chat_snapshot(
            paged_chat,
            include_execution_steps=bool(payload.get("include_execution_steps")),
        )
        snapshot["turn_count"] = int(total or 0)
        return snapshot, start > 0, (str(start) if start > 0 else "")

    def _current_chat_snapshot(self, *, include_execution_steps: bool = False) -> dict:
        chat = dict(self._current_chat_state or {})
        if not chat:
            chat = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天", "turns": self.active_session_turns}
        chat["id"] = str(chat.get("id") or self.active_chat_id or self.current_chat_id or "")
        chat["title"] = str(chat.get("title") or "新聊天")
        chat["model"] = str(chat.get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID)
        chat["created_at"] = float(chat.get("created_at") or self.active_session_started_at or time.time())
        chat["updated_at"] = float(chat.get("updated_at") or time.time())
        chat["turns"] = [self._remote_turn_payload(turn) for turn in self.active_session_turns if isinstance(turn, dict)]
        chat["chat_id"] = chat["id"]
        chat["turn_count"] = len(chat["turns"])
        chat["running"] = bool(self.is_running)
        chat["request_kind"] = "user_input" if self.active_codex_pending_request else ""
        chat["current"] = True
        chat["active"] = True
        chat["pinned"] = bool(chat.get("pinned"))
        chat["detail_panel_mode"] = str(chat.get("detail_panel_mode") or "answers").strip() or "answers"
        execution_steps, execution_step_count = self._remote_execution_step_payload(
            chat,
            include_execution_steps=include_execution_steps,
        )
        chat["execution_steps"] = execution_steps
        chat["execution_step_count"] = execution_step_count
        chat["title_source"] = str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default"))
        chat["title_updated_at"] = float(chat.get("title_updated_at") or chat["updated_at"])
        chat["title_revision"] = int(chat.get("title_revision") or 1)
        return chat

    def _codex_answer_filter_menu_label(self) -> str:
        return "取消过滤英文内容" if self.codex_answer_english_filter_enabled else "在回答中过滤英文内容"

    def _toggle_codex_answer_filter(self) -> None:
        self.codex_answer_english_filter_enabled = not self.codex_answer_english_filter_enabled
        self._save_state()
        self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
        if self.view_mode in {"active", "history"}:
            self._render_answer_list()

    def _queue_input_attachments(self, attachments: list[dict], *, update_input: bool = True) -> bool:
        success, failed = self._normalize_outgoing_attachments(attachments)
        self._pending_input_attachments.extend(success + failed)
        if update_input and (success or failed):
            marker = self._input_attachment_marker_text(success + failed)
            current = self.input_edit.GetValue()
            if marker and marker not in current:
                next_value = f"{current}\n{marker}".strip() if current.strip() else marker
                self.input_edit.SetValue(next_value)
                self.input_edit.SetInsertionPointEnd()
            self.input_edit.SetFocus()
        return bool(success or failed)

    def _load_chat_attachments_via_dialog(self) -> bool:
        dlg = wx.FileDialog(
            self,
            "选择图片或文件",
            wildcard="所有文件 (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            paths = [str(path or "").strip() for path in dlg.GetPaths() if str(path or "").strip()]
        finally:
            dlg.Destroy()
        attachments = [{"name": Path(path).name, "path": path, "kind": self._normalize_attachment_kind(path)} for path in paths]
        if not attachments:
            return False
        self._pending_input_attachments = []
        self._queue_input_attachments(attachments, update_input=False)
        ok, message = self._submit_question("", source="local")
        if not ok and message:
            wx.MessageBox(message, "提示", wx.OK | wx.ICON_WARNING)
        return ok

    def _read_clipboard_attachments(self) -> list[dict]:
        attachments = []
        if not wx.TheClipboard.Open():
            return attachments
        try:
            file_data = wx.FileDataObject()
            if wx.TheClipboard.GetData(file_data):
                for path in file_data.GetFilenames():
                    text = str(path or "").strip()
                    if text:
                        attachments.append({"name": Path(text).name, "path": text, "kind": self._normalize_attachment_kind(text)})
            if attachments:
                return attachments
            bitmap_data = wx.BitmapDataObject()
            if wx.TheClipboard.GetData(bitmap_data):
                bitmap = bitmap_data.GetBitmap()
                if bitmap and bitmap.IsOk():
                    file_path = self.chat_uploads_dir / f"clipboard_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.png"
                    if bitmap.SaveFile(str(file_path), wx.BITMAP_TYPE_PNG):
                        attachments.append({"name": file_path.name, "path": str(file_path), "kind": "image"})
        finally:
            wx.TheClipboard.Close()
        return attachments

    def _try_paste_clipboard_attachments_to_input(self) -> bool:
        attachments = self._read_clipboard_attachments()
        if not attachments:
            return False
        self._queue_input_attachments(attachments, update_input=True)
        self.SetStatusText("已添加附件到输入框")
        return True

    def _show_tools_menu(self) -> None:
        menu = wx.Menu()
        voice_id = wx.NewIdRef()
        filter_id = wx.NewIdRef()
        attach_id = wx.NewIdRef()
        menu.Append(voice_id, "语音通话设置")
        menu.Append(attach_id, "载入图片或文件")
        filter_item = menu.AppendCheckItem(filter_id, "过滤英文内容")
        filter_item.Check(bool(self.codex_answer_english_filter_enabled))
        self.Bind(wx.EVT_MENU, self._on_open_realtime_call_settings, id=voice_id)
        self.Bind(wx.EVT_MENU, lambda _evt: self._load_chat_attachments_via_dialog(), id=attach_id)
        self.Bind(wx.EVT_MENU, lambda _evt: self._toggle_codex_answer_filter(), id=filter_id)
        try:
            self.PopupMenu(menu, (0, 0))
        finally:
            menu.Destroy()

    def _cancel_pending_tools_menu_open(self) -> None:
        self._alt_menu_armed = False
        self._alt_menu_suppressed = False

    def _arm_tools_menu_open(self) -> None:
        self._alt_menu_armed = True
        self._alt_menu_suppressed = False

    def _suppress_tools_menu_open(self) -> None:
        if getattr(self, "_alt_menu_armed", False):
            self._alt_menu_suppressed = True

    def _handle_alt_key_up(self) -> bool:
        should_open = bool(getattr(self, "_alt_menu_armed", False)) and not bool(getattr(self, "_alt_menu_suppressed", False))
        self._cancel_pending_tools_menu_open()
        if should_open:
            self._show_tools_menu()
            return True
        return False

    def _focus_control_safely(self, control) -> bool:
        if control is None:
            return False
        try:
            if hasattr(control, "IsEnabled") and not control.IsEnabled():
                return False
        except Exception:
            pass
        try:
            control.SetFocus()
            return True
        except Exception:
            return False

    def _focus_current_detail_list(self) -> bool:
        return self._focus_control_safely(self._current_detail_tab_target())

    def _focus_input_box(self) -> bool:
        return self._focus_control_safely(getattr(self, "input_edit", None))

    def _focus_history_list(self) -> bool:
        return self._focus_control_safely(getattr(self, "history_list", None))

    def _focus_visible_notes_list(self) -> bool:
        entry_list = getattr(self, "notes_entry_list", None)
        notebook_list = getattr(self, "notes_notebook_list", None)
        view = str(getattr(getattr(self, "notes_controller", None), "notes_view", "notes_list") or "notes_list")
        if view == "note_detail":
            return self._focus_control_safely(entry_list)
        return self._focus_control_safely(notebook_list)

    def _handle_window_focus_shortcut(self, key: int, alt_down: bool, ctrl_down: bool) -> bool:
        if not alt_down or ctrl_down:
            return False
        try:
            letter = chr(int(key)).upper()
        except Exception:
            return False
        handlers = {
            "F": self._focus_current_detail_list,
            "D": self._focus_input_box,
            "G": self._focus_history_list,
            "B": self._focus_visible_notes_list,
        }
        handler = handlers.get(letter)
        if handler is None:
            return False
        self._suppress_tools_menu_open()
        return bool(handler())

    def _update_busy_state(self) -> None:
        busy = bool(self._active_request_count or self.is_running)
        self.is_running = busy
        self.new_chat_button.Enable()
        if busy:
            self._set_input_hint_sent()
        else:
            self._set_input_hint_idle()

    def _add_system_message_to_chat(self, text: str) -> None:
        message = str(text or "")
        if not message:
            return
        turn = {
            "question": "",
            "answer_md": message,
            "model": "system",
            "created_at": time.time(),
        }
        self._apply_nonrecoverable_turn_metadata(turn, "system", "")
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)

    def _remote_api_message_ui(self, payload: dict) -> tuple[int, dict]:
        text = str(payload.get("text") or "").strip()
        if not text:
            return 400, {"accepted": False, "error": "empty_text"}
        requested_chat_id = str(payload.get("chat_id") or "").strip()
        if requested_chat_id:
            if requested_chat_id != self.active_chat_id:
                if self._find_archived_chat(requested_chat_id):
                    self._switch_current_chat(requested_chat_id)
                else:
                    self.active_chat_id = requested_chat_id
                    self.current_chat_id = requested_chat_id
                    self._current_chat_state["id"] = requested_chat_id
            chat_id = requested_chat_id
        else:
            chat_id = self.active_chat_id or self.current_chat_id or self._ensure_active_chat_id()
            if not self.current_chat_id and self.active_chat_id:
                self.current_chat_id = self.active_chat_id
            if not self._current_chat_state.get("id"):
                self._current_chat_state["id"] = chat_id
        if threading.current_thread() is threading.main_thread():
            current_model = self._resolve_current_model()
        else:
            current_model = str(self.selected_model or DEFAULT_MODEL_ID)
        model = str(payload.get("model") or current_model or DEFAULT_MODEL_ID).strip()
        resolved = model_id_from_display_name(model)
        if resolved in MODEL_IDS:
            model = resolved
        elif model not in MODEL_IDS:
            model = DEFAULT_MODEL_ID
        if threading.current_thread() is threading.main_thread():
            self.model_combo.SetValue(model_display_name(model))
        self.selected_model = model
        if threading.current_thread() is threading.main_thread():
            self.input_edit.SetValue(text)
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
        title_revision_before = 0
        if isinstance(target_chat, dict):
            try:
                title_revision_before = int(target_chat.get("title_revision") or 0)
            except Exception:
                title_revision_before = 0
        ok, message = self._submit_question(text, source="remote-ws", model=model, chat_id=chat_id)
        if ok:
            self._push_remote_state(chat_id)
            target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
            title_revision_after = title_revision_before
            if isinstance(target_chat, dict):
                try:
                    title_revision_after = int(target_chat.get("title_revision") or 0)
                except Exception:
                    title_revision_after = title_revision_before
            if title_revision_after == title_revision_before:
                self._push_remote_history_changed(chat_id)
        return 200 if ok else 400, {"accepted": ok, "message": message, "chat_id": chat_id, "model": model}

    def _remote_api_new_chat_ui(self, payload: dict) -> tuple[int, dict]:
        chat = self._start_remote_new_chat(payload)
        return 200, {"accepted": True, **chat}

    def _start_remote_new_chat(self, payload: dict | None = None) -> dict:
        previous_chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        archived = self._archive_active_session(quick_title=True, schedule_async_rename=True)
        self.view_mode = "active"
        self.view_history_id = None
        self._pending_context_usage_by_turn = {}
        self._active_claudecode_client = None
        self.current_chat_id = ""
        self.active_chat_id = ""
        self.active_session_turns = []
        now = time.time()
        default_title = str((payload or {}).get("title") or "").strip() or self._next_default_chat_title()
        self.active_session_started_at = now
        self._current_chat_state = {
            "id": "",
            "title": default_title,
            "title_manual": False,
            "title_source": "default",
            "title_updated_at": now,
            "title_revision": 1,
            "turns": self.active_session_turns,
            "context_usage": None,
            "created_at": now,
            "updated_at": now,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
        self.active_chat_id = str(uuid.uuid4())
        self.current_chat_id = self.active_chat_id
        self._current_chat_state["id"] = self.active_chat_id
        self._reset_answer_visible_row_limit()
        self._reset_current_turn_execution_view()
        model = str((payload or {}).get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID).strip()
        resolved = model_id_from_display_name(model)
        if resolved in MODEL_IDS:
            model = resolved
        elif model not in MODEL_IDS:
            model = DEFAULT_MODEL_ID
        self.selected_model = model
        if threading.current_thread() is threading.main_thread():
            self.model_combo.SetValue(model_display_name(model))
            self.input_edit.SetFocus()
        self._current_chat_state["model"] = model
        self._save_state()
        self._refresh_history(archived["id"] if archived else previous_chat_id or None)
        self._render_answer_list()
        self.SetStatusText("已开始远程新聊天")
        self._push_remote_history_changed(self.active_chat_id)
        return {
            "chat_id": self.active_chat_id,
            "title": self._current_chat_state["title"],
            "model": model,
            "created_at": now,
            "title_source": self._current_chat_state["title_source"],
            "title_updated_at": self._current_chat_state["title_updated_at"],
            "title_revision": self._current_chat_state["title_revision"],
        }

    def _remote_api_reply_request_ui(self, payload: dict) -> tuple[int, dict]:
        text = str(payload.get("text") or "").strip()
        ok, message = self._handle_remote_pending_request_reply(text)
        return 200 if ok else 400, {"accepted": ok, "message": message}

    def _remote_api_model_list_ui(self) -> tuple[int, dict]:
        return 200, {
            "accepted": True,
            "models": [
                {"id": model_id, "label": model_display_name(model_id)}
                for model_id in VISIBLE_MODEL_IDS
            ],
        }

    def _remote_api_history_list_ui(self, _payload: dict | None = None) -> tuple[int, dict]:
        cached = getattr(self, "_remote_history_list_cache", None)
        if isinstance(cached, dict):
            return 200, copy.deepcopy(cached)
        chats = []
        if self.active_chat_id or self.active_session_turns:
            current = dict(self._current_chat_state or {})
            if not current:
                current = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天", "turns": self.active_session_turns}
            current["id"] = str(current.get("id") or self.active_chat_id or self.current_chat_id or "")
            current["title"] = str(current.get("title") or "新聊天")
            current["model"] = str(current.get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID)
            current["created_at"] = float(current.get("created_at") or self.active_session_started_at or time.time())
            current["updated_at"] = float(current.get("updated_at") or time.time())
            current["turns"] = self.active_session_turns
            current_summary = self._remote_chat_summary(current)
            current_summary["running"] = bool(self.is_running)
            current_summary["request_kind"] = "user_input" if self.active_codex_pending_request else ""
            chats.append(current_summary)
        for chat in self.archived_chats:
            chats.append(self._remote_chat_summary(chat))
        body = {"accepted": True, "chats": chats}
        self._remote_history_list_cache = copy.deepcopy(body)
        return 200, body

    def _invalidate_remote_history_list_cache(self) -> None:
        self._remote_history_list_cache = None

    def _invalidate_remote_state_cache(self) -> None:
        self._remote_state_cache = None
        try:
            self._remote_state_cache_revision = int(getattr(self, "_remote_state_cache_revision", 0) or 0) + 1
        except Exception:
            self._remote_state_cache_revision = 1

    def _remote_api_history_read_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            source_chat = dict(self._current_chat_state or {
                "id": self.active_chat_id or self.current_chat_id or "",
                "title": "新聊天",
                "turns": self.active_session_turns,
            })
            source_chat["turns"] = self.active_session_turns
            chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, payload)
        else:
            source_chat = self._find_archived_chat(chat_id) or {}
            if (
                getattr(self, "_chat_store_enabled", False)
                and getattr(self, "chat_store", None) is not None
                and hasattr(self.chat_store, "load_turns_page")
                and not isinstance(source_chat.get("turns"), list)
            ):
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page_from_store(source_chat, payload)
            else:
                source_chat = self._hydrate_chat_from_store(source_chat, include_execution_steps=bool(payload.get("include_execution_steps"))) or {}
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, payload)
        return 200, {"accepted": True, "chat": chat, "has_more": has_more, "oldest_cursor": oldest_cursor}

    def _remote_api_state_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        chat_id = str(payload.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        include_execution_steps = bool(payload.get("include_execution_steps"))
        raw_limit = payload.get("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
        raw_before = payload.get("before_turn_index")
        cache_key = (
            chat_id,
            include_execution_steps,
            str(raw_limit),
            str(raw_before),
            int(getattr(self, "_remote_state_cache_revision", 0) or 0),
        )
        cache = getattr(self, "_remote_state_cache", None)
        if isinstance(cache, dict) and cache.get("key") == cache_key and isinstance(cache.get("body"), dict):
            return 200, copy.deepcopy(cache["body"])
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            source_chat = dict(self._current_chat_state or {})
            if not source_chat:
                source_chat = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天"}
            source_chat["id"] = str(source_chat.get("id") or self.active_chat_id or self.current_chat_id or "")
            source_chat["turns"] = self.active_session_turns
            source_chat.setdefault("execution_steps", self._current_chat_state.get("execution_steps") if isinstance(self._current_chat_state, dict) else [])
            state_payload = dict(payload)
            state_payload.setdefault("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
            chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, state_payload)
            chat["running"] = bool(self.is_running)
            chat["current"] = True
            chat["active"] = True
        else:
            state_payload = dict(payload)
            state_payload.setdefault("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
            source_chat = self._find_archived_chat(chat_id) or {}
            if (
                getattr(self, "_chat_store_enabled", False)
                and getattr(self, "chat_store", None) is not None
                and hasattr(self.chat_store, "load_turns_page")
                and not isinstance(source_chat.get("turns"), list)
            ):
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page_from_store(source_chat, state_payload)
            else:
                source_chat = self._hydrate_chat_from_store(
                    source_chat,
                    include_execution_steps=include_execution_steps,
                ) or {}
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, state_payload)
        status = "waiting_user_input" if self.active_codex_pending_request else "idle"
        request_kind = "user_input" if self.active_codex_pending_request else ""
        chat.update(
            {
                "chat_id": chat.get("chat_id") or chat_id,
                "has_more": has_more,
                "oldest_cursor": oldest_cursor,
                "status": status,
                "request_kind": request_kind,
                "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
                "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
                "remote_runtime": dict(getattr(self, "remote_control_runtime_status", {}) or {}),
                "remote_runtime_url": str(getattr(self, "remote_control_runtime_url", "") or ""),
                "remote_nats_runtime": dict(getattr(self, "remote_nats_runtime_status", {}) or {}),
                "remote_nats_runtime_url": str(getattr(self, "remote_nats_runtime_url", "") or ""),
            }
        )
        body = {"accepted": True, **chat}
        self._remote_state_cache = {"key": cache_key, "body": copy.deepcopy(body)}
        return 200, body

    def _remote_api_notes_snapshot(self, _payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_pull_since(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_push_ops(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_subscribe(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_ack(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_ping(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_retired(self) -> tuple[int, dict]:
        return 410, {
            "accepted": False,
            "error": "retired",
            "message": "旧 notes 同步协议已退役，请使用 CouchDB 同步",
        }

    def _remote_api_rename_chat_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not chat_id or not title:
            return 400, {"accepted": False, "error": "invalid_payload"}
        chat = self._find_archived_chat(chat_id)
        if not chat and chat_id == self.active_chat_id:
            chat = self._current_chat_state
        if not isinstance(chat, dict):
            return 404, {"accepted": False, "error": "not_found"}
        incoming_source = str(payload.get("title_source") or "manual").strip() or "manual"
        incoming_updated_at = float(payload.get("title_updated_at") or time.time())
        current_updated_at = float(chat.get("title_updated_at") or chat.get("updated_at") or 0.0)
        incoming_revision = int(payload.get("title_revision") or (int(chat.get("title_revision") or 0) + 1))
        current_revision = int(chat.get("title_revision") or 0)
        current_source = str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")).strip() or "default"
        incoming_priority = self._title_source_priority(incoming_source)
        current_priority = self._title_source_priority(current_source)
        if incoming_updated_at < current_updated_at:
            return 200, {
                "accepted": True,
                "chat_id": chat_id,
                "title": str(chat.get("title") or title),
                "title_source": current_source,
                "title_updated_at": current_updated_at,
                "title_revision": current_revision,
            }
        if incoming_updated_at == current_updated_at and incoming_priority < current_priority:
            return 200, {
                "accepted": True,
                "chat_id": chat_id,
                "title": str(chat.get("title") or title),
                "title_source": current_source,
                "title_updated_at": current_updated_at,
                "title_revision": current_revision,
            }
        resolved_revision = incoming_revision
        if incoming_updated_at == current_updated_at and incoming_priority == current_priority:
            resolved_revision = max(current_revision, incoming_revision)
        chat["title"] = title
        chat["title_manual"] = incoming_source == "manual"
        chat["title_source"] = incoming_source
        chat["title_updated_at"] = incoming_updated_at
        chat["title_revision"] = resolved_revision
        self._save_state()
        self._refresh_history(chat_id)
        self._push_remote_history_changed(chat_id)
        return 200, {
            "accepted": True,
            "chat_id": chat_id,
            "title": title,
            "title_source": incoming_source,
            "title_updated_at": incoming_updated_at,
            "title_revision": resolved_revision,
        }

    def _remote_api_update_settings_ui(self, payload: dict) -> tuple[int, dict]:
        if "codex_answer_english_filter_enabled" in payload:
            self.codex_answer_english_filter_enabled = bool(payload.get("codex_answer_english_filter_enabled"))
            self._invalidate_remote_state_cache()
            self._save_state()
        return 200, {
            "accepted": True,
            "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
        }

    def _handle_remote_pending_request_reply(self, text: str) -> tuple[bool, str]:
        pending = self.active_codex_pending_request
        if not pending:
            return False, "当前没有待回复的请求"
        client = self._ensure_codex_client()
        try:
            client.respond_tool_request_user_input(pending.get("request_id"), {"reply": [str(text or "")]})
        except Exception as exc:
            return False, str(exc)
        self.active_codex_pending_request = None
        self._save_state()
        return True, ""

    def _should_queue_codex_ui_event(self, chat_id: str, event: CodexEvent) -> bool:
        return True

    def _dispatch_codex_event_to_ui(self, chat_id: str, event: CodexEvent) -> None:
        if not event or not self._should_queue_codex_ui_event(chat_id, event):
            return
        if not self._is_ui_alive():
            return
        should_schedule = False
        with self._codex_ui_event_lock:
            self._pending_codex_ui_events.append((str(chat_id or "").strip(), event))
            if not self._codex_ui_event_flush_scheduled:
                self._codex_ui_event_flush_scheduled = True
                should_schedule = True
        if should_schedule and not self._call_after_if_alive(self._drain_codex_ui_events):
            with self._codex_ui_event_lock:
                self._codex_ui_event_flush_scheduled = False

    def _drain_codex_ui_events(self) -> None:
        batch_size = self._codex_ui_event_batch_size()
        with self._codex_ui_event_lock:
            batch = self._pending_codex_ui_events[:batch_size]
            del self._pending_codex_ui_events[: len(batch)]
            has_more = bool(self._pending_codex_ui_events)
            self._codex_ui_event_flush_scheduled = has_more
        self._codex_ui_batch_depth += 1
        try:
            for queued_chat_id, queued_event in batch:
                self._on_codex_event_for_chat(queued_chat_id, queued_event)
        finally:
            self._codex_ui_batch_depth = max(0, self._codex_ui_batch_depth - 1)
            self._flush_deferred_execution_list_updates()
            self._start_execution_step_persist_worker()
        if has_more:
            self._codex_ui_event_drain_timer = self._call_later_if_alive(
                CODEX_UI_EVENT_BATCH_DELAY_MS,
                self._drain_codex_ui_events,
            )
            if self._codex_ui_event_drain_timer is None and not self._call_after_if_alive(self._drain_codex_ui_events):
                with self._codex_ui_event_lock:
                    self._codex_ui_event_flush_scheduled = False
        else:
            self._codex_ui_event_drain_timer = None

    def _codex_ui_event_batch_size(self) -> int:
        if self._primary_navigation_control_has_focus():
            return max(1, min(CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE, CODEX_UI_EVENT_BATCH_SIZE))
        return CODEX_UI_EVENT_BATCH_SIZE

    def _primary_navigation_control_has_focus(self) -> bool:
        controls = [
            getattr(self, "execution_list", None),
            getattr(self, "answer_list", None),
            getattr(self, "history_list", None),
            getattr(self, "input_edit", None),
            getattr(self, "model_combo", None),
            getattr(self, "notes_notebook_list", None),
            getattr(self, "notes_entry_list", None),
        ]
        for control in controls:
            if control is None:
                continue
            try:
                if control.HasFocus():
                    return True
            except Exception:
                continue
        return False

    def _on_codex_event(self, event: CodexEvent) -> None:
        chat_id = self._resolve_codex_event_chat_id(event)
        if threading.current_thread() is not threading.main_thread():
            self._dispatch_codex_event_to_ui(chat_id, event)
            return
        self._on_codex_event_for_chat(chat_id, event)

    def _handle_codex_request_dialog(self, request: dict) -> None:
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        questions = params.get("questions") if isinstance(params.get("questions"), list) else []
        dlg = CodexUserInputDialog(self, questions)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            answers = dlg.get_answers()
        finally:
            dlg.Destroy()
        client = self._ensure_codex_client()
        client.respond_tool_request_user_input(request.get("request_id"), answers)

    def _on_codex_event_for_chat(self, chat_id: str, event: CodexEvent) -> None:
        if not isinstance(event, CodexEvent):
            return
        chat_id = str(chat_id or "").strip()
        event_type = str(getattr(event, "type", "") or "").strip()
        event_turn_id = self._event_turn_id(event)
        event_thread_id = self._event_thread_id(event)
        if not chat_id and (event_turn_id or event_thread_id):
            resolved_chat_id = self._resolve_codex_event_chat_id(event)
            if resolved_chat_id and resolved_chat_id != chat_id:
                chat_id = resolved_chat_id
        elif chat_id and (event_turn_id or event_thread_id):
            known_chat_id = self._known_codex_event_chat_id(event)
            if known_chat_id and known_chat_id != chat_id:
                chat_id = known_chat_id
        is_current_chat = chat_id in {self.active_chat_id, self.current_chat_id, "", None}
        identity_chat = self._current_chat_state if is_current_chat else self._find_archived_chat(chat_id)
        if isinstance(identity_chat, dict) and not self._codex_event_turn_is_compatible_with_chat(identity_chat, event):
            return
        execution_entry = None if event_type == "agent_message_delta" else self._build_execution_entry(event)
        appended_execution_step = False
        if not is_current_chat:
            if event_type == "agent_message_delta":
                self._buffer_execution_delta(chat_id, event)
                return
            self._flush_execution_delta(chat_id, event_turn_id or None)
            target_chat = self._find_archived_chat(chat_id)
            target_turns = target_chat.get("turns") if isinstance(target_chat, dict) and isinstance(target_chat.get("turns"), list) else []
            target_idx = self._event_turn_index(target_turns, event)
            if event_type == "token_count" and event.usage and target_idx >= 0 and isinstance(target_chat, dict):
                turn = target_turns[target_idx] if target_idx < len(target_turns) and isinstance(target_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if str(turn.get("request_status") or "").strip() == "done":
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(target_chat, target_idx), None)
                    changed = self._set_chat_context_usage(target_chat, event.usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(target_chat, target_idx, event.usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
                return
            if execution_entry:
                self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
                appended_execution_step = True
            if target_idx >= 0 and isinstance(target_chat, dict):
                turn = target_turns[target_idx]
                if event_type == "item_completed" and str(event.phase or "") == "final_answer":
                    self._apply_codex_final_answer_to_turn(turn, str(event.text or ""))
                    target_chat["updated_at"] = time.time()
                elif event_type == "subagent_result":
                    if self._apply_codex_subagent_result_to_turn(turn, str(event.text or "")):
                        target_chat["updated_at"] = time.time()
                elif event_type == "turn_completed":
                    turn["request_status"] = "done"
                    turn["request_error"] = ""
                    if (
                        (str(turn.get("answer_md") or "").strip() == REQUESTING_TEXT or self._is_codex_subagent_result_answer(turn))
                        and str(event.text or "").strip()
                    ):
                        self._apply_codex_final_answer_to_turn(turn, str(event.text or ""))
                    target_chat["updated_at"] = time.time()
                    self._refresh_context_usage_after_done(target_chat, target_turns, target_idx, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                self._mark_chat_turns_dirty(chat_id, target_idx)
                self._refresh_visible_history_chat(chat_id)
            self._defer_codex_state_save()
            return
        if event_type == "agent_message_delta":
            self._buffer_execution_delta(chat_id, event)
        else:
            self._flush_execution_delta(chat_id, event_turn_id or None)
        if execution_entry:
            appended_execution_step = self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
        if event_type == "token_count" and event.usage:
            target_idx = self._event_turn_index(self.active_session_turns, event)
            if target_idx < 0:
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0:
                turn = self.active_session_turns[target_idx] if target_idx < len(self.active_session_turns) and isinstance(self.active_session_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if completed_turn:
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(self._current_chat_state, target_idx), None)
                    changed = self._set_chat_context_usage(self._current_chat_state, event.usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(self._current_chat_state, target_idx, event.usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
            return
        if event_type == "server_request":
            self.active_codex_pending_request = None
            self._push_remote_status("waiting_user_input", "user_input")
            self._play_finish_sound()
            if str(event.method or "") == "item/tool/requestUserInput":
                self._handle_codex_request_dialog({"request_id": event.request_id, "method": event.method, "params": event.params})
            self._defer_chat_state_save()
            return
        if event_type == "subagent_result":
            target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0 and target_idx < len(self.active_session_turns):
                turn = self.active_session_turns[target_idx]
                if self._apply_codex_subagent_result_to_turn(turn, str(event.text or "")):
                    self._update_active_answer_row(target_idx)
                    if self._find_answer_row_index(target_idx) < 0 and self.view_mode == "active":
                        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
                    self._push_remote_final_answer(chat_id or self.active_chat_id or self.current_chat_id or "", str(turn.get("answer_md") or ""))
                    self._defer_codex_state_save()
            return
        if event_type == "turn_completed":
            if is_current_chat:
                self.active_codex_turn_active = False
                self.active_codex_pending_request = None
            target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0 and target_idx < len(self.active_session_turns):
                turn = self.active_session_turns[target_idx]
                turn["request_status"] = "done"
                turn["request_error"] = ""
                if (
                    (str(turn.get("answer_md") or "").strip() == REQUESTING_TEXT or self._is_codex_subagent_result_answer(turn))
                    and str(event.text or "").strip()
                ):
                    self._apply_codex_final_answer_to_turn(turn, str(event.text or ""))
                self._refresh_context_usage_after_done(self._current_chat_state, self.active_session_turns, target_idx, str(turn.get("model") or DEFAULT_CODEX_MODEL))
                self._update_active_answer_row(target_idx)
                self._mark_chat_turns_dirty(start_index=target_idx)
            if is_current_chat:
                self.is_running = False
                self._active_request_count = 0
                self.new_chat_button.Enable()
                self._set_input_hint_idle()
            if is_current_chat:
                self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
                self._play_finish_sound()
                self._defer_chat_state_save()
                if self.view_mode == "active":
                    self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
            return
        if appended_execution_step and not str(getattr(event, "text", "") or "").strip():
            self._defer_codex_state_save()
        if event_type in {"item_completed", "agent_message_delta", "plan_updated", "diff_updated", "stderr", "turn_started", "item_started"}:
            self.active_codex_latest_assistant_text = str(event.text or "")
            self.active_codex_latest_assistant_phase = str(event.phase or "")
            if event_type == "item_completed" and str(event.status or "") == "imageView":
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    path = str((event.data or {}).get("path") or "").strip()
                    if path and Path(path).is_file():
                        attachment = {
                            "name": Path(path).name,
                            "path": path,
                            "kind": "image",
                            "direction": "incoming",
                            "status": "success",
                            "open_path": path,
                            "source": "codex",
                        }
                        if self._record_received_attachment(self.active_session_turns[target_idx], attachment):
                            self._defer_codex_state_save()
                            self._render_answer_list_compat(refresh_execution=self._detail_panel_mode() != "execution")
                return
            if event_type == "item_completed" and str(event.phase or "") == "final_answer":
                self.active_codex_pending_prompt = str(event.text or "")
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    turn = self.active_session_turns[target_idx]
                    self._apply_codex_final_answer_to_turn(turn, str(event.text or ""))
                    self._update_active_answer_row(target_idx)
                    self._mark_chat_turns_dirty(start_index=target_idx)
                self._defer_codex_state_save()
                self._push_remote_final_answer(chat_id or self.active_chat_id or self.current_chat_id or "", str(event.text or ""))
                self._render_answer_list_compat(refresh_execution=self._detail_panel_mode() != "execution")
                self._call_later_if_alive(120, self._focus_latest_answer)
                return
            if event.text:
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    turn = self.active_session_turns[target_idx]
                    if str(event.phase or "") == "final_answer":
                        turn["answer_md"] = str(event.text or "")
                    elif not str(turn.get("answer_md") or "").strip():
                        turn["answer_md"] = REQUESTING_TEXT
                    self._update_active_answer_row(target_idx)
                self._defer_codex_state_save()
            return

    def _defer_codex_state_save(self) -> None:
        active_idx = int(getattr(self, "active_turn_idx", -1) or -1)
        if active_idx >= 0:
            self._mark_chat_turns_dirty(start_index=active_idx)
        self._codex_background_flush_dirty = True
        if getattr(self, "_codex_background_flush_scheduled", False):
            return
        self._codex_background_flush_scheduled = True
        timer = self._call_later_if_alive(CODEX_BACKGROUND_FLUSH_DELAY_MS, self._flush_codex_background_updates)
        if timer is None and not self._call_after_if_alive(self._flush_codex_background_updates):
            self._flush_codex_background_updates()

    def _flush_codex_background_updates(self) -> None:
        self._codex_background_flush_scheduled = False
        if not getattr(self, "_codex_background_flush_dirty", False):
            return
        self._codex_background_flush_dirty = False
        self._save_state()

    def _defer_chat_state_save(self) -> None:
        self._chat_state_flush_dirty = True
        if getattr(self, "_chat_state_flush_scheduled", False):
            return
        self._chat_state_flush_scheduled = True
        timer = self._call_later_if_alive(300, self._flush_chat_state_save)
        if timer is None and not self._call_after_if_alive(self._flush_chat_state_save):
            self._flush_chat_state_save()

    def _flush_chat_state_save(self) -> None:
        self._chat_state_flush_scheduled = False
        if not getattr(self, "_chat_state_flush_dirty", False):
            return
        self._chat_state_flush_dirty = False
        self._save_state()

    def _get_or_create_codex_client(self, chat_id: str, model: str = "") -> CodexAppServerClient:
        key = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip() or self._ensure_active_chat_id()
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = self._codex_clients.get(key)
        if client is not None and getattr(client, "codex_model", DEFAULT_CODEX_MODEL) == codex_model:
            return client
        if client is not None:
            client.close()
        client = CodexAppServerClient(on_event=lambda event, cid=key: self._dispatch_codex_event_to_ui(cid, event), codex_model=codex_model)
        self._codex_clients[key] = client
        return client

    def _ensure_codex_client(self, model: str = "") -> CodexAppServerClient:
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = getattr(self, "_codex_client", None)
        if client is None or getattr(client, "codex_model", DEFAULT_CODEX_MODEL) != codex_model:
            if client is not None:
                client.close()
            client = CodexAppServerClient(on_event=self._on_codex_event, codex_model=codex_model)
            self._codex_client = client
        return client

    def _load_chat_as_current(self, chat: dict) -> None:
        if getattr(self, "_chat_store_enabled", False):
            self._current_chat_state = dict(chat or {})
            self.active_session_turns = list((chat or {}).get("turns") or [])
            self._current_chat_state["turns"] = self.active_session_turns
            execution_steps = (chat or {}).get("execution_steps")
            if isinstance(execution_steps, list):
                self._current_chat_state["execution_steps"] = list(execution_steps)
        else:
            self._current_chat_state = copy.deepcopy(chat or {})
            self.active_session_turns = copy.deepcopy((chat or {}).get("turns") or [])
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        self._current_chat_state["title_manual"] = title_manual
        self.current_chat_id = str(chat.get("id") or "").strip() or str(uuid.uuid4())
        self.active_chat_id = self.current_chat_id
        self._current_chat_state["turns"] = self.active_session_turns
        self.active_session_started_at = float(chat.get("created_at") or time.time())
        self.active_turn_idx = len(self.active_session_turns) - 1
        self.selected_model = model_id_from_display_name(str(chat.get("model") or self.selected_model or STARTUP_DEFAULT_MODEL_ID))
        if not is_visible_model_id(self.selected_model):
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        self.model_combo.SetValue(model_display_name(self.selected_model))
        self.active_codex_thread_id = ""
        self.active_codex_turn_id = ""
        self.active_codex_turn_active = False
        self.active_codex_pending_prompt = ""
        self.active_codex_pending_request = None
        self.active_codex_request_queue = []
        self.active_codex_thread_flags = []
        self.active_codex_latest_assistant_text = ""
        self.active_codex_latest_assistant_phase = ""
        self._reset_answer_visible_row_limit()
        self._save_state()

    def _read_remote_control_token(self) -> str:
        return self._read_remote_control_setting(
            "REMOTE_CONTROL_TOKEN",
            "CLAUDECODE_REMOTE_CONTROL_TOKEN",
            default=DEFAULT_REMOTE_CONTROL_TOKEN,
        )

    def _is_process_elevated(self) -> bool:
        if os.name != "nt":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _runtime_environment_summary(self) -> dict:
        runtime = self._remote_runtime_config()
        return {
            "is_frozen": bool(getattr(sys, "frozen", False)),
            "is_elevated": self._is_process_elevated(),
            "is_fixed_domain_remote_mode": bool(runtime["fixed_domain_mode"]),
        }

    def _set_remote_runtime_status(
        self,
        *,
        local_listener_ready: bool | None = None,
        public_ws_ready: bool | None = None,
        last_remote_error: str | None = None,
        published_url: str | None = None,
    ) -> None:
        status = dict(getattr(self, "remote_control_runtime_status", {}) or {})
        status.setdefault("local_listener_ready", False)
        status.setdefault("public_ws_ready", False)
        status.setdefault("last_remote_error", "")
        status.setdefault("published_url", "")
        if local_listener_ready is not None:
            status["local_listener_ready"] = bool(local_listener_ready)
        if public_ws_ready is not None:
            status["public_ws_ready"] = bool(public_ws_ready)
        if last_remote_error is not None:
            status["last_remote_error"] = str(last_remote_error or "")
        if published_url is not None:
            status["published_url"] = str(published_url or "")
        self.remote_control_runtime_status = status
        self.remote_control_runtime_url = status["published_url"] if status["public_ws_ready"] else ""

    def _set_status_text_safe(self, text: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.SetStatusText(str(text or ""))
            return
        self._call_after_if_alive(self.SetStatusText, str(text or ""))

    def _format_remote_startup_error(self, message: str) -> str:
        env = self._runtime_environment_summary()
        return (
            f"{message} "
            f"(frozen={env['is_frozen']}, elevated={env['is_elevated']}, fixed_domain={env['is_fixed_domain_remote_mode']})"
        )

    def _set_remote_nats_runtime_status(
        self,
        *,
        enabled: bool | None = None,
        tcp_url: str | None = None,
        websocket_url: str | None = None,
        cloudflared_url: str | None = None,
        last_error: str | None = None,
    ) -> None:
        status = dict(getattr(self, "remote_nats_runtime_status", {}) or {})
        status.setdefault("enabled", False)
        status.setdefault("tcp_url", "")
        status.setdefault("websocket_url", "")
        status.setdefault("cloudflared_url", "")
        status.setdefault("last_error", "")
        if enabled is not None:
            status["enabled"] = bool(enabled)
        if tcp_url is not None:
            status["tcp_url"] = str(tcp_url or "")
        if websocket_url is not None:
            status["websocket_url"] = str(websocket_url or "")
        if cloudflared_url is not None:
            status["cloudflared_url"] = str(cloudflared_url or "")
        if last_error is not None:
            status["last_error"] = str(last_error or "")
        self.remote_nats_runtime_status = status
        self.remote_nats_runtime_url = status["websocket_url"] if status["enabled"] else ""

    def _remote_nats_call_ui(self, callback: Callable[[], tuple[int, dict]]) -> tuple[int, dict]:
        if threading.current_thread() is threading.main_thread():
            return callback()

        done = threading.Event()
        result: dict[str, object] = {}

        def _run() -> None:
            try:
                result["value"] = callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        if not wx_call_after_if_alive(_run):
            raise RuntimeError("UI is not available for NATS command handling")
        if not done.wait(30):
            raise TimeoutError("NATS command handling timed out on UI thread")
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        value = result.get("value")
        if isinstance(value, tuple):
            return value
        raise RuntimeError("NATS command handler returned invalid response")

    def _stop_remote_servers(self) -> None:
        self._stop_managed_cloudflared_process()
        transport = getattr(self, "_remote_nats_transport", None)
        if transport is not None:
            try:
                transport.stop()
            except Exception:
                pass
        self._remote_nats_transport = None
        process = getattr(self, "_remote_nats_process", None)
        if process is not None:
            try:
                process.stop()
            except Exception:
                pass
        self._remote_nats_process = None
        self._set_remote_nats_runtime_status(enabled=False)

    def _start_remote_servers(self, *, token: str, host: str, port: int) -> None:
        self._start_remote_nats_server_if_configured(token=token, host=host)

    def _build_remote_nats_url(self) -> str:
        token = self._read_remote_control_token()
        if not token:
            return ""
        base = getattr(self, "remote_nats_runtime_status", {}).get("cloudflared_url") or DEFAULT_REMOTE_NATS_CLOUDFLARED_URL
        return f"{base}?token={token}"

    def _on_copy_remote_nats_url(self, _event) -> None:
        url = self._build_remote_nats_url()
        if not url:
            wx.MessageBox("未配置远程控制令牌", "提示", wx.OK | wx.ICON_WARNING)
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(url))
            finally:
                wx.TheClipboard.Close()
        self.SetStatusText("已复制远程控制地址")

    def _start_remote_nats_server_if_configured(self, *, token: str, host: str) -> None:
        if getattr(self, "_remote_nats_transport", None) is not None:
            return
        preferred_websocket_port = self._resolve_remote_nats_websocket_port(
            DEFAULT_REMOTE_NATS_WEBSOCKET_PORT
        )
        websocket_port = preferred_websocket_port
        self._remote_nats_websocket_port = websocket_port
        preferred_ports = [DEFAULT_REMOTE_NATS_PORT, *REMOTE_NATS_PORT_FALLBACKS]
        seen_ports = set()
        last_error = ""
        for attempt_idx, tcp_port in enumerate(preferred_ports):
            if tcp_port in seen_ports or tcp_port <= 0:
                continue
            seen_ports.add(tcp_port)
            config = NatsRuntimeConfig(
                app_data_dir=resolve_app_data_dir(),
                token=token,
                host=host,
                port=tcp_port,
                websocket_host="127.0.0.1",
                websocket_port=websocket_port,
            )
            tcp_url = f"nats://127.0.0.1:{config.port}"
            websocket_url = f"ws://127.0.0.1:{config.websocket_port}/nats"
            process = None
            reused_existing_runtime = False
            try:
                process = NatsServerProcess(config)
                try:
                    process.start()
                except Exception as exc:
                    message = str(exc or "").lower()
                    port_in_use = "already in use" in message
                    port_unavailable = (
                        "access permissions" in message
                        or "forbidden by its access permissions" in message
                        or "unavailable for binding" in message
                    )
                    if not port_in_use and not port_unavailable:
                        raise
                    process = None
                    if attempt_idx > 0:
                        continue
                    if port_unavailable:
                        continue
                    reused_existing_runtime = True
                    websocket_port = self._probe_existing_remote_nats_websocket_port()
                    if websocket_port is None:
                        raise RuntimeError("Could not determine websocket port for existing NATS runtime")
                    self._remote_nats_websocket_port = websocket_port
                    websocket_url = f"ws://127.0.0.1:{websocket_port}/nats"
                transport = RemoteNatsTransport(
                    pair_id="default",
                    token=token,
                    on_message=lambda payload: self._run_remote_ui_route(self._remote_api_message_ui, payload),
                    on_new_chat=lambda payload: self._run_remote_ui_route(self._remote_api_new_chat_ui, payload),
                    on_reply_request=lambda payload: self._run_remote_ui_route(self._remote_api_reply_request_ui, payload),
                    on_state=lambda payload: self._run_remote_ui_route(self._remote_api_state_ui, payload),
                    on_rename_chat=lambda payload: self._run_remote_ui_route(self._remote_api_rename_chat_ui, payload),
                    on_update_settings=lambda payload: self._run_remote_ui_route(self._remote_api_update_settings_ui, payload),
                    on_model_list=lambda: self._run_remote_ui_route(self._remote_api_model_list_ui),
                    on_history_list=lambda: self._run_remote_ui_route(self._remote_api_history_list_ui),
                    on_history_read=lambda payload: self._run_remote_ui_route(self._remote_api_history_read_ui, payload),
                    on_notes_changes=self._remote_api_notes_changes,
                    on_notes_bulk_docs=self._remote_api_notes_bulk_docs,
                )
                transport.start_threaded(tcp_url)
            except Exception as exc:
                last_error = str(exc)
                if process is not None:
                    try:
                        process.stop()
                    except Exception:
                        pass
                message = str(exc or "").lower()
                if (
                    reused_existing_runtime
                    or "already in use" in message
                    or "access permissions" in message
                    or "forbidden by its access permissions" in message
                    or "unavailable for binding" in message
                ):
                    websocket_port = preferred_websocket_port
                    self._remote_nats_websocket_port = websocket_port
                    continue
                self._set_remote_nats_runtime_status(
                    enabled=False,
                    tcp_url=tcp_url,
                    websocket_url=websocket_url,
                    cloudflared_url=DEFAULT_REMOTE_NATS_CLOUDFLARED_URL,
                    last_error=last_error,
                )
                return
            self._remote_nats_process = process
            self._remote_nats_transport = transport
            self._ensure_cloudflared_origin_bridge()
            self._set_remote_nats_runtime_status(
                enabled=True,
                tcp_url=tcp_url,
                websocket_url=websocket_url,
                cloudflared_url=DEFAULT_REMOTE_NATS_CLOUDFLARED_URL,
                last_error="",
            )
            if reused_existing_runtime:
                self.SetStatusText("远程 NATS 已复用现有本地运行时")
            return
        self._set_remote_nats_runtime_status(
            enabled=False,
            tcp_url=f"nats://127.0.0.1:{DEFAULT_REMOTE_NATS_PORT}",
            websocket_url=f"ws://127.0.0.1:{self._remote_nats_websocket_port}/nats",
            cloudflared_url=DEFAULT_REMOTE_NATS_CLOUDFLARED_URL,
            last_error=last_error or "远程 NATS 运行时未成功启动。",
        )
        return

    def _resolve_remote_nats_websocket_port(self, preferred_port: int) -> int:
        candidates = [preferred_port, *REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS]
        seen = set()
        for port in candidates:
            if port in seen or port <= 0:
                continue
            seen.add(port)
            if self._can_bind_loopback_tcp_port(port):
                return port
        return preferred_port

    def _can_bind_loopback_tcp_port(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                return False
        except Exception:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("127.0.0.1", int(port)))
                return True
        except Exception:
            return False

    def _probe_existing_remote_nats_websocket_port(self) -> int | None:
        candidates = []
        runtime_url = str((getattr(self, "remote_nats_runtime_status", {}) or {}).get("websocket_url") or "").strip()
        if runtime_url:
            try:
                parsed = urlsplit(runtime_url)
                if parsed.port:
                    candidates.append(int(parsed.port))
            except Exception:
                pass
        candidates.extend(
            [
                getattr(self, "_remote_nats_websocket_port", DEFAULT_REMOTE_NATS_WEBSOCKET_PORT),
                DEFAULT_REMOTE_NATS_WEBSOCKET_PORT,
                *REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS,
            ]
        )
        seen = set()
        for port in candidates:
            try:
                port = int(port)
            except Exception:
                continue
            if port in seen or port <= 0:
                continue
            seen.add(port)
            if self._probe_remote_nats_websocket_port(port):
                return port
        return None

    def _probe_remote_nats_websocket_port(self, port: int) -> bool:
        if port <= 0 or not self._remote_local_listener_ready(port):
            return False
        url = f"ws://127.0.0.1:{int(port)}/nats"
        ok, _detail = self._verify_remote_public_ws(url)
        return ok

    def _ensure_cloudflared_origin_bridge(self) -> None:
        listen_port = int(getattr(self, "remote_control_port", 0) or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        target_port = int(
            getattr(self, "_remote_nats_websocket_port", DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
            or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT
        )
        for delete_args, add_args in (
            (
                [
                    "netsh",
                    "interface",
                    "portproxy",
                    "delete",
                    "v4tov4",
                    f"listenport={listen_port}",
                    "listenaddress=127.0.0.1",
                ],
                [
                    "netsh",
                    "interface",
                    "portproxy",
                    "add",
                    "v4tov4",
                    f"listenport={listen_port}",
                    "listenaddress=127.0.0.1",
                    f"connectport={target_port}",
                    "connectaddress=127.0.0.1",
                ],
            ),
            (
                [
                    "netsh",
                    "interface",
                    "portproxy",
                    "delete",
                    "v6tov4",
                    f"listenport={listen_port}",
                    "listenaddress=::1",
                ],
                [
                    "netsh",
                    "interface",
                    "portproxy",
                    "add",
                    "v6tov4",
                    f"listenport={listen_port}",
                    "listenaddress=::1",
                    f"connectport={target_port}",
                    "connectaddress=127.0.0.1",
                ],
            ),
        ):
            self._run_remote_check_command(delete_args, timeout=10.0)
            result = self._run_remote_check_command(add_args, timeout=10.0)
            if result is None:
                continue
            detail = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
            if result.returncode == 0 or "already exists" in detail:
                continue

    def _run_remote_check_command(self, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess | None:
        kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run(args, **kwargs)
        except Exception:
            return None

    def _query_cloudflared_service(self) -> dict:
        result = self._run_remote_check_command(["sc.exe", "query", "cloudflared"])
        config_result = self._run_remote_check_command(["sc.exe", "qc", "cloudflared"])
        text = ""
        if result is not None:
            text = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        config_text = ""
        if config_result is not None:
            config_text = f"{config_result.stdout or ''}\n{config_result.stderr or ''}".strip()
        normalized = text.lower()
        binary_path = ""
        for line in config_text.splitlines():
            match = re.match(r"\s*BINARY_PATH_NAME\s*:\s*(.+)", line, flags=re.IGNORECASE)
            if match:
                binary_path = match.group(1).strip()
                break
        exists = bool(
            (result and (result.returncode == 0 or "state" in normalized))
            or (config_result and config_result.returncode == 0 and bool(binary_path))
        )
        running = "running" in normalized
        detail = "\n".join(part for part in (text, config_text) if part)
        return {"exists": exists, "running": running, "detail": detail, "binary_path": binary_path}

    def _query_cloudflared_process_command_lines(self) -> list[str]:
        if os.name != "nt":
            return []
        result = self._run_remote_check_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Select-Object -ExpandProperty CommandLine",
            ],
            timeout=10.0,
        )
        if result is None or result.returncode != 0:
            return []
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def _cloudflared_process_targets_port(self, websocket_port: int) -> bool:
        desired_url = f"http://127.0.0.1:{int(websocket_port)}"
        for command_line in self._query_cloudflared_process_command_lines():
            rewritten, changed = self._replace_cloudflared_service_url(command_line, websocket_port)
            if rewritten is not None and not changed:
                return True
            if desired_url in command_line:
                return True
        return False

    @staticmethod
    def _replace_cloudflared_service_url(bin_path: str, websocket_port: int) -> tuple[str, bool] | tuple[None, bool]:
        match = re.search(r"--url\s+(?:\"([^\"]*)\"|'([^']*)'|([^\s]+))", bin_path, flags=re.IGNORECASE)
        if not match:
            return None, False
        current_url = match.group(1) or match.group(2) or match.group(3)
        desired_url = f"http://127.0.0.1:{int(websocket_port)}"
        same_target = False
        try:
            parsed = urlsplit(current_url)
            same_target = parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == int(websocket_port)
        except Exception:
            try:
                same_target = current_url.strip() == desired_url
            except Exception:
                same_target = False
        if same_target:
            return bin_path, False
        quote = "\"" if match.group(1) is not None else ("'" if match.group(2) is not None else "")
        replacement = f"--url {quote}{desired_url}{quote}"
        return f"{bin_path[:match.start()]}{replacement}{bin_path[match.end():]}", True

    def _set_cloudflared_service_bin_path(self, bin_path: str) -> bool:
        if not bin_path:
            return False
        command = f"binPath= {bin_path}"
        result = self._run_remote_check_command(["sc.exe", "config", "cloudflared", command], timeout=15.0)
        return bool(result and result.returncode == 0)

    def _ensure_cloudflared_service_url(self, websocket_port: int) -> tuple[bool, bool]:
        state = self._query_cloudflared_service()
        if not state.get("exists"):
            return self._cloudflared_process_targets_port(websocket_port), False
        current_bin_path = str(state.get("binary_path") or "").strip()
        if not current_bin_path:
            return self._cloudflared_process_targets_port(websocket_port), False
        rewritten, changed = self._replace_cloudflared_service_url(current_bin_path, websocket_port)
        if rewritten is None:
            return self._cloudflared_process_targets_port(websocket_port), False
        if not changed:
            return True, False
        return self._set_cloudflared_service_bin_path(rewritten), True

    def _start_cloudflared_service(self) -> bool:
        state = self._query_cloudflared_service()
        if not state["exists"]:
            return False
        if state["running"]:
            return True
        deadline = time.time() + REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while time.time() < deadline:
            result = self._run_remote_check_command(["sc.exe", "start", "cloudflared"], timeout=15.0)
            detail = ""
            if result is not None:
                detail = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
            if result is not None and (result.returncode == 0 or "already running" in detail):
                break
            time.sleep(0.2)
        deadline = time.time() + REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self._query_cloudflared_service()["running"]:
                return True
            time.sleep(0.2)
        return self._query_cloudflared_service()["running"]

    def _kill_cloudflared_processes(self) -> None:
        self._run_remote_check_command(["taskkill", "/F", "/IM", "cloudflared.exe"], timeout=15.0)

    def _restart_cloudflared_service(self) -> bool:
        state = self._query_cloudflared_service()
        if not state["exists"]:
            return False
        self._run_remote_check_command(["sc.exe", "stop", "cloudflared"], timeout=15.0)
        deadline = time.time() + REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while time.time() < deadline:
            if not self._query_cloudflared_service()["running"]:
                break
            time.sleep(0.2)
        if self._query_cloudflared_service()["running"]:
            self._kill_cloudflared_processes()
            deadline = time.time() + REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
            while time.time() < deadline:
                if not self._query_cloudflared_service()["running"]:
                    break
                time.sleep(0.2)
        return self._start_cloudflared_service()

    def _managed_cloudflared_command_line(self, websocket_port: int) -> str:
        state = self._query_cloudflared_service()
        command_line = str(state.get("binary_path") or "").strip()
        if not command_line:
            for candidate in self._query_cloudflared_process_command_lines():
                if " tunnel run " in f" {candidate} ".lower():
                    command_line = candidate
                    break
        if not command_line:
            return ""
        rewritten, _changed = self._replace_cloudflared_service_url(command_line, websocket_port)
        if rewritten is not None:
            return rewritten
        return f'{command_line} --url http://127.0.0.1:{int(websocket_port)}'

    def _start_managed_cloudflared_process(self, websocket_port: int) -> bool:
        existing = getattr(self, "_managed_cloudflared_process", None)
        if existing is not None and existing.poll() is None:
            return True
        command_line = self._managed_cloudflared_command_line(websocket_port)
        if not command_line:
            return False
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._managed_cloudflared_process = subprocess.Popen(command_line, **popen_kwargs)
            return True
        except Exception:
            self._managed_cloudflared_process = None
            return False

    def _stop_managed_cloudflared_process(self) -> None:
        process = getattr(self, "_managed_cloudflared_process", None)
        self._managed_cloudflared_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass

    def _remote_runtime_probe_host(self) -> str:
        host = str(self.remote_control_host or "127.0.0.1").strip() or "127.0.0.1"
        return "127.0.0.1" if host in {"0.0.0.0", "::", "::0"} else host

    def _remote_local_listener_ready(self, port: int) -> bool:
        try:
            with socket.create_connection((self._remote_runtime_probe_host(), int(port)), timeout=2.0):
                return True
        except Exception:
            return False

    def _verify_remote_local_health(self, token: str, port: int) -> tuple[bool, str]:
        runtime = getattr(self, "remote_nats_runtime_status", {}) or {}
        websocket_url = str(runtime.get("websocket_url") or "").strip()
        if not websocket_url:
            return False, "本地 NATS websocket 地址未配置。"
        parsed = urlsplit(websocket_url)
        probe_host = parsed.hostname or self._remote_runtime_probe_host()
        probe_port = int(parsed.port or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
        probe_url = urlunsplit((parsed.scheme or "ws", f"{probe_host}:{probe_port}", parsed.path or "/nats", "", ""))
        return self._verify_remote_public_ws(probe_url)

    async def _verify_remote_public_ws_async(self, url: str) -> tuple[bool, str]:
        timeout = ClientTimeout(total=REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS)
        path = (urlsplit(str(url or "")).path or "").strip().lower()
        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.ws_connect(url, heartbeat=REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS) as ws:
                    if path.endswith("/nats"):
                        message = await ws.receive(timeout=REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS)
                        info_text = ""
                        if message.type == WSMsgType.TEXT:
                            info_text = str(message.data or "")
                        elif message.type == WSMsgType.BINARY:
                            try:
                                info_text = bytes(message.data or b"").decode("utf-8", errors="replace")
                            except Exception:
                                info_text = ""
                        ok = info_text.startswith("INFO ")
                        return ok, ("" if ok else f"公网 NATS 隧道响应异常：{message.type} {message.data!r}")
                    message = await ws.receive(timeout=REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS)
        except Exception as exc:
            return False, f"公网隧道握手失败：{exc}"
        if message.type != WSMsgType.TEXT:
            return False, f"公网隧道返回了非文本消息：{message.type}"
        try:
            payload = json.loads(message.data)
        except Exception as exc:
            return False, f"公网隧道返回了无效响应：{exc}"
        ok = payload.get("type") == "connected" and bool(payload.get("ok")) and bool((payload.get("body") or {}).get("accepted"))
        return ok, ("" if ok else f"公网隧道响应异常：{payload}")

    def _verify_remote_public_ws(self, url: str) -> tuple[bool, str]:
        return asyncio.run(self._verify_remote_public_ws_async(url))

    def _verify_remote_public_ws_with_retries(self, url: str) -> tuple[bool, str]:
        last_detail = ""
        attempts = max(1, int(REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS * 2))
        for attempt_idx in range(attempts):
            ok, detail = self._verify_remote_public_ws(url)
            if ok:
                return True, ""
            last_detail = detail
            if attempt_idx + 1 < attempts:
                time.sleep(0.5)
        return False, last_detail

    def _ensure_remote_nats_startup_connectivity(self, *, token: str, published_url: str) -> None:
        runtime = self._remote_runtime_config()
        if not runtime["fixed_domain_mode"] or getattr(self, "_remote_nats_transport", None) is None:
            return
        self._set_remote_runtime_status(
            local_listener_ready=False,
            public_ws_ready=False,
            last_remote_error="",
            published_url=published_url,
        )
        websocket_url = str((getattr(self, "remote_nats_runtime_status", {}) or {}).get("websocket_url") or "").strip()
        parsed = urlsplit(websocket_url)
        port = int(parsed.port or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
        if port <= 0:
            raise RuntimeError(self._format_remote_startup_error("远程控制本地监听端口无效。"))
        if not self._remote_local_listener_ready(port):
            raise RuntimeError(self._format_remote_startup_error(f"远程控制端口 {port} 未正常监听。"))
        self._set_remote_runtime_status(local_listener_ready=True, published_url=published_url)
        local_ok, local_detail = self._verify_remote_local_health(token, port)
        if not local_ok:
            raise RuntimeError(self._format_remote_startup_error(local_detail or "远程控制本地健康检查失败。"))
        service_configured, service_reconfigured = self._ensure_cloudflared_service_url(port)
        managed_cloudflared_started = False
        if not service_configured:
            raise RuntimeError(self._format_remote_startup_error("cloudflared 服务未安装或配置缺失。"))
        if service_reconfigured:
            if not self._restart_cloudflared_service():
                if not self._start_managed_cloudflared_process(port):
                    raise RuntimeError(self._format_remote_startup_error("cloudflared 已更新映射但重启失败。"))
                managed_cloudflared_started = True
        elif not self._start_cloudflared_service():
            if not self._start_managed_cloudflared_process(port):
                raise RuntimeError(self._format_remote_startup_error("cloudflared 服务未安装或无法启动。"))
            managed_cloudflared_started = True
        if managed_cloudflared_started:
            public_ok, public_detail = self._verify_remote_public_ws_with_retries(published_url)
        else:
            public_ok, public_detail = self._verify_remote_public_ws(published_url)
        if public_ok:
            self._set_remote_runtime_status(
                local_listener_ready=True,
                public_ws_ready=True,
                last_remote_error="",
                published_url=published_url,
            )
            if managed_cloudflared_started or getattr(self, "_managed_cloudflared_process", None) is not None:
                self._set_status_text_safe(
                    f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}；cloudflared 进程与 rc.tingyou.cc 已验证"
                )
            else:
                self._set_status_text_safe(
                    f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}；cloudflared 与 rc.tingyou.cc 已验证"
                )
            return
        if not self._restart_cloudflared_service():
            if not self._start_managed_cloudflared_process(port):
                raise RuntimeError(self._format_remote_startup_error(public_detail or "cloudflared 重启失败。"))
            managed_cloudflared_started = True
        public_ok, public_detail = self._verify_remote_public_ws(published_url)
        if not public_ok:
            raise RuntimeError(self._format_remote_startup_error(public_detail or "rc.tingyou.cc 公网隧道验证失败。"))
        self._set_remote_runtime_status(
            local_listener_ready=True,
            public_ws_ready=True,
            last_remote_error="",
            published_url=published_url,
        )
        if managed_cloudflared_started or getattr(self, "_managed_cloudflared_process", None) is not None:
            self._set_status_text_safe(
                f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}；cloudflared 进程已恢复 rc.tingyou.cc 连接"
            )
        else:
            self._set_status_text_safe(
                f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}；cloudflared 已重启并恢复 rc.tingyou.cc 连接"
            )

    def _start_remote_nats_runtime_if_configured(self, *, ensure_connectivity: bool = False) -> None:
        token = self._read_remote_control_token() or self.remote_control_token
        if not token or getattr(self, "_remote_nats_transport", None) is not None:
            return
        runtime = self._remote_runtime_config()
        host = runtime["host"]
        port = runtime["port"]
        published_url = f"{runtime['published_base']}?token={token}"
        self._set_remote_runtime_status(
            local_listener_ready=False,
            public_ws_ready=False,
            last_remote_error="",
            published_url=published_url,
        )
        attempts = 2 if (ensure_connectivity and runtime["fixed_domain_mode"]) else 1
        last_error = ""
        for attempt_idx in range(attempts):
            self._stop_remote_servers()
            self._start_remote_servers(token=token, host=host, port=port)
            runtime_status = dict(getattr(self, "remote_nats_runtime_status", {}) or {})
            if getattr(self, "_remote_nats_transport", None) is None or not runtime_status.get("enabled"):
                last_error = str(runtime_status.get("last_error") or "").strip()
                if not last_error:
                    last_error = "远程 NATS 运行时未成功启动。"
                raise RuntimeError(last_error)
            self.remote_control_runtime_mode = "fixed_domain" if runtime["fixed_domain_mode"] else "local"
            self.remote_control_runtime_bind = (
                getattr(self, "remote_nats_runtime_status", {}).get("websocket_url")
                or f"ws://127.0.0.1:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats"
            )
            self._set_remote_runtime_status(
                local_listener_ready=True,
                public_ws_ready=not runtime["fixed_domain_mode"],
                last_remote_error="",
                published_url=published_url,
            )
            if not ensure_connectivity:
                if runtime["fixed_domain_mode"]:
                    self._set_status_text_safe(
                        f"远程 NATS 本地监听已启动：监听 {self.remote_control_runtime_bind}；等待公网验证 {published_url}"
                    )
                else:
                    self._set_status_text_safe(
                        f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}"
                    )
                return
            try:
                if runtime["fixed_domain_mode"]:
                    self._ensure_remote_nats_startup_connectivity(token=token, published_url=published_url)
                    if self.remote_control_runtime_status.get("public_ws_ready"):
                        self._set_remote_runtime_status(
                            local_listener_ready=True,
                            public_ws_ready=True,
                            last_remote_error="",
                            published_url=published_url,
                        )
                else:
                    self._set_status_text_safe(
                        f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}"
                    )
                if not runtime["fixed_domain_mode"]:
                    self._set_remote_runtime_status(
                        local_listener_ready=True,
                        public_ws_ready=True,
                        last_remote_error="",
                        published_url=published_url,
                    )
                return
            except Exception as exc:
                last_error = str(exc)
                self._set_remote_runtime_status(
                    local_listener_ready=True,
                    public_ws_ready=False,
                    last_remote_error=last_error,
                    published_url=published_url,
                )
                if attempt_idx + 1 >= attempts:
                    self._set_status_text_safe(f"远程 NATS 启动失败：{last_error}")
                    raise

    def _start_claudecode_remote_nats_runtime_if_configured(self) -> None:
        return

    def _on_char_hook(self, event):
        key = event.GetKeyCode()
        ctrl_down = self._event_control_down(event)
        alt_down = self._event_alt_down(event)
        notes_has_focus = bool(
            getattr(self, "notes_notebook_list", None)
            and (
                self.notes_notebook_list.HasFocus()
                or self.notes_entry_list.HasFocus()
                or self.notes_editor.HasFocus()
            )
        )
        if key == wx.WXK_ALT and alt_down and not ctrl_down:
            self._arm_tools_menu_open()
            return
        self._suppress_tools_menu_open()
        if (
            ctrl_down
            and not alt_down
            and key in (ord("C"), ord("c"))
            and hasattr(self, "answer_list")
            and self.answer_list.HasFocus()
            and self._copy_selected_answer_to_clipboard()
        ):
            return
        if self._handle_window_focus_shortcut(key, alt_down, ctrl_down):
            return
        if (
            key == wx.WXK_MENU
            and notes_has_focus
        ):
            self._show_notes_menu()
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and notes_has_focus
            and not ctrl_down
            and not alt_down
        ):
            self._on_notes_key_down(event)
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.history_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            if self._activate_selected_history():
                return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.answer_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            if self._try_open_selected_answer_detail():
                return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and hasattr(self, "execution_list")
            and self.execution_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            self._try_open_selected_execution_detail()
            return
        if key == wx.WXK_F1 and not ctrl_down and not alt_down:
            self._toggle_detail_panel_mode(focus_detail=True)
            return
        if self._is_continue_shortcut(key, alt_down):
            self._submit_question("继续", source="local")
            return
        if ctrl_down and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            direction = -1 if key == wx.WXK_LEFT else 1
            if self._navigate_history_chats(direction):
                return
        if self._is_send_shortcut(key, ctrl_down, alt_down):
            if self.input_edit.HasFocus():
                event.Skip()
                return
            self._trigger_send()
            return
        if self._is_new_chat_shortcut(key, alt_down):
            self._trigger_new_chat()
            return
        event.Skip()

    def _event_control_down(self, event) -> bool:
        ctrl_down = getattr(event, "ControlDown", None)
        if callable(ctrl_down) and bool(ctrl_down()):
            return True
        try:
            return bool(wx.GetKeyState(wx.WXK_CONTROL))
        except Exception:
            return False

    def _event_alt_down(self, event) -> bool:
        alt_down = getattr(event, "AltDown", None)
        if callable(alt_down) and bool(alt_down()):
            return True
        try:
            return bool(wx.GetKeyState(wx.WXK_ALT))
        except Exception:
            return False

    def _is_real_escape_keydown(self, event) -> bool:
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        ctrl_pressed = bool(ctrl_down()) if callable(ctrl_down) else False
        alt_pressed = bool(alt_down()) if callable(alt_down) else False
        if ctrl_pressed or alt_pressed:
            return False
        shift_down = getattr(event, "ShiftDown", None)
        if callable(shift_down) and shift_down():
            return False
        if event.GetKeyCode() != wx.WXK_ESCAPE:
            return False
        get_raw = getattr(event, "GetRawKeyCode", None)
        if not callable(get_raw):
            return False
        try:
            raw = int(get_raw())
        except Exception:
            return False
        return raw == 27

    def _on_any_key_down_escape_minimize(self, event) -> bool:
        if getattr(self, "notes_editor", None):
            try:
                if getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_edit":
                    return False
            except Exception:
                pass
        if self._is_real_escape_keydown(event):
            self._minimize_to_tray()
            return True
        return False

    def _on_frame_key_down(self, event) -> None:
        if self._on_any_key_down_escape_minimize(event):
            return
        event.Skip()

    def _on_input_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        key = event.GetKeyCode()
        if key != wx.WXK_ALT:
            self._suppress_tools_menu_open()
        if event.ControlDown() and key in (ord("V"), ord("v")) and self.input_edit.HasFocus():
            if self._try_paste_clipboard_attachments_to_input():
                return
        if self._is_send_shortcut(key, event.ControlDown(), event.AltDown()) and self._has_input_ime_candidates():
            event.Skip()
            return
        if self._is_send_shortcut(key, event.ControlDown(), event.AltDown()):
            self._trigger_send()
            return
        if self._is_new_chat_shortcut(key, event.AltDown()):
            self._trigger_new_chat()
            return
        event.Skip()

    def _is_continue_shortcut(self, key, alt):
        return alt and key in (ord("C"), ord("c"))

    def _on_input_key_up(self, event):
        if event.GetKeyCode() == wx.WXK_ALT:
            if self._handle_alt_key_up():
                return
        event.Skip()

    def _has_input_ime_candidates(self) -> bool:
        hwnd = self.input_edit.GetHandle() if self.input_edit else 0
        if not hwnd:
            return False
        try:
            imm32 = ctypes.windll.imm32
            himc = imm32.ImmGetContext(wintypes.HWND(hwnd))
            if not himc:
                return False
            try:
                # Only when IME candidate list is present should Enter be
                # handled by IME (commit/select candidate) instead of send.
                list_count = wintypes.DWORD(0)
                buf_len = wintypes.DWORD(0)
                if not imm32.ImmGetCandidateListCountW(himc, ctypes.byref(list_count), ctypes.byref(buf_len)):
                    return False
                if int(list_count.value) <= 0 or int(buf_len.value) <= 0:
                    return False
                size = int(imm32.ImmGetCandidateListW(himc, 0, None, 0))
                if size <= 0:
                    return False
                buf = (ctypes.c_byte * size)()
                copied = int(imm32.ImmGetCandidateListW(himc, 0, ctypes.byref(buf), size))
                if copied <= 0:
                    return False

                class _CandidateHeader(ctypes.Structure):
                    _fields_ = [
                        ("dwSize", wintypes.DWORD),
                        ("dwStyle", wintypes.DWORD),
                        ("dwCount", wintypes.DWORD),
                        ("dwSelection", wintypes.DWORD),
                        ("dwPageStart", wintypes.DWORD),
                        ("dwPageSize", wintypes.DWORD),
                    ]

                if copied < ctypes.sizeof(_CandidateHeader):
                    return False
                header = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(_CandidateHeader)).contents
                return int(header.dwCount) > 0
            finally:
                imm32.ImmReleaseContext(wintypes.HWND(hwnd), himc)
        except Exception:
            return False

    def _on_global_ctrl_keyup(self, combo_used: bool, side: str) -> None:
        self._voice_input.on_ctrl_keyup(combo_used=combo_used, side=side)

    def _on_global_ctrl_hook_error(self, message: str) -> None:
        self.global_ctrl_backend_status = getattr(self._global_ctrl_hook, "backend_state", "unknown")
        self.SetStatusText(message)

    def _global_chat_navigation_target_state(self) -> tuple[bool, dict]:
        try:
            frame_hwnd = int(self.GetHandle() or 0)
        except Exception:
            frame_hwnd = 0
        fg_hwnd = 0
        root_hwnd = 0
        if frame_hwnd > 0:
            try:
                user32 = ctypes.windll.user32
                fg_hwnd = int(user32.GetForegroundWindow() or 0)
                ga_root = 2
                root_hwnd = int(user32.GetAncestor(wintypes.HWND(fg_hwnd), ga_root) or 0) if fg_hwnd else 0
            except Exception:
                fg_hwnd = 0
                root_hwnd = 0
        details = {
            "frame_hwnd": frame_hwnd,
            "fg_hwnd": fg_hwnd,
            "root_hwnd": root_hwnd,
        }
        return (frame_hwnd > 0 and fg_hwnd > 0 and root_hwnd == frame_hwnd), details

    def _log_ctrl_navigation_debug(self, direction: str, *, reason: str = "", result: str = "", details: dict | None = None) -> None:
        path = self.app_data_dir / "ctrl_navigation_debug.log"
        detail_text = ""
        if details:
            detail_text = " ".join(f"{key}={value}" for key, value in details.items())
        parts = [f"direction={direction}"]
        if reason:
            parts.append(f"reason={reason}")
        if result:
            parts.append(f"result={result}")
        if detail_text:
            parts.append(detail_text)
        line = " ".join(parts).strip()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            return

    def _on_global_ctrl_arrow(self, direction: str) -> None:
        direction_key = str(direction or "").strip().lower()
        step = -1 if direction_key == "left" else 1
        active, details = self._global_chat_navigation_target_state()
        if not active:
            self._log_ctrl_navigation_debug(direction_key, reason="inactive_target", details=details)
            self.SetStatusText("Ctrl+左右未生效：当前前台焦点不属于本程序")
            return
        navigated = bool(self._navigate_history_chats(step))
        self._log_ctrl_navigation_debug(
            direction_key,
            result="navigated" if navigated else "no_target",
            details=details,
        )

    def _is_ui_alive(self) -> bool:
        app = wx.GetApp()
        if app is None:
            return False
        try:
            if hasattr(self, "IsBeingDeleted") and self.IsBeingDeleted():
                return False
            if hasattr(self, "GetHandle") and not self.GetHandle():
                return False
        except Exception:
            return False
        return True

    def _call_after_if_alive(self, func, *args, **kwargs) -> bool:
        return wx_call_after_if_alive(func, *args, **kwargs)

    def _call_later_if_alive(self, delay_ms: int, func, *args, **kwargs):
        return wx_call_later_if_alive(delay_ms, func, *args, **kwargs)

    def _is_send_shortcut(self, key, ctrl, alt):
        return ((not ctrl) and (not alt) and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)) or (alt and key in (ord("S"), ord("s")))

    def _is_new_chat_shortcut(self, key, alt):
        return alt and key in (ord("N"), ord("n"))

    def _ensure_tray_icon(self):
        if self._tray_icon:
            return
        frame = self

        class _TrayIcon(wx.adv.TaskBarIcon):
            def __init__(self):
                super().__init__()
                icon = wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))
                self.SetIcon(icon, APP_WINDOW_TITLE)
                self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_activate)
                self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, self._on_activate)

            def _on_activate(self, _event):
                frame._restore_from_tray()

        self._tray_icon = _TrayIcon()

    def _minimize_to_tray(self):
        self._ensure_tray_icon()
        self.Hide()
        self._is_in_tray = True

    def _restore_from_tray(self):
        self.Show()
        self.Iconize(False)
        self.Raise()
        self._is_in_tray = False
        self.input_edit.SetFocus()

    def _restore_or_raise(self):
        if self._is_in_tray or (not self.IsShown()):
            self._restore_from_tray()
            return
        self.Iconize(False)
        self.Raise()
        try:
            self.RequestUserAttention(wx.USER_ATTENTION_INFO)
        except Exception:
            pass

    def _register_global_hotkey(self):
        try:
            if not self._show_hotkey_registered:
                self._show_hotkey_registered = bool(self.RegisterHotKey(HOTKEY_ID_SHOW, wx.MOD_CONTROL, wx.WXK_F12))
        except Exception:
            self._show_hotkey_registered = False
        realtime_call_hotkeys = [
            (HOTKEY_ID_REALTIME_CALL, VK_OEM_5),
            (HOTKEY_ID_REALTIME_CALL_ALT, self._resolve_backslash_hotkey_vk()),
            (HOTKEY_ID_REALTIME_CALL_ALT2, VK_OEM_102),
        ]
        registered_ids = set()
        seen_vks = set()
        for hotkey_id, vk_code in realtime_call_hotkeys:
            if not isinstance(vk_code, int) or vk_code <= 0 or vk_code in seen_vks:
                continue
            seen_vks.add(vk_code)
            try:
                if self.RegisterHotKey(hotkey_id, wx.MOD_CONTROL, vk_code):
                    registered_ids.add(hotkey_id)
            except Exception:
                pass
        self._realtime_call_hotkey_registered_ids = registered_ids

    def _resolve_backslash_hotkey_vk(self):
        try:
            mapped = int(ctypes.windll.user32.VkKeyScanW(ord("\\")))
        except Exception:
            return VK_OEM_5
        if mapped < 0:
            return VK_OEM_5
        vk_code = mapped & 0xFF
        return vk_code or VK_OEM_5

    def _unregister_global_hotkey(self):
        try:
            if self._show_hotkey_registered:
                self.UnregisterHotKey(HOTKEY_ID_SHOW)
        except Exception:
            pass
        self._show_hotkey_registered = False
        for hotkey_id in tuple(self._realtime_call_hotkey_registered_ids):
            try:
                self.UnregisterHotKey(hotkey_id)
            except Exception:
                pass
        self._realtime_call_hotkey_registered_ids = set()

    def _on_global_hotkey(self, event):
        hotkey_id = event.GetId()
        if hotkey_id == HOTKEY_ID_SHOW:
            self._restore_or_raise()
            return
        if hotkey_id in {
            HOTKEY_ID_REALTIME_CALL,
            HOTKEY_ID_REALTIME_CALL_ALT,
            HOTKEY_ID_REALTIME_CALL_ALT2,
        }:
            self._toggle_realtime_call()

    def _on_show_sync_tray_state(self, event):
        # When the window is restored externally (e.g. second EXE launch), keep tray state in sync.
        if event.IsShown():
            self._is_in_tray = False
        event.Skip()

    def _trigger_send(self):
        if self.send_button.IsEnabled():
            self._post_send_click()

    def _post_send_click(self):
        if not self.send_button.IsEnabled():
            return
        ev = wx.CommandEvent(wx.wxEVT_BUTTON, self.send_button.GetId())
        ev.SetEventObject(self.send_button)
        wx.PostEvent(self.send_button, ev)

    def _trigger_new_chat(self):
        if self.new_chat_button.IsEnabled():
            ev = wx.CommandEvent(wx.wxEVT_BUTTON, self.new_chat_button.GetId())
            ev.SetEventObject(self.new_chat_button)
            wx.PostEvent(self.new_chat_button, ev)

    def _on_send_clicked(self, _):
        q = self.input_edit.GetValue().strip()
        ok, message = self._submit_question(q, source="local")
        if not ok and message:
            wx.MessageBox(message, "提示", wx.OK | wx.ICON_WARNING)
            return

    def _submit_question(self, question: str, source: str = "local", model: str | None = None, chat_id: str = "") -> tuple[bool, str]:
        raw_question = str(question or "")
        q = self._strip_attachment_markers(raw_question)
        requested_attachments = list(getattr(self, "_pending_input_attachments", []) or [])
        success_attachments, failed_attachments = self._normalize_outgoing_attachments(requested_attachments)
        outgoing_attachments = success_attachments + failed_attachments
        display_question = q
        if not q and not outgoing_attachments:
            return False, "请输入问题，输入框内容为空"

        # 检查是否有活跃的 Claude Code 客户端在等待输入
        if hasattr(self, '_active_claudecode_client') and self._active_claudecode_client is not None:
            # 将消息发送到 Claude Code 的 stdin 队列
            self._active_claudecode_client.send_user_input(q)
            # 清空输入框
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            return True, ""

        resolved_model = str(model or self._resolve_current_model() or "").strip()
        resolved_model = model_id_from_display_name(resolved_model)
        if resolved_model not in MODEL_IDS and not (is_codex_model(resolved_model) or is_claudecode_model(resolved_model) or is_openclaw_model(resolved_model)):
            resolved_model = DEFAULT_MODEL_ID
        if (not chat_id) and self.view_mode == "history":
            selected_history_id = str(self.view_history_id or "").strip()
            if selected_history_id and selected_history_id not in {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}:
                if not self._switch_current_chat(selected_history_id):
                    return False, "载入历史聊天失败"
        if chat_id:
            if chat_id != self.active_chat_id:
                if self._find_archived_chat(chat_id):
                    self._switch_current_chat(chat_id)
                else:
                    self.active_chat_id = chat_id
                    self.current_chat_id = chat_id
        if not self.active_session_started_at:
            self.active_session_started_at = time.time()
        if not self.active_chat_id:
            self._ensure_active_chat_id()
        self.selected_model = resolved_model
        self.model_combo.SetValue(model_display_name(resolved_model))
        self._current_chat_state["id"] = self.active_chat_id or self.current_chat_id or ""
        self._current_chat_state["model"] = resolved_model
        self._current_chat_state["turns"] = self.active_session_turns
        self._current_chat_state.setdefault("title", self._next_default_chat_title())
        self._current_chat_state.setdefault("title_manual", False)
        self._current_chat_state.setdefault("title_source", "default")
        self._current_chat_state.setdefault("title_updated_at", time.time())
        self._current_chat_state.setdefault("title_revision", 1)
        worker_question = q
        if success_attachments and not is_codex_model(resolved_model):
            attachment_context = self._build_cli_attachment_context(success_attachments)
            worker_question = f"{q}\n\n{attachment_context}".strip() if q else attachment_context
        codex_local_command = self._parse_codex_local_command(q) if is_codex_model(resolved_model) and not outgoing_attachments else None
        if codex_local_command:
            command_name = str(codex_local_command.get("name") or "").strip()
            command_args = str(codex_local_command.get("args") or "").strip()
            now = time.time()
            turn_idx = len(self.active_session_turns)
            turn = {
                "question": display_question,
                "answer_md": REQUESTING_TEXT,
                "model": resolved_model,
                "created_at": now,
                "local_command": command_name,
                "local_command_args": command_args,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = turn_idx
            self._mark_chat_turns_dirty(start_index=turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._current_chat_state["updated_at"] = now
            if len([item for item in self.active_session_turns if str((item or {}).get("question") or "").strip()]) == 1:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
            self._mark_turn_request_pending(turn, resolved_model, command_name)
            self.is_running = True
            self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._pending_input_attachments = []
            self._defer_chat_state_save()
            self._play_send_sound()
            self.SetStatusText(f"正在执行 Codex 命令：/{command_name}")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            if self._detail_panel_mode() == "execution":
                self._render_answer_list_compat(refresh_execution=False)
            else:
                self._render_answer_list()
            if source == "local":
                self._start_codex_local_command_worker_for_turn(
                    chat_id or self.active_chat_id or self.current_chat_id or "",
                    turn_idx,
                    command_name,
                    command_args,
                    resolved_model,
                )
            return True, ""
        if (not success_attachments) and (not q):
            now = time.time()
            turn = {
                "question": display_question,
                "answer_md": "",
                "model": resolved_model,
                "created_at": now,
                "attachments": outgoing_attachments,
                "suppress_empty_answer_row": True,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = len(self.active_session_turns) - 1
            self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._current_chat_state["updated_at"] = now
            self._pending_input_attachments = []
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._defer_chat_state_save()
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            if self._detail_panel_mode() == "execution":
                self._render_answer_list_compat(refresh_execution=False)
            else:
                self._render_answer_list()
            return True, ""
        if is_openclaw_model(resolved_model):
            self._ensure_active_chat_id()
            openclaw_session_id = self._openclaw_session_id_for_active_chat()
            now = time.time()
            turn = {
                "question": display_question,
                "answer_md": "",
                "model": resolved_model,
                "created_at": now,
                "origin": "local" if source == "local" else source,
                "question_origin": "local" if source == "local" else source,
                "attachments": outgoing_attachments,
                "openclaw_session_id": openclaw_session_id,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = len(self.active_session_turns) - 1
            self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._current_chat_state["updated_at"] = now
            if len([item for item in self.active_session_turns if str((item or {}).get("question") or "").strip()]) == 1:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
            self._mark_turn_request_pending(turn, resolved_model, worker_question)
            self.is_running = True
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._pending_input_attachments = []
            self._defer_chat_state_save()
            self._play_send_sound()
            self.SetStatusText("已发送")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            self._refresh_openclaw_sync_lifecycle()
            threading.Thread(
                target=self._worker,
                args=("", self.active_turn_idx, worker_question, resolved_model, False, chat_id or self.active_chat_id, openclaw_session_id),
                daemon=True,
            ).start()
            return True, ""

        turn_idx = len(self.active_session_turns)
        now = time.time()
        turn = {
            "question": display_question,
            "answer_md": REQUESTING_TEXT,
            "model": resolved_model,
            "created_at": now,
            "attachments": outgoing_attachments,
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = turn_idx
        self._mark_chat_turns_dirty(start_index=turn_idx)
        self._reset_answer_visible_row_limit()
        self._reset_current_turn_execution_view()
        self._current_chat_state["updated_at"] = now
        if len([item for item in self.active_session_turns if str((item or {}).get("question") or "").strip()]) == 1:
            self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
        self._mark_turn_request_pending(turn, resolved_model, worker_question)
        if is_claudecode_model(resolved_model):
            self.active_claudecode_session_id = str(self.active_claudecode_session_id or "").strip()
        self.is_running = True
        self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
        self.input_edit.SetValue("")
        self.input_edit.SetFocus()
        self._pending_input_attachments = []
        self._defer_chat_state_save()
        self._play_send_sound()
        self.SetStatusText("已发送")
        self.view_mode = "active"
        self.view_history_id = None
        self._active_answer_row_index = -1
        self._refresh_openclaw_sync_lifecycle()
        if self._detail_panel_mode() == "execution":
            self._render_answer_list_compat(refresh_execution=False)
        else:
            self._render_answer_list()
        if is_codex_model(resolved_model) and source == "local":
            self._start_codex_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, resolved_model)
        elif is_claudecode_model(resolved_model) and source == "local":
            self._start_claudecode_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, worker_question, self.active_claudecode_session_id)
        else:
            t = threading.Thread(target=self._worker, args=(os.getenv("OPENROUTER_API_KEY", "").strip(), turn_idx, worker_question, resolved_model, False, chat_id or self.active_chat_id or self.current_chat_id or ""), daemon=True)
            t.start()
        return True, ""

    def _on_voice_state(self, text: str):
        if getattr(self._voice_input, "state", "") == "recording":
            self._play_voice_begin_sound()

    def _on_voice_error(self, msg: str):
        self._play_voice_wrong_sound()

    def _on_realtime_call_status(self, message: str):
        if message:
            self.SetStatusText(message)

    def _on_realtime_call_error(self, message: str):
        self.SetStatusText(message)
        self._play_voice_wrong_sound()

    def _on_realtime_call_active_changed(self, active: bool):
        if active:
            self.SetStatusText("实时语音通话中")

    def _toggle_realtime_call(self):
        action = self._realtime_call.toggle()
        if action == "start":
            self._play_voice_begin_sound()
            return
        if action == "stop":
            self._play_voice_end_sound()

    def _on_open_realtime_call_settings(self, _event):
        dlg = RealtimeCallSettingsDialog(
            self,
            role=self.realtime_call_role,
            speech_rate=self.realtime_call_speech_rate,
        )
        if dlg.ShowModal() == wx.ID_OK:
            settings = dlg.get_settings()
            self._apply_realtime_call_settings(settings)
        dlg.Destroy()

    def _apply_realtime_call_settings(self, settings: RealtimeCallSettings):
        normalized = settings.normalized()
        self.realtime_call_role = normalized.role
        self.realtime_call_speech_rate = normalized.speech_rate
        message = self._realtime_call.update_settings(normalized)
        wx_call_after_if_alive(self._realtime_call.prepare)
        self._save_state()
        self.SetStatusText(message)

    def _extract_committed_char(self, event):
        if event.ControlDown() or event.AltDown():
            return ""
        key = event.GetUnicodeKey() if hasattr(event, "GetUnicodeKey") else event.GetKeyCode()
        if key in (
            wx.WXK_NONE,
            wx.WXK_RETURN,
            wx.WXK_NUMPAD_ENTER,
            wx.WXK_TAB,
            wx.WXK_ESCAPE,
            wx.WXK_UP,
            wx.WXK_DOWN,
            wx.WXK_LEFT,
            wx.WXK_RIGHT,
            wx.WXK_HOME,
            wx.WXK_END,
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
            wx.WXK_DELETE,
            wx.WXK_BACK,
        ):
            return ""
        if wx.WXK_F1 <= key <= wx.WXK_F24:
            return ""
        if key < 32:
            return ""
        try:
            ch = chr(key)
        except Exception:
            return ""
        if not ch.strip():
            return ""
        # Ignore ASCII alnum process keys (e.g. pinyin composition: h a o d e).
        if ch.isascii() and ch.isalnum():
            return ""
        return ch

    def _queue_answer_char_redirect(self, ch: str) -> None:
        if not ch or not self._is_ui_alive():
            return
        self._answer_committed_buffer += ch
        if self._answer_redirect_timer and self._answer_redirect_timer.IsRunning():
            self._answer_redirect_timer.Stop()
        self._answer_redirect_timer = self._call_later_if_alive(220, self._flush_answer_committed_buffer_to_input)

    def _flush_answer_committed_buffer_to_input(self) -> None:
        text = self._answer_committed_buffer
        self._answer_committed_buffer = ""
        if not text:
            return
        self.input_edit.SetFocus()
        old = self.input_edit.GetValue()
        self.input_edit.SetValue(old + text)
        self.input_edit.SetInsertionPointEnd()

    def _append_text_to_focused_editor(self, text: str):
        if not text:
            return
        focus = wx.Window.FindFocus()
        target = focus if isinstance(focus, wx.TextCtrl) and focus.IsEditable() else self.input_edit
        target.SetFocus()
        try:
            target.SetInsertionPointEnd()
        except Exception:
            pass
        wrote = False
        writer = getattr(target, "WriteText", None)
        if callable(writer):
            try:
                writer(text)
                wrote = True
            except Exception:
                wrote = False
        if not wrote:
            old = target.GetValue()
            target.SetValue(old + text)
        try:
            target.SetInsertionPointEnd()
        except Exception:
            pass
        self._notify_accessible_text_update(target)

    def _notify_accessible_text_update(self, window: wx.Window | None) -> None:
        if window is None:
            return
        try:
            hwnd = int(window.GetHandle() or 0)
        except Exception:
            hwnd = 0
        self._notify_accessible_value_change(hwnd)

    def _notify_accessible_value_change(self, hwnd: int | None) -> None:
        try:
            hwnd = int(hwnd or 0)
        except Exception:
            hwnd = 0
        if hwnd <= 0:
            return
        try:
            user32 = ctypes.windll.user32
            user32.NotifyWinEvent(EVENT_OBJECT_VALUECHANGE, wintypes.HWND(hwnd), OBJID_CLIENT, CHILDID_SELF)
        except Exception:
            return

    def _inject_text_to_foreground_window(self, text: str) -> bool:
        if not text:
            return False
        try:
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            if not fg_hwnd:
                return False
            pid = wintypes.DWORD(0)
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid))

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            target_hwnd = fg_hwnd
            if fg_tid and user32.GetGUIThreadInfo(fg_tid, ctypes.byref(gti)):
                if gti.hwndFocus:
                    target_hwnd = gti.hwndFocus

            data = text.encode("utf-16-le")
            sent = 0
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                if unit == 0:
                    continue
                if user32.PostMessageW(target_hwnd, WM_CHAR, unit, 0):
                    sent += 1
            if sent > 0:
                self._notify_accessible_value_change(target_hwnd)
            return sent > 0
        except Exception:
            return False

    def _type_text_to_system_focus(self, text: str) -> bool:
        if not text:
            return False
        try:
            user32 = ctypes.windll.user32

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class _INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

            events = []
            for ch in text:
                cp = ord(ch)
                if cp > 0xFFFF:
                    continue
                down = INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUTUNION(
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=cp,
                            dwFlags=KEYEVENTF_UNICODE,
                            time=0,
                            dwExtraInfo=None,
                        )
                    ),
                )
                up = INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUTUNION(
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=cp,
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                            time=0,
                            dwExtraInfo=None,
                        )
                    ),
                )
                events.extend([down, up])
            if not events:
                return False

            n_inputs = len(events)
            arr_type = INPUT * n_inputs
            sent = user32.SendInput(n_inputs, arr_type(*events), ctypes.sizeof(INPUT))
            if sent != n_inputs:
                return False
            return True
        except Exception:
            return False

    def _insert_text_to_system_focus(self, text: str) -> bool:
        norm = remove_trailing_punctuation(text)
        if not norm:
            return False
        if self._inject_text_to_foreground_window(norm):
            return True
        if self._type_text_to_system_focus(norm):
            return True
        return False

    def _optimize_voice_text(self, text: str) -> str:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return sanitize_optimized_text(text)
        try:
            # 使用更短的超时时间提高响应速度
            c = ChatClient(api_key=api_key, model=VOICE_OPTIMIZE_MODEL, timeout=15)
            out = c.rewrite_text(text=text, instruction=VOICE_OPTIMIZE_PROMPT, model=VOICE_OPTIMIZE_MODEL)
            return sanitize_optimized_text(out or text)
        except Exception:
            return sanitize_optimized_text(text)

    def _on_voice_result(self, text: str, mode: str = MODE_DIRECT):
        if mode == MODE_OPTIMIZE:
            def _worker():
                optimized = self._optimize_voice_text(text)
                wx_call_after_if_alive(self._on_voice_optimized_result, optimized)

            threading.Thread(target=_worker, daemon=True).start()
            return

        self._finalize_voice_input_with_feedback(text)

    def _on_voice_optimized_result(self, text: str):
        self._finalize_voice_input_with_feedback(text)

    def _finalize_voice_input_with_feedback(self, text: str):
        # 顺序：写入焦点 -> 延迟朗读写入内容（结束音效在单击 Ctrl 停止录音时立即播放）
        focus = wx.Window.FindFocus()
        if isinstance(focus, wx.TextCtrl) and focus.IsEditable():
            self._append_text_to_focused_editor(text)
        elif self.IsActive():
            self._append_text_to_focused_editor(text)
        elif not self._insert_text_to_system_focus(text):
            self._play_voice_wrong_sound()
            return
        if self._call_later_if_alive(200, self._speak_text_via_screen_reader, text) is None:
            self._speak_text_via_screen_reader(text)

    def _on_voice_stop_recording(self):
        self._play_voice_end_sound()

    def _speak_text_via_screen_reader(self, text: str):
        content = remove_trailing_punctuation(text)
        if not content:
            self.voice_screen_reader_status = {
                "last_text": "",
                "last_success": False,
                "last_error": "empty after normalization",
            }
            return
        self.voice_screen_reader_status = {
            "last_text": content,
            "last_success": None,
            "last_error": "",
        }
        errors = []
        try:
            ok = bool(self._zdsr_tts.speak(content))
        except Exception as exc:
            ok = False
            errors.append(str(exc))
        if not ok:
            try:
                self._zdsr_tts = ZDSRTTSClient()
                ok = bool(self._zdsr_tts.speak(content))
            except Exception as exc:
                ok = False
                errors.append(str(exc))
        self.voice_screen_reader_status = {
            "last_text": content,
            "last_success": ok,
            "last_error": "" if ok else ("; ".join(err for err in errors if err) or "speak returned false"),
        }

    def _is_model_endpoint_unavailable_error(self, model: str, error_text: str) -> bool:
        checker = getattr(ChatClient, "is_no_endpoint_error", None)
        if callable(checker):
            return bool(checker(error_text, model=model))
        txt = str(error_text or "")
        return ("HTTP 404" in txt) and ("No endpoints found for" in txt) and (model in txt)

    def _candidate_fallback_models(self, failed_model: str) -> list[str]:
        candidates = []
        if failed_model.startswith("deepseek/") and failed_model != "deepseek/deepseek-r1-0528":
            candidates.append("deepseek/deepseek-r1-0528")
        if failed_model != DEFAULT_MODEL_ID:
            candidates.append(DEFAULT_MODEL_ID)
        out = []
        for m in candidates:
            if m in MODEL_IDS and m not in out:
                out.append(m)
        return out

    def _chat_target_for_request(self, chat_id: str) -> tuple[dict | None, list, bool]:
        normalized = str(chat_id or "").strip()
        current_ids = {
            str(self.active_chat_id or "").strip(),
            str(self.current_chat_id or "").strip(),
            str((self._current_chat_state or {}).get("id") or "").strip() if isinstance(getattr(self, "_current_chat_state", None), dict) else "",
            "",
        }
        if normalized in current_ids:
            if not isinstance(getattr(self, "_current_chat_state", None), dict):
                self._current_chat_state = {}
            turns = self.active_session_turns if isinstance(self.active_session_turns, list) else []
            return self._current_chat_state, turns, True
        if normalized:
            chat = self._find_archived_chat(normalized)
            if isinstance(chat, dict):
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                return chat, turns, False
        return None, [], False

    def _openclaw_session_id_for_worker_chat(self, chat_id: str, target_chat: dict | None, is_current_target: bool, explicit_session_id: str = "") -> str:
        session_id = str(explicit_session_id or "").strip()
        if session_id:
            return session_id
        if is_current_target:
            return self._ensure_active_openclaw_session_id()
        if isinstance(target_chat, dict):
            session_id = str(target_chat.get("openclaw_session_id") or "").strip()
            if not session_id:
                session_id = self._make_openclaw_session_id(chat_id)
                target_chat["openclaw_session_key"] = str(target_chat.get("openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
                target_chat["openclaw_session_id"] = session_id
            return session_id
        return ""

    def _claudecode_session_id_for_worker_chat(self, target_chat: dict | None, is_current_target: bool) -> str:
        if is_current_target:
            return str(self.active_claudecode_session_id or "").strip()
        if isinstance(target_chat, dict):
            return str(target_chat.get("claudecode_session_id") or "").strip()
        return ""

    def _sync_claudecode_session_id_for_worker_chat(self, target_chat: dict | None, is_current_target: bool, session_id: str) -> None:
        value = str(session_id or "").strip()
        if not value:
            return
        if isinstance(target_chat, dict):
            target_chat["claudecode_session_id"] = value
        if is_current_target:
            self.active_claudecode_session_id = value

    def _worker(
        self,
        api_key: str,
        turn_idx: int,
        question: str,
        model: str,
        from_recovery: bool = False,
        chat_id: str = "",
        openclaw_session_id: str = "",
    ):
        full = ""
        err = ""
        used_model = model
        fallback_msg = ""
        target_chat, target_turns, is_current_target = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
        skip_done = False
        try:
            if is_openclaw_model(model):
                session_id = self._openclaw_session_id_for_worker_chat(chat_id, target_chat, is_current_target, openclaw_session_id)
                c = OpenClawClient(model=model, cli_manager=self._cli_agent_manager)
                c.stream_chat(question, session_id=session_id)
                last_context_usage = getattr(c, "last_context_usage", None)
                if last_context_usage:
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
                full = ""
            elif is_codex_model(model):
                self._run_codex_turn_worker(chat_id, turn_idx, question, model, from_recovery=from_recovery)
                skip_done = True
                full = ""
            elif is_claudecode_model(model):
                def on_delta(d):
                    wx_call_after_if_alive(self._on_delta, turn_idx, d, chat_id)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request
                    request_msg = format_remote_user_input_request(params)
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n", chat_id)
                    return ""

                def on_approval(params: dict) -> str:
                    """处理批准请求"""
                    from claudecode_remote_protocol import format_remote_approval_request
                    request_msg = format_remote_approval_request(params)
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n", chat_id)
                    return ""

                client = ClaudeCodeClient(full_auto=True, cli_manager=self._cli_agent_manager)
                full, new_session_id = client.stream_chat(
                    question,
                    session_id=self._claudecode_session_id_for_worker_chat(target_chat, is_current_target),
                    on_delta=on_delta,
                    on_user_input=on_user_input,
                    on_approval=on_approval
                )
                if new_session_id:
                    self._sync_claudecode_session_id_for_worker_chat(target_chat, is_current_target, new_session_id)
                last_context_usage = getattr(client, "last_context_usage", None)
                if last_context_usage:
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
            else:
                def on_delta(d):
                    wx_call_after_if_alive(self._on_delta, turn_idx, d, chat_id)

                c = ChatClient(api_key=api_key, model=model)
                full = c.stream_chat(question, on_delta, history_turns=history_turns)
                if c.last_context_usage:
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = c.last_context_usage
        except Exception as e:
            err = str(e)
            if (not is_openclaw_model(model)) and (not is_codex_model(model)) and self._is_model_endpoint_unavailable_error(model, err):
                for fb_model in self._candidate_fallback_models(model):
                    try:
                        c = ChatClient(api_key=api_key, model=fb_model)
                        full = c.stream_chat(question, on_delta, history_turns=history_turns)
                        if c.last_context_usage:
                            self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = c.last_context_usage
                        used_model = fb_model
                        err = ""
                        fallback_msg = f"模型 {model} 当前不可用，已回退到 {fb_model}"
                        break
                    except Exception as fb_e:
                        err = str(fb_e)
        if skip_done and not err:
            return
        self._call_after_if_alive(self._on_done, turn_idx, full, err, used_model, fallback_msg, chat_id)

    def _on_delta(self, turn_idx: int, delta: str, chat_id: str = ""):
        self._on_delta_for_chat(turn_idx, delta, chat_id or self.active_chat_id or self.current_chat_id or "")

    def _on_delta_for_chat(self, turn_idx: int, delta: str, chat_id: str = ""):
        if not delta:
            return
        target_chat, target_turns, is_current_chat = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        if turn_idx < 0 or turn_idx >= len(target_turns):
            return
        cur = str(target_turns[turn_idx].get("answer_md") or "")
        if cur == REQUESTING_TEXT:
            cur = ""
        target_turns[turn_idx]["answer_md"] = cur + remove_emojis(delta)
        target_turns[turn_idx]["request_last_attempt_at"] = time.time()
        resolved_chat_id = str(chat_id or (target_chat.get("id") if isinstance(target_chat, dict) else "") or self.active_chat_id or self.current_chat_id or "").strip()
        self._mark_chat_turns_dirty(resolved_chat_id, turn_idx)
        # 流式阶段不刷新回答列表；待完成后一次性展示完整回答。
        self._defer_chat_state_save()

    def _on_done(self, turn_idx: int, full: str, err: str, used_model: str, fallback_msg: str, chat_id: str = ""):
        target_chat, target_turns, is_current_chat = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        should_render = True
        if turn_idx < 0 and is_openclaw_model(used_model) and not err:
            should_render = False
        if not is_current_chat:
            should_render = False
        if 0 <= turn_idx < len(target_turns):
            if used_model:
                target_turns[turn_idx]["model"] = used_model
            if err:
                target_turns[turn_idx]["answer_md"] = err
            else:
                if is_openclaw_model(used_model):
                    should_render = False
                elif is_codex_model(used_model):
                    if full.strip():
                        target_turns[turn_idx]["answer_md"] = remove_emojis(full.strip())
                elif is_claudecode_model(used_model):
                    clean = remove_emojis(full.strip())
                    target_turns[turn_idx]["answer_md"] = clean if clean else "未返回任何内容。"
                else:
                    clean = remove_emojis(full.strip())
                    target_turns[turn_idx]["answer_md"] = clean if clean else "未返回任何内容。"
            if str(target_turns[turn_idx].get("request_status") or "") == "pending":
                if err:
                    self._mark_turn_request_failed(target_turns[turn_idx], err)
                else:
                    self._mark_turn_request_done(target_turns[turn_idx])
            if not err:
                for attachment in self._extract_existing_file_attachments_from_text(str(target_turns[turn_idx].get("answer_md") or ""), str(used_model or "")):
                    self._record_received_attachment(target_turns[turn_idx], attachment)
                self._refresh_context_usage_after_done(target_chat, target_turns, turn_idx, used_model)
            self._active_request_count = 0
            if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""} and isinstance(target_chat, dict):
                target_chat["updated_at"] = time.time()
                title = self._summarize_last_turn_locally(target_turns)
                if title and not target_chat.get("title_manual"):
                    target_chat["title"] = title
            resolved_dirty_chat_id = str(chat_id or (target_chat.get("id") if isinstance(target_chat, dict) else "") or self.active_chat_id or self.current_chat_id or "").strip()
            self._mark_chat_turns_dirty(resolved_dirty_chat_id, turn_idx)

        if is_current_chat:
            self.is_running = False
            self.new_chat_button.Enable()
            self._set_input_hint_idle()
        if fallback_msg and is_current_chat:
            self.selected_model = used_model or self.selected_model
            if used_model:
                self.model_combo.SetValue(used_model)
            self.SetStatusText(fallback_msg)
        elif is_current_chat:
            if is_openclaw_model(used_model) and not err:
                self.SetStatusText("已发送，等待 OpenClaw 同步回复")
            else:
                self.SetStatusText("答复完成")
        resolved_chat_id = chat_id or self.active_chat_id or self.current_chat_id or ""
        if (not is_openclaw_model(used_model)) and resolved_chat_id:
            self._push_remote_state(resolved_chat_id)
            if not err and 0 <= turn_idx < len(target_turns):
                final_text = str(target_turns[turn_idx].get("answer_md") or "").strip()
                if final_text and final_text != REQUESTING_TEXT:
                    self._push_remote_final_answer(resolved_chat_id, final_text)
            self._push_remote_history_changed(resolved_chat_id)
        self._defer_chat_state_save()
        if self._is_ui_alive():
            self._refresh_history(resolved_chat_id or None)

        if should_render and self.view_mode == "active":
            self._refresh_answer_list_preserving_selection()
            self._call_later_if_alive(120, self._focus_latest_answer)
        if (not is_openclaw_model(used_model)) or err:
            self._play_finish_sound()

    def _set_chat_context_usage(self, chat: dict, usage) -> bool:
        if not isinstance(chat, dict) or usage is None:
            return False
        if hasattr(usage, "to_dict"):
            usage = usage.to_dict()
        if isinstance(usage, dict):
            if not self._context_usage_payload_changed(chat.get("context_usage"), usage):
                return False
            chat["context_usage"] = usage
            self._invalidate_remote_state_cache()
            return True
        return False

    def _refresh_context_usage_after_done(self, target_chat: dict, target_turns: list, turn_idx: int, used_model: str) -> None:
        pending = self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(target_chat, turn_idx), None)
        if pending and self._pending_context_usage_matches_model(pending, used_model):
            self._set_chat_context_usage(target_chat, pending)
            return
        if pending:
            self._pending_context_usage_by_turn[self._context_usage_pending_key_from_chat(target_chat, turn_idx)] = pending
        if self._is_authoritative_context_usage_model(used_model):
            if isinstance(target_chat, dict):
                target_chat.pop("context_usage", None)
            return
        self._schedule_context_usage_estimate(target_chat, target_turns, turn_idx, used_model)

    def _schedule_context_usage_estimate(self, target_chat: dict, target_turns: list, turn_idx: int, used_model: str) -> None:
        if not isinstance(target_chat, dict):
            return
        chat_id = str(target_chat.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        if not chat_id:
            return
        key = (chat_id, int(turn_idx))
        with self._context_usage_estimate_lock:
            if key in self._context_usage_estimate_keys:
                return
            self._context_usage_estimate_keys.add(key)
        turns_snapshot = list(target_turns or [])
        model = str(used_model or "").strip()

        def _worker() -> None:
            try:
                usage = estimate_turns_tokens(turns_snapshot, model=model)
            except Exception:
                usage = None
            self._call_after_if_alive(self._apply_context_usage_estimate, key, usage)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_context_usage_estimate(self, key: tuple[str, int], usage) -> None:
        with self._context_usage_estimate_lock:
            self._context_usage_estimate_keys.discard(key)
        if usage is None:
            return
        chat_id, turn_idx = key
        chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
        if not isinstance(chat, dict):
            return
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        if turns and int(turn_idx) >= len(turns):
            return
        if self._set_chat_context_usage(chat, usage):
            self._defer_chat_state_save()

    def _context_usage_pending_key(self, chat_id: str, turn_idx: int) -> tuple[str, int]:
        key_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        return (key_chat_id, int(turn_idx))

    def _context_usage_pending_key_from_chat(self, chat: dict, turn_idx: int) -> tuple[str, int]:
        chat_id = str((chat or {}).get("id") or "").strip() if isinstance(chat, dict) else ""
        return self._context_usage_pending_key(chat_id, turn_idx)

    def _focus_latest_answer(self):
        if not self._can_focus_completion_result():
            return
        for i in range(len(self.answer_meta) - 1, -1, -1):
            if self.answer_meta[i][0] == "answer":
                self.answer_list.SetSelection(i)
                self.answer_list.SetFocus()
                break

    def _summarize_title(self, turns, api_key: str) -> str:
        if not turns:
            return "新聊天"
        title = self._summarize_last_turn_locally(turns)
        return title.strip()[:40] or "新聊天"

    def _title_source_turns(self, turns):
        first_question = self._title_source_question(turns)
        if not first_question:
            return []
        return [{"question": first_question, "answer_md": "", "model": ""}]

    def _build_title_transcript(self, turns) -> str:
        first_question = self._title_source_question(turns)
        if not first_question:
            return ""
        return f"首轮用户提问：{first_question}".strip()

    def _summarize_last_turn_locally(self, turns) -> str:
        first_question = self._title_source_question(turns)
        title = self._compact_first_question_title(first_question, 18)
        return title or "新聊天"

    def _summarize_recent_topic(self, turns, api_key: str) -> str:
        if not turns:
            return EMPTY_CURRENT_CHAT_TITLE
        return self._summarize_last_turn_locally(turns)

    def _apply_archived_title(self, chat_id: str, title: str) -> None:
        if not title:
            return
        c = self._find_archived_chat(chat_id)
        if not c:
            return
        if c.get("title_manual"):
            return
        c["title"] = title.strip()[:40] or c.get("title") or "新聊天"
        self._save_state()
        self._refresh_history(chat_id)

    def _schedule_async_archive_rename(self, chat_id: str, turns_snapshot: list[dict], model_snapshot: str) -> None:
        return

    def _archive_active_session(self, quick_title: bool = False, schedule_async_rename: bool = False, save_after_archive: bool = True):
        self._flush_chat_state_save()
        if not self.active_session_turns:
            return None
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False) and getattr(self, "chat_store", None) is not None)
        turn_count = len(self.active_session_turns)
        if use_chat_store:
            self._defer_chat_state_save()
            turns_snapshot = list(self.active_session_turns)
        else:
            turns_snapshot = copy.deepcopy(self.active_session_turns)
        model_snapshot = self._resolve_current_model()
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip() if (not quick_title) else ""
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        title = str(self._current_chat_state.get("title") or "").strip()
        if not title:
            title = self._next_default_chat_title()
        created = self.active_session_started_at or time.time()
        archived_id = str(self.active_chat_id or self.current_chat_id or uuid.uuid4())
        archived = {
            "id": archived_id,
            "title": title,
            "title_manual": title_manual,
            "title_source": str(self._current_chat_state.get("title_source") or ("manual" if title_manual else "default")),
            "title_updated_at": float(self._current_chat_state.get("title_updated_at") or time.time()),
            "title_revision": int(self._current_chat_state.get("title_revision") or 1),
            "pinned": False,
            "created_at": created,
            "updated_at": float(self._current_chat_state.get("updated_at") or created or time.time()),
            "turn_count": turn_count,
            "source_chat_id": self.active_chat_id or self.current_chat_id or "",
            "openclaw_session_key": self.active_openclaw_session_key,
            "openclaw_session_id": self.active_openclaw_session_id,
            "openclaw_session_file": self.active_openclaw_session_file,
            "openclaw_sync_offset": self.active_openclaw_sync_offset,
            "openclaw_last_event_id": self.active_openclaw_last_event_id,
            "openclaw_last_synced_at": self.active_openclaw_last_synced_at,
            "codex_thread_id": self.active_codex_thread_id,
            "codex_turn_id": self.active_codex_turn_id,
            "codex_turn_active": self.active_codex_turn_active,
            "codex_pending_prompt": self.active_codex_pending_prompt,
            "codex_pending_request": self.active_codex_pending_request,
            "codex_request_queue": copy.deepcopy(self.active_codex_request_queue),
            "codex_thread_flags": copy.deepcopy(self.active_codex_thread_flags),
            "codex_latest_assistant_text": self.active_codex_latest_assistant_text,
            "codex_latest_assistant_phase": self.active_codex_latest_assistant_phase,
            "claudecode_session_id": self.active_claudecode_session_id,
            "detail_panel_mode": str(self._current_chat_state.get("detail_panel_mode") or "").strip() or "answers",
            "execution_steps": (
                list(self._current_chat_state.get("execution_steps"))
                if use_chat_store and isinstance(self._current_chat_state.get("execution_steps"), list)
                else (
                    copy.deepcopy(self._current_chat_state.get("execution_steps"))
                    if isinstance(self._current_chat_state.get("execution_steps"), list)
                    else []
                )
            ),
        }
        archived["turns"] = turns_snapshot
        if "context_usage" in self._current_chat_state:
            archived["context_usage"] = copy.deepcopy(self._current_chat_state.get("context_usage"))
        self.archived_chats.append(archived)
        if not use_chat_store:
            self._mark_chat_turns_dirty(archived_id, 0)
        self._sort_archived_chats()

        self.active_session_turns = []
        self._current_chat_state["turns"] = self.active_session_turns
        self.active_session_started_at = 0.0
        self.active_turn_idx = -1
        self.active_chat_id = ""
        self.active_openclaw_session_key = DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = ""
        self.active_openclaw_session_file = ""
        self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = 0.0
        self.active_codex_thread_id = ""
        self.active_codex_turn_id = ""
        self.active_codex_turn_active = False
        self.active_codex_pending_prompt = ""
        self.active_codex_pending_request = None
        self.active_codex_request_queue = []
        self.active_codex_thread_flags = []
        self.active_codex_latest_assistant_text = ""
        self.active_codex_latest_assistant_phase = ""
        self.active_claudecode_session_id = ""
        self._active_claudecode_client = None
        self.view_mode = "active"
        self.view_history_id = None
        if save_after_archive:
            self._save_state()
        self._refresh_openclaw_sync_lifecycle()
        if schedule_async_rename:
            self._schedule_async_archive_rename(str(archived["id"]), turns_snapshot, model_snapshot)
        return archived

    def _load_project_folder(self, _event=None):
        dlg = wx.DirDialog(
            self,
            "选择项目文件夹",
            defaultPath=self.active_project_folder or str(Path.cwd()),
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            project_folder = str(dlg.GetPath() or "").strip()
        finally:
            dlg.Destroy()
        if not project_folder:
            return
        self.active_project_folder = project_folder
        folder_name = Path(project_folder).name or project_folder
        self._add_system_message_to_chat(f"已载入项目文件夹：{project_folder}")
        self._defer_chat_state_save()
        self._render_answer_list()
        self.SetStatusText(f"已载入项目：{folder_name}")

    def _on_new_chat_clicked(self, _):
        self.view_mode = "active"
        self.view_history_id = None
        self._pending_context_usage_by_turn = {}
        self._active_claudecode_client = None
        archived = self._archive_active_session(quick_title=True, schedule_async_rename=True)
        self.current_chat_id = ""
        self.active_chat_id = ""
        now = time.time()
        self._current_chat_state = {
            "id": "",
            "title": self._next_default_chat_title(),
            "title_manual": False,
            "title_source": "default",
            "title_updated_at": now,
            "title_revision": 1,
            "turns": self.active_session_turns,
            "context_usage": None,
            "created_at": now,
            "updated_at": now,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
        self.active_chat_id = self._ensure_active_chat_id()
        self.current_chat_id = self.active_chat_id
        self._current_chat_state["id"] = self.active_chat_id
        model = model_id_from_display_name(str(self._resolve_current_model() or DEFAULT_MODEL_ID).strip())
        if model not in MODEL_IDS and not (is_codex_model(model) or is_claudecode_model(model) or is_openclaw_model(model)):
            model = DEFAULT_MODEL_ID
        self.selected_model = model
        self._current_chat_state["model"] = model
        if is_openclaw_model(self.selected_model):
            self._openclaw_session_id_for_active_chat()
        self._refresh_history(archived["id"] if archived else None)
        self._render_answer_list()
        self.input_edit.SetFocus()
        self.SetStatusText("已开始新聊天")
        self._save_state()
        self._refresh_openclaw_sync_lifecycle()
        self._push_remote_history_changed(self.active_chat_id)

    def _on_answer_key_down(self, event):
        key = event.GetKeyCode()
        ctrl = self._event_control_down(event)
        alt = self._event_alt_down(event)
        if not ctrl and not alt and key in (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_HOME, wx.WXK_END):
            if self._move_answer_list_selection_for_key(key):
                return
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            event.Skip()
            return
        if ctrl and key in (ord("C"), ord("c")):
            self._copy_selected_answer_to_clipboard()
            stop_propagation = getattr(event, "StopPropagation", None)
            if callable(stop_propagation):
                stop_propagation()
            return
        item_type, turn_idx, plain, _ = self.answer_meta[idx]
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._try_open_selected_answer_detail():
                return
        event.Skip()

    def _copy_selected_answer_to_clipboard(self) -> bool:
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return False
        item_type, _turn_idx, plain, detail = self.answer_meta[idx]
        if item_type not in ("question", "answer"):
            return False
        source_text = detail if item_type == "answer" and detail else plain
        cleaned_text = "\n".join(line for line in str(source_text).split("\n") if line.strip())
        if not cleaned_text:
            return False
        if not self._set_clipboard_text(cleaned_text):
            return False
        self.SetStatusText("已复制")
        return True

    def _move_answer_list_selection_for_key(self, key: int) -> bool:
        count = self.answer_list.GetCount()
        if count <= 0:
            return False
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = 0
        if key == wx.WXK_UP:
            new_idx = max(0, idx - 1)
        elif key == wx.WXK_DOWN:
            new_idx = min(count - 1, idx + 1)
        elif key == wx.WXK_HOME:
            new_idx = 0
        elif key == wx.WXK_END:
            new_idx = count - 1
        else:
            return False
        if new_idx == idx:
            return True
        self.answer_list.SetSelection(new_idx)
        try:
            if not self.answer_list.HasFocus():
                self.answer_list.SetFocus()
        except Exception:
            pass
        return True

    def _on_answer_char(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._try_open_selected_answer_detail():
                return
        ch = self._extract_committed_char(event)
        if ch:
            self._queue_answer_char_redirect(ch)
        event.Skip()

    def _on_answer_activate(self, _event):
        self._try_open_selected_answer_detail()

    def _on_execution_key_down(self, event):
        key = event.GetKeyCode()
        ctrl = event.ControlDown()
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_execution_activate(event)
            return
        if ctrl and key in (ord("C"), ord("c")):
            idx = self.execution_list.GetSelection()
            if idx != wx.NOT_FOUND and wx.TheClipboard.Open():
                try:
                    detail_text = ""
                    if 0 <= idx < len(self.execution_meta):
                        detail_text = str(self.execution_meta[idx][3] or "")
                    text = detail_text.strip() or str(self.execution_list.GetString(idx) or "").strip()
                    if text:
                        wx.TheClipboard.SetData(wx.TextDataObject(text))
                        self.SetStatusText("已复制")
                finally:
                    wx.TheClipboard.Close()
            stop = getattr(event, "StopPropagation", None)
            if callable(stop):
                stop()
            return
        event.Skip()

    def _on_execution_char(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_execution_activate(event)
            return
        ch = self._extract_committed_char(event)
        if ch:
            self._queue_answer_char_redirect(ch)
        event.Skip()

    def _on_execution_activate(self, _event):
        self._try_open_selected_execution_detail()

    def _try_open_selected_execution_detail(self) -> bool:
        if self._detail_panel_mode() != "execution":
            return False
        idx = self.execution_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.execution_meta):
            return False
        item_type, step_idx, _, detail_text = self.execution_meta[idx]
        if item_type == "more":
            self._show_more_execution_rows()
            try:
                if self.execution_list.GetCount() > 0:
                    self.execution_list.SetSelection(0)
            except Exception:
                pass
            return True
        if item_type != "execution" or step_idx < 0:
            return False
        row_text = str(self.execution_meta[idx][2] or "")
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        state_steps = state.get("execution_steps") if isinstance(state.get("execution_steps"), list) else []
        if str(detail_text or "").strip() and 0 <= step_idx < len(state_steps) and isinstance(state_steps[step_idx], dict):
            step = state_steps[step_idx]
        elif str(detail_text or "").strip():
            step = {"step": row_text, "list_text": row_text, "detail_text": str(detail_text or "")}
        else:
            if not row_text.strip():
                return False
            step = {"step": row_text, "list_text": row_text, "detail_text": row_text}
        detail_source = str(
            step.get("detail_text")
            or step.get("message")
            or step.get("step")
            or step.get("title")
            or step.get("text")
            or step.get("content")
            or step.get("description")
            or detail_text
            or ""
        ).strip()
        if not detail_source:
            return False
        try:
            page_path = self._ensure_execution_detail_page(step, step_idx)
            self._open_local_webpage(page_path)
            self.SetStatusText("已打开执行过程详情网页")
            self._save_state()
        except Exception:
            wx.MessageBox("打开详情网页失败。", "提示", wx.OK | wx.ICON_WARNING)
            return False
        return True

    def _try_open_selected_answer_detail(self) -> bool:
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return False
        item_type, turn_idx, _, detail = self.answer_meta[idx]
        if item_type == "more":
            self._show_more_answer_rows()
            try:
                if self.answer_list.GetCount() > 0:
                    self.answer_list.SetSelection(0)
            except Exception:
                pass
            return True
        if item_type == "attachment":
            path = str(detail or "").strip()
            if not path or not Path(path).is_file():
                return False
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                self.SetStatusText("已打开附件")
            except Exception:
                wx.MessageBox("打开附件失败。", "提示", wx.OK | wx.ICON_WARNING)
                return False
            return True
        if item_type not in ("question", "answer"):
            return False
        turns = self._get_view_turns()
        if not (0 <= turn_idx < len(turns)):
            return False
        turn = turns[turn_idx]
        try:
            if item_type == "question":
                question = str(turn.get("question") or "").strip()
                if not question:
                    return False
                page_path = self._ensure_question_detail_page(turn, turn_idx)
                status_text = "已打开问题详情网页"
            else:
                answer_md = str(turn.get("answer_md") or "")
                if not answer_md or answer_md == REQUESTING_TEXT:
                    return False
                page_path = self._ensure_answer_detail_page(turn, turn_idx)
                status_text = "已打开回答详情网页"
            self._open_local_webpage(page_path)
            self.SetStatusText(status_text)
            self._save_state()
        except Exception:
            wx.MessageBox("打开详情网页失败。", "提示", wx.OK | wx.ICON_WARNING)
            return False
        return True

    def _on_history_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate_selected_history()
            return
        if key == wx.WXK_MENU:
            self._show_history_menu()
            return
        if key == wx.WXK_DELETE:
            self._history_delete(None)
            return
        event.Skip()

    def _on_history_char(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate_selected_history()
            return
        event.Skip()

    def _on_history_selected(self, event):
        event.Skip()

    def _on_history_context(self, _):
        self._show_history_menu()

    def _show_history_chat(self, chat_id: str, *, focus_answer_list: bool = True) -> bool:
        selected_id = str(chat_id or "").strip()
        if not selected_id:
            return False
        self._flush_relevant_execution_deltas_for_switch()
        current_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}
        current_ids.discard("")
        if selected_id in current_ids:
            self.view_mode = "active"
            self.view_history_id = None
            self._render_answer_list()
            self._refresh_history(selected_id)
            self._save_state()
            if focus_answer_list:
                self.answer_list.SetFocus()
            return True
        chat = self._hydrate_chat_from_store(
            self._find_archived_chat(selected_id),
            include_execution_steps=False,
        )
        if not chat:
            return False
        self.view_mode = "history"
        self.view_history_id = selected_id
        self._render_answer_list()
        self._refresh_history(selected_id)
        self._save_state()
        self.SetStatusText("已切换到历史聊天")
        if focus_answer_list:
            self.answer_list.SetFocus()
        return True

    def _activate_selected_history(self) -> bool:
        if self.history_list.GetCount() == 0:
            return False
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = 0
            self.history_list.SetSelection(0)
        if idx < 0 or idx >= len(self.history_ids):
            return False

        return self._show_history_chat(self.history_ids[idx])

    def _load_history_selection(self):
        return self._activate_selected_history()

    def _find_archived_chat(self, chat_id):
        for c in self.archived_chats:
            if c.get("id") == chat_id:
                return c
        return None

    def _get_all_chat_ids_in_order(self):
        """Get all chat IDs in order: current chat first, then archived chats sorted by updated_at descending."""
        all_ids = []
        current_id = self.current_chat_id or self.active_chat_id
        if current_id:
            all_ids.append(current_id)
        # Sort archived chats by updated_at descending, with pinned chats first
        sorted_archived = sorted(
            self.archived_chats,
            key=lambda c: (-c.get("pinned", False), -c.get("updated_at", 0))
        )
        for c in sorted_archived:
            cid = c.get("id")
            if cid and cid != current_id:
                all_ids.append(cid)
        return all_ids

    def _adjacent_history_chat_id(self, direction: int):
        """Get the adjacent chat ID in the specified direction (1 for next, -1 for previous)."""
        all_ids = self._get_all_chat_ids_in_order()
        anchor_id = str(self.view_history_id or "").strip() if self.view_mode == "history" else ""
        if not anchor_id:
            anchor_id = self.current_chat_id or self.active_chat_id
        if not anchor_id:
            idx = self.history_list.GetSelection() if hasattr(self, "history_list") else wx.NOT_FOUND
            if idx != wx.NOT_FOUND and 0 <= idx < len(self.history_ids):
                anchor_id = self.history_ids[idx]
        if len(all_ids) <= 1 or anchor_id not in all_ids:
            return None
        current_idx = all_ids.index(anchor_id)
        target_idx = current_idx + direction
        if target_idx < 0 or target_idx >= len(all_ids):
            return None
        return all_ids[target_idx]

    def _navigate_history_chats(self, direction: int) -> bool:
        chat_id = self._adjacent_history_chat_id(direction)
        if not chat_id:
            return False
        return self._show_history_chat(chat_id, focus_answer_list=False)

    def _switch_current_chat(self, chat_id: str) -> bool:
        """Switch to a different chat."""
        if chat_id == self.current_chat_id:
            return True
        self._flush_relevant_execution_deltas_for_switch()
        chat = self._hydrate_chat_from_store(self._find_archived_chat(chat_id), include_execution_steps=False)
        if not chat:
            return False
        # Archive current session if it has turns
        if self.active_session_turns:
            self._archive_active_session(quick_title=True, schedule_async_rename=True)
        # Load the selected chat
        turns = chat.get("turns") or []
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False) and getattr(self, "chat_store", None) is not None)
        self.active_session_turns = list(turns) if use_chat_store else copy.deepcopy(turns)
        self.active_session_started_at = float(chat.get("created_at") or time.time())
        self.active_chat_id = str(chat.get("source_chat_id") or chat.get("id") or "").strip() or str(uuid.uuid4())
        self.active_openclaw_session_key = str(chat.get("openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = str(chat.get("openclaw_session_id") or "").strip()
        self.active_openclaw_session_file = str(chat.get("openclaw_session_file") or "").strip()
        try:
            self.active_openclaw_sync_offset = max(int(chat.get("openclaw_sync_offset") or 0), 0)
        except Exception:
            self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = str(chat.get("openclaw_last_event_id") or "").strip()
        try:
            self.active_openclaw_last_synced_at = float(chat.get("openclaw_last_synced_at") or 0.0)
        except Exception:
            self.active_openclaw_last_synced_at = 0.0
        self.active_codex_thread_id = str(chat.get("codex_thread_id") or "").strip()
        self.active_codex_turn_id = str(chat.get("codex_turn_id") or "").strip()
        self.active_codex_turn_active = bool(chat.get("codex_turn_active", False))
        self.active_codex_pending_prompt = str(chat.get("codex_pending_prompt") or "").strip()
        pending_request = chat.get("codex_pending_request")
        self.active_codex_pending_request = pending_request if isinstance(pending_request, dict) else None
        request_queue = chat.get("codex_request_queue")
        self.active_codex_request_queue = request_queue if isinstance(request_queue, list) else []
        thread_flags = chat.get("codex_thread_flags")
        self.active_codex_thread_flags = thread_flags if isinstance(thread_flags, list) else []
        self.active_codex_latest_assistant_text = str(chat.get("codex_latest_assistant_text") or "").strip()
        self.active_codex_latest_assistant_phase = str(chat.get("codex_latest_assistant_phase") or "").strip()
        self.active_claudecode_session_id = str(chat.get("claudecode_session_id") or "").strip()
        self.current_chat_id = chat_id
        if use_chat_store:
            self._current_chat_state = dict(chat)
            self._current_chat_state["turns"] = self.active_session_turns
            if not isinstance(self._current_chat_state.get("execution_steps"), list):
                self._current_chat_state["execution_steps"] = []
        else:
            self._current_chat_state = copy.deepcopy(chat)
        if (not self.active_openclaw_session_id) and any(is_openclaw_model(str(turn.get("model") or "")) for turn in self.active_session_turns):
            self.active_openclaw_session_id = self._make_openclaw_session_id(self.active_chat_id)
        self.active_turn_idx = len(self.active_session_turns) - 1
        resolved_model = ""
        for t in reversed(self.active_session_turns):
            m = str(t.get("model") or "").strip()
            if is_visible_model_id(m):
                resolved_model = m
                break
        if not resolved_model:
            resolved_model = self.selected_model if is_visible_model_id(self.selected_model) else DEFAULT_MODEL_ID
        self.selected_model = resolved_model
        if threading.current_thread() is threading.main_thread():
            self.model_combo.SetValue(self.selected_model)
        # Remove from archived chats since it's now active
        self.archived_chats = [c for c in self.archived_chats if c.get("id") != chat_id]
        self._reset_answer_visible_row_limit()
        self._reset_execution_visible_row_limit()
        self._render_answer_list()
        self._refresh_openclaw_sync_lifecycle()
        self._refresh_history(chat_id)
        self._save_state()
        return True

    def _show_history_menu(self):
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.history_ids):
            return
        m = wx.Menu()
        i_del = wx.NewIdRef()
        i_pin = wx.NewIdRef()
        i_clr = wx.NewIdRef()
        i_ren = wx.NewIdRef()
        c = self._find_archived_chat(self.history_ids[idx])
        if not c:
            return
        m.Append(i_del, "删除聊天")
        m.Append(i_pin, "取消置顶" if c.get("pinned") else "置顶聊天")
        m.Append(i_clr, "清空所有非置顶聊天")
        m.Append(i_ren, "重命名聊天")
        self.Bind(wx.EVT_MENU, self._history_delete, id=i_del)
        self.Bind(wx.EVT_MENU, self._history_pin, id=i_pin)
        self.Bind(wx.EVT_MENU, self._history_clear_non_pinned, id=i_clr)
        self.Bind(wx.EVT_MENU, self._history_rename, id=i_ren)
        self.PopupMenu(m)
        m.Destroy()

    def _notes_current_notebook(self):
        notebook_id = str(self.notes_controller.active_notebook_id or self._notes_selected_notebook_id() or "").strip()
        if not notebook_id:
            return None
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            return projection.get_notebook(notebook_id, include_deleted=True)
        return self.notes_store.get_notebook(notebook_id, include_deleted=True)

    def _notes_current_entry(self):
        active_entry_id = str(getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        if active_entry_id:
            entry_id = active_entry_id
        elif str(getattr(self.notes_controller, "notes_view", "") or "") == "note_edit":
            entry_id = ""
        else:
            entry_id = str(self._notes_selected_entry_id() or "").strip()
        if not entry_id:
            return None
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            return projection.get_entry(entry_id, include_deleted=True)
        return self.notes_store.get_entry(entry_id, include_deleted=True)

    def _notes_selected_notebook_id(self) -> str:
        idx = self.notes_notebook_list.GetSelection()
        if idx != wx.NOT_FOUND and 0 <= idx < len(getattr(self, "_notes_notebook_ids", [])):
            return str(self._notes_notebook_ids[idx])
        return str(self.notes_controller.active_notebook_id or "")

    def _notes_selected_entry_id(self) -> str:
        idx = self.notes_entry_list.GetSelection()
        if idx != wx.NOT_FOUND and 0 <= idx < len(getattr(self, "_notes_entry_ids", [])):
            return str(self._notes_entry_ids[idx])
        return str(self.notes_controller.active_entry_id or "")

    def _notes_sync_view_visibility(self) -> None:
        controller = getattr(self, "notes_controller", None)
        view = str(getattr(controller, "notes_view", "notes_list") or "notes_list")
        show_notes_list = view != "note_detail"
        show_note_detail = view == "note_detail"
        show_note_edit = view == "note_edit"
        if hasattr(self, "notes_content_label"):
            self.notes_content_label.SetLabel("笔记详情：" if show_note_detail else ("笔记条目编辑：" if show_note_edit else "笔记："))
        if hasattr(self, "notes_list_panel"):
            self.notes_list_panel.Show(show_notes_list)
        if hasattr(self, "notes_detail_panel"):
            self.notes_detail_panel.Show(show_note_detail)
        if hasattr(self, "notes_edit_panel"):
            self.notes_edit_panel.Show(show_note_edit)
        try:
            self.notes_notebook_list.Enable(True)
        except Exception:
            pass
        try:
            self.notes_entry_list.Enable(show_note_detail and bool(str(getattr(controller, "active_notebook_id", "") or "").strip()))
        except Exception:
            pass
        try:
            self.notes_editor.Enable(show_note_edit)
        except Exception:
            pass
        self._notes_rebuild_tab_order()
        try:
            self.Layout()
        except Exception:
            pass

    def _notes_primary_tab_target(self):
        view = str(getattr(getattr(self, "notes_controller", None), "notes_view", "notes_list") or "notes_list")
        if view == "note_detail":
            return self.notes_entry_list
        return self.notes_notebook_list

    def _notes_rebuild_tab_order(self) -> None:
        if not all(
            hasattr(self, attr)
            for attr in (
                "input_edit",
                "send_button",
                "new_chat_button",
                "model_combo",
                "history_list",
                "notes_notebook_list",
                "notes_entry_list",
                "notes_editor",
                "answer_list",
                "execution_list",
            )
        ):
            return
        primary_notes_ctrl = self._notes_primary_tab_target()
        detail_target = self._current_detail_tab_target()
        secondary_detail_target = self.execution_list if detail_target is self.answer_list else self.answer_list
        if primary_notes_ctrl is self.notes_entry_list:
            ordered_controls = [
                self.input_edit,
                self.new_chat_button,
                self.model_combo,
                self.send_button,
                self.history_list,
                primary_notes_ctrl,
                detail_target,
                secondary_detail_target,
                self.notes_notebook_list,
                self.notes_editor,
            ]
        else:
            ordered_controls = [
                self.input_edit,
                self.new_chat_button,
                self.model_combo,
                self.send_button,
                primary_notes_ctrl,
                self.history_list,
                detail_target,
                secondary_detail_target,
                self.notes_entry_list,
                self.notes_editor,
            ]
        seen = set()
        root_tab_order = []
        for ctrl in ordered_controls:
            marker = id(ctrl)
            if marker in seen:
                continue
            seen.add(marker)
            root_tab_order.append(ctrl)
        self.root_tab_order = root_tab_order
        self.chat_tab_order = root_tab_order[:7]
        self.notes_tab_order = [primary_notes_ctrl] + [
            ctrl
            for ctrl in (self.notes_notebook_list, self.notes_entry_list, self.notes_editor)
            if ctrl is not primary_notes_ctrl
        ]
        primary_notes_panel = self.notes_detail_panel if primary_notes_ctrl is self.notes_entry_list else self.notes_list_panel
        for previous, nxt in zip(self.root_tab_order, self.root_tab_order[1:]):
            try:
                nxt.MoveAfterInTabOrder(previous)
            except Exception:
                pass
        if primary_notes_ctrl is self.notes_entry_list:
            try:
                self.history_list.MoveAfterInTabOrder(self.send_button)
            except Exception:
                pass
            try:
                primary_notes_panel.MoveAfterInTabOrder(self.history_list)
            except Exception:
                pass
            try:
                primary_notes_ctrl.MoveAfterInTabOrder(primary_notes_panel)
            except Exception:
                pass
            try:
                detail_target.MoveAfterInTabOrder(primary_notes_ctrl)
            except Exception:
                pass
        else:
            try:
                primary_notes_panel.MoveAfterInTabOrder(self.send_button)
            except Exception:
                pass
            try:
                primary_notes_ctrl.MoveAfterInTabOrder(primary_notes_panel)
            except Exception:
                pass
            try:
                self.history_list.MoveAfterInTabOrder(primary_notes_ctrl)
            except Exception:
                pass
            try:
                detail_target.MoveAfterInTabOrder(self.history_list)
            except Exception:
                pass

    def _handle_ctrl_history_navigation(self, event) -> bool:
        key = event.GetKeyCode()
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        if key not in (wx.WXK_LEFT, wx.WXK_RIGHT):
            return False
        if not (callable(ctrl_down) and ctrl_down()):
            return False
        if callable(alt_down) and alt_down():
            return False
        direction = -1 if key == wx.WXK_LEFT else 1
        self._navigate_history_chats(direction)
        return True

    def _handle_primary_tab_navigation(self, event) -> bool:
        key = event.GetKeyCode()
        if key != wx.WXK_TAB:
            return False
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        shift_down = getattr(event, "ShiftDown", None)
        if callable(ctrl_down) and ctrl_down():
            return False
        if callable(alt_down) and alt_down():
            return False
        backwards = bool(shift_down()) if callable(shift_down) else False

        focus = wx.Window.FindFocus()
        detail_target = self._current_detail_tab_target()
        detail_controls = {ctrl for ctrl in (getattr(self, "answer_list", None), getattr(self, "execution_list", None)) if ctrl is not None}
        if backwards:
            if focus is self.input_edit:
                if detail_target is not None:
                    detail_target.SetFocus()
                return True
            if focus is detail_target or focus in detail_controls:
                self.history_list.SetFocus()
                return True
            if focus is self.history_list:
                self._notes_primary_tab_target().SetFocus()
                return True
            return False

        if focus in {self.notes_notebook_list, self.notes_entry_list}:
            self.history_list.SetFocus()
            return True
        if focus is self.history_list:
            if detail_target is not None:
                detail_target.SetFocus()
            return True
        if focus is detail_target or focus in detail_controls:
            self.input_edit.SetFocus()
            return True
        return False

    def _on_generic_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        event.Skip()

    def _listbox_strings(self, control) -> list[str]:
        try:
            return [control.GetString(i) for i in range(control.GetCount())]
        except Exception:
            return []

    def _replace_listbox_items_if_changed(self, control, labels: list[str], selected_idx: int | None = None) -> bool:
        normalized = [str(label or "") for label in labels]
        cache = getattr(self, "_listbox_label_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._listbox_label_cache = cache
        cache_key = id(control)
        normalized_tuple = tuple(normalized)
        cached_labels = cache.get(cache_key)
        current_matches = cached_labels == normalized_tuple
        if not current_matches and cached_labels is None:
            current_matches = self._listbox_strings(control) == normalized
        if current_matches:
            if selected_idx is not None:
                try:
                    if control.GetSelection() != selected_idx:
                        control.SetSelection(selected_idx)
                except Exception:
                    pass
            cache[cache_key] = normalized_tuple
            return False
        control.Clear()
        for label in normalized:
            control.Append(label)
        if normalized and selected_idx is not None:
            try:
                control.SetSelection(max(0, min(int(selected_idx), len(normalized) - 1)))
            except Exception:
                pass
        cache[cache_key] = normalized_tuple
        return True

    def _notes_refresh_notebooks(self, select_id: str | None = None) -> None:
        query = str(getattr(self, "_notes_search_query", "") or "").strip()
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            notebooks = projection.search_notebooks(query) if query else projection.list_notebooks()
        else:
            notebooks = self.notes_store.search_notebooks(query) if query else self.notes_store.list_notebooks()
        self._notes_notebook_ids = [nb.id for nb in notebooks]
        if not notebooks:
            self.notes_notebook_list.Enable(True)
            self._replace_listbox_items_if_changed(self.notes_notebook_list, ["暂无笔记本"], 0)
            return
        self.notes_notebook_list.Enable(True)
        target = str(select_id or self.notes_controller.active_notebook_id or notebooks[0].id)
        selected_idx = 0
        if target in self._notes_notebook_ids:
            selected_idx = self._notes_notebook_ids.index(target)
        labels = [f"{'★ ' if notebook.pinned else ''}{notebook.title}" for notebook in notebooks]
        self._replace_listbox_items_if_changed(self.notes_notebook_list, labels, selected_idx)

    def _notes_refresh_entries(self, notebook_id: str | None = None, select_id: str | None = None) -> None:
        notebook_id = str(notebook_id or self.notes_controller.active_notebook_id or "").strip()
        self._notes_entry_ids = []
        if not notebook_id:
            self.notes_entry_list.Enable(False)
            self._replace_listbox_items_if_changed(self.notes_entry_list, ["请选择笔记本"], None)
            return
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook_id)
        else:
            entries = self.notes_store.list_entries(notebook_id)
        self.notes_entry_list.Enable(True)
        self._notes_entry_ids = [entry.id for entry in entries]
        if not entries:
            self._replace_listbox_items_if_changed(self.notes_entry_list, ["暂无条目"], None)
            return
        target = str(select_id or self.notes_controller.active_entry_id or entries[0].id)
        selected_idx = 0
        if target in self._notes_entry_ids:
            selected_idx = self._notes_entry_ids.index(target)
        labels = []
        for entry in entries:
            labels.append(self._notes_entry_label(entry))
        self._replace_listbox_items_if_changed(self.notes_entry_list, labels, selected_idx)

    def _notes_entry_label(self, entry) -> str:
        prefix = "★ " if getattr(entry, "pinned", False) else ""
        raw = str(getattr(entry, "content", "") or "")
        preview = raw[: max(NOTES_ENTRY_LABEL_MAX_CHARS * 4, NOTES_ENTRY_LABEL_MAX_CHARS)]
        label = re.sub(r"\s*[\r\n]+\s*", " / ", preview).strip()
        max_body = max(1, NOTES_ENTRY_LABEL_MAX_CHARS - len(prefix))
        if len(label) > max_body:
            label = label[: max(1, max_body - 3)].rstrip() + "..."
        return f"{prefix}{label}"

    def _notes_sync_editor(self) -> None:
        entry = self._notes_current_entry()
        draft = ""
        if self.notes_controller.entry_editor_dirty:
            draft = self.notes_controller.entry_editor_draft
        elif entry is not None:
            draft = entry.content
        elif self.notes_controller.entry_editor_draft:
            draft = self.notes_controller.entry_editor_draft
        try:
            self._notes_editor_syncing = True
            self.notes_editor.SetValue(draft)
            self.notes_editor.SetInsertionPointEnd()
        except Exception:
            pass
        finally:
            self._notes_editor_syncing = False

    def _notes_refresh_ui(self) -> None:
        if not hasattr(self, "notes_notebook_list"):
            return
        if not hasattr(self, "notes_controller"):
            return
        self._notes_refresh_notebooks()
        self._notes_refresh_entries()
        self._notes_sync_view_visibility()
        if self.notes_controller.notes_view == "note_edit":
            self._notes_sync_editor()

    def _sync_notes_ui(self) -> None:
        self._notes_refresh_ui()

    def _open_notes_root(self) -> None:
        if not hasattr(self, "notes_controller"):
            return
        self.notes_controller.root_tab = "notes"
        if not self.notes_controller.active_notebook_id:
            projection = getattr(self, "notes_projection", None)
            first = projection.list_notebooks() if projection is not None else self.notes_store.list_notebooks()
            if first:
                self.notes_controller.active_notebook_id = first[0].id
        self.notes_controller.notes_view = "notes_list"
        self.notes_controller.active_entry_id = ""
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        try:
            self.notes_notebook_list.SetFocus()
        except Exception:
            pass

    def _open_chat_root(self) -> None:
        try:
            self.input_edit.SetFocus()
        except Exception:
            pass

    def _on_notes_editor_changed(self, event) -> None:
        if getattr(self, "_notes_editor_syncing", False):
            if event is not None:
                event.Skip()
            return
        if not hasattr(self, "notes_controller"):
            return
        draft = ""
        try:
            draft = str(self.notes_editor.GetValue() or "")
        except Exception:
            draft = str(self.notes_controller.entry_editor_draft or "")
        self.notes_controller.root_tab = "notes"
        self.notes_controller.notes_view = "note_edit"
        capture = getattr(self.notes_controller, "capture_editor_state", None)
        if callable(capture):
            capture()
        self.notes_controller.entry_editor_draft = draft
        self.notes_controller.entry_editor_dirty = True
        self._current_notes_state = self.notes_controller.to_state_dict()
        if event is not None:
            event.Skip()

    def _prompt_notes_dirty_exit(self) -> str:
        dlg = wx.MessageDialog(self, "笔记有未保存更改。", "未保存更改", wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
        try:
            try:
                dlg.SetYesNoCancelLabels("保存", "不保存", "取消")
            except Exception:
                pass
            ret = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if ret == wx.ID_YES:
            return "save"
        if ret == wx.ID_NO:
            return "discard"
        return "cancel"

    def _notes_discard_current_entry_edits(self) -> bool:
        if not hasattr(self, "notes_controller"):
            return False
        entry = self._notes_current_entry()
        should_drop = (
            entry is not None
            and not str(entry.content or "").strip()
            and int(getattr(entry, "version", 0) or 0) <= 1
            and not bool(getattr(entry, "origin_entry_id", None))
            and not bool(getattr(entry, "is_conflict_copy", False))
        )
        if should_drop:
            try:
                self.notes_store.purge_entry(entry.id)
            except Exception:
                pass
            self.notes_controller.active_entry_id = ""
            self._notes_after_local_mutation()
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_draft = ""
        self.notes_controller.notes_view = "note_detail"
        self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        return True

    def _notes_request_exit_edit(self) -> bool:
        if not hasattr(self, "notes_controller"):
            return False
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.notes_view = "note_detail"
            self._current_notes_state = self.notes_controller.to_state_dict()
            self._notes_refresh_ui()
            return True
        if not self.notes_controller.entry_editor_dirty:
            return self._notes_discard_current_entry_edits()
        choice = self._prompt_notes_dirty_exit()
        if choice == "save":
            return self._notes_save_current_entry()
        if choice == "discard":
            return self._notes_discard_current_entry_edits()
        return False

    def _notes_sync_status_text(self, status: str | dict, message: str | None = None) -> str:
        if message:
            return str(message)
        if isinstance(status, dict):
            status = str(status.get("status") or "")
        mapping = {
            "pending": "待同步",
            "sending": "同步中",
            "failed": "同步失败",
            "acked": "笔记已同步",
            "synced": "笔记已同步",
            "saved": "笔记已保存",
            "conflict": "冲突",
        }
        return mapping.get(str(status or ""), str(status or ""))

    def _on_notes_sync_status_changed(self, status: str | dict, *, message: str | None = None, cursor: str | None = None) -> None:
        text = self._notes_sync_status_text(status, message)
        self._show_notes_sync_hint(text)
        self._push_remote_notes_sync_status(status, cursor=cursor, message=text)

    def _notes_after_local_mutation(self, *, message: str = "待同步") -> None:
        cursor = self.notes_store.current_cursor()
        self._push_remote_notes_changed(cursor)
        self._on_notes_sync_status_changed("pending", message=message, cursor=cursor)
        self._schedule_notes_couchdb_sync()

    def _show_notes_sync_hint(self, message: str) -> None:
        next_hint = str(message or "")
        if str(getattr(self, "notes_sync_hint", "") or "") == next_hint:
            return
        self.notes_sync_hint = next_hint

    def _on_notes_sync_status_changed_safe(self, status: str | dict, *, message: str | None = None, cursor: str | None = None) -> None:
        if threading.get_ident() == getattr(self, "_notes_ui_thread_id", 0):
            self._on_notes_sync_status_changed(status, message=message, cursor=cursor)
            return
        self._call_after_if_alive(self._on_notes_sync_status_changed, status, message=message, cursor=cursor)

    def _on_notes_remote_ops_applied_safe(self, result: dict | None) -> None:
        if threading.get_ident() == getattr(self, "_notes_ui_thread_id", 0):
            self._on_notes_remote_ops_applied(result)
            return
        self._call_after_if_alive(self._on_notes_remote_ops_applied, result)

    def _invalidate_notes_projection(self) -> None:
        projection = getattr(self, "notes_projection", None)
        invalidate = getattr(projection, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def _configure_notes_couchdb_sync_from_env(self) -> None:
        base_url = str(os.environ.get("NOTES_COUCHDB_URL") or "").strip()
        if not base_url:
            return
        database = str(os.environ.get("NOTES_COUCHDB_DATABASE") or "zhuge_notes").strip() or "zhuge_notes"
        self.notes_sync.configure(base_url, database)
        self._schedule_notes_couchdb_sync()

    def _next_notes_couchdb_rev(self, current_rev: str) -> str:
        try:
            generation = int(str(current_rev or "0").split("-", 1)[0]) + 1
        except Exception:
            generation = 1
        return f"{generation}-{uuid.uuid4().hex[:8]}"

    def _remote_api_notes_couchdb_changes(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = dict(payload or {})
        database = str(payload.get("database") or "").strip() or "zhuge_notes"
        if database != "zhuge_notes":
            return 404, {"error": "not_found", "reason": "unknown_database"}
        current_cursor = str(self.notes_store.current_cursor() or "0")
        try:
            requested_cursor = int(str(payload.get("since") or "0").strip() or "0")
        except Exception:
            requested_cursor = 0
        try:
            current_cursor_value = int(current_cursor)
        except Exception:
            current_cursor_value = 0
        if requested_cursor >= current_cursor_value:
            return 200, {"results": [], "last_seq": current_cursor}
        cache_key = (requested_cursor, current_cursor)
        cache = getattr(self, "_remote_notes_changes_cache", None)
        if isinstance(cache, dict) and cache.get("key") == cache_key and isinstance(cache.get("body"), dict):
            return 200, copy.deepcopy(cache["body"])
        snapshot = self.notes_store.load_documents()
        results: list[dict] = []
        for notebook in snapshot.notebooks:
            doc = self.notes_sync._notebook_to_couch_document(notebook)
            row = {"seq": current_cursor, "id": doc["_id"], "doc": doc}
            if doc.get("_deleted"):
                row["deleted"] = True
            results.append(row)
        for entry in snapshot.entries:
            doc = self.notes_sync._entry_to_couch_document(entry)
            row = {"seq": current_cursor, "id": doc["_id"], "doc": doc}
            if doc.get("_deleted"):
                row["deleted"] = True
            results.append(row)
        body = {"results": results, "last_seq": current_cursor}
        self._remote_notes_changes_cache = {"key": cache_key, "body": copy.deepcopy(body)}
        return 200, body

    def _invalidate_remote_notes_changes_cache(self) -> None:
        self._remote_notes_changes_cache = None

    def _remote_api_notes_changes(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_couchdb_changes(payload)

    def _remote_api_notes_couchdb_bulk_docs(self, payload: dict | None = None) -> tuple[int, list]:
        payload = dict(payload or {})
        database = str(payload.get("database") or "").strip() or "zhuge_notes"
        if database != "zhuge_notes":
            return 404, [{"error": "not_found", "reason": "unknown_database"}]
        docs = [dict(item) for item in list(payload.get("docs") or []) if isinstance(item, dict)]
        if not docs:
            return 201, []
        results: list[dict] = []
        applied: list[dict] = []
        touched = False
        with self.notes_store._connect() as conn:
            for doc in docs:
                remote_id = str(doc.get("_id") or "").strip()
                if not remote_id:
                    results.append({"id": "", "error": "bad_request", "reason": "missing_id"})
                    continue
                if remote_id.startswith("notebook:"):
                    row = conn.execute(
                        "SELECT rev FROM notebooks WHERE id = ?",
                        (remote_id.split(":", 1)[1],),
                    ).fetchone()
                    current_rev = str(row["rev"] or "") if row is not None else ""
                    supplied_rev = str(doc.get("_rev") or "")
                    if row is not None and current_rev and supplied_rev != current_rev:
                        results.append({"id": remote_id, "error": "conflict", "reason": "document update conflict"})
                        continue
                    normalized = dict(doc)
                    normalized["_rev"] = self._next_notes_couchdb_rev(current_rev)
                    applied_result = self.notes_sync._upsert_remote_notebook(conn, normalized)
                    if applied_result:
                        applied.append(applied_result)
                    results.append({"id": remote_id, "ok": True, "rev": normalized["_rev"]})
                    touched = True
                    continue
                if remote_id.startswith("entry:"):
                    row = conn.execute(
                        "SELECT rev FROM entries WHERE id = ?",
                        (remote_id.split(":", 1)[1],),
                    ).fetchone()
                    current_rev = str(row["rev"] or "") if row is not None else ""
                    supplied_rev = str(doc.get("_rev") or "")
                    if row is not None and current_rev and supplied_rev != current_rev:
                        results.append({"id": remote_id, "error": "conflict", "reason": "document update conflict"})
                        continue
                    normalized = dict(doc)
                    normalized["_rev"] = self._next_notes_couchdb_rev(current_rev)
                    applied_result = self.notes_sync._upsert_remote_entry(conn, normalized)
                    if applied_result:
                        applied.append(applied_result)
                    results.append({"id": remote_id, "ok": True, "rev": normalized["_rev"]})
                    touched = True
                    continue
                results.append({"id": remote_id, "error": "bad_request", "reason": "unsupported_document"})
            if touched:
                self.notes_store._next_cursor(conn)
        if touched:
            self._invalidate_remote_notes_changes_cache()
            self._on_notes_remote_ops_applied_safe(
                {
                    "cursor": self.notes_store.current_cursor(),
                    "applied": applied,
                    "conflicts": [],
                }
            )
        return 201, results

    def _remote_api_notes_bulk_docs(self, payload: dict | None = None) -> tuple[int, dict]:
        status, results = self._remote_api_notes_couchdb_bulk_docs(payload)
        return status, {"results": results}

    def _schedule_notes_couchdb_sync(self) -> None:
        if not getattr(self, "notes_sync", None) or not self.notes_sync.is_configured():
            return
        with self._notes_sync_worker_lock:
            if self._notes_sync_worker_scheduled:
                return
            self._notes_sync_worker_scheduled = True
        worker = threading.Thread(target=self._run_notes_couchdb_sync, daemon=True)
        worker.start()

    def _run_notes_couchdb_sync(self) -> None:
        try:
            self.notes_sync.sync_once()
        except Exception:
            self._on_notes_sync_status_changed_safe("failed", message="同步失败", cursor=self.notes_sync.get_checkpoint())
        finally:
            with self._notes_sync_worker_lock:
                self._notes_sync_worker_scheduled = False

    def _on_notes_remote_ops_applied(self, result: dict | None) -> None:
        result = dict(result or {})
        self._invalidate_remote_notes_changes_cache()
        self._invalidate_notes_projection()
        active_entry_id = str(getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        active_changed = False
        if active_entry_id:
            for op_result in list(result.get("applied") or []):
                if not isinstance(op_result, dict):
                    continue
                entry = op_result.get("entry")
                if isinstance(entry, dict) and str(entry.get("id") or "") == active_entry_id:
                    active_changed = True
                    break
                conflicts = list(op_result.get("conflicts") or [])
                for conflict in conflicts:
                    if isinstance(conflict, dict) and str(conflict.get("origin_entry_id") or "") == active_entry_id:
                        active_changed = True
                        break
                if active_changed:
                    break
            if not active_changed:
                for conflict in list(result.get("conflicts") or []):
                    if isinstance(conflict, dict) and str(conflict.get("origin_entry_id") or "") == active_entry_id:
                        active_changed = True
                        break
        if self._notes_remote_ops_affect_visible_ui(result, active_entry_id=active_entry_id):
            self._notes_refresh_ui()
        cursor = str(result.get("cursor") or self.notes_store.current_cursor() or "0")
        if active_changed:
            message = "当前编辑的笔记已被远程更新，已刷新。"
            if any(isinstance(item, dict) for item in list(result.get("conflicts") or [])):
                message = "当前编辑的笔记已产生冲突，已刷新。"
            self._show_notes_sync_hint(message)
            self._push_remote_notes_conflict({"entry_id": active_entry_id, "cursor": cursor, "conflicts": list(result.get("conflicts") or [])})
            self._push_remote_notes_sync_status({"status": "conflict"}, cursor=cursor, message=message)
        else:
            self._show_notes_sync_hint("笔记已同步")
            self._push_remote_notes_sync_status({"status": "synced"}, cursor=cursor, message="笔记已同步")

    def _notes_remote_ops_affect_visible_ui(self, result: dict, *, active_entry_id: str = "") -> bool:
        applied = list(result.get("applied") or [])
        conflicts = list(result.get("conflicts") or [])
        if not applied and not conflicts:
            return False
        active_notebook_id = str(getattr(self.notes_controller, "active_notebook_id", "") or "").strip()
        active_entry_id = str(active_entry_id or getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        for item in applied:
            if not isinstance(item, dict):
                return True
            entity_type = str(item.get("entity_type") or "").strip()
            if entity_type == "notebook":
                return True
            if entity_type != "entry":
                return True
            entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
            entry_id = str(item.get("entity_id") or entry.get("id") or "").strip()
            notebook_id = str(entry.get("notebook_id") or item.get("notebook_id") or "").strip()
            origin_entry_id = str(entry.get("origin_entry_id") or item.get("origin_entry_id") or "").strip()
            if active_entry_id and active_entry_id in {entry_id, origin_entry_id}:
                return True
            if active_notebook_id and notebook_id == active_notebook_id:
                return True
            if not notebook_id and not entry:
                return True
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                return True
            origin_entry_id = str(conflict.get("origin_entry_id") or "").strip()
            notebook_id = str(conflict.get("notebook_id") or "").strip()
            if active_entry_id and origin_entry_id == active_entry_id:
                return True
            if active_notebook_id and notebook_id == active_notebook_id:
                return True
        return False

    def _show_notes_menu(self) -> None:
        if self.notes_editor.HasFocus():
            return
        show_notebook_actions = self.notes_notebook_list.HasFocus() or self.notes_controller.notes_view == "notes_list"
        show_entry_actions = self.notes_entry_list.HasFocus() or self.notes_controller.notes_view == "note_detail"
        menu = wx.Menu()
        if show_notebook_actions:
            i_open_nb = wx.NewIdRef()
            i_new_nb = wx.NewIdRef()
            i_export_all = wx.NewIdRef()
            i_restore_backup = wx.NewIdRef()
            i_export_nb = wx.NewIdRef()
            i_copy_nb = wx.NewIdRef()
            i_del_nb = wx.NewIdRef()
            i_search_nb = wx.NewIdRef()
            i_ren_nb = wx.NewIdRef()
            menu.Append(i_open_nb, "打开笔记")
            menu.AppendSeparator()
            menu.Append(i_new_nb, "新建笔记")
            menu.Append(i_export_all, "导出所有笔记")
            menu.Append(i_restore_backup, "恢复笔记")
            menu.Append(i_export_nb, "导出到剪贴板")
            menu.Append(i_copy_nb, "复制笔记")
            menu.Append(i_del_nb, "删除笔记")
            menu.Append(i_search_nb, "搜索笔记")
            menu.Append(i_ren_nb, "重命名笔记")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_open_selected_notebook(), id=i_open_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_notebook(), id=i_new_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_all_to_file(), id=i_export_all)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_restore_from_backup_file(), id=i_restore_backup)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_notebook_to_clipboard(), id=i_export_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_notebook_to_clipboard(), id=i_copy_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_delete_notebook(), id=i_del_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_prompt_search(), id=i_search_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_rename_notebook(), id=i_ren_nb)
        elif show_entry_actions:
            i_new_entry = wx.NewIdRef()
            i_copy_entry = wx.NewIdRef()
            i_export_all = wx.NewIdRef()
            i_restore_backup = wx.NewIdRef()
            i_export_down = wx.NewIdRef()
            i_export_up = wx.NewIdRef()
            i_del_entry = wx.NewIdRef()
            i_edit_entry = wx.NewIdRef()
            i_pin_entry = wx.NewIdRef()
            i_bottom_entry = wx.NewIdRef()
            i_import_file = wx.NewIdRef()
            i_import_clip = wx.NewIdRef()
            menu.Append(i_new_entry, "新建笔记条目")
            menu.Append(i_copy_entry, "复制笔记条目")
            menu.Append(i_export_all, "导出所有笔记")
            menu.Append(i_restore_backup, "恢复笔记")
            menu.Append(i_export_down, "向下导出全部到剪贴板")
            menu.Append(i_export_up, "向上导出全部到剪贴板")
            menu.Append(i_del_entry, "删除笔记条目")
            menu.Append(i_edit_entry, "编辑笔记条目")
            menu.Append(i_pin_entry, "置顶笔记条目")
            menu.Append(i_bottom_entry, "置底笔记条目")
            menu.AppendSeparator()
            menu.Append(i_import_file, "从文件导入")
            menu.Append(i_import_clip, "从剪贴板导入")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_entry(), id=i_new_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_entry_to_clipboard(), id=i_copy_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_all_to_file(), id=i_export_all)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_restore_from_backup_file(), id=i_restore_backup)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_selected_range_to_clipboard("down"), id=i_export_down)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_selected_range_to_clipboard("up"), id=i_export_up)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_delete_entry(), id=i_del_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_edit_entry(), id=i_edit_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_pin_entry(), id=i_pin_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_to_bottom(), id=i_bottom_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_import_from_file(), id=i_import_file)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_import_from_clipboard(), id=i_import_clip)
        else:
            menu.Destroy()
            return
        self.PopupMenu(menu)
        menu.Destroy()

    def _notes_apply_search(self, query: str | None = None) -> None:
        if query is not None:
            self._notes_search_query = str(query or "")
        self._notes_refresh_notebooks()
        self._notes_refresh_entries()

    def _notes_prompt_search(self) -> bool:
        initial = str(getattr(self, "_notes_search_query", "") or "").strip()
        dlg = wx.TextEntryDialog(self, "请输入要搜索的笔记名称：", "搜索笔记", initial)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                try:
                    self.notes_notebook_list.SetFocus()
                except Exception:
                    pass
                return False
            self._notes_apply_search(dlg.GetValue())
            try:
                self.notes_notebook_list.SetFocus()
            except Exception:
                pass
            return True
        finally:
            dlg.Destroy()

    def _notes_select_notebook(self, notebook_id: str, entry_id: str | None = None, view: str = "note_detail", *, focus: bool = True) -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_notebook_id = str(notebook_id or "")
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_detail")
        self.notes_controller.entry_editor_dirty = False
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        if focus:
            try:
                if self.notes_controller.notes_view == "notes_list":
                    self.notes_notebook_list.SetFocus()
                elif self.notes_controller.notes_view == "note_detail":
                    self.notes_entry_list.SetFocus()
            except Exception:
                pass

    def _notes_select_entry(self, entry_id: str, view: str = "note_edit", *, focus: bool = True) -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_edit")
        self.notes_controller.entry_editor_dirty = False
        projection = getattr(self, "notes_projection", None)
        entry = projection.get_entry(entry_id, include_deleted=True) if projection is not None else self.notes_store.get_entry(entry_id, include_deleted=True)
        self.notes_controller.entry_editor_base_version = int(entry.version if entry is not None else 0)
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        if focus:
            try:
                if self.notes_controller.notes_view == "note_edit":
                    self.notes_editor.SetFocus()
            except Exception:
                pass

    def _notes_set_view(self, view: str) -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.notes_view = str(view or "notes_list")
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()

    def _notes_save_current_entry(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        capture = getattr(self.notes_controller, "capture_editor_state", None)
        if callable(capture):
            capture()
        content = str(self.notes_editor.GetValue() or "").strip()
        entry = self._notes_current_entry()
        if entry is None:
            if not content:
                return False
            entry = self.notes_store.create_entry(notebook.id, content, source="manual")
        else:
            base_version = int(getattr(self.notes_controller, "entry_editor_base_version", 0) or 0)
            if base_version and entry.version != base_version:
                source_label = "电脑端"
                conflict_content = f"【冲突副本：来自{source_label}】\n{content}"
                conflict = self.notes_store.create_entry(
                    notebook.id,
                    conflict_content,
                    source=str(entry.source or "manual"),
                    entry_id=None,
                    device_id=self.notes_store.device_id,
                    last_modified_by="desktop",
                    is_conflict_copy=True,
                    origin_entry_id=entry.id,
                )
                self.notes_controller.active_entry_id = conflict.id
                self.notes_controller.entry_editor_base_version = conflict.version
                self.notes_controller.entry_editor_draft = conflict_content
                self.notes_controller.entry_editor_dirty = False
                self.notes_controller.notes_view = "note_detail"
                self._current_notes_state = self.notes_controller.to_state_dict()
                self._notes_refresh_ui()
                self._push_remote_notes_conflict(conflict.to_dict())
                self._on_notes_sync_status_changed("conflict", cursor=self.notes_store.current_cursor(), message="冲突副本已创建")
                self._push_remote_notes_changed(self.notes_store.current_cursor())
                return True
            entry = self.notes_store.update_entry(entry.id, content, source="manual")
        self.notes_controller.active_entry_id = entry.id
        self.notes_controller.entry_editor_draft = content
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_base_version = entry.version
        self.notes_controller.notes_view = "note_detail"
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        try:
            self.notes_entry_list.SetFocus()
        except Exception:
            pass
        self._notes_after_local_mutation(message="笔记已保存，待同步")
        return True

    def _notes_create_notebook(self) -> bool:
        dlg = wx.TextEntryDialog(self, "请输入笔记本名称", "新建笔记本")
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title = str(dlg.GetValue() or "").strip()
        finally:
            dlg.Destroy()
        if not title:
            return False
        notebook = self.notes_store.create_notebook(title)
        self._notes_select_notebook(notebook.id, view="note_detail")
        self._notes_after_local_mutation()
        return True

    def _notes_rename_notebook(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        dlg = wx.TextEntryDialog(self, "请输入新的笔记本名称", "重命名笔记本", value=notebook.title)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title = str(dlg.GetValue() or "").strip()
        finally:
            dlg.Destroy()
        if not title:
            return False
        self.notes_store.rename_notebook(notebook.id, title)
        self._notes_refresh_ui()
        self._notes_after_local_mutation()
        return True

    def _notes_delete_notebook(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        if not self._confirm("确定删除该笔记本吗？"):
            return False
        self.notes_store.delete_notebook(notebook.id)
        self.notes_controller.active_notebook_id = ""
        self.notes_controller.active_entry_id = ""
        self.notes_controller.notes_view = "notes_list"
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        self._notes_after_local_mutation()
        return True

    def _notes_create_entry(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_notebook_id = notebook.id
        self.notes_controller.active_entry_id = ""
        self.notes_controller.notes_view = "note_edit"
        self.notes_controller.entry_editor_draft = ""
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        try:
            self.notes_editor.SetFocus()
        except Exception:
            pass
        return True

    def _notes_edit_entry(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        self._notes_select_entry(entry.id, view="note_edit")
        return True

    def _notes_delete_entry(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        if not self._confirm("确定删除该条目吗？"):
            return False
        notebook_id = entry.notebook_id
        self.notes_store.delete_entry(entry.id)
        self.notes_controller.active_entry_id = ""
        self.notes_controller.notes_view = "note_detail"
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_entries(notebook_id)
        self._notes_after_local_mutation()
        return True

    def _notes_pin_entry(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        self.notes_store.pin_entry(entry.id)
        self._notes_refresh_ui()
        self._notes_after_local_mutation()
        return True

    def _notes_move_entry_to_bottom(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        self.notes_store.move_entry_to_bottom(entry.id)
        self._notes_refresh_ui()
        self._notes_after_local_mutation()
        return True

    def _notes_import_from_file(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        dlg = wx.FileDialog(self, "选择导入文件", wildcard="文本文件 (*.txt)|*.txt|所有文件 (*.*)|*.*", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        created = import_note_entries_from_file(self.notes_store, notebook.id, path)
        if created:
            self._notes_select_notebook(notebook.id, created[0].id, view="note_detail")
            self._notes_after_local_mutation()
        return bool(created)

    def _notes_export_all_to_file(self) -> bool:
        default_name = f"notes-backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
        dlg = wx.FileDialog(
            self,
            "导出所有笔记",
            defaultFile=default_name,
            wildcard="笔记备份 (*.json)|*.json|所有文件 (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        if not path.suffix:
            path = path.with_suffix(".json")
        try:
            result = export_notes_backup(self.notes_store, path)
        except Exception as exc:
            self.SetStatusText(f"导出笔记失败：{exc}")
            return False
        self.SetStatusText(f"已导出所有笔记：{result['notebooks']} 个笔记，{result['entries']} 条笔记条目")
        return True

    def _notes_restore_from_backup_file(self) -> bool:
        dlg = wx.FileDialog(
            self,
            "恢复笔记",
            wildcard="笔记备份 (*.json)|*.json|所有文件 (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        try:
            result = restore_notes_backup(self.notes_store, path)
        except Exception as exc:
            self.SetStatusText(f"恢复笔记失败：{exc}")
            return False
        created_total = int(result.get("created_notebooks", 0) or 0) + int(result.get("created_entries", 0) or 0)
        if created_total:
            self._notes_refresh_ui()
            self._notes_after_local_mutation()
        self.SetStatusText(
            f"已恢复笔记：新增 {int(result.get('created_notebooks', 0) or 0)} 个笔记，"
            f"{int(result.get('created_entries', 0) or 0)} 条笔记条目"
        )
        return bool(created_total)

    def _notes_get_clipboard_text(self) -> str:
        if wx.TheClipboard.Open():
            try:
                data = wx.TextDataObject()
                if wx.TheClipboard.GetData(data):
                    return data.GetText()
            finally:
                wx.TheClipboard.Close()
        return ""

    def _set_clipboard_text(self, text: str) -> bool:
        payload = str(text or "")
        if not payload:
            return False
        if wx.TheClipboard.Open():
            try:
                return bool(wx.TheClipboard.SetData(wx.TextDataObject(payload)))
            finally:
                wx.TheClipboard.Close()
        return False

    @staticmethod
    def _notes_export_text(entries) -> str:
        parts = [str(getattr(entry, "content", "") or "") for entry in list(entries or [])]
        if not any(part.strip() for part in parts):
            return ""
        return "\n\n".join(parts)

    def _notes_export_entries_to_clipboard(self, entries, *, status_text: str) -> bool:
        text = self._notes_export_text(entries)
        if not text:
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText(status_text)
        return True

    def _notes_export_selected_range_to_clipboard(self, direction: str) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        selected_entry_id = str(self._notes_selected_entry_id() or "").strip()
        if not selected_entry_id:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        entry_ids = [str(entry.id) for entry in entries]
        if selected_entry_id not in entry_ids:
            return False
        selected_index = entry_ids.index(selected_entry_id)
        if str(direction or "") == "up":
            export_entries = entries[: selected_index + 1]
            status_text = "已向上导出笔记条目到剪贴板"
        else:
            export_entries = entries[selected_index:]
            status_text = "已向下导出笔记条目到剪贴板"
        return self._notes_export_entries_to_clipboard(export_entries, status_text=status_text)

    def _notes_export_notebook_to_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        return self._notes_export_entries_to_clipboard(entries, status_text="已导出笔记到剪贴板")

    def _notes_copy_notebook_to_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        text = "\n\n".join(str(entry.content or "") for entry in entries if str(entry.content or "").strip())
        if not text:
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText("已复制笔记")
        return True

    def _notes_copy_entry_to_clipboard(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        text = str(entry.content or "")
        if not text.strip():
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText("已复制笔记条目")
        return True

    def _notes_import_from_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        text = self._notes_get_clipboard_text()
        created = import_note_entries_from_clipboard(self.notes_store, notebook.id, text)
        if created:
            self._notes_select_notebook(notebook.id, created[0].id, view="note_detail")
            self._notes_after_local_mutation()
        return bool(created)

    def _on_notes_key_down(self, event):
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE and getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_edit":
            if self._notes_request_exit_edit():
                return
        if self._on_any_key_down_escape_minimize(event):
            return
        if key == wx.WXK_MENU:
            self._show_notes_menu()
            return
        if event.AltDown() and key in (ord("X"), ord("x")):
            if self.notes_notebook_list.HasFocus():
                if self._notes_create_notebook():
                    return
            if self.notes_entry_list.HasFocus():
                if self._notes_create_entry():
                    return
        if event.ControlDown() and key in (ord("C"), ord("c")):
            if self.notes_notebook_list.HasFocus():
                if self._notes_copy_notebook_to_clipboard():
                    return
            if self.notes_entry_list.HasFocus():
                if self._notes_copy_entry_to_clipboard():
                    return
        if (
            getattr(self, "notes_controller", None)
            and self.notes_controller.notes_view == "note_edit"
            and ((event.ControlDown() and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)) or (event.AltDown() and key in (ord("S"), ord("s"))))
        ):
            self._notes_save_current_entry()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self.notes_notebook_list.HasFocus():
                if self._notes_open_selected_notebook():
                    return
            if self.notes_entry_list.HasFocus():
                entry_id = self._notes_selected_entry_id()
                if entry_id:
                    self._notes_select_entry(entry_id, view="note_edit")
                    return
        if key == wx.WXK_BACK:
            if self.notes_entry_list.HasFocus() and getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_detail":
                notebook_id = self._notes_selected_notebook_id()
                if notebook_id:
                    self._notes_select_notebook(notebook_id, view="notes_list")
                    return
        if key == wx.WXK_DELETE:
            if self.notes_entry_list.HasFocus():
                self._notes_delete_entry()
                return
            if self.notes_notebook_list.HasFocus():
                self._notes_delete_notebook()
                return
        event.Skip()

    def _on_notes_context(self, _event):
        self._show_notes_menu()

    def _on_notes_notebook_selected(self, _event):
        notebook_id = self._notes_selected_notebook_id()
        if notebook_id:
            if notebook_id != str(self.notes_controller.active_notebook_id or ""):
                self.notes_controller.root_tab = "notes"
                self.notes_controller.active_notebook_id = notebook_id
                self.notes_controller.active_entry_id = ""
                self.notes_controller.notes_view = "notes_list"
                self.notes_controller.entry_editor_dirty = False
                self._current_notes_state = self.notes_controller.to_state_dict()
                self._notes_refresh_entries(notebook_id)
        else:
            self._notes_refresh_ui()

    def _on_notes_entry_selected(self, _event):
        entry_id = self._notes_selected_entry_id()
        if entry_id:
            current_view = self.notes_controller.notes_view or "note_detail"
            view = "note_detail" if current_view == "note_edit" else current_view
            self._notes_select_entry(entry_id, view=view, focus=False)

    def _notes_open_selected_notebook(self) -> bool:
        notebook_id = self._notes_selected_notebook_id()
        if not notebook_id:
            return False
        self._notes_select_notebook(notebook_id, view="note_detail")
        return True

    def _confirm(self, message: str, title: str = "确认") -> bool:
        # 默认按钮使用“是”，避免键盘焦点默认落在“否”。
        dlg = wx.MessageDialog(self, message, title, wx.YES_NO | wx.ICON_QUESTION)
        ret = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        return ret

    def _history_delete(self, _):
        self._flush_chat_state_save()
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.history_ids):
            return
        if not self._confirm("确定删除该聊天吗？"):
            return
        cid = self.history_ids[idx]
        target_chat = self._find_archived_chat(cid)
        if target_chat:
            self._cleanup_chat_detail_pages(target_chat)
        self.archived_chats = [c for c in self.archived_chats if c.get("id") != cid]
        if self.view_history_id == cid:
            self.view_mode = "active"
            self.view_history_id = None
        if cid == self.current_chat_id or cid == self.active_chat_id:
            self.current_chat_id = ""
            self.active_chat_id = ""
            self.active_session_turns = []
            self._current_chat_state = {"id": "", "turns": self.active_session_turns}
        self._save_state()
        self._refresh_history()
        self._push_remote_history_changed(cid)
        self._render_answer_list()

    def _cleanup_chat_detail_pages(self, chat: dict) -> None:
        items = list(chat.get("turns") or []) + list(chat.get("execution_steps") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            for k in ("question_detail_page_path", "answer_detail_page_path", "detail_page_path"):
                raw = str(item.get(k) or "").strip()
                if not raw:
                    continue
                try:
                    p = Path(raw)
                    if not p.exists():
                        continue
                    # Only remove generated pages under the app detail_pages directory.
                    if not p.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                        continue
                    p.unlink(missing_ok=True)
                except Exception:
                    continue

    def _history_pin(self, _):
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.history_ids):
            return
        c = self._find_archived_chat(self.history_ids[idx])
        if not c:
            return
        c["pinned"] = not bool(c.get("pinned"))
        self._save_state()
        self._refresh_history(c.get("id"))
        self._push_remote_history_changed(c.get("id"))

    def _history_clear_non_pinned(self, _):
        self._flush_chat_state_save()
        if not self._confirm("确定清空所有非置顶聊天吗？"):
            return
        self.archived_chats = [c for c in self.archived_chats if c.get("pinned")]
        if self.view_mode == "history" and self.view_history_id not in {str(c.get("id")) for c in self.archived_chats}:
            self.view_mode = "active"
            self.view_history_id = None
        self._save_state()
        self._refresh_history()
        self._push_remote_history_changed()
        self._render_answer_list()

    def _history_rename(self, _):
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.history_ids):
            return
        c = self._find_archived_chat(self.history_ids[idx])
        if not c:
            return
        d = RenameDialog(self, str(c.get("title") or ""))
        if d.ShowModal() == wx.ID_OK:
            name = d.get_value()
            if name:
                c["title"] = name
                c["title_manual"] = True
                self._save_state()
                self._refresh_history(c.get("id"))
                self._push_remote_history_changed(c.get("id"))
        d.Destroy()

    def _on_close(self, event: wx.CloseEvent):
        # Always allow close (e.g. Alt+F4) even during active reply.
        self._flush_chat_state_save()
        self._flush_execution_step_persists_sync()
        self._voice_input.cancel()
        self._realtime_call.shutdown()
        if self._answer_redirect_timer:
            try:
                if self._answer_redirect_timer.IsRunning():
                    self._answer_redirect_timer.Stop()
            except Exception:
                pass
            self._answer_redirect_timer = None
        for client in list(getattr(self, "_codex_clients", {}).values()):
            try:
                client.close()
            except Exception:
                pass
        if getattr(self, "_codex_client", None) is not None:
            try:
                self._codex_client.close()
            except Exception:
                pass
        try:
            self.notes_sync.close()
        except Exception:
            pass
        try:
            self._stop_remote_servers()
        except Exception:
            pass
        self._save_state(capture_notes_editor=True)
        self._global_ctrl_hook.stop()
        self._unregister_global_hotkey()
        if self._tray_icon:
            try:
                self._tray_icon.RemoveIcon()
                self._tray_icon.Destroy()
            except Exception:
                pass
            self._tray_icon = None
        event.Skip()

    def _play_finish_sound(self):
        if self.reply_sound:
            try:
                winsound.PlaySound(self.reply_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def _play_send_sound(self):
        if self.send_sound:
            try:
                winsound.PlaySound(self.send_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_OK)

    def _play_voice_begin_sound(self):
        if self.voice_begin_sound:
            try:
                winsound.PlaySound(self.voice_begin_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_OK)

    def _play_voice_end_sound(self):
        if self.voice_end_sound:
            try:
                winsound.PlaySound(self.voice_end_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def _play_voice_wrong_sound(self):
        if self.voice_wrong_sound:
            try:
                winsound.PlaySound(self.voice_wrong_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONHAND)


class ChatApp(wx.App):
    def OnInit(self):
        self._checker = wx.SingleInstanceChecker(APP_WINDOW_TITLE + "_single_instance")
        if self._checker.IsAnotherRunning():
            self._activate_existing_window()
            return False
        f = ChatFrame()
        f.Show()
        return True

    def _activate_existing_window(self):
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_WINDOW_TITLE)
        if hwnd:
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)


if __name__ == "__main__":
    app = ChatApp()
    app.MainLoop()

