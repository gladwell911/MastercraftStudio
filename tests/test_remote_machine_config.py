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


def test_known_laptop_name_replaces_stale_desktop_identity_without_its_credentials(tmp_path):
    path = tmp_path / "machine.json"
    path.write_text('{"pair_id":"default","domain":"wss://desktop.example/nats",'
                    '"token":"desktop-secret"}', encoding="utf-8")
    config = load_machine_config(path, default_domain="wss://desktop.example/nats",
                                 default_token="desktop-secret",
                                 environ={"COMPUTERNAME": " EmperorLaptop "})
    assert config == {"pair_id": "laptop", "domain": "", "token": ""}
    assert 'desktop-secret' not in path.read_text(encoding="utf-8")


def test_known_computer_name_preserves_matching_identity_and_explicit_credentials(tmp_path):
    path = tmp_path / "machine.json"
    saved = load_machine_config(path, default_domain="", default_token="", environ={
        "COMPUTERNAME": " EMPERORLAPTOP ", "REMOTE_CONTROL_DOMAIN": "https://saved.example",
        "REMOTE_CONTROL_TOKEN": "laptop-secret"})
    assert saved == {"pair_id": "laptop", "domain": "wss://saved.example/nats", "token": "laptop-secret"}
    assert load_machine_config(path, default_domain="", default_token="",
                               environ={"COMPUTERNAME": "emperorlaptop"}) == saved
    override = load_machine_config(path, default_domain="", default_token="", environ={
        "COMPUTERNAME": "emperorlaptop", "REMOTE_CONTROL_PAIR_ID": "default",
        "REMOTE_CONTROL_DOMAIN": "https://desktop.example", "REMOTE_CONTROL_TOKEN": "desktop-secret"})
    assert override == {"pair_id": "default", "domain": "wss://desktop.example/nats",
                        "token": "desktop-secret"}


def test_known_desktop_name_and_unknown_name_use_expected_identity(tmp_path):
    path = tmp_path / "machine.json"
    laptop = load_machine_config(path, default_domain="wss://desktop.example/nats",
                                 default_token="desktop-secret", environ={
                                     "REMOTE_CONTROL_PAIR_ID": "laptop", "REMOTE_CONTROL_DOMAIN": "laptop.example",
                                     "REMOTE_CONTROL_TOKEN": "laptop-secret"})
    assert load_machine_config(path, default_domain="wss://desktop.example/nats",
                               default_token="desktop-secret", environ={"COMPUTERNAME": "other-pc"}) == laptop
    desktop = load_machine_config(path, default_domain="wss://desktop.example/nats",
                                  default_token="desktop-secret", environ={"COMPUTERNAME": "emperorComputer"})
    assert desktop == {"pair_id": "default", "domain": "wss://desktop.example/nats",
                       "token": "desktop-secret"}
