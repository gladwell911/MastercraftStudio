import json
import sys
from pathlib import Path

import pytest

from remote_machine_config import (
    load_machine_config_with_restore,
    machine_credential_path,
    machine_restore_path,
    read_machine_restore,
    write_machine_restore,
)


DEFAULTS = dict(default_domain="wss://desktop.example/nats", default_token="desktop-secret")


def _write_restore(sj_root: Path, pair: str, *, machine: str = "emperorComputer",
                   domain: str = "wss://restore.example/nats", credential: str = "none") -> Path:
    path = machine_restore_path(sj_root, pair)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "machine": machine,
        "pair_id": pair,
        "domain": domain,
        "credential": credential,
    }), encoding="utf-8")
    return path


def test_empty_app_dir_recovers_machine_config_from_onedrive(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default")
    machine_path = tmp_path / "app" / "remote_machine.json"
    env = {"COMPUTERNAME": "emperorComputer"}

    config, hints = load_machine_config_with_restore(machine_path, environ=env, sj_root=sj_root, **DEFAULTS)

    assert config == {"pair_id": "default", "domain": "wss://restore.example/nats", "token": ""}
    assert any("已从 OneDrive 恢复" in hint for hint in hints)
    assert json.loads(machine_path.read_text(encoding="utf-8"))["domain"] == "wss://restore.example/nats"


def test_existing_local_config_wins_and_refreshes_restore_material(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default", domain="wss://stale.example/nats")
    machine_path = tmp_path / "app" / "remote_machine.json"
    machine_path.parent.mkdir(parents=True)
    machine_path.write_text(json.dumps({
        "pair_id": "default", "domain": "wss://local.example/nats", "token": "local-secret",
    }), encoding="utf-8")

    config, _ = load_machine_config_with_restore(machine_path, environ={"COMPUTERNAME": "emperorComputer"},
                                                 sj_root=sj_root, **DEFAULTS)

    assert config["domain"] == "wss://local.example/nats"
    restored = json.loads(machine_restore_path(sj_root, "default").read_text(encoding="utf-8"))
    assert restored["domain"] == "wss://local.example/nats"
    assert restored["machine"] == "emperorComputer"


def test_explicit_env_wins_over_local_config_and_restore(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default")
    machine_path = tmp_path / "app" / "remote_machine.json"
    env = {
        "COMPUTERNAME": "emperorComputer",
        "REMOTE_CONTROL_DOMAIN": "https://env.example",
        "REMOTE_CONTROL_TOKEN": "env-secret",
    }

    config, _ = load_machine_config_with_restore(machine_path, environ=env, sj_root=sj_root, **DEFAULTS)

    assert config == {"pair_id": "default", "domain": "wss://env.example/nats", "token": "env-secret"}
    assert json.loads(machine_path.read_text(encoding="utf-8")) == config


def test_restore_material_from_another_machine_is_rejected(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default", machine="otherComputer")
    machine_path = tmp_path / "app" / "remote_machine.json"

    config, hints = load_machine_config_with_restore(machine_path, environ={"COMPUTERNAME": "emperorComputer"},
                                                     sj_root=sj_root, **DEFAULTS)

    assert not machine_path.exists()
    assert any("另一台机器" in hint for hint in hints)
    assert config["pair_id"] == "default"


def test_placeholder_restore_file_is_ignored_with_hint(tmp_path):
    sj_root = tmp_path / "sj"
    path = machine_restore_path(sj_root, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    machine_path = tmp_path / "app" / "remote_machine.json"

    config, hints = load_machine_config_with_restore(machine_path, environ={"COMPUTERNAME": "emperorComputer"},
                                                     sj_root=sj_root, **DEFAULTS)

    assert not machine_path.exists()
    assert any("占位" in hint for hint in hints)
    assert config["domain"] == DEFAULTS["default_domain"]


def test_unknown_machine_without_restore_waits_with_actionable_hint(tmp_path):
    machine_path = tmp_path / "app" / "remote_machine.json"
    config, hints = load_machine_config_with_restore(machine_path, environ={"COMPUTERNAME": "mystery-pc"},
                                                     sj_root=tmp_path / "sj", **DEFAULTS)
    assert any("未识别本机角色" in hint for hint in hints)
    assert config["pair_id"] == "default"


def test_laptop_restore_never_inherits_desktop_material(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default")
    machine_path = tmp_path / "app" / "remote_machine.json"

    config, hints = load_machine_config_with_restore(machine_path, environ={"COMPUTERNAME": "emperorLaptop"},
                                                     sj_root=sj_root, **DEFAULTS)

    assert not machine_path.exists()
    assert config == {"pair_id": "laptop", "domain": "", "token": ""}


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI credential copy is Windows-only")
def test_dpapi_credential_round_trips_without_plaintext(tmp_path):
    sj_root = tmp_path / "sj"
    config = {"pair_id": "default", "domain": "wss://desktop.example/nats", "token": "restore-secret-123"}
    warning = write_machine_restore(sj_root, config, environ={"COMPUTERNAME": "emperorComputer"})
    assert warning is None

    blob = machine_credential_path(sj_root, "default").read_bytes()
    assert b"restore-secret-123" not in blob
    payload = json.loads(machine_restore_path(sj_root, "default").read_text(encoding="utf-8"))
    assert payload["credential"] == "dpapi"
    assert "restore-secret-123" not in json.dumps(payload)

    material, warning = read_machine_restore(sj_root, "default", environ={"COMPUTERNAME": "emperorComputer"})
    assert warning is None
    assert material["token"] == "restore-secret-123"


def test_env_reference_credential_reads_named_variable(tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "laptop", machine="emperorLaptop", credential="env:SJ_RESTORE_TOKEN")
    env = {"COMPUTERNAME": "emperorLaptop", "SJ_RESTORE_TOKEN": " referenced-secret "}

    material, warning = read_machine_restore(sj_root, "laptop", environ=env)

    assert warning is None
    assert material["token"] == "referenced-secret"

    missing, warning = read_machine_restore(sj_root, "laptop", environ={"COMPUTERNAME": "emperorLaptop"})
    assert missing is None
    assert "SJ_RESTORE_TOKEN" in warning


def test_broken_restore_json_reports_actionable_warning(tmp_path):
    sj_root = tmp_path / "sj"
    path = machine_restore_path(sj_root, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    material, warning = read_machine_restore(sj_root, "default", environ={"COMPUTERNAME": "emperorComputer"})

    assert material is None
    assert "不是有效 JSON" in warning


def test_write_restore_without_computer_name_skips(tmp_path):
    warning = write_machine_restore(tmp_path / "sj", {"pair_id": "default", "domain": "", "token": "x"},
                                    environ={})
    assert "未识别计算机名" in warning


def test_missing_credential_key_is_rejected_with_warning(tmp_path):
    sj_root = tmp_path / "sj"
    path = machine_restore_path(sj_root, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "machine": "emperorComputer",
        "pair_id": "default",
        "domain": "wss://restore.example/nats",
    }), encoding="utf-8")

    material, warning = read_machine_restore(sj_root, "default", environ={"COMPUTERNAME": "emperorComputer"})

    assert material is None
    assert "凭据" in warning


def test_cleared_token_removes_stale_credential_blob(tmp_path):
    sj_root = tmp_path / "sj"
    credential_path = machine_credential_path(sj_root, "default")
    credential_path.parent.mkdir(parents=True, exist_ok=True)
    credential_path.write_bytes(b"stale-blob")

    warning = write_machine_restore(
        sj_root,
        {"pair_id": "default", "domain": "wss://desktop.example/nats", "token": ""},
        environ={"COMPUTERNAME": "emperorComputer"},
    )

    assert warning is None
    assert not credential_path.exists()
    payload = json.loads(machine_restore_path(sj_root, "default").read_text(encoding="utf-8"))
    assert payload["credential"] == "none"


def test_dpapi_failure_still_refreshes_restore_json(monkeypatch, tmp_path):
    sj_root = tmp_path / "sj"
    _write_restore(sj_root, "default", domain="wss://old.example/nats")
    monkeypatch.setattr("remote_machine_config._dpapi_protect", lambda _data: None)

    warning = write_machine_restore(
        sj_root,
        {"pair_id": "default", "domain": "wss://new.example/nats", "token": "secret"},
        environ={"COMPUTERNAME": "emperorComputer"},
    )

    assert warning and "无法加密" in warning
    payload = json.loads(machine_restore_path(sj_root, "default").read_text(encoding="utf-8"))
    assert payload["domain"] == "wss://new.example/nats"
    assert payload["credential"] == "unavailable"


def test_unchanged_restore_material_skips_rewrite(tmp_path):
    sj_root = tmp_path / "sj"
    config = {"pair_id": "default", "domain": "wss://desktop.example/nats", "token": ""}
    assert write_machine_restore(sj_root, config, environ={"COMPUTERNAME": "emperorComputer"}) is None
    path = machine_restore_path(sj_root, "default")
    before = path.read_bytes()

    assert write_machine_restore(sj_root, config, environ={"COMPUTERNAME": "emperorComputer"}) is None
    assert path.read_bytes() == before
