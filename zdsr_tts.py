import ctypes
import os
import sys
import threading
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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init_tried = False
        self._ready = False
        self._api = None
        self._type = _env_int("ZDSR_TTS_TYPE", 0)
        self._channel_name = os.getenv("ZDSR_TTS_CHANNEL", "").strip()
        self._keydown_interrupt = _env_true("ZDSR_TTS_KEYDOWN_INTERRUPT", True)
        self._speak_interrupt = _env_true("ZDSR_TTS_INTERRUPT", True)

    def _candidate_dirs(self) -> list[Path]:
        dirs = []
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
                try:
                    return ctypes.WinDLL(str(p))
                except Exception:
                    continue
        for name in self._candidate_names():
            try:
                return ctypes.WinDLL(name)
            except Exception:
                continue
        return None

    def _ensure_ready(self) -> bool:
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
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
            channel = self._channel_name or None
            ret = int(api.InitTTS(self._type, channel, 1 if self._keydown_interrupt else 0))
            if ret != 0:
                return False
            self._api = api
            self._ready = True
            return True

    def speak(self, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        if not self._ensure_ready():
            return False
        try:
            self._api.StopSpeak()
        except Exception:
            pass
        try:
            ret = int(self._api.Speak(content, 1 if self._speak_interrupt else 0))
        except Exception:
            return False
        return ret == 0
