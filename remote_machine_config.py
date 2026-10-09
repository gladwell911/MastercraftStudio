"""Machine-local identity for the two fixed remote-control computers."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_pair(value: str) -> str:
    pair = str(value or "default").strip().lower()
    if pair not in {"default", "laptop"}:
        raise ValueError("remote pair must be default or laptop")
    return pair


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
                return str(env[key]).strip()
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
