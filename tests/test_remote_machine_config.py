import pytest
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from remote_machine_config import load_machine_config


@pytest.mark.parametrize('pair,domain,overrides,expected', [
    ('default', 'wss://rc.tingyou.cc/nats', {}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'https://rc.tingyou.cc/old', {}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://custom.example/nats', {}, 'wss://custom.example/nats'),
    ('laptop', 'wss://rc.tingyou.cc/nats', {}, 'wss://rc.tingyou.cc/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_TUNNEL_MODE': 'cloudflared'}, 'wss://rc.tingyou.cc/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': 'wss://rc.tingyou.cc/nats'}, 'wss://rc.tingyou.cc/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'CLAUDECODE_REMOTE_CONTROL_DOMAIN': 'wss://rc.tingyou.cc/nats'}, 'wss://rc.tingyou.cc/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_PORT': '18080'}, 'wss://rc.tingyou.cc/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_HOST': ''}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'CLAUDECODE_REMOTE_CONTROL_HOST': '  '}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_PORT': '  '}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'CLAUDECODE_REMOTE_CONTROL_PORT': ''}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': ''}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': '  '}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'CLAUDECODE_REMOTE_CONTROL_DOMAIN': ''}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'CLAUDECODE_REMOTE_CONTROL_DOMAIN': '  '}, 'wss://mc-desktop-test.tingyou.cc:8443/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': ' ', 'CLAUDECODE_REMOTE_CONTROL_DOMAIN': 'https://alias.example'}, 'wss://alias.example/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': 'https://primary.example', 'CLAUDECODE_REMOTE_CONTROL_DOMAIN': 'https://alias.example'}, 'wss://primary.example/nats'),
    ('default', 'wss://rc.tingyou.cc/nats', {'REMOTE_CONTROL_DOMAIN': '', 'CLAUDECODE_REMOTE_CONTROL_DOMAIN': 'wss://rc.tingyou.cc/nats'}, 'wss://rc.tingyou.cc/nats'),
])
def test_external_desktop_legacy_route_migration(tmp_path, pair, domain, overrides, expected):
    path = tmp_path / 'machine.json'
    path.write_text(json.dumps(dict(pair_id=pair, domain=domain, token='original-secret')), encoding='utf-8')
    env = {'REMOTE_CONTROL_TUNNEL_MODE': 'external', **overrides}
    loaded = load_machine_config(path, default_domain='', default_token='', environ=env)
    assert loaded == dict(pair_id=pair, domain=expected, token='original-secret')
    assert json.loads(path.read_text(encoding='utf-8')) == loaded
    before = path.stat().st_mtime_ns
    assert load_machine_config(path, default_domain='', default_token='', environ=env) == loaded
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize('token_key', ['REMOTE_CONTROL_TOKEN', 'CLAUDECODE_REMOTE_CONTROL_TOKEN'])
def test_external_desktop_blank_domain_preserves_explicit_token_clear(tmp_path, token_key):
    path = tmp_path / 'machine.json'
    path.write_text(json.dumps(dict(pair_id='default', domain='wss://rc.tingyou.cc/nats', token='old-secret')), encoding='utf-8')
    loaded = load_machine_config(path, default_domain='', default_token='', environ={
        'REMOTE_CONTROL_TUNNEL_MODE': 'external', 'REMOTE_CONTROL_DOMAIN': ' ', token_key: ''})
    assert loaded == dict(pair_id='default', domain='wss://mc-desktop-test.tingyou.cc:8443/nats', token='')


def test_external_desktop_without_machine_file_migrates_legacy_app_state(tmp_path):
    loaded = load_machine_config(tmp_path / 'machine.json',
        default_domain='wss://rc.tingyou.cc/nats', default_token='app-state-secret',
        environ={'CLAUDECODE_REMOTE_CONTROL_TUNNEL_MODE': 'external'})
    assert loaded == dict(pair_id='default', domain='wss://mc-desktop-test.tingyou.cc:8443/nats', token='app-state-secret')


@pytest.mark.parametrize("domain", [
    "ftp://host.example", "wss://user:secret@host.example", "wss://host.example:not-a-port",
    "wss://host.example:65536", "wss://host.example:-1", "nats://host.example:invalid",
])
def test_two_day_review_invalid_domain_preserves_saved_bytes(tmp_path, domain):
    path = tmp_path / "machine.json"
    path.write_text('{"pair_id":"default","domain":"wss://saved.example/nats","token":"saved"}', encoding="utf-8")
    prior = path.read_bytes()
    with pytest.raises(ValueError):
        load_machine_config(path, default_domain="", default_token="", environ={"REMOTE_CONTROL_DOMAIN": domain})
    assert path.read_bytes() == prior
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("domain, normalized", [
    ("https://host.example:443/old", "wss://host.example:443/nats"),
    ("ws://host.example:0", "ws://host.example:0/nats"),
    ("nats://host.example:65535/old", "nats://host.example:65535"),
])
def test_two_day_review_valid_ports_keep_normalization(tmp_path, domain, normalized):
    path = tmp_path / "machine.json"
    config = load_machine_config(path, default_domain="", default_token="", environ={"REMOTE_CONTROL_DOMAIN": domain})
    assert config["domain"] == normalized
    assert json.loads(path.read_text(encoding="utf-8")) == config


def test_two_day_review_overlapping_starts_use_independent_atomic_files(tmp_path, monkeypatch):
    path = tmp_path / "machine.json"
    barrier = threading.Barrier(2)
    replace = Path.replace
    temporary_paths = []
    def overlapping_replace(source, target):
        temporary_paths.append(source)
        # Both writes have completed before either atomically publishes.
        barrier.wait(timeout=5)
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", overlapping_replace)
    def initialize(host):
        return load_machine_config(path, default_domain="", default_token="", environ={
            "REMOTE_CONTROL_DOMAIN": f"wss://{host}.example", "REMOTE_CONTROL_TOKEN": host})
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(initialize, host) for host in ("first", "second")]
        results = [future.result(timeout=10) for future in futures]
    assert len(set(temporary_paths)) == 2
    assert json.loads(path.read_text(encoding="utf-8")) in results
    assert list(tmp_path.iterdir()) == [path]


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
