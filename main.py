import ctypes
import copy
import json
import os
import platform
import re
import shutil
import threading
import time
import uuid
import webbrowser
import winsound
import sys
from ctypes import wintypes
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import markdown
import wx
import wx.adv

from chat_client import ChatClient, DEFAULT_MODEL
from claudecode_client import ClaudeCodeClient, DEFAULT_CLAUDECODE_MODEL, is_claudecode_model
from codex_client import (
    CodexAppServerClient,
    CodexEvent,
    DEFAULT_CODEX_MODEL,
    is_codex_model,
)
from notes_import import import_note_entries_from_clipboard, import_note_entries_from_file
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
    normalize_openclaw_text,
    read_session_events,
    resolve_openclaw_sessions_dir,
)
from remote_http import RemoteControlHttpServer
from remote_ws import RemoteWebSocketServer
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
DEFAULT_REMOTE_CONTROL_DOMAIN = "wss://rc.tingyou.cc/ws"
VK_PROCESSKEY = 0xE5
VK_PACKET = 0xE7
VK_V = 0x56
VK_OEM_5 = 0xDC
VK_OEM_102 = 0xE2
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004

MODEL_IDS = [
    "openclaw/main",
    "codex/main",
    "claudecode/default",
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
DEFAULT_MODEL_ID = "openai/gpt-5.2"
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
        if isinstance(target, wx.Window):
            if hasattr(target, "IsBeingDeleted") and target.IsBeingDeleted():
                return False
            if hasattr(target, "GetHandle") and not target.GetHandle():
                return False
        return True
    except Exception:
        return False


def wx_call_after_if_alive(func, *args, **kwargs) -> bool:
    if wx.GetApp() is None:
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
    if wx.GetApp() is None:
        return None
    target = getattr(func, "__self__", None)
    if target is not None and not _wx_target_is_alive(target):
        return None
    try:
        return wx.CallLater(delay_ms, func, *args, **kwargs)
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


def normalize_remote_ws_endpoint(value: str, *, default_scheme: str = "wss") -> str:
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
    if not path:
        path = "/ws"
    elif path != "/ws":
        path = "/ws" if path.endswith("/ws") else f"{path}/ws"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def is_loopback_remote_host(value: str) -> bool:
    host = str(value or "").strip().lower()
    return host in {"", "127.0.0.1", "localhost", "::1"}


def model_display_name(model_id: str) -> str:
    """Convert model ID to display name."""
    if not model_id:
        return ""
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
        was_left_down = False
        was_right_down = False
        try:
            while self._running:
                left_down = bool(user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)
                right_down = bool(user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000)
                if (not self._hook or self._using_fallback) and (not self._fallback_notice_sent) and self.on_error:
                    self._fallback_notice_sent = True
                    wx_call_after_if_alive(self.on_error, "全局语音热键进入兼容模式（轮询）")
                should_emit = self._should_use_poller_release()
                if should_emit and was_left_down and (not left_down):
                    self._emit_ctrl_keyup(False, "left")
                if should_emit and was_right_down and (not right_down):
                    self._emit_ctrl_keyup(False, "right")
                was_left_down = left_down
                was_right_down = right_down
                time.sleep(0.015)
        finally:
            self._using_fallback = False

    def _should_use_poller_release(self) -> bool:
        if not self._running:
            return False
        if not self._hook or self._using_fallback:
            return True
        last = float(self._last_hook_event_at or 0.0)
        if last <= 0.0:
            return True
        return (time.monotonic() - last) >= self._hook_stale_seconds

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
        self.notes_db_path = self.app_data_dir / "notes.db"
        self.notes_device_id = f"desktop-{platform.node().strip().lower() or 'local'}"
        self.notes_store = NotesStore(self.notes_db_path, device_id=self.notes_device_id)
        self.notes_store.initialize()
        self.notes_sync = NotesSyncService(
            self.notes_store,
            broadcaster=self._on_notes_sync_push_result,
            on_remote_ops_applied=self._on_notes_remote_ops_applied,
            on_status_changed=self._on_notes_sync_status_changed,
        )
        self._current_notes_state = {}
        self.notes_sync_hint = ""
        self.send_sound = self._resolve_sound_path("send")
        self.reply_sound = self._resolve_sound_path("reply")
        self.voice_begin_sound = self._resolve_sound_path("inputBegin")
        self.voice_end_sound = self._resolve_sound_path("inputEnd")
        self.voice_wrong_sound = self._resolve_sound_path("inputWrong")
        self._zdsr_tts = ZDSRTTSClient()
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
        self._codex_clients: dict[str, CodexAppServerClient] = {}
        self._remote_ws_server = None
        self._remote_http_server = None
        self._codex_background_flush_scheduled = False
        self._codex_background_flush_dirty = False
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
        self.remote_control_port = 18080
        self.remote_control_autostart = True
        self.remote_control_runtime_mode = "local"
        self.remote_control_runtime_bind = ""
        self.remote_control_runtime_url = ""

        self.history_ids = []
        self.answer_meta = []
        self.is_running = False
        self._active_request_count = 0
        self.active_turn_idx = -1
        self._active_answer_row_index = -1
        self.input_hint_state = "输入"
        self._answer_committed_buffer = ""
        self._answer_redirect_timer = None
        self._openclaw_sync_thread = None
        self._openclaw_sync_stop = threading.Event()
        self._openclaw_sync_lock = threading.Lock()
        self._is_in_tray = False
        self._tray_icon = None
        self._show_hotkey_registered = False
        self._realtime_call_hotkey_registered_ids = set()
        self.realtime_call_role = DEFAULT_REALTIME_CALL_ROLE
        self.realtime_call_speech_rate = DEFAULT_REALTIME_CALL_SPEECH_RATE
        self._voice_input = VoiceInputController(
            on_state_change=lambda text: wx_call_after_if_alive(self._on_voice_state, text),
            on_result=lambda text, mode: wx_call_after_if_alive(self._on_voice_result, text, mode),
            on_error=lambda msg: wx_call_after_if_alive(self._on_voice_error, msg),
            on_stop_recording=lambda: wx_call_after_if_alive(self._on_voice_stop_recording),
        )
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
        self._load_state()
        self._realtime_call.update_settings(
            RealtimeCallSettings(role=self.realtime_call_role, speech_rate=self.realtime_call_speech_rate)
        )
        self._initialize_remote_control_settings()
        wx_call_after_if_alive(self._realtime_call.prepare)
        self._merge_legacy_archived_chats()
        if self.remote_control_autostart:
            self._start_remote_ws_server_if_configured()
        self._start_claudecode_remote_ws_server_if_configured()
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

        right.Add(wx.StaticText(panel, label="回答："), 0, wx.LEFT, 10)
        self.answer_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.answer_list.SetName("回答列表")
        right.Add(self.answer_list, 1, wx.EXPAND | wx.ALL, 10)
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
        self.send_button.MoveAfterInTabOrder(self.input_edit)
        self.new_chat_button.MoveAfterInTabOrder(self.send_button)
        self.model_combo.MoveAfterInTabOrder(self.new_chat_button)
        self.history_list.MoveAfterInTabOrder(self.model_combo)
        self.notes_list_panel.MoveAfterInTabOrder(self.history_list)
        self.answer_list.MoveAfterInTabOrder(self.notes_list_panel)
        self.chat_root_panel.SetSizer(root)

        self.root_tab_order = [
            self.input_edit,
            self.send_button,
            self.new_chat_button,
            self.model_combo,
            self.history_list,
            self.notes_notebook_list,
            self.answer_list,
            self.notes_entry_list,
            self.notes_editor,
        ]
        for previous, nxt in zip(self.root_tab_order, self.root_tab_order[1:]):
            try:
                nxt.MoveAfterInTabOrder(previous)
            except Exception:
                pass
        self.chat_tab_order = [
            self.input_edit,
            self.send_button,
            self.new_chat_button,
            self.model_combo,
            self.history_list,
            self.notes_notebook_list,
            self.answer_list,
        ]
        self.notes_tab_order = [
            self.notes_notebook_list,
            self.notes_entry_list,
            self.notes_editor,
        ]
        self._sync_notes_ui()

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
        self.Bind(wx.EVT_KEY_DOWN, self._on_any_key_down_escape_minimize)

        self.answer_list.Bind(wx.EVT_KEY_DOWN, self._on_answer_key_down)
        self.answer_list.Bind(wx.EVT_CHAR, self._on_answer_char)
        self.answer_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_answer_activate)
        self.history_list.Bind(wx.EVT_KEY_DOWN, self._on_history_key_down)
        self.history_list.Bind(wx.EVT_CHAR, self._on_history_char)
        self.history_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._activate_selected_history())
        self.history_list.Bind(wx.EVT_LISTBOX, self._on_history_selected)
        self.history_list.Bind(wx.EVT_CONTEXT_MENU, self._on_history_context)
        self.model_combo.Bind(wx.EVT_KEY_DOWN, self._on_any_key_down_escape_minimize)
        self.send_button.Bind(wx.EVT_KEY_DOWN, self._on_any_key_down_escape_minimize)
        self.new_chat_button.Bind(wx.EVT_KEY_DOWN, self._on_any_key_down_escape_minimize)

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
            self._save_state()

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

        archived = data.get("archived_chats")
        if isinstance(archived, list):
            self.archived_chats = archived
        else:
            legacy = data.get("chats")
            if isinstance(legacy, list):
                self.archived_chats = legacy
        changed = False
        for chat in self.archived_chats:
            if isinstance(chat, dict) and self._normalize_archived_chat(chat):
                changed = True

        active_turns = data.get("active_session_turns")
        if isinstance(active_turns, list):
            self.active_session_turns = active_turns
        active_chat = data.get("active_chat")
        if isinstance(active_chat, dict):
            self._current_chat_state = active_chat
            if not isinstance(active_turns, list):
                chat_turns = active_chat.get("turns")
                if isinstance(chat_turns, list):
                    self.active_session_turns = chat_turns
            self.active_chat_id = str(active_chat.get("id") or self.active_chat_id or "").strip()
        self.active_chat_id = str(data.get("active_chat_id") or self.active_chat_id or "").strip()
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
            self.remote_control_port = int(data.get("remote_control_port") or self.remote_control_port or 18080)
        except Exception:
            self.remote_control_port = 18080
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
        if hasattr(self, "notes_controller"):
            self.notes_controller.restore_state(self._current_notes_state)
            self._notes_refresh_ui()
        self._sort_archived_chats()
        if changed:
            self._save_state()

    def _save_state(self):
        if hasattr(self, "notes_controller"):
            capture = getattr(self.notes_controller, "capture_editor_state", None)
            if callable(capture):
                capture()
            self._current_notes_state = self.notes_controller.to_state_dict()
        data = {
            "selected_model_id": self.selected_model,
            "archived_chats": self.archived_chats,
            "active_session_turns": self.active_session_turns,
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
            domain = normalize_remote_ws_endpoint(domain, default_scheme="wss")
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
            default=str(self.remote_control_port or 18080),
        ).strip() or "18080"
        try:
            port = int(port_text)
        except Exception:
            port = 18080
        if fixed_domain_mode:
            if port <= 0:
                port = 18080
        if port < 0:
            port = 18080
        if port == 0 and port_text != "0":
            port = 18080
        if port != self.remote_control_port:
            self.remote_control_port = port
            changed = True

        autostart = self._read_remote_control_bool_setting(
            "REMOTE_CONTROL_AUTOSTART",
            "CLAUDECODE_REMOTE_CONTROL_AUTOSTART",
            default=self.remote_control_autostart,
        )
        if autostart != self.remote_control_autostart:
            self.remote_control_autostart = autostart
            changed = True

        if changed:
            self._save_state()

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
        domain = normalize_remote_ws_endpoint(
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
            default=str(self.remote_control_port or 18080),
        ).strip() or "18080"
        try:
            port = int(port_text)
        except Exception:
            port = 18080
        if fixed_domain_mode:
            if is_loopback_remote_host(host):
                host = "0.0.0.0"
            if port <= 0:
                port = 18080
            published_base = domain
            publish_port = 18080
        else:
            if port < 0:
                port = 18080
            publish_port = port if port > 0 else 18080
            published_host = "127.0.0.1" if host == "0.0.0.0" else host
            published_base = normalize_remote_ws_endpoint(
                f"ws://{published_host}:{publish_port}/ws",
                default_scheme="ws",
            )
        return {
            "fixed_domain_mode": fixed_domain_mode,
            "host": host,
            "port": port,
            "published_base": published_base,
            "published_bind": f"ws://{host}:{publish_port}/ws",
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

    def _normalize_archived_chat(self, chat: dict) -> bool:
        changed = False
        title_manual = chat.get("title_manual")
        if not isinstance(title_manual, bool):
            title_manual = bool(title_manual)
            chat["title_manual"] = title_manual
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
        return normalized[:max_length].strip() if normalized else ""

    def _apply_auto_title_from_first_question(self, question: str, *, push_remote: bool = False) -> str:
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        if title_manual:
            return str(self._current_chat_state.get("title") or "").strip()
        title = self._compact_first_question_title(str(question or "").strip(), 18)
        if not title:
            title = self._summarize_recent_topic(
                [{"question": str(question or "").strip(), "answer_md": "", "model": self._resolve_current_model()}],
                os.getenv("OPENROUTER_API_KEY", "").strip(),
            )
        if not title:
            return ""
        self._current_chat_state["title"] = title
        self._current_chat_state["updated_at"] = time.time()
        self._refresh_history(self.active_chat_id or self.current_chat_id or None)
        if push_remote:
            self._push_remote_history_changed(self.active_chat_id or self.current_chat_id or "")
        return title

    def _sort_archived_chats(self):
        self.archived_chats.sort(
            key=lambda c: (0 if c.get("pinned") else 1, -float(c.get("created_at") or c.get("updated_at") or 0.0))
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
        if self.active_session_turns:
            summarized = self._summarize_last_turn_locally(self.active_session_turns).strip()
            if summarized:
                return summarized
        return title or EMPTY_CURRENT_CHAT_TITLE

    def _refresh_history(self, keep_id=None):
        self._sort_archived_chats()
        self.history_list.Clear()
        self.history_ids = []
        current_id = self._current_history_id()
        if current_id:
            self.history_list.Append(f"[当前] {self._current_history_title()}")
            self.history_ids.append(current_id)
        for c in self.archived_chats:
            if current_id and str(c.get("id") or "") == current_id:
                continue
            title = str(c.get("title") or "新聊天")
            disp = f"[置顶] {title}" if c.get("pinned") else title
            self.history_list.Append(disp)
            self.history_ids.append(str(c.get("id")))
        target = keep_id if keep_id is not None else self.view_history_id
        if target in self.history_ids:
            self.history_list.SetSelection(self.history_ids.index(target))
        elif self.history_list.GetCount() > 0:
            self.history_list.SetSelection(0)
        self._request_listbox_repaint(self.history_list)

    def _get_view_turns(self):
        if self.view_mode == "history":
            chat = self._find_archived_chat(self.view_history_id)
            if chat:
                return chat.get("turns") or []
            return []
        return self.active_session_turns

    def _render_answer_list(self):
        self.answer_list.Clear()
        self.answer_meta = []
        self._active_answer_row_index = -1
        turns = self._get_view_turns()
        if not turns:
            self.answer_list.Append("暂无对话内容")
            self.answer_meta.append(("info", -1, "", ""))
            self._request_listbox_repaint(self.answer_list)
            return
        for i, t in enumerate(turns):
            q = str(t.get("question") or "")
            a_md, a = self._turn_answer_markdown(t)
            show_pending_placeholder = a_md != REQUESTING_TEXT
            show_user_rows = not (
                (is_openclaw_model(str(t.get("model") or "")) or is_codex_model(str(t.get("model") or "")) or is_claudecode_model(str(t.get("model") or "")))
                and (not q.strip())
                and bool(a_md.strip())
            )
            if show_user_rows:
                self.answer_list.Append("我")
                self.answer_meta.append(("user", i, "我", ""))
                self.answer_list.Append(q)
                self.answer_meta.append(("question", i, q, ""))
            if show_pending_placeholder:
                self.answer_list.Append("小诸葛")
                self.answer_meta.append(("ai", i, "小诸葛", ""))
                self.answer_list.Append(a)
                self.answer_meta.append(("answer", i, a, a_md))
                if self.view_mode == "active" and i == self.active_turn_idx:
                    self._active_answer_row_index = self.answer_list.GetCount() - 1
        if self.answer_list.GetCount() > 0 and self.answer_list.GetSelection() == wx.NOT_FOUND:
            # 首次渲染时仅设置选中项，不主动把焦点移到回答列表。
            self.answer_list.SetSelection(self.answer_list.GetCount() - 1)
        self._request_listbox_repaint(self.answer_list)

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
        return self._is_foreground_window() and not self.IsIconized()

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
        self.answer_list.SetString(row, text)
        item_type, idx, _, _ = self.answer_meta[row]
        self.answer_meta[row] = (item_type, idx, text, answer_md)
        self._request_listbox_repaint(self.answer_list)
        return True

    def _on_model_changed(self, _):
        v = self.model_combo.GetValue().strip()
        if v:
            resolved = model_id_from_display_name(v)
            self.selected_model = resolved if resolved in MODEL_IDS else v
            self._refresh_openclaw_sync_lifecycle()
            self._save_state()

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
            pointer = load_session_pointer(
                resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json",
                session_key=self.active_openclaw_session_key or DEFAULT_OPENCLAW_SESSION_KEY,
            )
            if pointer and str(pointer.session_id or "").strip():
                self.active_openclaw_session_id = str(pointer.session_id or "").strip()
                self.active_openclaw_session_file = str(pointer.session_file or "").strip()
            else:
                self.active_openclaw_session_id = self._make_openclaw_session_id(self._ensure_active_chat_id())
        return self.active_openclaw_session_id

    def _has_openclaw_turns(self, turns: list[dict] | None = None) -> bool:
        for turn in turns or self.active_session_turns:
            if is_openclaw_model(str(turn.get("model") or "")):
                return True
        return False

    def _is_openclaw_sync_target_active(self) -> bool:
        return is_openclaw_model(self.selected_model) or self._has_openclaw_turns()

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
            pointer = load_session_pointer(sessions_json, self.active_openclaw_session_key or DEFAULT_OPENCLAW_SESSION_KEY)
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

    def _refresh_openclaw_sync_lifecycle(self, force_replay: bool = False) -> None:
        if not self._is_openclaw_sync_target_active():
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
        if not self._is_openclaw_sync_target_active():
            return
        pointer = load_session_pointer(
            resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json",
            session_key=self.active_openclaw_session_key or DEFAULT_OPENCLAW_SESSION_KEY,
        )
        if pointer is None:
            return

        session_file = str(pointer.session_file or "").strip()
        if not session_file:
            return

        with self._openclaw_sync_lock:
            previous_file = self.active_openclaw_session_file
            previous_session_id = self.active_openclaw_session_id
            previous_offset = int(self.active_openclaw_sync_offset or 0)
        session_id = str(pointer.session_id or "").strip()
        file_changed = previous_file != session_file
        session_changed = bool(session_id and previous_session_id and (previous_session_id != session_id))
        needs_replay = file_changed or session_changed
        offset = 0 if needs_replay else previous_offset
        new_offset, events = read_session_events(session_file, offset=offset)
        wx_call_after_if_alive(
            self._apply_openclaw_sync_batch,
            {
                "session_id": session_id,
                "session_file": session_file,
                "offset": new_offset,
                "updated_at": float(pointer.updated_at or 0.0),
                "file_changed": file_changed,
                "session_changed": session_changed,
            },
            events,
        )

    def _apply_openclaw_sync_batch(self, sync_state: dict, events: list[OpenClawSyncEvent]) -> None:
        changed = False
        file_changed = bool(sync_state.get("file_changed"))
        session_changed = bool(sync_state.get("session_changed"))
        had_prior_sync = bool(self.active_openclaw_last_synced_at or self.active_openclaw_session_file)
        assistant_changed = False
        if file_changed or session_changed:
            self.active_openclaw_session_file = str(sync_state.get("session_file") or "").strip()
            self.active_openclaw_sync_offset = 0
            self.active_openclaw_last_event_id = ""
        session_id = str(sync_state.get("session_id") or "").strip()
        if session_id:
            self.active_openclaw_session_id = session_id
        self.active_openclaw_session_file = str(sync_state.get("session_file") or self.active_openclaw_session_file).strip()
        for event in events:
            result = self._apply_openclaw_sync_event(event)
            if result == "visible":
                changed = True
                if event.role == "assistant":
                    assistant_changed = True
        self.active_openclaw_sync_offset = int(sync_state.get("offset") or self.active_openclaw_sync_offset or 0)
        self.active_openclaw_last_synced_at = time.time()
        if events:
            self.active_openclaw_last_event_id = str(events[-1].event_id or self.active_openclaw_last_event_id)
        if changed:
            should_render = assistant_changed
            if self.view_mode == "active" and should_render:
                self._render_answer_list()
            self.SetStatusText("已同步 OpenClaw 主会话")
            if assistant_changed and had_prior_sync:
                self._play_finish_sound()
        self._save_state()

    def _apply_openclaw_sync_event(self, event: OpenClawSyncEvent) -> str:
        event_id = str(event.event_id or "").strip()
        if event_id and self._has_openclaw_event_id(event_id):
            return ""
        text = remove_emojis(normalize_openclaw_text(event.text))
        if not text:
            return ""
        if event.role == "user":
            merged = self._merge_openclaw_user_event(text, event)
            if merged:
                return merged
            self.active_session_turns.append(
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
            self._apply_nonrecoverable_turn_metadata(self.active_session_turns[-1], "openclaw/main", text)
            self.active_turn_idx = len(self.active_session_turns) - 1
            if len([turn for turn in self.active_session_turns if str((turn or {}).get("question") or "").strip()]) == 1:
                self._apply_auto_title_from_first_question(text, push_remote=True)
            return "visible"

        merged = self._merge_openclaw_assistant_event(text, event)
        if merged:
            return merged
        self.active_session_turns.append(
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
        self._apply_nonrecoverable_turn_metadata(self.active_session_turns[-1], "openclaw/main", "")
        self.active_turn_idx = len(self.active_session_turns) - 1
        return "visible"

    def _has_openclaw_event_id(self, event_id: str) -> bool:
        eid = str(event_id or "").strip()
        if not eid:
            return False
        for turn in self.active_session_turns:
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

    def _merge_openclaw_user_event(self, text: str, event: OpenClawSyncEvent) -> str:
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or time.time())
        for idx in range(len(self.active_session_turns) - 1, -1, -1):
            turn = self.active_session_turns[idx]
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

    def _merge_openclaw_assistant_event(self, text: str, event: OpenClawSyncEvent) -> str:
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or time.time())
        event_id = str(event.event_id or "").strip()
        for idx in range(len(self.active_session_turns) - 1, -1, -1):
            turn = self.active_session_turns[idx]
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
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            target_chat = self._current_chat_state
        is_current_target = chat_id in {self.active_chat_id, self.current_chat_id, ""}
        thread_id = str(target_chat.get("codex_thread_id") or self.active_codex_thread_id or "").strip()
        turn_id = str(target_chat.get("codex_turn_id") or self.active_codex_turn_id or "").strip()
        recovery_context = bool(getattr(self, "_codex_recovery_context", False))
        use_shared_client = (not from_recovery) and (not recovery_context) and chat_id in {self.active_chat_id, self.current_chat_id, ""}
        client = self._ensure_codex_client() if use_shared_client else self._get_or_create_codex_client(chat_id or self.active_chat_id or self.current_chat_id or "")

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

        def _send_turn(thread_value: str, steer: bool) -> dict:
            if steer:
                if not turn_id:
                    raise RuntimeError("Codex app-server cannot steer without an active turn id.")
                return client.steer_turn(thread_value, turn_id, question)
            return client.start_turn(thread_value, question)

        try:
            target_turns = self.active_session_turns if is_current_target else (target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else [])
            if not isinstance(target_turns, list) or turn_idx < 0 or turn_idx >= len(target_turns):
                target_turns = self.active_session_turns
            send_question = question
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
                        thread_id = _start_new_thread()
                    else:
                        raise
            should_steer = self._codex_should_steer_turn(target_chat, is_current_target) and bool(turn_id)
            try:
                if should_steer:
                    turn_resp = _send_turn(thread_id, should_steer)
                else:
                    turn_resp = client.start_turn(thread_id, send_question)
            except Exception as exc:
                if should_steer and self._is_codex_no_active_turn_error(exc):
                    should_steer = False
                    turn_resp = client.start_turn(thread_id, send_question)
                elif self._is_codex_thread_missing_error(exc):
                    self._forget_codex_thread_resume(client, thread_id)
                    _clear_stale_codex_thread_state()
                    history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
                    send_question = self._build_codex_rollout_recovery_prompt(history_turns, question)
                    thread_id = _start_new_thread()
                    should_steer = False
                    turn_resp = client.start_turn(thread_id, send_question)
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
                client = ClaudeCodeClient(full_auto=True)
                # 保存客户端引用，以便在用户发送消息时使用
                self._active_claudecode_client = client
                def on_delta(delta):
                    wx_call_after_if_alive(self._on_delta, turn_idx, delta)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request, parse_remote_user_input_reply

                    # 格式化请求消息
                    request_msg = format_remote_user_input_request(params)

                    # 显示请求消息
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")

                    # 显示交互式界面（如果有回调）
                    if hasattr(self, '_show_claudecode_user_input'):
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
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n")

                    # 显示交互式界面（如果有回调）
                    if hasattr(self, '_show_claudecode_approval'):
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
                    self.active_claudecode_session_id = new_session_id
                wx_call_after_if_alive(self._on_done, turn_idx, full_text, "", DEFAULT_CLAUDECODE_MODEL, "", chat_id)
            except Exception as exc:
                error_msg = str(exc)
                wx_call_after_if_alive(self._on_done, turn_idx, "", error_msg, DEFAULT_CLAUDECODE_MODEL, "", chat_id)
            finally:
                # 清除客户端引用
                self._active_claudecode_client = None

        threading.Thread(target=_worker, daemon=True).start()

    def _push_remote_status(self, status: str, request_kind: str = "") -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        payload = {
            "type": "status",
            "chat_id": self.active_chat_id or self.current_chat_id or "",
            "status": status,
            "request_kind": request_kind,
            "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
            "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
            "ts": time.time(),
        }
        try:
            server.broadcast_event(payload)
        except Exception:
            pass

    def _push_remote_state(self, chat_id: str | None = None) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        status, body = self._remote_api_state_ui({"chat_id": chat_id or self.active_chat_id or self.current_chat_id or ""})
        if status >= 400:
            return
        try:
            server.broadcast_event(
                {
                    "type": "state",
                    "chat_id": chat_id or self.active_chat_id or self.current_chat_id or "",
                    "body": body,
                    "ts": time.time(),
                }
            )
        except Exception:
            pass

    def _push_remote_final_answer(self, chat_id: str, text: str) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
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
        try:
            server.broadcast_event(payload)
        except Exception:
            pass

    def _push_remote_history_changed(self, chat_id: str | None = None) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        payload = {
            "type": "history_changed",
            "chat_id": str(chat_id or ""),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": time.time(),
        }
        try:
            server.broadcast_event(payload)
        except Exception:
            pass

    def _push_remote_notes_changed(self, cursor: str | None = None) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        payload = {
            "type": "notes_changed",
            "cursor": str(cursor or self.notes_store.current_cursor() or "0"),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": time.time(),
        }
        try:
            server.broadcast_event(payload)
        except Exception:
            pass

    def _push_remote_notes_conflict(self, payload: dict | None = None) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        conflict = dict(payload or {})
        conflict.update(
            {
                "type": "notes_conflict",
                "event_id": f"evt-{uuid.uuid4().hex[:8]}",
                "ts": time.time(),
            }
        )
        try:
            server.broadcast_event(conflict)
        except Exception:
            pass

    def _push_remote_notes_sync_status(self, status: str | dict, *, cursor: str | None = None, message: str | None = None) -> None:
        server = getattr(self, "_remote_ws_server", None)
        if server is None:
            return
        payload = dict(status) if isinstance(status, dict) else {"status": str(status or "")}
        if cursor is not None:
            payload["cursor"] = str(cursor or "")
        if message is not None:
            payload["message"] = str(message or "")
        payload.update({"type": "notes_sync_status", "event_id": f"evt-{uuid.uuid4().hex[:8]}", "ts": time.time()})
        try:
            server.broadcast_event(payload)
        except Exception:
            pass

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
        answer = REQUESTING_TEXT if answer_md == REQUESTING_TEXT else remove_emojis(md_to_plain(self._answer_markdown_for_output(answer_md, model)))
        return {
            "question": question,
            "answer": answer,
            "model": model,
            "created_at": float(turn.get("created_at") or 0.0),
            "assistant_only": (not question.strip()) and bool(answer_md.strip()),
            "pending": str(turn.get("request_status") or "").strip() == "pending" or answer_md == REQUESTING_TEXT,
            "request_status": str(turn.get("request_status") or ""),
            "request_error": str(turn.get("request_error") or ""),
        }

    def _remote_chat_snapshot(self, chat: dict) -> dict:
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
                "turns": [],
            }
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
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
            "turn_count": len(turns),
            "running": False,
            "request_kind": request_kind,
            "current": bool(chat_id) and chat_id == current_id,
            "active": bool(chat_id) and chat_id == current_id,
            "pinned": bool(chat.get("pinned")),
            "turns": [self._remote_turn_payload(turn) for turn in turns if isinstance(turn, dict)],
        }

    def _current_chat_snapshot(self) -> dict:
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
        return chat

    def _codex_answer_filter_menu_label(self) -> str:
        return "取消过滤英文内容" if self.codex_answer_english_filter_enabled else "在回答中过滤英文内容"

    def _toggle_codex_answer_filter(self) -> None:
        self.codex_answer_english_filter_enabled = not self.codex_answer_english_filter_enabled
        self._save_state()
        self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
        if self.view_mode in {"active", "history"}:
            self._render_answer_list()

    def _show_tools_menu(self) -> None:
        menu = wx.Menu()
        voice_id = wx.NewIdRef()
        filter_id = wx.NewIdRef()
        menu.Append(voice_id, "语音通话设置")
        filter_item = menu.AppendCheckItem(filter_id, "过滤英文内容")
        filter_item.Check(bool(self.codex_answer_english_filter_enabled))
        self.Bind(wx.EVT_MENU, self._on_open_realtime_call_settings, id=voice_id)
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
        ok, message = self._submit_question(text, source="remote-ws", model=model, chat_id=chat_id)
        return 200 if ok else 400, {"accepted": ok, "message": message, "chat_id": chat_id, "model": model}

    def _remote_api_new_chat_ui(self, payload: dict) -> tuple[int, dict]:
        chat = self._start_remote_new_chat(payload)
        return 200, {"accepted": True, **chat}

    def _start_remote_new_chat(self, payload: dict | None = None) -> dict:
        previous_chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        archived = self._archive_active_session(quick_title=True, schedule_async_rename=True)
        self.current_chat_id = ""
        self.active_chat_id = ""
        self.active_session_turns = []
        now = time.time()
        self.active_session_started_at = now
        self._current_chat_state = {
            "id": "",
            "title": str((payload or {}).get("title") or EMPTY_CURRENT_CHAT_TITLE),
            "title_manual": False,
            "turns": self.active_session_turns,
            "created_at": now,
            "updated_at": now,
        }
        self.active_chat_id = str(uuid.uuid4())
        self.current_chat_id = self.active_chat_id
        self._current_chat_state["id"] = self.active_chat_id
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
        }

    def _remote_api_reply_request_ui(self, payload: dict) -> tuple[int, dict]:
        text = str(payload.get("text") or "").strip()
        ok, message = self._handle_remote_pending_request_reply(text)
        return 200 if ok else 400, {"accepted": ok, "message": message}

    def _remote_api_history_list_ui(self, _payload: dict | None = None) -> tuple[int, dict]:
        chats = []
        if self.active_chat_id or self.active_session_turns:
            chats.append(self._current_chat_snapshot())
        for chat in self.archived_chats:
            chats.append(self._remote_chat_snapshot(chat))
        return 200, {"accepted": True, "chats": chats}

    def _remote_api_history_read_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            chat = self._current_chat_snapshot()
        else:
            chat = self._remote_chat_snapshot(self._find_archived_chat(chat_id) or {})
        return 200, {"accepted": True, "chat": chat}

    def _remote_api_state_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        chat_id = str((payload or {}).get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            chat = self._current_chat_snapshot()
        else:
            chat = self._remote_chat_snapshot(self._find_archived_chat(chat_id) or {})
        status = "waiting_user_input" if self.active_codex_pending_request else "idle"
        request_kind = "user_input" if self.active_codex_pending_request else ""
        chat.update(
            {
                "chat_id": chat.get("chat_id") or chat_id,
                "status": status,
                "request_kind": request_kind,
                "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
                "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
            }
        )
        return 200, {"accepted": True, **chat}

    def _remote_api_notes_snapshot(self, _payload: dict | None = None) -> tuple[int, dict]:
        return 200, self.notes_sync.snapshot()

    def _remote_api_notes_pull_since(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        cursor = str(payload.get("cursor") or "0").strip() or "0"
        return 200, self.notes_sync.pull_since(cursor)

    def _remote_api_notes_push_ops(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        result = self.notes_sync.push_ops(list(payload.get("ops") or []))
        return 200, result

    def _remote_api_notes_subscribe(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        return 200, self.notes_sync.subscribe(payload)

    def _remote_api_notes_ack(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        return 200, self.notes_sync.ack(payload)

    def _remote_api_notes_ping(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        return 200, self.notes_sync.ping(payload)

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
        chat["title"] = title
        chat["title_manual"] = True
        self._save_state()
        self._refresh_history(chat_id)
        self._push_remote_history_changed(chat_id)
        return 200, {"accepted": True, "chat_id": chat_id, "title": title}

    def _remote_api_update_settings_ui(self, payload: dict) -> tuple[int, dict]:
        if "codex_answer_english_filter_enabled" in payload:
            self.codex_answer_english_filter_enabled = bool(payload.get("codex_answer_english_filter_enabled"))
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
        event_type = str(getattr(event, "type", "") or "")
        if chat_id not in {self.active_chat_id, self.current_chat_id, "", None} and event_type in {"agent_message_delta", "stderr", "plan_updated", "diff_updated"}:
            return False
        return True

    def _dispatch_codex_event_to_ui(self, chat_id: str, event: CodexEvent) -> None:
        if not event or not self._should_queue_codex_ui_event(chat_id, event):
            return
        if not self._is_ui_alive():
            return
        self._call_after_if_alive(self._on_codex_event_for_chat, chat_id, event)

    def _on_codex_event(self, event: CodexEvent) -> None:
        self._on_codex_event_for_chat(self.active_chat_id or self.current_chat_id or "", event)

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
        is_current_chat = chat_id in {self.active_chat_id, self.current_chat_id, "", None}
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, "", None} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            target_chat = self._current_chat_state
        if (not is_current_chat) and (
            (event.type == "item_completed" and str(event.phase or "") != "final_answer")
            or event.type in {"plan_updated", "diff_updated", "stderr"}
        ):
            self._codex_background_flush_dirty = True
            if not getattr(self, "_codex_background_flush_scheduled", False):
                self._codex_background_flush_scheduled = True
                self._call_later_if_alive(CODEX_BACKGROUND_FLUSH_DELAY_MS, self._flush_codex_background_updates)
            return
        if event.type == "server_request":
            self.active_codex_pending_request = None
            self._push_remote_status("waiting_user_input", "user_input")
            self._play_finish_sound()
            if str(event.method or "") == "item/tool/requestUserInput":
                self._handle_codex_request_dialog({"request_id": event.request_id, "method": event.method, "params": event.params})
            self._save_state()
            return
        if event.type == "turn_completed":
            if is_current_chat:
                self.active_codex_turn_active = False
                self.active_codex_pending_request = None
            target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0 and target_idx < len(self.active_session_turns):
                turn = self.active_session_turns[target_idx]
                turn["request_status"] = "done"
                turn["request_error"] = ""
                if str(turn.get("answer_md") or "").strip() == REQUESTING_TEXT and str(event.text or "").strip():
                    turn["answer_md"] = str(event.text or "")
                self._update_active_answer_row(target_idx)
            if is_current_chat:
                self.is_running = False
                self._active_request_count = 0
                self.new_chat_button.Enable()
                self._set_input_hint_idle()
            if is_current_chat:
                self._push_remote_state(self.active_chat_id or self.current_chat_id or "")
                self._play_finish_sound()
                self._save_state()
                if self.view_mode == "active":
                    self._render_answer_list()
            return
        if event.type in {"item_completed", "agent_message_delta", "plan_updated", "diff_updated", "stderr"}:
            self.active_codex_latest_assistant_text = str(event.text or "")
            self.active_codex_latest_assistant_phase = str(event.phase or "")
            if event.type == "item_completed" and event.phase == "final_answer":
                self.active_codex_pending_prompt = str(event.text or "")
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    turn = self.active_session_turns[target_idx]
                    if not str(turn.get("question") or "").strip():
                        turn["question"] = str(event.text or "")
                    elif (
                        not str(turn.get("answer_md") or "").strip()
                        or str(turn.get("answer_md") or "") == REQUESTING_TEXT
                    ):
                        turn["answer_md"] = str(event.text or "")
                    self._update_active_answer_row(target_idx)
                self._save_state()
                self._push_remote_final_answer(chat_id or self.active_chat_id or self.current_chat_id or "", str(event.text or ""))
                self._render_answer_list()
                self._call_later_if_alive(120, self._focus_latest_answer)
                return
            if event.text:
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
                if target_idx >= 0 and target_idx < len(self.active_session_turns):
                    turn = self.active_session_turns[target_idx]
                    if event.phase == "final_answer":
                        turn["answer_md"] = str(event.text or "")
                    elif not str(turn.get("answer_md") or "").strip():
                        turn["answer_md"] = REQUESTING_TEXT
                    self._update_active_answer_row(target_idx)
                self._save_state()
            return

    def _flush_codex_background_updates(self) -> None:
        self._codex_background_flush_scheduled = False
        if not getattr(self, "_codex_background_flush_dirty", False):
            return
        self._codex_background_flush_dirty = False
        self._save_state()

    def _get_or_create_codex_client(self, chat_id: str) -> CodexAppServerClient:
        key = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip() or self._ensure_active_chat_id()
        client = self._codex_clients.get(key)
        if client is not None:
            return client
        client = CodexAppServerClient(on_event=lambda event, cid=key: self._dispatch_codex_event_to_ui(cid, event))
        self._codex_clients[key] = client
        return client

    def _ensure_codex_client(self) -> CodexAppServerClient:
        client = getattr(self, "_codex_client", None)
        if client is None:
            client = CodexAppServerClient(on_event=self._on_codex_event)
            self._codex_client = client
        return client

    def _load_chat_as_current(self, chat: dict) -> None:
        self._current_chat_state = copy.deepcopy(chat or {})
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        self._current_chat_state["title_manual"] = title_manual
        self.current_chat_id = str(chat.get("id") or "").strip() or str(uuid.uuid4())
        self.active_chat_id = self.current_chat_id
        self.active_session_turns = copy.deepcopy(chat.get("turns") or [])
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
        self._save_state()

    def _read_remote_control_token(self) -> str:
        return self._read_remote_control_setting(
            "REMOTE_CONTROL_TOKEN",
            "CLAUDECODE_REMOTE_CONTROL_TOKEN",
            default=DEFAULT_REMOTE_CONTROL_TOKEN,
        )

    def _build_remote_ws_url(self) -> str:
        token = self._read_remote_control_token()
        if not token:
            return ""
        runtime = self._remote_runtime_config()
        base = runtime["published_base"]
        return f"{base}?token={token}"

    def _on_copy_remote_ws_url(self, _event) -> None:
        url = self._build_remote_ws_url()
        if not url:
            wx.MessageBox("未配置远程控制令牌", "提示", wx.OK | wx.ICON_WARNING)
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(url))
            finally:
                wx.TheClipboard.Close()
        self.SetStatusText("已复制远程控制地址")

    def _start_remote_ws_server_if_configured(self) -> None:
        token = self._read_remote_control_token() or self.remote_control_token
        if not token or getattr(self, "_remote_ws_server", None) is not None:
            return
        runtime = self._remote_runtime_config()
        host = runtime["host"]
        port = runtime["port"]
        self._remote_ws_server = RemoteWebSocketServer(
            host=host,
            port=port,
            token=token,
            on_message=self._remote_api_message_ui,
            on_new_chat=self._remote_api_new_chat_ui,
            on_reply_request=self._remote_api_reply_request_ui,
            on_state=self._remote_api_state_ui,
            on_rename_chat=self._remote_api_rename_chat_ui,
            on_update_settings=self._remote_api_update_settings_ui,
            on_history_list=self._remote_api_history_list_ui,
            on_history_read=self._remote_api_history_read_ui,
            on_notes_snapshot=self._remote_api_notes_snapshot,
            on_notes_pull_since=self._remote_api_notes_pull_since,
            on_notes_push_ops=self._remote_api_notes_push_ops,
            on_notes_subscribe=self._remote_api_notes_subscribe,
            on_notes_ack=self._remote_api_notes_ack,
            on_notes_ping=self._remote_api_notes_ping,
        )
        self._remote_ws_server.start()
        published_url = f"{runtime['published_base']}?token={token}"
        self.remote_control_runtime_mode = "fixed_domain" if runtime["fixed_domain_mode"] else "local"
        self.remote_control_runtime_bind = f"ws://{host}:{self._remote_ws_server.bound_port}/ws"
        self.remote_control_runtime_url = published_url
        self.SetStatusText(
            f"远程 WebSocket 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}"
        )

    def _start_claudecode_remote_ws_server_if_configured(self) -> None:
        return

    def _on_char_hook(self, event):
        key = event.GetKeyCode()
        notes_has_focus = bool(
            getattr(self, "notes_notebook_list", None)
            and (
                self.notes_notebook_list.HasFocus()
                or self.notes_entry_list.HasFocus()
                or self.notes_editor.HasFocus()
            )
        )
        if key == wx.WXK_ALT and event.AltDown() and not event.ControlDown():
            self._arm_tools_menu_open()
            return
        self._suppress_tools_menu_open()
        if (
            key == wx.WXK_MENU
            and notes_has_focus
        ):
            self._show_notes_menu()
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and notes_has_focus
            and not event.ControlDown()
            and not event.AltDown()
        ):
            self._on_notes_key_down(event)
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.history_list.HasFocus()
            and not event.ControlDown()
            and not event.AltDown()
        ):
            if self._activate_selected_history():
                return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.answer_list.HasFocus()
            and not event.ControlDown()
            and not event.AltDown()
        ):
            if self._try_open_selected_answer_detail():
                return
        if self._is_continue_shortcut(key, event.AltDown()):
            self._submit_question("继续", source="local")
            return
        if event.ControlDown() and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            direction = -1 if key == wx.WXK_LEFT else 1
            chat_id = self._adjacent_history_chat_id(direction)
            if chat_id and self._switch_current_chat(chat_id):
                return
        if self._is_send_shortcut(key, event.ControlDown(), event.AltDown()):
            if self.input_edit.HasFocus():
                event.Skip()
                return
            self._trigger_send()
            return
        if self._is_new_chat_shortcut(key, event.AltDown()):
            self._trigger_new_chat()
            return
        event.Skip()

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
        event.Skip()
        return False

    def _on_input_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        key = event.GetKeyCode()
        if key != wx.WXK_ALT:
            self._suppress_tools_menu_open()
        if event.ControlDown() and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            direction = -1 if key == wx.WXK_LEFT else 1
            chat_id = self._adjacent_history_chat_id(direction)
            if chat_id and self._switch_current_chat(chat_id):
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
        self.SetStatusText(message)

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
        q = str(question or "").strip()
        if not q:
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
        self._current_chat_state.setdefault("title", EMPTY_CURRENT_CHAT_TITLE)
        self._current_chat_state.setdefault("title_manual", False)
        if is_openclaw_model(resolved_model):
            self._ensure_active_chat_id()
            self._ensure_active_openclaw_session_id()
            turn = {
                "question": q,
                "answer_md": "",
                "model": resolved_model,
                "created_at": time.time(),
                "origin": "local" if source == "local" else source,
                "question_origin": "local" if source == "local" else source,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = len(self.active_session_turns) - 1
            if len([item for item in self.active_session_turns if str((item or {}).get("question") or "").strip()]) == 1:
                self._apply_auto_title_from_first_question(q, push_remote=(source != "local"))
            self._mark_turn_request_pending(turn, resolved_model, q)
            self.is_running = True
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._save_state()
            self._play_send_sound()
            self.SetStatusText("已发送")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            self._refresh_openclaw_sync_lifecycle()
            threading.Thread(target=self._worker, args=("", self.active_turn_idx, q, resolved_model, False, chat_id or self.active_chat_id), daemon=True).start()
            return True, ""

        turn_idx = len(self.active_session_turns)
        turn = {
            "question": q,
            "answer_md": REQUESTING_TEXT,
            "model": resolved_model,
            "created_at": time.time(),
        }
        self.active_session_turns.append(turn)
        self.active_turn_idx = turn_idx
        if len([item for item in self.active_session_turns if str((item or {}).get("question") or "").strip()]) == 1:
            self._apply_auto_title_from_first_question(q, push_remote=(source != "local"))
        self._mark_turn_request_pending(turn, resolved_model, q)
        if is_claudecode_model(resolved_model):
            self.active_claudecode_session_id = str(self.active_claudecode_session_id or "").strip()
        self.is_running = True
        self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
        self.input_edit.SetValue("")
        self.input_edit.SetFocus()
        self._save_state()
        self._play_send_sound()
        self.SetStatusText("已发送")
        self.view_mode = "active"
        self.view_history_id = None
        self._active_answer_row_index = -1
        self._refresh_openclaw_sync_lifecycle()
        self._render_answer_list()
        if is_codex_model(resolved_model) and source == "local":
            self._start_codex_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, resolved_model)
        elif is_claudecode_model(resolved_model) and source == "local":
            self._start_claudecode_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, self.active_claudecode_session_id)
        else:
            t = threading.Thread(target=self._worker, args=(os.getenv("OPENROUTER_API_KEY", "").strip(), turn_idx, q, resolved_model, False, chat_id or self.active_chat_id or self.current_chat_id or ""), daemon=True)
            t.start()
        return True, ""

    def _on_voice_state(self, text: str):
        self.SetStatusText(text)
        if getattr(self._voice_input, "state", "") == "recording":
            self._play_voice_begin_sound()

    def _on_voice_error(self, msg: str):
        self.SetStatusText(msg)
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
        old = target.GetValue()
        target.SetValue(old + text)
        target.SetInsertionPointEnd()
        target.SetFocus()

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
        has_trailing_punct = remove_trailing_punctuation(text) != str(text or "")
        if isinstance(focus, wx.TextCtrl) and focus.IsEditable() and not has_trailing_punct:
            self._append_text_to_focused_editor(text)
        elif self.IsActive() and not has_trailing_punct:
            self._append_text_to_focused_editor(text)
        elif not self._insert_text_to_system_focus(text):
            self.SetStatusText("语音输入失败：无法写入当前焦点位置")
            self._play_voice_wrong_sound()
            return
        wx_call_later_if_alive(200, self._speak_text_via_screen_reader, text)

    def _on_voice_stop_recording(self):
        self._play_voice_end_sound()

    def _speak_text_via_screen_reader(self, text: str):
        content = remove_trailing_punctuation(text)
        if not content:
            return
        try:
            self._zdsr_tts.speak(content)
        except Exception:
            pass

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

    def _worker(self, api_key: str, turn_idx: int, question: str, model: str, from_recovery: bool = False, chat_id: str = ""):
        full = ""
        err = ""
        used_model = model
        fallback_msg = ""
        history_turns = self.active_session_turns[:turn_idx] if turn_idx > 0 else []
        skip_done = False
        try:
            if is_openclaw_model(model):
                session_id = self._ensure_active_openclaw_session_id()
                c = OpenClawClient(model=model)
                c.stream_chat(question, session_id=session_id)
                full = ""
            elif is_codex_model(model):
                self._run_codex_turn_worker(chat_id, turn_idx, question, model, from_recovery=from_recovery)
                skip_done = True
                full = ""
            elif is_claudecode_model(model):
                def on_delta(d):
                    wx_call_after_if_alive(self._on_delta, turn_idx, d)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request
                    request_msg = format_remote_user_input_request(params)
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n")
                    return ""

                def on_approval(params: dict) -> str:
                    """处理批准请求"""
                    from claudecode_remote_protocol import format_remote_approval_request
                    request_msg = format_remote_approval_request(params)
                    wx_call_after_if_alive(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n")
                    return ""

                client = ClaudeCodeClient(full_auto=True)
                full, new_session_id = client.stream_chat(
                    question,
                    session_id=str(self.active_claudecode_session_id or ""),
                    on_delta=on_delta,
                    on_user_input=on_user_input,
                    on_approval=on_approval
                )
                if new_session_id:
                    self.active_claudecode_session_id = new_session_id
            else:
                def on_delta(d):
                    wx_call_after_if_alive(self._on_delta, turn_idx, d)

                c = ChatClient(api_key=api_key, model=model)
                full = c.stream_chat(question, on_delta, history_turns=history_turns)
        except Exception as e:
            err = str(e)
            if (not is_openclaw_model(model)) and (not is_codex_model(model)) and self._is_model_endpoint_unavailable_error(model, err):
                for fb_model in self._candidate_fallback_models(model):
                    try:
                        c = ChatClient(api_key=api_key, model=fb_model)
                        full = c.stream_chat(question, on_delta, history_turns=history_turns)
                        used_model = fb_model
                        err = ""
                        fallback_msg = f"模型 {model} 当前不可用，已回退到 {fb_model}"
                        break
                    except Exception as fb_e:
                        err = str(fb_e)
        if skip_done and not err:
            return
        self._call_after_if_alive(self._on_done, turn_idx, full, err, used_model, fallback_msg)

    def _on_delta(self, turn_idx: int, delta: str, chat_id: str = ""):
        self._on_delta_for_chat(turn_idx, delta, chat_id or self.active_chat_id or self.current_chat_id or "")

    def _on_delta_for_chat(self, turn_idx: int, delta: str, chat_id: str = ""):
        if not delta:
            return
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        target_turns = self.active_session_turns
        if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""}:
            chat = self._find_archived_chat(chat_id)
            if isinstance(chat, dict):
                target_turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        if turn_idx < 0 or turn_idx >= len(target_turns):
            return
        cur = str(target_turns[turn_idx].get("answer_md") or "")
        if cur == REQUESTING_TEXT:
            cur = ""
        target_turns[turn_idx]["answer_md"] = cur + remove_emojis(delta)
        target_turns[turn_idx]["request_last_attempt_at"] = time.time()
        # 流式阶段不刷新回答列表；待完成后一次性展示完整回答。
        self._save_state()

    def _on_done(self, turn_idx: int, full: str, err: str, used_model: str, fallback_msg: str, chat_id: str = ""):
        if self.active_chat_id and self.current_chat_id != self.active_chat_id:
            self.current_chat_id = self.active_chat_id
        should_render = True
        if turn_idx < 0 and is_openclaw_model(used_model) and not err:
            should_render = False
        target_turns = self.active_session_turns
        target_chat = self._current_chat_state
        is_current_chat = chat_id in {self.active_chat_id, self.current_chat_id, ""}
        if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""}:
            archived = self._find_archived_chat(chat_id)
            if isinstance(archived, dict):
                target_chat = archived
                target_turns = archived.get("turns") if isinstance(archived.get("turns"), list) else []
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
            self._active_request_count = 0
            if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""} and isinstance(target_chat, dict):
                target_chat["updated_at"] = time.time()
                title = self._summarize_recent_topic(target_turns, os.getenv("OPENROUTER_API_KEY", "").strip())
                if title and not target_chat.get("title_manual"):
                    target_chat["title"] = title

        if is_current_chat:
            self.is_running = False
            self.new_chat_button.Enable()
            self._set_input_hint_idle()
        if fallback_msg:
            self.selected_model = used_model or self.selected_model
            if used_model:
                self.model_combo.SetValue(used_model)
            self.SetStatusText(fallback_msg)
        else:
            if is_openclaw_model(used_model) and not err:
                self.SetStatusText("已发送，等待 OpenClaw 同步回复")
            else:
                self.SetStatusText("答复完成")
        if is_codex_model(used_model) or is_claudecode_model(used_model):
            self._push_remote_state(chat_id or self.active_chat_id or self.current_chat_id or "")
        self._save_state()
        if self._is_ui_alive():
            self._refresh_history(chat_id or self.active_chat_id or self.current_chat_id or None)

        if should_render and self.view_mode == "active":
            self._render_answer_list()
            self._call_later_if_alive(120, self._focus_latest_answer)
        if (not is_openclaw_model(used_model)) or err:
            self._play_finish_sound()

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
        transcript = self._build_title_transcript(turns)
        title = ""
        if api_key:
            try:
                client = ChatClient(api_key=api_key, model=self.selected_model or DEFAULT_MODEL, timeout=15)
                prompt = (
                    "请只根据首轮用户提问总结一个准确、简洁的中文标题（6-16字），"
                    "去掉寒暄和无信息前缀，只输出标题，不要标点和引号。\n\n"
                    f"{transcript}"
                )
                title = client.generate_chat_title(prompt)
            except Exception:
                title = ""
        if not title:
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
        if api_key:
            title = self._summarize_title(turns, api_key)
            if title and title != "新聊天":
                return title
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
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return

        def _worker():
            title = ""
            try:
                transcript = self._build_title_transcript(turns_snapshot)
                client = ChatClient(api_key=api_key, model=model_snapshot or DEFAULT_MODEL, timeout=15)
                prompt = (
                    "请根据给定聊天片段生成一个准确、简洁的中文标题（6-16字），"
                    "只输出标题，不要标点和引号。\n\n"
                    f"{transcript}"
                )
                title = client.generate_chat_title(prompt).strip()[:40]
            except Exception:
                title = ""
            if title:
                wx_call_after_if_alive(self._apply_archived_title, chat_id, title)

        threading.Thread(target=_worker, daemon=True).start()

    def _archive_active_session(self, quick_title: bool = False, schedule_async_rename: bool = False, save_after_archive: bool = True):
        if not self.active_session_turns:
            return None
        turns_snapshot = copy.deepcopy(self.active_session_turns)
        model_snapshot = self._resolve_current_model()
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip() if (not quick_title) else ""
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        if title_manual and str(self._current_chat_state.get("title") or "").strip():
            title = str(self._current_chat_state.get("title") or "").strip()
        else:
            title = self._summarize_recent_topic(turns_snapshot, api_key)
        created = self.active_session_started_at or time.time()
        archived_id = str(self.active_chat_id or self.current_chat_id or uuid.uuid4())
        archived = {
            "id": archived_id,
            "title": title,
            "title_manual": title_manual,
            "pinned": False,
            "created_at": created,
            "turns": turns_snapshot,
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
        }
        self.archived_chats.append(archived)
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
        self._save_state()
        self._render_answer_list()
        self.SetStatusText(f"已载入项目：{folder_name}")

    def _on_new_chat_clicked(self, _):
        if self._has_openclaw_turns() or ((not self.active_session_turns) and is_openclaw_model(self._resolve_current_model())):
            self.active_session_turns = []
            self._current_chat_state["turns"] = self.active_session_turns
            self.active_turn_idx = -1
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            self._seek_openclaw_sync_to_current_tail()
            self._save_state()
            self._render_answer_list()
            self._send_openclaw_hidden_message("/new")
            self.input_edit.SetFocus()
            self.SetStatusText("已开始新的 OpenClaw 会话")
            return
        archived = self._archive_active_session(quick_title=True, schedule_async_rename=True)
        self.current_chat_id = ""
        self.active_chat_id = ""
        self._current_chat_state = {"id": "", "title": EMPTY_CURRENT_CHAT_TITLE, "title_manual": False, "turns": self.active_session_turns}
        self.active_chat_id = self._ensure_active_chat_id()
        self.current_chat_id = self.active_chat_id
        self._current_chat_state["id"] = self.active_chat_id
        self._refresh_history(archived["id"] if archived else None)
        self._render_answer_list()
        self.input_edit.SetFocus()
        self.SetStatusText("已开始新聊天")
        self._push_remote_history_changed(self.active_chat_id)

    def _on_answer_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        key = event.GetKeyCode()
        ctrl = event.ControlDown()
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            event.Skip()
            return
        item_type, turn_idx, plain, _ = self.answer_meta[idx]
        if ctrl and key in (ord("C"), ord("c")):
            if item_type in ("question", "answer") and wx.TheClipboard.Open():
                # 去除多余空行：将连续的空行替换为单个空行
                source_text = _ if item_type == "answer" and _ else plain
                cleaned_text = "\n".join(line for line in str(source_text).split("\n") if line.strip())
                wx.TheClipboard.SetData(wx.TextDataObject(cleaned_text))
                wx.TheClipboard.Close()
                self.SetStatusText("已复制")
            # 阻止事件传播，保持焦点不变
            event.StopPropagation()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._try_open_selected_answer_detail():
                return
        event.Skip()

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

    def _try_open_selected_answer_detail(self) -> bool:
        idx = self.answer_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.answer_meta):
            return False
        item_type, turn_idx, _, _ = self.answer_meta[idx]
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

    def _activate_selected_history(self) -> bool:
        if self.is_running:
            self._show_ok_dialog("当前正在请求中，请等待完成后再载入历史聊天。", "提示")
            return False
        if self.history_list.GetCount() == 0:
            return False
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = 0
            self.history_list.SetSelection(0)
        if idx < 0 or idx >= len(self.history_ids):
            return False

        selected_id = self.history_ids[idx]
        if selected_id in {self.active_chat_id, self.current_chat_id}:
            self.view_mode = "active"
            self.view_history_id = None
            self._render_answer_list()
            return True
        chat = self._find_archived_chat(selected_id)
        if not chat:
            return False

        self._archive_active_session(quick_title=True, schedule_async_rename=True)

        turns = chat.get("turns") or []
        self.active_session_turns = copy.deepcopy(turns)
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
        self.model_combo.SetValue(self.selected_model)

        self.archived_chats = [c for c in self.archived_chats if str(c.get("id")) != selected_id]
        self.view_mode = "active"
        self.view_history_id = None
        self.input_edit.SetValue("")
        self._render_answer_list()
        self._refresh_history()
        self._refresh_openclaw_sync_lifecycle(force_replay=not bool(self.active_openclaw_session_file))
        self._save_state()
        self.SetStatusText("已载入历史聊天，已切换为当前会话")
        self.answer_list.SetFocus()
        return True

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
        current_id = self.current_chat_id or self.active_chat_id
        if len(all_ids) <= 1 or current_id not in all_ids:
            return None
        current_idx = all_ids.index(current_id)
        target_idx = (current_idx + direction) % len(all_ids)
        return all_ids[target_idx]

    def _switch_current_chat(self, chat_id: str) -> bool:
        """Switch to a different chat."""
        if chat_id == self.current_chat_id:
            return True
        chat = self._find_archived_chat(chat_id)
        if not chat:
            return False
        # Archive current session if it has turns
        if self.active_session_turns:
            self._archive_active_session(quick_title=True, schedule_async_rename=True)
        # Load the selected chat
        turns = chat.get("turns") or []
        self.active_session_turns = copy.deepcopy(turns)
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
        show_notes_list = True
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
            self.notes_notebook_list.Enable(self.notes_notebook_list.GetCount() > 0)
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
        try:
            self.Layout()
        except Exception:
            pass

    def _notes_refresh_notebooks(self, select_id: str | None = None) -> None:
        query = str(getattr(self, "_notes_search_query", "") or "").strip()
        notebooks = self.notes_store.search_notebooks(query) if query else self.notes_store.list_notebooks()
        self._notes_notebook_ids = [nb.id for nb in notebooks]
        self.notes_notebook_list.Clear()
        if not notebooks:
            self.notes_notebook_list.Append("暂无笔记本")
            self.notes_notebook_list.Enable(False)
            return
        self.notes_notebook_list.Enable(True)
        for notebook in notebooks:
            label = f"{'★ ' if notebook.pinned else ''}{notebook.title}"
            self.notes_notebook_list.Append(label)
        target = str(select_id or self.notes_controller.active_notebook_id or notebooks[0].id)
        if target in self._notes_notebook_ids:
            self.notes_notebook_list.SetSelection(self._notes_notebook_ids.index(target))
        else:
            self.notes_notebook_list.SetSelection(0)

    def _notes_refresh_entries(self, notebook_id: str | None = None, select_id: str | None = None) -> None:
        notebook_id = str(notebook_id or self.notes_controller.active_notebook_id or "").strip()
        self._notes_entry_ids = []
        self.notes_entry_list.Clear()
        if not notebook_id:
            self.notes_entry_list.Append("请选择笔记本")
            self.notes_entry_list.Enable(False)
            return
        entries = self.notes_store.list_entries(notebook_id)
        self.notes_entry_list.Enable(True)
        self._notes_entry_ids = [entry.id for entry in entries]
        if not entries:
            self.notes_entry_list.Append("暂无条目")
            return
        for entry in entries:
            prefix = "★ " if entry.pinned else ""
            self.notes_entry_list.Append(f"{prefix}{entry.content[:32]}")
        target = str(select_id or self.notes_controller.active_entry_id or entries[0].id)
        if target in self._notes_entry_ids:
            self.notes_entry_list.SetSelection(self._notes_entry_ids.index(target))
        else:
            self.notes_entry_list.SetSelection(0)

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
        self._notes_sync_view_visibility()
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
            first = self.notes_store.list_notebooks()
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

    def _show_notes_sync_hint(self, message: str) -> None:
        self.notes_sync_hint = str(message or "")
        try:
            self.SetStatusText(self.notes_sync_hint)
        except Exception:
            pass

    def _on_notes_remote_ops_applied(self, result: dict | None) -> None:
        result = dict(result or {})
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

    def _show_notes_menu(self) -> None:
        if self.notes_editor.HasFocus():
            return
        show_notebook_actions = self.notes_notebook_list.HasFocus() or self.notes_controller.notes_view == "notes_list"
        show_entry_actions = self.notes_entry_list.HasFocus() or self.notes_controller.notes_view == "note_detail"
        menu = wx.Menu()
        if show_notebook_actions:
            i_open_nb = wx.NewIdRef()
            i_new_nb = wx.NewIdRef()
            i_copy_nb = wx.NewIdRef()
            i_del_nb = wx.NewIdRef()
            i_search_nb = wx.NewIdRef()
            i_ren_nb = wx.NewIdRef()
            menu.Append(i_open_nb, "打开笔记")
            menu.AppendSeparator()
            menu.Append(i_new_nb, "新建笔记")
            menu.Append(i_copy_nb, "复制笔记")
            menu.Append(i_del_nb, "删除笔记")
            menu.Append(i_search_nb, "搜索笔记")
            menu.Append(i_ren_nb, "重命名笔记")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_open_selected_notebook(), id=i_open_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_notebook(), id=i_new_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_notebook_to_clipboard(), id=i_copy_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_delete_notebook(), id=i_del_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_prompt_search(), id=i_search_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_rename_notebook(), id=i_ren_nb)
        elif show_entry_actions:
            i_new_entry = wx.NewIdRef()
            i_copy_entry = wx.NewIdRef()
            i_del_entry = wx.NewIdRef()
            i_edit_entry = wx.NewIdRef()
            i_pin_entry = wx.NewIdRef()
            i_bottom_entry = wx.NewIdRef()
            i_import_file = wx.NewIdRef()
            i_import_clip = wx.NewIdRef()
            menu.Append(i_new_entry, "新建笔记条目")
            menu.Append(i_copy_entry, "复制笔记条目")
            menu.Append(i_del_entry, "删除笔记条目")
            menu.Append(i_edit_entry, "编辑笔记条目")
            menu.Append(i_pin_entry, "置顶笔记条目")
            menu.Append(i_bottom_entry, "置底笔记条目")
            menu.AppendSeparator()
            menu.Append(i_import_file, "从文件导入")
            menu.Append(i_import_clip, "从剪贴板导入")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_entry(), id=i_new_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_entry_to_clipboard(), id=i_copy_entry)
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

    def _notes_select_notebook(self, notebook_id: str, entry_id: str | None = None, view: str = "note_detail") -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_notebook_id = str(notebook_id or "")
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_detail")
        self.notes_controller.entry_editor_dirty = False
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        try:
            if self.notes_controller.notes_view == "notes_list":
                self.notes_notebook_list.SetFocus()
            elif self.notes_controller.notes_view == "note_detail":
                self.notes_entry_list.SetFocus()
        except Exception:
            pass

    def _notes_select_entry(self, entry_id: str, view: str = "note_edit") -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_edit")
        self.notes_controller.entry_editor_dirty = False
        entry = self.notes_store.get_entry(entry_id, include_deleted=True)
        self.notes_controller.entry_editor_base_version = int(entry.version if entry is not None else 0)
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
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

    def _notes_copy_notebook_to_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
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
            self._notes_select_notebook(notebook_id, view="notes_list")
        else:
            self._notes_refresh_ui()

    def _on_notes_entry_selected(self, _event):
        entry_id = self._notes_selected_entry_id()
        if entry_id:
            self._notes_select_entry(entry_id, view=self.notes_controller.notes_view or "note_detail")

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
        turns = chat.get("turns") or []
        for turn in turns:
            for k in ("question_detail_page_path", "answer_detail_page_path", "detail_page_path"):
                raw = str(turn.get(k) or "").strip()
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
        if self._remote_ws_server:
            try:
                self._remote_ws_server.stop()
            except Exception:
                pass
            self._remote_ws_server = None
        self._save_state()
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

