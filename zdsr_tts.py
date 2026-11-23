import ctypes
import os
import sys
import threading
import weakref
from pathlib import Path


def _env_true(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


class ZDSRTTSClient:
    """
    Thin wrapper around ZDSRAPI:
      InitTTS(type, channelName, bKeyDownInterrupt)
      Speak(text, bInterrupt)
      StopSpeak()
    """

    # The SDK keeps the selected channel in the loaded DLL, shared by all
    # WinDLL wrappers in this process. Select a channel and call it atomically.
    _lock = threading.Lock()
    _active_clients = weakref.WeakValueDictionary()

    def __init__(self, *, chat_navigation: bool = False) -> None:
        self._init_tried = False
        self._ready = False
        self._api = None
        self.speech_generation = 0
        self._chat_navigation = chat_navigation
        self._type = 1 if chat_navigation else _env_int("ZDSR_TTS_TYPE", 0)
        self._channel_name = f"MC.ChatNavigation.{os.getpid()}" if chat_navigation else os.getenv("ZDSR_TTS_CHANNEL", "").strip()
        self._keydown_interrupt = not chat_navigation and _env_true("ZDSR_TTS_KEYDOWN_INTERRUPT", True)
        self._speak_interrupt = chat_navigation or _env_true("ZDSR_TTS_INTERRUPT", True)

    def _candidate_dirs(self) -> list[Path]:
        dirs = []
        if self._chat_navigation:
            # The installed SDK matches the running reader and reports when a
            # newly created independent channel is still initializing.
            for variable in ("ProgramFiles(x86)", "ProgramFiles"):
                program_files = os.environ.get(variable)
                if program_files:
                    dirs.append(Path(program_files) / "zdsr" / "zdsr")
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            dirs.append(exe_dir)
            dirs.append(exe_dir / "_internal")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                dirs.append(Path(meipass))
        dirs.append(Path(__file__).resolve().parent)
        dirs.append(Path.cwd())
        out = []
        seen = set()
        for d in dirs:
            if not d.exists():
                continue
            key = str(d).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
        return out

    def _candidate_names(self) -> list[str]:
        # Prefer matching current process bitness.
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            return ["ZDSRAPI_x64.dll", "ZDSRAPI.dll"]
        return ["ZDSRAPI.dll", "ZDSRAPI_x64.dll"]

    def _load_api(self):
        for base in self._candidate_dirs():
            for name in self._candidate_names():
                p = base / name
                if not p.exists():
                    continue
                dll_cookie = None
                try:
                    add_dll_directory = getattr(os, "add_dll_directory", None)
                    if callable(add_dll_directory):
                        dll_cookie = add_dll_directory(str(base))
                except Exception:
                    dll_cookie = None
                try:
                    return ctypes.WinDLL(str(p))
                except Exception:
                    continue
                finally:
                    if dll_cookie is not None:
                        try:
                            dll_cookie.close()
                        except Exception:
                            pass
        for name in self._candidate_names():
            try:
                return ctypes.WinDLL(name)
            except Exception:
                continue
        return None

    def _reset_state(self) -> None:
        if self._api is not None and self._active_clients.get(self._module_key) is self:
            self._active_clients.pop(self._module_key)
        self._ready = False
        self._init_tried = False
        self._api = None

    @property
    def _module_key(self):
        return getattr(self._api, "_handle", id(self._api))

    def _ensure_ready(self) -> bool:
        if not self._ready:
            if self._init_tried:
                return False
            self._init_tried = True
            api = self._load_api()
            if not api:
                return False
            try:
                api.InitTTS.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int]
                api.InitTTS.restype = ctypes.c_int
                api.Speak.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
                api.Speak.restype = ctypes.c_int
                api.StopSpeak.argtypes = []
                api.StopSpeak.restype = None
            except Exception:
                return False
            self._api = api
        if self._active_clients.get(self._module_key) is not self:
            self._active_clients.pop(self._module_key, None)
            try:
                ret = int(self._api.InitTTS(self._type, self._channel_name or None, int(self._keydown_interrupt)))
            except Exception:
                ret = -1
            if ret != 0:
                self._reset_state()
                return False
            self._active_clients[self._module_key] = self
        self._ready = True
        return True

    def _speak_once(self, content: str) -> bool:
        if not self._chat_navigation:
            self._stop_selected_channel()
        try:
            ret = int(self._api.Speak(content, 1 if self._speak_interrupt else 0))
        except Exception:
            return False
        return ret == 0

    def speak(self, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        with self._lock:
            self.speech_generation += 1
            if not self._ensure_ready():
                return False
            if self._speak_once(content):
                return True
            self._reset_state()
            return self._ensure_ready() and self._speak_once(content)

    def _stop_selected_channel(self) -> None:
        try:
            self._api.StopSpeak()
        except Exception:
            pass

    def stop(self) -> None:
        with self._lock:
            if self._ready and self._ensure_ready():
                self._stop_selected_channel()

    def speak_chat_navigation(self, title: str) -> bool | None:
        """Replace the title, or wait while the independent channel initializes."""
        with self._lock:
            try:
                if not self._ensure_ready():
                    return False
                if self._raw_speak_state() == 9:
                    return None
                result = int(self._api.Speak(title, 1))
            except Exception:
                result = 1
            if result == 9:
                return None
            if result:
                self._stop_selected_channel()
                self._reset_state()
            return result == 0

    def _raw_speak_state(self) -> int | None:
        try:
            query = self._api.GetSpeakState
            query.argtypes = []
            query.restype = ctypes.c_int
            return int(query())
        except Exception:
            return None

    def speak_state(self) -> bool | None:
        """Return speaking/idle, or None when the 争渡 status API is unavailable."""
        with self._lock:
            if not self._ready or not self._ensure_ready():
                return None
            return {3: True, 4: False}.get(self._raw_speak_state())
