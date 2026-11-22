"""Machine-local identity for the two fixed remote-control computers."""
from __future__ import annotations

import ctypes
import json
import os
import tempfile
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MACHINE_RESTORE_VERSION = 1
KNOWN_MACHINE_PAIRS = {"emperorcomputer": "default", "emperorlaptop": "laptop"}
_ENV_SETTING_KEYS = tuple(
    key
    for name in ("DOMAIN", "HOST", "PORT", "TOKEN", "PAIR_ID", "TUNNEL_MODE", "AUTOSTART", "NATS_PORT")
    for key in (f"REMOTE_CONTROL_{name}", f"CLAUDECODE_REMOTE_CONTROL_{name}")
)


def normalize_pair(value: str) -> str:
    pair = str(value or "default").strip().lower()
    if pair not in {"default", "laptop"}:
        raise ValueError("remote pair must be default or laptop")
    return pair


def resolve_onedrive_sj_root(environ=None) -> Path | None:
    env = os.environ if environ is None else environ
    onedrive = str(env.get("OneDriveConsumer") or env.get("OneDrive") or "").strip()
    if not onedrive:
        return None
    return Path(onedrive) / "OneDrive" / "code" / "data" / "sj"


def machine_restore_path(sj_root: Path, pair: str) -> Path:
    return Path(sj_root) / "mc-private" / "machines" / f"{normalize_pair(pair)}.json"


def machine_credential_path(sj_root: Path, pair: str) -> Path:
    return Path(sj_root) / "mc-private" / "credentials" / f"{normalize_pair(pair)}.bin"


def _dpapi_protect(data: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(ctypes.c_ulong(len(data)), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(ctypes.c_ulong(len(data)), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, indent=2))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_machine_identity(environ) -> tuple[str | None, str]:
    env = os.environ if environ is None else environ
    explicit = next((str(env[key]).strip() for key in (
        "REMOTE_CONTROL_PAIR_ID", "CLAUDECODE_REMOTE_CONTROL_PAIR_ID") if key in env), None)
    computer = str(env.get("COMPUTERNAME") or "").strip()
    computer_pair = KNOWN_MACHINE_PAIRS.get(computer.casefold())
    pair = normalize_pair(explicit if explicit is not None else computer_pair or "default")
    return (computer or None), pair


def _has_explicit_remote_env(environ) -> bool:
    env = os.environ if environ is None else environ
    return any(str(env.get(key) or "").strip() for key in _ENV_SETTING_KEYS)


def read_machine_restore(sj_root: Path | None, pair: str, environ=None) -> tuple[dict | None, str | None]:
    """Return (material, warning). Identity-mismatched or placeholder material is rejected."""
    if sj_root is None:
        return None, None
    path = machine_restore_path(sj_root, pair)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, f"OneDrive 恢复资料 {path} 不是有效 JSON，请修复或删除后重试。"
    if not isinstance(payload, dict):
        return None, f"OneDrive 恢复资料 {path} 不是有效对象，请修复或删除后重试。"
    restore_machine = str(payload.get("machine") or "").strip().casefold()
    if not restore_machine:
        return None, f"OneDrive 恢复资料 {path} 缺少机器标识，属于占位文件，已忽略。"
    if payload.get("version") != MACHINE_RESTORE_VERSION:
        return None, f"OneDrive 恢复资料 {path} 版本不匹配，请重新生成。"
    env = os.environ if environ is None else environ
    computer = str(env.get("COMPUTERNAME") or "").strip().casefold()
    if restore_machine != computer:
        return None, (f"OneDrive 恢复资料 {path} 属于另一台机器，"
                      f"本机不应用其配置；如需在此机器使用请显式选择 REMOTE_CONTROL_PAIR_ID。")
    if normalize_pair(str(payload.get("pair_id") or "")) != normalize_pair(pair):
        return None, f"OneDrive 恢复资料 {path} 的角色与本机身份冲突，已忽略。"
    credential_kind = str(payload.get("credential") or "").strip()
    if "credential" not in payload:
        return None, f"OneDrive 恢复资料 {path} 缺少凭据字段，属于无效资料，已忽略。"
    token = ""
    if credential_kind == "dpapi":
        try:
            protected = machine_credential_path(sj_root, pair).read_bytes()
        except OSError:
            protected = b""
        decrypted = _dpapi_unprotect(protected) if protected else None
        if decrypted is None:
            return None, f"OneDrive 凭据副本 {pair} 无法在本机解密；请从密码管理器恢复后重试。"
        token = decrypted.decode("utf-8", errors="replace")
    elif credential_kind == "unavailable":
        return None, f"OneDrive 恢复资料 {path} 的凭据副本暂不可用；请从密码管理器恢复后重试。"
    elif credential_kind.startswith("env:"):
        env_name = credential_kind.split(":", 1)[1].strip()
        token = str((os.environ if environ is None else environ).get(env_name) or "").strip()
        if not token:
            return None, f"OneDrive 恢复资料引用环境变量 {env_name}，但当前未设置。"
    elif credential_kind and credential_kind != "none":
        return None, f"OneDrive 恢复资料 {path} 使用未知的凭据方式 {credential_kind}。"
    material = {
        "pair_id": normalize_pair(pair),
        "domain": str(payload.get("domain") or "").strip(),
        "token": "".join(token.split()),
    }
    return material, None


def write_machine_restore(sj_root: Path | None, config: dict, environ=None) -> str | None:
    """Publish identity/domain plus a DPAPI-protected credential copy. Returns a warning or None."""
    if sj_root is None:
        return None
    env = os.environ if environ is None else environ
    computer = str(env.get("COMPUTERNAME") or "").strip()
    if not computer:
        return "未识别计算机名，跳过 OneDrive 恢复资料更新。"
    pair = normalize_pair(config.get("pair_id", "default"))
    token = "".join(str(config.get("token") or "").split())
    restore_path = machine_restore_path(sj_root, pair)
    credential_path = machine_credential_path(sj_root, pair)
    try:
        old_payload = json.loads(restore_path.read_text(encoding="utf-8"))
        if not isinstance(old_payload, dict):
            old_payload = None
    except (OSError, ValueError):
        old_payload = None
    try:
        old_blob = credential_path.read_bytes()
    except OSError:
        old_blob = None

    warning = None
    credential = "none"
    blob = old_blob
    if token:
        blob = None
        # An unchanged token keeps its existing copy: DPAPI output is not
        # byte-stable, so re-encrypting would churn OneDrive on every launch.
        if (old_payload is not None
                and str(old_payload.get("credential") or "").strip() == "dpapi"
                and old_blob):
            existing = _dpapi_unprotect(old_blob)
            if (existing is not None
                    and existing.decode("utf-8", errors="replace") == token):
                blob = old_blob
                credential = "dpapi"
        if blob is None:
            protected = _dpapi_protect(token.encode("utf-8"))
            if protected is None:
                # DPAPI failed (non-Windows host or transient failure): still
                # refresh the JSON and carry the old credential reference (or
                # mark it unavailable) so the rest of the material stays fresh.
                previous = str(old_payload.get("credential") or "").strip() if old_payload else ""
                if previous == "dpapi" and old_blob:
                    credential = "dpapi"
                    blob = old_blob
                    warning = "当前环境无法加密凭据副本；恢复资料已刷新，凭据沿用旧副本。"
                elif previous and previous not in ("dpapi", "none"):
                    credential = previous
                    blob = old_blob
                    warning = "当前环境无法加密凭据副本；恢复资料已刷新，凭据沿用旧值。"
                else:
                    credential = "unavailable"
                    blob = old_blob
                    warning = "当前环境无法加密凭据副本；恢复资料已刷新，凭据标记为不可用。"
            else:
                credential = "dpapi"
                blob = protected
    elif old_blob is not None:
        # The token was explicitly cleared; the stale credential copy must go.
        blob = None

    payload = {
        "version": MACHINE_RESTORE_VERSION,
        "machine": computer,
        "pair_id": pair,
        "domain": str(config.get("domain") or "").strip(),
        "credential": credential,
    }
    if old_payload == payload and blob == old_blob:
        # Unchanged material must not trigger an OneDrive sync round-trip.
        return warning
    if credential == "dpapi" and blob is not None and blob != old_blob:
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = credential_path.with_suffix(".tmp")
        temporary.write_bytes(blob)
        temporary.replace(credential_path)
    elif credential == "none" and old_blob is not None:
        try:
            credential_path.unlink(missing_ok=True)
        except OSError:
            warning = ((warning + "；") if warning else "") + \
                f"无法删除旧凭据副本 {credential_path}，请手动检查。"
    _write_json_atomic(restore_path, payload)
    return warning


def load_machine_config_with_restore(path: Path, *, default_domain: str, default_token: str,
                                     environ=None, sj_root: Path | None = None):
    """Priority: valid explicit env vars > existing local machine config >
    identity-matched OneDrive restore material > known-machine identity defaults.
    Returns (config, hints)."""
    env = os.environ if environ is None else environ
    hints: list[str] = []
    if sj_root is None:
        sj_root = resolve_onedrive_sj_root(env)
    if _has_explicit_remote_env(env) or path.is_file():
        config = load_machine_config(path, default_domain=default_domain,
                                     default_token=default_token, environ=env)
        warning = write_machine_restore(sj_root, config, env)
        if warning:
            hints.append(warning)
        return config, hints
    computer, pair = _read_machine_identity(env)
    material, warning = read_machine_restore(sj_root, pair, env)
    if warning:
        hints.append(warning)
    if material is not None:
        _write_json_atomic(path, material)
        config = load_machine_config(path, default_domain=default_domain,
                                     default_token=default_token, environ=env)
        hints.append(f"已从 OneDrive 恢复 {pair} 远程配置。")
        return config, hints
    if computer is None or computer.casefold() not in KNOWN_MACHINE_PAIRS:
        hints.append("未识别本机角色且没有可用恢复资料；远程服务保持等待，"
                     "可显式设置 REMOTE_CONTROL_PAIR_ID 或放入 mc-private 恢复资料。")
    config = load_machine_config(path, default_domain=default_domain,
                                 default_token=default_token, environ=env)
    # Nothing authoritative was found; do not persist identity defaults that
    # would shadow restore material placed later.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        hints.append(f"无法清理临时机器配置 {path}，请手动检查。")
    return config, hints


def load_machine_config(path: Path, *, default_domain: str, default_token: str,
                        environ=None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(persisted, dict):
            persisted = {}
    except (OSError, ValueError):
        persisted = {}
    def setting(name, fallback):
        for key in (f"REMOTE_CONTROL_{name}", f"CLAUDECODE_REMOTE_CONTROL_{name}"):
            if key in env:
                value = str(env[key]).strip()
                # Runtime ignores blank address overrides; TOKEN still allows
                # an explicit empty value to clear the saved credential.
                if name == "DOMAIN" and not value:
                    continue
                return value
        return str(persisted.get(name.lower(), fallback) or "").strip()
    explicit_pair = next((str(env[key]).strip() for key in (
        "REMOTE_CONTROL_PAIR_ID", "CLAUDECODE_REMOTE_CONTROL_PAIR_ID") if key in env), None)
    computer_pair = {"emperorcomputer": "default", "emperorlaptop": "laptop"}.get(
        str(env.get("COMPUTERNAME") or "").strip().casefold())
    pair = normalize_pair(explicit_pair if explicit_pair is not None else
                          computer_pair or persisted.get("pair_id", "default"))
    if str(persisted.get("pair_id", "default")).strip().lower() != pair:
        persisted = {}
    domain = setting("DOMAIN", default_domain if pair == "default" else "")
    if domain:
        uri = urlsplit(domain if "://" in domain else "wss://" + domain)
        scheme = {"https": "wss", "http": "ws"}.get(uri.scheme, uri.scheme)
        if scheme not in {"ws", "wss", "nats"} or not uri.hostname or uri.username or uri.password:
            raise ValueError("invalid remote machine domain")
        # Parsing the authority alone does not validate numeric/ranged ports.
        uri.port
        domain = urlunsplit((scheme, uri.netloc, "" if scheme == "nats" else "/nats", "", ""))
    # The desktop external relay moved independently of the old Cloudflare
    # endpoint. Upgrade only its inherited default; explicit routing still wins.
    explicit_route = any(str(env.get(key) or "").strip() for name in ("DOMAIN", "HOST", "PORT")
                         for key in (f"REMOTE_CONTROL_{name}", f"CLAUDECODE_REMOTE_CONTROL_{name}"))
    tunnel_mode = next((str(env[key]).strip().lower() for key in (
        "REMOTE_CONTROL_TUNNEL_MODE", "CLAUDECODE_REMOTE_CONTROL_TUNNEL_MODE")
        if str(env.get(key) or "").strip()), "cloudflared")
    if (pair == "default" and tunnel_mode == "external" and not explicit_route
            and domain == "wss://rc.tingyou.cc/nats"):
        domain = "wss://mc-desktop-test.tingyou.cc:8443/nats"
    token = "".join(setting("TOKEN", default_token if pair == "default" else "").split())
    config = {"pair_id": pair, "domain": domain, "token": token}
    if config != persisted:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(config, indent=2))
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return config
