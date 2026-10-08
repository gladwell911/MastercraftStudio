import pytest
from remote_machine_config import load_machine_config


def test_fixed_profiles_persist_and_environment_wins(tmp_path):
    path = tmp_path / "machine.json"
    default = dict(default_domain="wss://desktop.example/nats", default_token="desktop-secret")
    desktop = load_machine_config(path, **default, environ={})
    assert desktop["pair_id"] == "default"
    laptop = load_machine_config(path, **default, environ={
        "REMOTE_CONTROL_PAIR_ID": " LAPTOP ", "REMOTE_CONTROL_DOMAIN": "https://laptop.example/ws",
        "REMOTE_CONTROL_TOKEN": " laptop secret "})
    assert laptop == {"pair_id": "laptop", "domain": "wss://laptop.example/nats", "token": "laptopsecret"}
    assert load_machine_config(path, **default, environ={}) == laptop


def test_unconfigured_laptop_never_inherits_desktop(tmp_path):
    config = load_machine_config(tmp_path / "machine.json", default_domain="wss://desktop.example/nats",
                                 default_token="desktop-secret", environ={"REMOTE_CONTROL_PAIR_ID": "laptop"})
    assert config == {"pair_id": "laptop", "domain": "", "token": ""}
    path = tmp_path / "copied-machine.json"
    load_machine_config(path, default_domain="wss://desktop.example/nats", default_token="desktop-secret", environ={})
    copied = load_machine_config(path, default_domain="wss://desktop.example/nats", default_token="desktop-secret", environ={"REMOTE_CONTROL_PAIR_ID": "laptop"})
    assert copied == config


def test_invalid_identity_cannot_replace_durable_config(tmp_path):
    path = tmp_path / "machine.json"
    path.write_text('{"pair_id":"default","domain":"","token":"saved"}')
    prior = path.read_bytes()
    with pytest.raises(ValueError):
        load_machine_config(path, default_domain="", default_token="", environ={"REMOTE_CONTROL_PAIR_ID": "other"})
    assert path.read_bytes() == prior
