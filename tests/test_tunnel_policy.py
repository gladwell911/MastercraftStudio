import pytest
from tunnel_policy import connector_argv, TunnelRestartBudget
from tunnel_policy import ConnectorLease


@pytest.mark.parametrize("protocol", ["auto", "quic", "http2"])
def test_validated_protocol_and_origin_are_single_argv_options(protocol):
    argv = connector_argv('cloudflared.exe tunnel --protocol=quic run --token "private token" --url http://localhost:4200',
                          origin_port=18080, protocol=protocol)
    assert argv.count("--protocol") == 1
    assert argv[argv.index("--protocol") + 1] == protocol
    assert argv[argv.index("--token") + 1] == "private token"
    assert argv[-2:] == ["--url", "http://127.0.0.1:18080"]


@pytest.mark.parametrize("command,protocol,port", [
    ("cmd.exe /c cloudflared tunnel run", "auto", 18080),
    ("cloudflared tunnel run", "http2 --token evil", 18080),
    ("cloudflared tunnel run", "auto", 0),
    ("cloudflared tunnel --url", "auto", 18080),
])
def test_invalid_argv_is_rejected(command, protocol, port):
    with pytest.raises(ValueError):
        connector_argv(command, origin_port=port, protocol=protocol)


def test_restart_requires_sustained_failure_healthy_origin_network_and_budget():
    budget = TunnelRestartBudget()
    def attempt(now, **kwargs):
        return budget.allow(now, origin_healthy=kwargs.get("origin", True),
                            network_available=kwargs.get("network", True), channels_healthy=kwargs.get("channels", False))
    assert not attempt(0)
    assert not attempt(59)
    assert attempt(60)
    assert attempt(120)
    assert not attempt(180)
    assert not attempt(601, network=False)
    assert not attempt(602, origin=False)
    assert not attempt(603)
    assert attempt(663)
    assert not attempt(664, channels=True)
    assert not attempt(665)


def test_connector_lease_is_scoped_to_pair_tunnel_and_released(tmp_path):
    first = ConnectorLease(tmp_path, "laptop", "cloudflared tunnel --protocol quic run fixture --url http://127.0.0.1:18080")
    same = ConnectorLease(tmp_path, "laptop", "cloudflared tunnel --protocol http2 run fixture --url http://127.0.0.1:19080")
    other = ConnectorLease(tmp_path, "other", "cloudflared tunnel run fixture")
    assert first.path == same.path and first.path != other.path
    first.acquire()
    try:
        with pytest.raises(OSError):
            same.acquire()
        other.acquire()
        other.close()
    finally:
        first.close()
    same.acquire()
    same.close()


def test_token_file_formats_and_rotation_share_canonical_tunnel_lease(tmp_path):
    import base64
    import json
    import subprocess
    tunnel_id = "3ac4c76d-ded4-438b-92a8-8d5c2aff037c"
    def token(secret, identity=tunnel_id):
        return base64.urlsafe_b64encode(json.dumps({"a": "account", "t": identity, "s": secret}).encode()).decode()
    credential = tmp_path / "folder with spaces" / "token.txt"
    credential.parent.mkdir()
    credential.write_text(token("original") + "\n", encoding="utf-8")
    def lease(arguments):
        return ConnectorLease(tmp_path, "laptop", subprocess.list2cmdline(["cloudflared.exe", "tunnel", "run", *arguments]))
    first = lease(["--token-file", credential.as_posix()])
    assert first.path == lease(["--token-file=" + str(credential)]).path
    assert first.path == lease(["--token", token("original")]).path
    credential.write_text(token("rotated"), encoding="utf-8")
    assert first.path == lease(["--token-file", str(credential)]).path
    assert first.path == lease([tunnel_id.upper()]).path
    copied = tmp_path / "copy.txt"
    copied.write_text(token("rotated"), encoding="utf-8")
    assert first.path == lease(["--token-file=" + str(copied)]).path
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"TunnelID": tunnel_id}), encoding="utf-8")
    assert first.path == lease(["--credentials-file=" + str(credentials)]).path
    assert first.path != lease(["--token", token("other", "a775d492-df28-4c22-bcea-000000000001")]).path
    first.acquire()
    try:
        with pytest.raises(OSError):
            lease(["--token-file", str(copied)]).acquire()
    finally:
        first.close()


def test_unreadable_token_file_fails_without_credential_disclosure(tmp_path):
    with pytest.raises(ValueError, match="^TUNNEL_TOKEN_FILE_UNREADABLE$"):
        ConnectorLease(tmp_path, "laptop", f'cloudflared tunnel run --token-file "{tmp_path / "private-name"}"')
