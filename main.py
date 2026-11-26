import ctypes
import copy
import asyncio
import base64
import functools
import hashlib
import json
import math
import os
import platform
import re
import socket
import subprocess
import shutil
import sqlite3
import logging
import threading
import time
import uuid
import webbrowser
import winsound
import sys
from dataclasses import dataclass
from collections import Counter, deque
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import markdown
import wx
import wx.adv

import mc_frame_bindings
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from common_commands_models import CommonCommandCreate, CommonCommandUpdate, CommonCommandsSnapshot
from common_commands_store import (
    CommonCommandsReadError,
    CommonCommandsWriteError,
    CommonCommandsVersionConflictError,
    DesktopCommonCommandsStore,
)
from chat_store import ChatStore
from remote_machine_config import load_machine_config
from execution_projection import (
    collapse_kimi_execution_lifecycle,
    execution_command_list_text,
    execution_list_text_from_detail,
    execution_row_id,
    execution_step_detail_text,
    execution_turn_context_steps,
    project_execution_rows,
    should_show_execution_step,
)
from chat_client import ChatClient, DEFAULT_MODEL, DEFAULT_OPENROUTER_API_KEY
from claudecode_client import ClaudeCodeClient, DEFAULT_CLAUDECODE_MODEL, is_claudecode_model
from cli_agent_manager import get_default_cli_agent_manager
from codex_client import (
    CodexEvent,
    CODEX_APPROVAL_MODES,
    normalize_codex_approval_mode,
    normalize_codex_sandbox,
    CODEX_SANDBOX_MODES,
    DEFAULT_CODEX_SANDBOX,
    DEFAULT_CODEX_APPROVAL_MODE,
    codex_approval_parameters,
    DEFAULT_CODEX_MODEL,
    codex_model_label_for_model,
    codex_service_tier_label,
    is_codex_model,
    normalize_codex_service_tier,
    read_codex_cli_model_label,
)
import codex_worker_protocol
from codex_worker_client import CodexWorkerClient
from codex_worker_process import main as codex_worker_main
from kimi_server_client import (
    DEFAULT_KIMI_MODEL,
    KimiServerClient,
    KimiServerError,
    event_from_payload as kimi_event_from_payload,
    event_to_payload as kimi_event_to_payload,
    is_kimi_model,
    kimi_model_to_server_alias,
    kimi_snapshot_total_tokens,
)
from context_usage import (
    context_usage_from_dict,
    estimate_turns_tokens,
    format_context_usage_label,
)
from file_transfer import CopypartyFileService, DesktopFileLibrary, FileDirection, FileTransferStatus, extract_windows_file_paths
from listbox_model import IncrementalListBoxModel
from notes_import import import_note_entries_from_clipboard, import_note_entries_from_file
from notes_backup import export_notes_backup, restore_notes_backup
from notes_projection import DesktopNotesProjection
from notes_store import (
    ENTRY_COLUMNS,
    MAY_DOCUMENT_CACHE_ENTRY_COLUMNS,
    NOTEBOOK_COLUMNS,
    PRE_PLACEMENT_ENTRY_COLUMNS,
    NotesPlacementConflict,
    NotesStore,
)
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
from chat_information import ChatInformationSubscriptions
from realtime_call import (
    DEFAULT_REALTIME_CALL_ROLE,
    DEFAULT_REALTIME_CALL_SPEECH_RATE,
    RealtimeCallController,
    RealtimeCallSettings,
)
from remote_nats_protocol import build_file_command_event
from speech_input import MODE_DIRECT, MODE_OPTIMIZE, VoiceInputController
from zdsr_tts import ZDSRTTSClient

from mc_frame_keyboard import (
    CHILDID_SELF,
    EVENT_OBJECT_FOCUS,
    EVENT_OBJECT_VALUECHANGE,
    GlobalCtrlTapHook,
    HOTKEY_ID_REALTIME_CALL,
    HOTKEY_ID_REALTIME_CALL_ALT,
    HOTKEY_ID_REALTIME_CALL_ALT2,
    HOTKEY_ID_SHOW,
    HOTKEY_ID_UNREAD,
    INPUT_KEYBOARD,
    KBDLLHOOKSTRUCT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    OBJID_CLIENT,
    VK_CONTROL,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_MENU,
    VK_OEM_102,
    VK_OEM_5,
    VK_PACKET,
    VK_PROCESSKEY,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
    VK_V,
    WH_KEYBOARD_LL,
    WM_CHAR,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_QUIT,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
)
from mc_frame_settings import (
    ANSWER_LIST_DEFAULT_VISIBLE_ROWS,
    ANSWER_LIST_EXPAND_ROWS,
    APP_DIR_NAME,
    APP_STATE_FILE,
    APP_WINDOW_TITLE,
    CODEX_BACKGROUND_FLUSH_DELAY_MS,
    CODEX_UI_EVENT_BATCH_DELAY_MS,
    CODEX_UI_EVENT_BATCH_SIZE,
    CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE,
    CONTEXT_USAGE_SYNC_TURN_LIMIT,
    DEFAULT_MODEL_ID,
    DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT,
    DEFAULT_REMOTE_CONTROL_DOMAIN,
    DEFAULT_REMOTE_CONTROL_TOKEN,
    DEFAULT_REMOTE_NATS_CLOUDFLARED_URL,
    DEFAULT_REMOTE_NATS_PORT,
    DEFAULT_REMOTE_NATS_WEBSOCKET_PORT,
    EMPTY_CURRENT_CHAT_TITLE,
    EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
    EXECUTION_LIST_EXPAND_ROWS,
    HIDDEN_MODEL_IDS,
    IDLE_UI_REFRESH_DELAY_MS,
    LEGACY_EMPTY_CURRENT_CHAT_TITLE,
    MAX_RECOVERY_ATTEMPTS,
    MODEL_IDS,
    NAVIGATION_QUIET_ALT_KEYS,
    NAVIGATION_QUIET_KEYS,
    NAVIGATION_QUIET_SECONDS,
    NOTES_ENTRY_LABEL_MAX_CHARS,
    REMOTE_CLOUDFLARED_ORIGIN_PORT_FALLBACKS,
    REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS,
    REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS,
    REMOTE_NATS_PORT_FALLBACKS,
    REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS,
    REMOTE_STATE_DEFAULT_TURN_LIMIT,
    REQUESTING_TEXT,
    STARTUP_DEFAULT_MODEL_ID,
    UI_PERF_MAX_SLOW_SAMPLES,
    UI_PERF_SLOW_THRESHOLD_MS,
    VISIBLE_MODEL_IDS,
    VOICE_OPTIMIZE_MODEL,
    VOICE_OPTIMIZE_PROMPT,
)


def openrouter_api_key_for_app() -> str:
    return DEFAULT_OPENROUTER_API_KEY

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

    def _guarded_call():
        # wx GUI work is only safe on the main thread. CallAfter callbacks
        # always run there in production; a non-main thread here means a test
        # double executed the callback synchronously from a worker thread.
        if threading.current_thread() is not threading.main_thread():
            return
        # Re-check at execution time: the target may have been destroyed while
        # this callback sat in the pending-event queue (e.g. teardown racing a
        # background worker thread). RuntimeError covers wxPython wrappers
        # whose C++ object is already deleted.
        if target is not None and not _wx_target_is_alive(target):
            return
        try:
            func(*args, **kwargs)
        except RuntimeError:
            return

    try:
        _guarded_call = functools.wraps(func)(_guarded_call)
    except Exception:
        pass

    try:
        wx.CallAfter(_guarded_call)
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


def resolve_app_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir.parent / "history"
    return Path(__file__).resolve().parent / "dist" / "history"


def resolve_common_commands_path() -> Path:
    if getattr(sys, "frozen", False):
        onedrive_dir = os.getenv("OneDriveConsumer") or os.getenv("OneDrive")
        if not onedrive_dir:
            raise RuntimeError("未检测到个人版 OneDrive 路径，无法读取常用命令。请先登录 OneDrive 并重新启动程序。")
        path = Path(onedrive_dir) / "OneDrive" / "code" / "data" / "sj" / "common_commands.json"
        if not path.is_file():
            raise RuntimeError(f"未找到 OneDrive 常用命令文件：{path}。请等待 OneDrive 同步完成后重试。")
        return path
    return resolve_app_data_dir() / "common_commands.json"


def resolve_notes_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        onedrive_dir = os.getenv("OneDriveConsumer") or os.getenv("OneDrive")
        if not onedrive_dir:
            raise RuntimeError("未检测到个人版 OneDrive 路径，无法读取笔记。请先登录 OneDrive 并重新启动程序。")
        path = Path(onedrive_dir) / "OneDrive" / "code" / "data" / "sj" / "notes.db"
        if not path.is_file():
            raise RuntimeError(f"未找到 OneDrive 笔记数据库：{path}。请等待 OneDrive 同步完成后重试。")
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
                valid = conn.execute("PRAGMA quick_check").fetchone()
                def columns(table: str) -> set[str]:
                    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                current = (NOTEBOOK_COLUMNS <= columns("notebooks")
                           and any(required <= columns("entries") for required in (
                               ENTRY_COLUMNS, PRE_PLACEMENT_ENTRY_COLUMNS, MAY_DOCUMENT_CACHE_ENTRY_COLUMNS)))
                legacy_notebook_columns = {
                    "id", "title", "created_at", "updated_at", "version", "device_id",
                    "last_modified_by", "is_conflict_copy", "origin_notebook_id", "deleted_at",
                }
                legacy_entry_columns = {
                    "id", "notebook_id", "content", "created_at", "updated_at", "sort_order",
                    "pinned", "version", "device_id", "last_modified_by", "is_conflict_copy",
                    "origin_entry_id", "source", "deleted_at",
                }
                legacy = (legacy_notebook_columns <= columns("notebooks")
                          and legacy_entry_columns <= columns("note_entries"))
                if valid is None or valid[0] != "ok" or not (current or legacy):
                    raise sqlite3.DatabaseError("invalid notes database")
        except sqlite3.Error as exc:
            raise RuntimeError("OneDrive 笔记数据库无法通过完整性检查，请等待同步完成或恢复有效备份后重试。") from exc
        return path.parent
    return Path(r"D:\code\note")


_CHAT_TITLE_RULES_CACHE: dict | None = None


def shared_chat_title_rules_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return bundle_root / "assets" / "chat_title_rules.json"
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
    return current.parent / "assets" / "chat_title_rules.json"


def load_chat_title_rules(path: Path | None = None, *, refresh: bool = False) -> dict:
    global _CHAT_TITLE_RULES_CACHE
    target = Path(path) if path is not None else shared_chat_title_rules_path()
    if _CHAT_TITLE_RULES_CACHE is not None and not refresh and path is None:
        return _CHAT_TITLE_RULES_CACHE
    rule_fields = (
        "leading_phrases",
        "action_prefixes",
        "what_is_prefixes",
        "question_suffixes",
        "trailing_punctuation",
    )
    valid_document = False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        valid_document = isinstance(data, dict) and all(
            field not in data or isinstance(data[field], list)
            for field in rule_fields
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    values = {
        field: data.get(field) if isinstance(data.get(field), list) else []
        for field in rule_fields
    }
    rules = {
        field: [str(item) for item in values[field] if str(item).strip()]
        for field in rule_fields
    }
    if path is None and valid_document:
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
    if model_id == AGY_MODEL_ID:
        return "AGY"
    if model_id.startswith("kimi/"):
        return "Kimi Code"
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
    if display_name.lower() == "agy":
        return AGY_MODEL_ID
    if display_name == "Kimi Code":
        return "kimi/main"
    if display_name == "openclaw":
        return "openclaw/main"
    return display_name


def normalize_model_id(value: str, *, default: str = DEFAULT_MODEL_ID) -> str:
    """Resolve display labels and accepted dynamic CLI model IDs consistently."""
    resolved = model_id_from_display_name(str(value or "").strip())
    if resolved in MODEL_IDS or is_codex_model(resolved) or is_claudecode_model(resolved) or is_kimi_model(resolved) or is_openclaw_model(resolved):
        return resolved
    return default


def is_visible_model_id(model_id: str) -> bool:
    return str(model_id or "").strip() in VISIBLE_MODEL_IDS


# Frame modules use this module for application paths and existing injection points.
mc_frame_bindings.bind_entry(globals())

from mc_frame_answers import AnswersMixin
from mc_frame_chat_information import ChatInformationMixin
from mc_frame_codex import CodexSessionsMixin
from mc_frame_common_commands import CommonCommandsMixin
from mc_frame_dialogs import (
    AnswerTextViewerDialog,
    AnswerViewerPayload,
    ChatInformationDialog,
    CodexUserInputDialog,
    CommonCommandEditDialog,
    CommonCommandsDialog,
    RealtimeCallSettingsDialog,
    RenameDialog,
)
from mc_frame_execution import ExecutionPageMixin
from mc_frame_execution_errors import ExecutionIdentityBlocked, ExecutionPagePending
from mc_frame_files import FileManagerMixin
from mc_frame_history import ChatHistoryMixin
from mc_frame_kimi_events import KimiEventsMixin
from mc_frame_kimi_sessions import KimiSessionsMixin
from mc_frame_navigation import ChatNavigationMixin
from mc_frame_notes import NotesMixin
from mc_frame_openclaw import OpenClawSessionsMixin
from mc_frame_presentation import (
    UNKNOWN_TIME_LABEL,
    WECHAT_TIME_GAP_SECONDS,
    _ParagraphStripper,
    _Stripper,
    _execution_timestamp,
    _finite_timestamp,
    _md_block_to_plain_preserving_ordered_lists,
    answer_detail_plain_text,
    answer_time_projection,
    compact_markdown_summary,
    execution_answer_to_plain,
    is_cli_filtered_model,
    md_to_plain,
    md_to_plain_preserving_paragraphs,
    remove_emojis,
    remove_trailing_punctuation,
    sanitize_optimized_text,
    should_show_time,
    wechat_time_label,
)
from mc_frame_remote_proxy import CloudflaredOriginProxy
from mc_frame_remote_routes import RemoteRoutesMixin
from mc_frame_remote_runtime import RemoteRuntimeMixin
from agy_acp import AGY_MODEL_ID
from mc_frame_agy import AgySessionsMixin
from mc_frame_native_history import NativeHistoryMixin
from mc_frame_requests import ChatRequestsMixin
from mc_frame_voice import VoiceInputMixin


class ChatFrame(
    CommonCommandsMixin,
    ChatInformationMixin,
    FileManagerMixin,
    NotesMixin,
    ChatHistoryMixin,
    AnswersMixin,
    ExecutionPageMixin,
    RemoteRoutesMixin,
    RemoteRuntimeMixin,
    VoiceInputMixin,
    OpenClawSessionsMixin,
    CodexSessionsMixin,
    KimiSessionsMixin,
    KimiEventsMixin,
    ChatRequestsMixin,
    NativeHistoryMixin,
    AgySessionsMixin,
    ChatNavigationMixin,
    wx.Frame,
):
    def __init__(self):
        super().__init__(None, title=APP_WINDOW_TITLE, size=(1200, 800))
        self.CreateStatusBar(1)
        self.SetStatusText("就绪")

        self.app_data_dir = resolve_app_data_dir()
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self._agy_clients = {}
        self._agy_busy = {}
        self._agy_new_chat_pending = False
        self._agy_remote_creations = 0
        self._agy_creation_lock = threading.Lock()
        self.state_path = self.app_data_dir / APP_STATE_FILE
        self.detail_pages_dir = self.app_data_dir / "detail_pages"
        self.detail_pages_dir.mkdir(parents=True, exist_ok=True)
        self.chat_uploads_dir = self.app_data_dir / "chat_uploads"
        self.chat_uploads_dir.mkdir(parents=True, exist_ok=True)
        file_storage_dir = os.environ.get("DESKTOP_FILE_STORAGE_DIR", "").strip()
        self.file_library = DesktopFileLibrary(storage_dir=file_storage_dir) if file_storage_dir else DesktopFileLibrary()
        self.file_service = CopypartyFileService(self.file_library)
        self.file_http_base_url = self.file_service.base_url
        self._cloudflared_origin_proxy = None
        self.notes_data_dir = resolve_notes_data_dir()
        self.notes_data_dir.mkdir(parents=True, exist_ok=True)
        self.notes_db_path = self.notes_data_dir / "notes.db"
        self.notes_device_id = f"desktop-{platform.node().strip().lower() or 'local'}"
        self.notes_store = NotesStore(self.notes_db_path, device_id=self.notes_device_id)
        self.notes_store.initialize()
        self.common_commands_store = DesktopCommonCommandsStore(resolve_common_commands_path())
        self.common_commands_store.initialize()
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
        self.codex_sandbox = DEFAULT_CODEX_SANDBOX
        self.codex_approval_policy = DEFAULT_CODEX_APPROVAL_MODE
        self.codex_approval_defaults_version = 1
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
        self._active_claudecode_chat_id = ""
        self._active_claudecode_client_lock = threading.Lock()
        self.active_kimi_session_id = ""
        self.active_kimi_turn_id = ""
        self.active_kimi_turn_active = False
        self.active_kimi_pending_prompt = ""
        self.active_kimi_request_queue = []
        self._kimi_client = None
        self._kimi_client_lock = threading.Lock()
        self._kimi_active_turns: dict[str, dict] = {}
        self._kimi_prompt_owners: dict[tuple[str, str, str], dict] = {}
        self._kimi_owner_lock = threading.RLock()
        self._kimi_submission_locks: dict[str, threading.Lock] = {}
        self._kimi_submission_sessions: dict[str, str] = {}
        self._kimi_early_events: dict[tuple[str, str], list[CodexEvent]] = {}
        self._kimi_early_event_key_limit = 128
        self._kimi_completed_identities: set[tuple[str, str, str, str]] = set()
        self._kimi_terminal_tombstones: set[tuple[str, str, str]] = set()
        self._kimi_tombstone_order: deque[tuple[str, str, str]] = deque()
        self._kimi_replay_window = 512
        self._kimi_idle_proofs: dict[tuple[str, str, str], int] = {}
        self._kimi_owner_generation = 0
        self._kimi_turn_answer_parts: dict[tuple[str, str, str, str], list[str]] = {}
        self._kimi_turn_answer_segments: dict[tuple[str, str, str, str, str], dict[int, str]] = {}
        self._kimi_incomplete_sessions: set[str] = set()
        self._kimi_incomplete_streams: set[tuple[str, str, str, str, str]] = set()
        self._kimi_recovery_workers: set[str] = set()
        self._kimi_recovery_intents: dict[str, str] = {}
        self._kimi_recovery_requested_generation: dict[str, int] = {}
        self._kimi_pending_submissions: dict[str, list[dict]] = {}
        self._kimi_interactions: dict[tuple[str, str], dict] = {}
        self._kimi_interaction_dialog = None
        self._kimi_reconcile_attempts = 8
        self._kimi_reconcile_backoff = 0.25
        self._kimi_thinking_sync_interval = 2.0
        self._kimi_thinking_sync_at: dict[str, float] = {}
        self._kimi_thinking_synced: dict[tuple[str, str], set[str]] = {}
        self._codex_clients: dict[str, CodexWorkerClient] = {}
        self._chat_information_request_generation = 0
        self._chat_information_request: tuple[str | None, int, tuple[str, str, str, str]] | None = None
        self._kimi_information_generation = 0
        self._kimi_information_requests: dict[str, dict] = {}
        self._codex_worker_active_turns: dict[str, dict] = {}
        self._remote_nats_process = None
        self._remote_nats_transport = None
        self._remote_health_lock = threading.RLock()
        self._remote_health_stop = threading.Event()
        self._remote_health_generation = 0
        self._remote_health_thread = None
        self._managed_cloudflared_process = None
        self._managed_cloudflared_log_handle = None
        self._remote_nats_websocket_port = DEFAULT_REMOTE_NATS_WEBSOCKET_PORT
        self._codex_background_flush_scheduled = False
        self._codex_background_flush_dirty = False
        self._pending_codex_ui_events: list[tuple] = []
        self._codex_ui_event_lock = threading.Lock()
        self._close_admission_lock = threading.Lock()
        self._accepted_background_callbacks = 0
        self._codex_ui_event_flush_scheduled = False
        self._codex_ui_event_drain_timer = None
        self._pending_kimi_ui_events: list[tuple[str, CodexEvent]] = []
        self._kimi_ui_event_lock = threading.Lock()
        self._kimi_ui_event_flush_scheduled = False
        self._kimi_ui_event_drain_timer = None
        self._codex_ui_batch_depth = 0
        self._background_ui_update_depth = 0
        self._background_answer_list_dirty = False
        self._background_history_dirty_ids = set()
        self._background_context_usage_dirty_ids = set()
        self._pending_history_reorder = False
        self._history_list_dirty = False
        self._pending_history_keep_id = None
        self._execution_list_dirty = False
        self._openclaw_lifecycle_dirty = False
        self._idle_ui_refresh_timer = None
        self._idle_ui_refresh_scheduled = False
        self._last_primary_interaction_at = time.monotonic()
        self._navigation_quiet_until = 0.0
        self._navigation_quiet_last_trigger = ""
        self._navigation_quiet_flush_timer = None
        self._answer_refresh_deadline_timer = None
        self._answer_refresh_deadline_owner = None
        self._answer_refresh_deadline_turns = []
        self._answer_refresh_destroy_window_id = self.GetId()
        self.Bind(wx.EVT_WINDOW_DESTROY, self._stop_answer_refresh_deadline)
        self._deferred_background_ui_counts = {}
        self._pending_execution_tail_appends = {}
        self.ui_perf_slow_threshold_ms = UI_PERF_SLOW_THRESHOLD_MS
        self.ui_perf_slow_samples: list[dict] = []
        self._execution_list_deferred_repaint = False
        self._execution_list_deferred_select_latest = False
        self._pending_execution_step_persists = []
        # Tests and embedders may run the worker synchronously; re-entrancy
        # keeps that execution mode from deadlocking while preserving the
        # production thread hand-off.
        self._execution_step_persist_lock = threading.RLock()
        self._execution_step_persist_scheduled = False
        self._execution_step_persist_worker_running = False
        self._execution_step_persist_thread = None
        self._kimi_execution_write_counts = {}
        self._kimi_execution_write_results = deque()
        self._kimi_execution_write_result_scheduled = False
        self._kimi_execution_boundaries = {}
        self._kimi_execution_private_keys = set()
        self._kimi_thinking_sync_counts = {}
        self._kimi_thinking_sync_jobs = set()
        self._kimi_rest_candidate_updates = {}
        self._kimi_execution_buffer_index = {}
        self._kimi_rest_withdrawals = {}
        self._chat_turn_dirty_from: dict[str, int] = {}
        self._remote_turn_payload_cache: dict[int, tuple[tuple, dict]] = {}
        self._context_usage_estimate_lock = threading.Lock()
        self._context_usage_estimate_keys: set[tuple[str, int]] = set()
        self._alt_menu_armed = False
        self._alt_menu_suppressed = False
        self.common_commands_dialog = None
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
        self._codex_early_context_usage_owner = {}
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
            on_state_change=self._on_voice_state,
            on_result=self._on_voice_result,
            on_error=self._on_voice_error,
            on_stop_recording=self._on_voice_stop_recording,
            dispatch=lambda deliver: wx_call_after_if_alive(self._deliver_voice_event, deliver),
        )
        self._chat_navigation_left_id = wx.NewIdRef()
        self._chat_navigation_right_id = wx.NewIdRef()
        self._clear_context_id = wx.NewIdRef()
        self._common_commands_menu_id = wx.NewIdRef()
        self._chat_information_menu_id = wx.NewIdRef()
        self._kimi_retry_interaction_menu_id = wx.NewIdRef()
        self._chat_information_dialog = None
        self._chat_information_previous_focus = None
        self._file_manager_menu_id = wx.NewIdRef()
        self._realtime_call_settings_menu_id = wx.NewIdRef()
        self._load_chat_attachments_menu_id = wx.NewIdRef()
        self._codex_answer_filter_menu_id = wx.NewIdRef()
        self._codex_sandbox_menu_id = wx.NewIdRef()
        self._codex_approval_policy_menu_id = wx.NewIdRef()
        self._notes_move_entry_up_id = wx.NewIdRef()
        self._notes_move_entry_down_id = wx.NewIdRef()
        self._notes_move_entry_top_id = wx.NewIdRef()
        self._notes_move_entry_bottom_id = wx.NewIdRef()
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
        self.notes_controller.restore_state(self._current_notes_state)
        if (str(getattr(self.notes_controller, "notes_view", "") or "") == "note_edit"
                and self._notes_guard_embedded_edit_view(
                    getattr(self.notes_controller, "active_entry_id", ""))):
            self.notes_controller.notes_view = "note_detail"
        self._notes_refresh_ui()
        self._pending_clear_recoveries = self.chat_store.recoverable_clear_operations()
        self._realtime_call.update_settings(
            RealtimeCallSettings(role=self.realtime_call_role, speech_rate=self.realtime_call_speech_rate)
        )
        self._initialize_remote_control_settings()
        wx_call_after_if_alive(self._realtime_call.prepare)
        if not getattr(self, "_chat_store_enabled", False):
            self._merge_legacy_archived_chats()
        self._schedule_remote_nats_autostart()
        self._start_file_service_if_configured()
        self._start_claudecode_remote_nats_runtime_if_configured()
        self._refresh_openclaw_sync_lifecycle(force_replay=not bool(self.active_openclaw_session_file))
        # The restored owner is authoritative, including for an empty chat.
        # Legacy rows may use their own newest valid turn once, then the
        # deterministic startup default; never borrow the ambient selector.
        restored_model = self._model_for_chat_selection(self._current_chat_state)
        self.selected_model = restored_model or STARTUP_DEFAULT_MODEL_ID
        if isinstance(self._current_chat_state, dict) and self.active_chat_id:
            needs_model_backfill = self._current_chat_state.get("model") != self.selected_model
            self._current_chat_state["model"] = self.selected_model
            if needs_model_backfill:
                self._defer_chat_state_save()
        self.model_combo.SetValue(model_display_name(self.selected_model))
        self._sync_codex_speed_combo_from_chat(self._current_chat_state)
        self._refresh_history()
        self._render_answer_list()
        self._set_input_hint_idle()
        if self._kimi_pending_owners() and self._kimi_client is None:
            self._recover_kimi_pending_owners()
        self._start_native_history_refresh()
        wx_call_after_if_alive(self.input_edit.SetFocus)

    def _build_ui(self):
        app_menu = wx.Menu()
        app_menu.Append(int(self._clear_context_id), "清空上下文\tAlt+A")
        app_menu.Append(int(self._common_commands_menu_id), "常用命令\tAlt+Z")
        app_menu.Append(int(self._chat_information_menu_id), "聊天信息\tAlt+Y")
        self._kimi_retry_interaction_menu_item = app_menu.Append(int(self._kimi_retry_interaction_menu_id), "重试 Kimi 请求")
        self._kimi_retry_interaction_menu_item.Enable(False)
        app_menu.Append(int(self._file_manager_menu_id), "文件管理")
        app_menu.AppendSeparator()
        app_menu.Append(int(self._realtime_call_settings_menu_id), "语音通话设置")
        app_menu.Append(int(self._load_chat_attachments_menu_id), "载入图片或文件")
        filter_item = app_menu.AppendCheckItem(int(self._codex_answer_filter_menu_id), "过滤英文内容")
        filter_item.Check(bool(getattr(self, "codex_answer_english_filter_enabled", False)))
        app_menu.Append(int(self._codex_sandbox_menu_id), "Codex 访问权限")
        app_menu.Append(int(self._codex_approval_policy_menu_id), "Codex 执行审批")
        menu_bar = wx.MenuBar()
        menu_bar.Append(app_menu, "应用(&A)")
        self.SetMenuBar(menu_bar)
        self.Bind(wx.EVT_MENU_OPEN, self._on_chat_information_menu_open)

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
        self.input_edit = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.TE_DONTWRAP | wx.HSCROLL, size=(-1, 140)
        )
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
        row.Add(wx.StaticText(panel, label="速度："), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 6)
        self.codex_speed_combo = wx.ComboBox(panel, choices=["标准", "快速"], style=wx.CB_READONLY, size=(96, -1))
        self.codex_speed_combo.SetName("速度")
        self.codex_speed_combo.SetValue("标准")
        row.Add(self.codex_speed_combo, 0, wx.ALIGN_CENTER_VERTICAL)
        right.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.detail_title_label = wx.StaticText(panel, label="回答：")
        right.Add(self.detail_title_label, 0, wx.LEFT, 10)
        self.answer_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.answer_list.SetName("回答列表")
        right.Add(self.answer_list, 1, wx.EXPAND | wx.ALL, 10)
        self.execution_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.execution_list.SetName("执行过程列表")
        right.Add(self.execution_list, 1, wx.EXPAND | wx.ALL, 10)
        self.file_manager_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.file_manager_list.SetName("文件管理")
        right.Add(self.file_manager_list, 1, wx.EXPAND | wx.ALL, 10)
        self.file_manager_list.Hide()
        self.extracted_file_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.extracted_file_list.SetName("提取文件")
        right.Add(self.extracted_file_list, 1, wx.EXPAND | wx.ALL, 10)
        self.extracted_file_list.Hide()
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
        self.notes_editor = wx.TextCtrl(self.notes_edit_panel, style=wx.TE_MULTILINE | wx.TE_DONTWRAP | wx.HSCROLL)
        self.notes_editor.SetName("笔记")
        self.notes_editor.SetToolTip("笔记")
        edit_sizer.Add(self.notes_editor, 1, wx.EXPAND | wx.ALL, 10)
        self.notes_edit_panel.SetSizer(edit_sizer)
        self.notes_content_label = wx.StaticText(panel, label="笔记：")
        right.Add(self.notes_content_label, 0, wx.LEFT, 10)
        right.Add(self.notes_detail_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        right.Add(self.notes_edit_panel, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.new_chat_button.MoveAfterInTabOrder(self.input_edit)
        self.model_combo.MoveAfterInTabOrder(self.new_chat_button)
        self.codex_speed_combo.MoveAfterInTabOrder(self.model_combo)
        self.send_button.MoveAfterInTabOrder(self.codex_speed_combo)
        self.notes_list_panel.MoveAfterInTabOrder(self.send_button)
        self.history_list.MoveAfterInTabOrder(self.notes_list_panel)
        self.answer_list.MoveAfterInTabOrder(self.history_list)
        self.execution_list.MoveAfterInTabOrder(self.answer_list)
        self.chat_root_panel.SetSizer(root)

        self.root_tab_order = []
        self.chat_tab_order = []
        self.notes_tab_order = []
        self.history_list_model = IncrementalListBoxModel(self.history_list)
        self.answer_list_model = IncrementalListBoxModel(self.answer_list)
        self.execution_list_model = IncrementalListBoxModel(self.execution_list)
        self.file_manager_list_model = IncrementalListBoxModel(self.file_manager_list)
        self.file_manager_ids = []
        self.extracted_file_list_model = IncrementalListBoxModel(self.extracted_file_list)
        self.extracted_file_ids = []
        self.notes_notebook_list_model = IncrementalListBoxModel(self.notes_notebook_list)
        self.notes_entry_list_model = IncrementalListBoxModel(self.notes_entry_list)
        self._notes_rebuild_tab_order()
        self._sync_notes_ui()

    def _bind_events(self):
        self.send_button.Bind(wx.EVT_BUTTON, self._on_send_clicked)
        self.new_chat_button.Bind(wx.EVT_BUTTON, self._on_new_chat_clicked)
        self.model_combo.Bind(wx.EVT_COMBOBOX, self._on_model_changed)
        self.codex_speed_combo.Bind(wx.EVT_COMBOBOX, self._on_codex_speed_changed)
        self.input_edit.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("input_key_down", self._on_input_key_down))
        self.input_edit.Bind(wx.EVT_TEXT, self._monitored_ui_handler("input_text", self._on_input_text_changed))
        self.input_edit.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.send_button.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.new_chat_button.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.model_combo.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.codex_speed_combo.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.answer_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.history_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CHILD_FOCUS, self._on_chat_navigation_child_focus)
        self.Bind(wx.EVT_SHOW, self._on_show_sync_tray_state)
        self.Bind(wx.EVT_ACTIVATE, self._on_application_activate)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_SHOW)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_UNREAD)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL_ALT)
        self.Bind(wx.EVT_HOTKEY, self._on_global_hotkey, id=HOTKEY_ID_REALTIME_CALL_ALT2)
        self.Bind(wx.EVT_KEY_DOWN, self._on_frame_key_down)

        self.answer_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("answer_key_down", self._on_answer_key_down))
        self.answer_list.Bind(wx.EVT_CHAR, self._on_answer_char)
        self.answer_list.Bind(wx.EVT_LEFT_DOWN, self._on_answer_mouse_down)
        self.answer_list.Bind(wx.EVT_LEFT_DCLICK, self._on_answer_mouse_double_click)
        self.answer_list.Bind(wx.EVT_LEFT_UP, self._on_answer_mouse_read)
        self.answer_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_answer_activate)
        self.execution_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("execution_key_down", self._on_execution_key_down))
        self.execution_list.Bind(wx.EVT_KEY_UP, self._on_input_key_up)
        self.execution_list.Bind(wx.EVT_CHAR, self._on_execution_char)
        self.execution_list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_execution_activate)
        self.execution_list.Bind(wx.EVT_LISTBOX, self._on_execution_selection_changed)
        self.execution_list.Bind(wx.EVT_KILL_FOCUS, self._on_execution_focus_lost)
        self.file_manager_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("file_manager_key_down", self._on_file_manager_key_down))
        self.file_manager_list.Bind(wx.EVT_CONTEXT_MENU, self._on_file_manager_context)
        self.file_manager_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._open_selected_file_manager_record())
        self.extracted_file_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("extracted_file_key_down", self._on_extracted_file_key_down))
        self.extracted_file_list.Bind(wx.EVT_CONTEXT_MENU, self._on_extracted_file_context)
        self.history_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("history_key_down", self._on_history_key_down))
        self.history_list.Bind(wx.EVT_CHAR, self._on_history_char)
        self.history_list.Bind(wx.EVT_LEFT_DOWN, self._on_history_mouse_down)
        self.history_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._activate_selected_history())
        self.history_list.Bind(wx.EVT_LISTBOX, self._on_history_selected)
        self.history_list.Bind(wx.EVT_CONTEXT_MENU, self._on_history_context)
        self.model_combo.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("generic_key_down", self._on_generic_key_down))
        self.codex_speed_combo.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("generic_key_down", self._on_generic_key_down))
        self.send_button.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("generic_key_down", self._on_generic_key_down))
        self.new_chat_button.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("generic_key_down", self._on_generic_key_down))
        self.new_chat_button.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.new_chat_button.Bind(wx.EVT_CHAR, self._on_generic_key_down)
        self.Bind(wx.EVT_MENU, lambda _evt: self._handle_vertical_chat_shortcut(-1), id=int(self._chat_navigation_left_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._handle_vertical_chat_shortcut(1), id=int(self._chat_navigation_right_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._trigger_shortcut_clear(), id=int(self._clear_context_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_common_commands_surface(), id=int(self._common_commands_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_chat_information(), id=int(self._chat_information_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_pending_kimi_interaction(retry=True),
                  id=int(self._kimi_retry_interaction_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_file_manager(), id=int(self._file_manager_menu_id))
        self.Bind(wx.EVT_MENU, self._on_open_realtime_call_settings, id=int(self._realtime_call_settings_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._load_chat_attachments_via_dialog(), id=int(self._load_chat_attachments_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._toggle_codex_answer_filter(), id=int(self._codex_answer_filter_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_codex_sandbox(), id=int(self._codex_sandbox_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._show_codex_approval_policy(), id=int(self._codex_approval_policy_menu_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_up(), id=int(self._notes_move_entry_up_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_down(), id=int(self._notes_move_entry_down_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_to_top(), id=int(self._notes_move_entry_top_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_to_bottom(), id=int(self._notes_move_entry_bottom_id))
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_UP, int(self._chat_navigation_left_id)),
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_DOWN, int(self._chat_navigation_right_id)),
                    wx.AcceleratorEntry(wx.ACCEL_ALT, ord("A"), int(self._clear_context_id)),
                    wx.AcceleratorEntry(wx.ACCEL_ALT, ord("Z"), int(self._common_commands_menu_id)),
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_HOME, int(self._notes_move_entry_top_id)),
                    wx.AcceleratorEntry(wx.ACCEL_CTRL, wx.WXK_END, int(self._notes_move_entry_bottom_id)),
                ]
            )
        )

        if hasattr(self, "notes_notebook_list"):
            self.notes_notebook_list.Bind(wx.EVT_LISTBOX, self._on_notes_notebook_selected)
            self.notes_notebook_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._notes_open_selected_notebook())
            self.notes_notebook_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("notes_key_down", self._on_notes_key_down))
            self.notes_notebook_list.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)
            self.notes_entry_list.Bind(wx.EVT_LISTBOX, self._on_notes_entry_selected)
            self.notes_entry_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _evt: self._notes_edit_entry())
            self.notes_entry_list.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("notes_key_down", self._on_notes_key_down))
            self.notes_entry_list.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)
            self.notes_editor.Bind(wx.EVT_TEXT, self._on_notes_editor_changed)
            self.notes_editor.Bind(wx.EVT_KEY_DOWN, self._monitored_ui_handler("notes_key_down", self._on_notes_key_down))
            self.notes_editor.Bind(wx.EVT_CONTEXT_MENU, self._on_notes_context)

    def _set_input_hint_idle(self):
        self.input_hint_state = "输入"
        self.input_edit.SetName(self.input_hint_state)

    def _set_input_hint_sent(self):
        self.input_hint_state = "已发送"
        self.input_edit.SetName(self.input_hint_state)

    def _open_local_webpage(self, page_path: Path) -> bool:
        try:
            os.startfile(str(page_path))  # type: ignore[attr-defined]
            return True
        except Exception:
            return bool(webbrowser.open(page_path.resolve().as_uri()))

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

    def _request_listbox_repaint(self, *controls) -> None:
        with self._measure_ui_operation("listbox_repaint"):
            for control in controls:
                if control is None:
                    continue
                try:
                    control.Refresh()
                except Exception:
                    pass

    @contextmanager
    def _measure_ui_operation(self, name: str, **metadata):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            self._record_slow_ui_operation(name, elapsed_ms, **metadata)

    def _record_slow_ui_operation(self, name: str, elapsed_ms: float, **metadata) -> None:
        threshold = float(getattr(self, "ui_perf_slow_threshold_ms", UI_PERF_SLOW_THRESHOLD_MS) or UI_PERF_SLOW_THRESHOLD_MS)
        if elapsed_ms < threshold:
            return
        samples = getattr(self, "ui_perf_slow_samples", None)
        if not isinstance(samples, list):
            samples = []
            self.ui_perf_slow_samples = samples
        sample = {
            "name": str(name or "unknown"),
            "elapsed_ms": float(elapsed_ms),
            "ts": time.time(),
        }
        if metadata:
            sample["metadata"] = dict(metadata)
        samples.append(sample)
        overflow = len(samples) - UI_PERF_MAX_SLOW_SAMPLES
        if overflow > 0:
            del samples[:overflow]

    def _monitored_ui_handler(self, name: str, handler):
        def _wrapped(event):
            self._mark_primary_interaction()
            with self._measure_ui_operation(name):
                return handler(event)

        _wrapped._ui_perf_name = str(name or "")
        return _wrapped

    def _mark_primary_interaction(self) -> None:
        self._last_primary_interaction_at = time.monotonic()

    def _primary_navigation_control_is_recently_active(self) -> bool:
        if not self._primary_navigation_control_has_focus():
            return False
        last = float(getattr(self, "_last_primary_interaction_at", 0.0) or 0.0)
        if last <= 0:
            return False
        elapsed_ms = max(0.0, (time.monotonic() - last) * 1000.0)
        return elapsed_ms < IDLE_UI_REFRESH_DELAY_MS

    def _schedule_idle_ui_refresh(self, delay_ms: int = IDLE_UI_REFRESH_DELAY_MS) -> None:
        if bool(getattr(self, "_idle_ui_refresh_scheduled", False)):
            return
        self._idle_ui_refresh_scheduled = True
        timer = self._call_later_if_alive(int(delay_ms), self._flush_idle_ui_refreshes)
        if timer is None:
            self._idle_ui_refresh_scheduled = False
            return
        self._idle_ui_refresh_timer = timer

    def _record_chat_activity(self, chat: dict, observed_at: float) -> None:
        """Record an accepted message fact using the persisted history clock."""
        chat["updated_at"] = max(float(chat.get("updated_at") or 0.0), float(observed_at))
        self._upsert_history_row(str(chat.get("id") or ""), message_fact=True)
        self._defer_chat_state_save()

    def _mark_history_list_dirty(self, keep_id: str | None = None) -> None:
        self._history_list_dirty = True
        if keep_id is not None:
            self._pending_history_keep_id = keep_id
        self._schedule_idle_ui_refresh()

    def _mark_openclaw_lifecycle_dirty(self) -> None:
        self._openclaw_lifecycle_dirty = True
        self._schedule_idle_ui_refresh()

    def _flush_idle_ui_refreshes(self) -> None:
        self._idle_ui_refresh_scheduled = False
        self._idle_ui_refresh_timer = None
        if not self._is_ui_alive():
            return
        has_history_work = bool(getattr(self, "_history_list_dirty", False) or getattr(self, "_pending_history_reorder", False))
        has_execution_work = bool(getattr(self, "_execution_list_dirty", False))
        has_openclaw_work = bool(getattr(self, "_openclaw_lifecycle_dirty", False))
        if not has_history_work and not has_execution_work and not has_openclaw_work:
            return
        if self._navigation_quiet_active():
            remaining_ms = int(max(1.0, (float(getattr(self, "_navigation_quiet_until", 0.0) or 0.0) - time.monotonic()) * 1000.0))
            self._schedule_idle_ui_refresh(remaining_ms)
            return
        if self._primary_navigation_control_is_recently_active():
            self._schedule_idle_ui_refresh()
            return
        if has_history_work:
            keep_id = getattr(self, "_pending_history_keep_id", None)
            self._history_list_dirty = False
            self._pending_history_reorder = False
            self._pending_history_keep_id = None
            with self._measure_ui_operation("idle_history_list_refresh"):
                self._refresh_history(keep_id)
        if bool(getattr(self, "_execution_list_dirty", False)) and self._execution_list_visible_for_updates():
            with self._measure_ui_operation("idle_execution_list_refresh"):
                self._render_execution_list(force=True)
        if bool(getattr(self, "_openclaw_lifecycle_dirty", False)):
            self._openclaw_lifecycle_dirty = False
            with self._measure_ui_operation("idle_openclaw_lifecycle_refresh"):
                self._refresh_openclaw_sync_lifecycle()
        self._flush_pending_background_ui_updates()

    def _on_input_text_changed(self, event) -> None:
        event.Skip()

    def _is_foreground_window(self) -> bool:
        return self.IsActive()

    def _can_focus_completion_result(self) -> bool:
        # 只在窗口本来就在前台且未最小化时，才允许把焦点移到最新回答。
        if not self._is_foreground_window() or self.IsIconized():
            return False
        if self._notes_editor_has_protected_focus():
            return False
        try:
            focus = wx.Window.FindFocus()
        except Exception:
            focus = None
        if focus is None:
            for ctrl in (
                getattr(self, "input_edit", None),
                getattr(self, "answer_list", None),
                getattr(self, "send_button", None),
                getattr(self, "execution_list", None),
                getattr(self, "history_list", None),
                getattr(self, "model_combo", None),
                getattr(self, "codex_speed_combo", None),
                getattr(self, "notes_notebook_list", None),
                getattr(self, "notes_entry_list", None),
                getattr(self, "notes_editor", None),
            ):
                if ctrl is None:
                    continue
                try:
                    if ctrl.HasFocus():
                        return ctrl in {
                            getattr(self, "input_edit", None),
                            getattr(self, "answer_list", None),
                            getattr(self, "send_button", None),
                        }
                except Exception:
                    continue
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

        # This is only a candidate.  Allocation or an accepted submission is
        # the owner-consuming action that commits it to a chat.

    def _on_application_activate(self, event) -> None:
        active = bool(event.GetActive())
        self._application_active = active
        if not active:
            self._release_execution_focus_lease()
            self._cancel_chat_navigation_focus()
        event.Skip()

                        # Content pagination does not change the readable identity.

    def _set_status_text_safe(self, text: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.SetStatusText(str(text or ""))
            return
        self._call_after_if_alive(self.SetStatusText, str(text or ""))

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

    def _wx_main_loop_running(self) -> bool:
        app = wx.GetApp()
        if app is None:
            return False
        try:
            return bool(app.IsMainLoopRunning())
        except Exception:
            return False

    def _call_after_if_alive(self, func, *args, **kwargs) -> bool:
        lock = getattr(self, "_close_admission_lock", None)
        settlement = getattr(func, "__name__", "") in {
            "_advance_close", "_finish_shortcut_clear", "_drain_kimi_execution_write_results",
            "_drain_kimi_ui_events", "_drain_codex_ui_events", "_continue_kimi_thinking_sync",
        }
        if lock is None or settlement or threading.current_thread() is threading.main_thread():
            return wx_call_after_if_alive(func, *args, **kwargs)
        with lock:
            if getattr(self, "_close_producer_fenced", False):
                return False
            self._accepted_background_callbacks += 1

        @functools.wraps(func)
        def deliver():
            try:
                if self._is_ui_alive():
                    func(*args, **kwargs)
            finally:
                with lock:
                    self._accepted_background_callbacks -= 1
                self._schedule_close_progress()

        if wx_call_after_if_alive(deliver):
            return True
        with lock:
            self._accepted_background_callbacks -= 1
        return False

    def _call_later_if_alive(self, delay_ms: int, func, *args, **kwargs):
        return wx_call_later_if_alive(delay_ms, func, *args, **kwargs)

    def _on_close(self, event: wx.CloseEvent):
        if getattr(self, "_close_complete", False):
            event.Skip()
            return
        can_veto = getattr(event, "CanVeto", lambda: True)()
        if can_veto:
            event.Veto()
        if getattr(self, "_close_write_error", None) is not None:
            self._close_write_error = None
            self._close_write_done.clear()
            self._close_write_started = False
            self.Hide()
            self._advance_close()
        if not getattr(self, "_close_started", False):
            self._close_started = time.monotonic()
            transport = getattr(self, "_remote_nats_transport", None)
            self._close_publication_pair = (transport.subjects.pair_id
                if transport is not None and int(getattr(transport, "protocol_version", 1) or 1) >= 2 else "")
            self._close_pair_id = self._read_pair_id()
            self._closing = True
            self._stop_native_history_refresh()
            self.Hide()
            self._close_hidden_at = time.monotonic()
            self._cancel_chat_navigation_focus()
            if self._tray_icon:
                try:
                    self._tray_icon.RemoveIcon()
                    self._tray_icon.Destroy()
                except Exception:
                    pass
                self._tray_icon = None
            self._unregister_global_hotkey()
            self._capture_notes_ui_state(capture_notes_editor=True)
            self._voice_input.shutdown()
            self._stop_answer_refresh_deadline()
            self._release_execution_focus_lease()
            self._invalidate_execution_scan()
            if self._answer_redirect_timer:
                self._answer_redirect_timer.Stop()
                self._answer_redirect_timer = None
            self._start_close_producers()
            self._advance_close()
        if not can_veto:
            # A system close can arrive before the usual wx callback. Pump
            # the same drain for a bounded interval and keep its async writer
            # running if the OS grants more time.
            deadline = time.monotonic() + 4.0
            app = wx.GetApp()
            if app is not None and not getattr(self, "_close_force_waiting", False):
                self._close_force_waiting = True
                try:
                    while (time.monotonic() < deadline and not getattr(self, "_close_complete", False)
                           and not getattr(self, "_close_write_error", None)):
                        app.Yield()
                        self._advance_close()
                        time.sleep(.01)
                finally:
                    self._close_force_waiting = False

    def _start_close_producers(self) -> None:
        clients = list(getattr(self, "_codex_clients", {}).values())
        clients.extend((getattr(self, "_codex_client", None), getattr(self, "_kimi_client", None)))
        clients = list({id(client): client for client in clients if client is not None}.values())
        self._close_producers_done = threading.Event()
        self._close_write_done = threading.Event()
        self._close_write_started = False
        self._close_write_error = None
        close_tasks = [client.close for client in clients]
        close_tasks.extend((self.notes_sync.close, self._close_agy_clients, self._stop_remote_servers,
                            self._global_ctrl_hook.stop, self._realtime_call.shutdown,
                            self._stop_openclaw_sync))
        service = getattr(self, "file_service", None)
        if service is not None:
            close_tasks.append(service.stop)
        # Notes close detaches network I/O and waits for locally accepted writes.
        notes_close = close_tasks.pop(len(clients))
        tunnel_process = getattr(self, "_managed_cloudflared_process", None)
        nats_process = getattr(getattr(self, "_remote_nats_process", None), "_process", None)

        def close_task(task):
            try:
                task()
            except Exception:
                logging.getLogger(__name__).exception("Close task failed")

        def close_producers():
            deadline = self._close_started + 3.0
            notes = threading.Thread(target=close_task, args=(notes_close,), daemon=True)
            notes.start()
            workers = [threading.Thread(target=close_task, args=(task,), daemon=True) for task in close_tasks]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(max(0, deadline - .4 - time.monotonic()))
            processes = [getattr(client, "process", None) for client in clients]
            processes.extend((tunnel_process, nats_process))
            for action, cutoff in (("terminate", deadline - .15), ("kill", deadline)):
                for proc in processes:
                    try:
                        if proc is not None and proc.poll() is None:
                            getattr(proc, action)()
                    except Exception:
                        pass
                for worker in workers:
                    worker.join(max(0, cutoff - time.monotonic()))
            with self._close_admission_lock, self._codex_ui_event_lock, self._kimi_ui_event_lock:
                self._close_producer_fenced = True
            notes.join()
            self._close_producers_at = time.monotonic()
            self._close_producers_done.set()
            self._call_after_if_alive(self._advance_close)

        threading.Thread(target=close_producers, name="mc-close-producers", daemon=True).start()

    def _schedule_close_progress(self) -> None:
        if getattr(self, "_close_started", False) and not getattr(self, "_close_complete", False):
            self._call_after_if_alive(self._advance_close)

    def _advance_close(self) -> None:
        if getattr(self, "_close_complete", False) or not self._is_ui_alive():
            return
        timer = getattr(self, "_close_poll_timer", None)
        if timer is not None:
            timer.Stop()
            self._close_poll_timer = None
        if self._close_complete_if_written():
            return
        if getattr(self, "_pending_kimi_ui_events", None):
            self._drain_kimi_ui_events()
        if getattr(self, "_pending_codex_ui_events", None):
            self._drain_codex_ui_events()
        if getattr(self, "_kimi_execution_write_results", None):
            self._drain_kimi_execution_write_results()
        self._start_execution_step_persist_worker()
        with self._execution_step_persist_lock:
            execution_pending = (self._execution_step_persist_worker_running
                                 or bool(self._pending_execution_step_persists))
        with self._close_admission_lock:
            background_pending = self._accepted_background_callbacks
        pending = (background_pending or execution_pending or getattr(self, "_pending_kimi_ui_events", None)
                   or getattr(self, "_pending_codex_ui_events", None)
                   or getattr(self, "_kimi_execution_write_counts", None)
                   or getattr(self, "_kimi_thinking_sync_counts", None)
                   or getattr(self, "_kimi_execution_boundaries", None)
                   or getattr(self, "_shortcut_clear_pending", None))
        if self._close_producers_done.is_set() and not pending and not self._close_write_started:
            self._close_write_started = True
            if not hasattr(self, "_close_write_batch"):
                try:
                    self._close_write_batch = self._capture_close_write_batch()
                except Exception as exc:
                    self._close_write_error = exc
                    self._close_write_done.set()
                    self._close_complete_if_written()
                    return
            batch = self._close_write_batch

            def write_final_state():
                for attempt in range(3):
                    try:
                        self._write_close_batch(batch)
                    except (sqlite3.OperationalError, OSError) as exc:
                        if attempt < 2:
                            time.sleep(.15 * (attempt + 1))
                            continue
                        self._close_write_error = exc
                    except Exception as exc:
                        self._close_write_error = exc
                    else:
                        self._close_write_error = None
                    self._close_write_done.set()
                    self._call_after_if_alive(self._advance_close)
                    return

            threading.Thread(target=write_final_state, name="mc-close-writes", daemon=True).start()
        if self._close_producers_done.is_set() and not self._close_write_started:
            self._close_poll_timer = wx.CallLater(25, self._advance_close)

    def _close_complete_if_written(self) -> bool:
        if not self._close_write_done.is_set():
            return False
        if self._close_write_error is not None:
            if not self.IsShown():
                logging.getLogger(__name__).error("MC close persistence failed", exc_info=self._close_write_error)
                self.Show()
                self.Raise()
                message = "未能保存聊天，数据仍在内存中。解决存储错误后，按 Alt+F4 重试保存并退出。"
                self.SetStatusText(message)
                self.SetTitle(message)
                self._speak_text_via_screen_reader(message)
            return True
        self._close_complete = True
        self._close_poll_timer = None
        logging.getLogger(__name__).info(
            "MC close timing: hide %.1f ms, producers %.1f ms, final %.1f ms",
            (self._close_hidden_at - self._close_started) * 1000,
            (self._close_producers_at - self._close_started) * 1000,
            (time.monotonic() - self._close_started) * 1000,
        )
        self.Destroy()
        return True


def app_instance_key() -> str:
    # Separate installed copies have separate chat stores. Prevent two writers
    # to the same store without blocking a copy installed in another directory.
    data_path = os.path.normcase(str(resolve_app_data_dir().resolve()))
    scope = hashlib.sha256(data_path.encode("utf-8")).hexdigest()[:24]
    return APP_WINDOW_TITLE + "_single_instance_" + scope


class ChatApp(wx.App):
    def OnInit(self):
        terminal_start = "--shared-terminal-start" in sys.argv[1:]
        self._checker = wx.SingleInstanceChecker(app_instance_key())
        if self._checker.IsAnotherRunning():
            if not terminal_start:
                self._activate_existing_window()
            return False
        try:
            resolve_common_commands_path()
            resolve_notes_data_dir()
        except RuntimeError as exc:
            wx.MessageBox(str(exc), "启动数据不可用", wx.OK | wx.ICON_ERROR)
            return False
        frame = ChatFrame()
        if terminal_start:
            frame.ShowWithoutActivating()
        else:
            frame.Show()
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            self._start_shared_terminal(frame, report_dialog=not terminal_start)
        return True

    def _start_shared_terminal(self, frame, *, report_dialog):
        from shared_terminal import TerminalError, TerminalServer
        from shared_terminal_command import register_commands, terminal_endpoint
        from shared_terminal_routes import dispatch_terminal

        gui = Path(sys.executable)
        try:
            self._shared_terminal = TerminalServer(
                terminal_endpoint(gui), lambda op, body: dispatch_terminal(frame, op, body),
            )
            self._shared_terminal.__enter__()
            conflicts = []
            if os.getenv("MC_SHARED_TERMINAL_REGISTER", "1") != "0":
                conflicts = register_commands(gui.with_name("mc_worker.exe"))
            if conflicts:
                message = "\n".join(conflicts)
                logging.getLogger(__name__).warning("PowerShell command registration conflicts: %s", message)
                if report_dialog:
                    wx.CallAfter(frame._show_ok_dialog,
                                 message + "\n如需使用 MC 命令，请先调整这些同名命令，再重启 MC。", "PowerShell 命令冲突")
        except (OSError, TerminalError) as exc:
            logging.getLogger(__name__).warning("MC terminal setup failed: %s", exc)
            if report_dialog:
                wx.CallAfter(frame._show_ok_dialog,
                             "无法设置 PowerShell 命令。请检查当前用户的本机应用目录和 Path 设置，再重启 MC。",
                             "PowerShell 命令设置失败")

    def OnExit(self):
        service = getattr(self, "_shared_terminal", None)
        if service is not None:
            service.close()
        return super().OnExit()

    def _activate_existing_window(self):
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_WINDOW_TITLE)
        if hwnd:
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)


if __name__ == "__main__" and mc_frame_bindings.app is not None and vars(mc_frame_bindings.app) is globals():
    if "--codex-worker" in sys.argv:
        raise SystemExit(codex_worker_main())
    app = ChatApp()
    app.MainLoop()
