from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest


def test_pyinstaller_specs_exclude_zdsr_dlls_from_upx():
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("ZhugeQA_A11y.spec", "zgwd.spec"):
        text = (root / spec_name).read_text(encoding="utf-8")

        assert "'ZDSRAPI.dll'" in text
        assert "'ZDSRAPI_x64.dll'" in text
        assert "upx_exclude=['ZDSRAPI.dll', 'ZDSRAPI_x64.dll']" in text


def test_pyinstaller_specs_do_not_import_unused_pydub():
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("ZhugeQA_A11y.spec", "zgwd.spec"):
        text = (root / spec_name).read_text(encoding="utf-8")

        assert "'pydub'" not in text
        assert '"pydub"' not in text

    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "pydub" not in requirements.lower()


def test_default_pyinstaller_spec_does_not_bundle_runtime_history():
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "zgwd.spec").read_text(encoding="utf-8")

    assert "dist/history" not in spec_text
    assert "('dist/history', 'history')" not in spec_text


def test_default_pyinstaller_spec_bundles_chat_title_rules():
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("ZhugeQA_A11y.spec", "zgwd.spec"):
        spec_text = (root / spec_name).read_text(encoding="utf-8")

        assert "('assets/chat_title_rules.json', 'assets')" in spec_text


def test_default_pyinstaller_spec_builds_separate_console_worker_executable():
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "zgwd.spec").read_text(encoding="utf-8")
    launcher_text = (root / "worker_launcher.py").read_text(encoding="utf-8")

    assert "['worker_launcher.py']" in spec_text
    assert "name='mc_worker'" in spec_text
    assert "console=True" in spec_text
    assert "worker_exe," in spec_text
    assert "from codex_worker_process import main" in launcher_text


def test_package_script_builds_and_validates_before_updating_and_launching_final_package():
    root = Path(__file__).resolve().parents[1]
    script_text = (root / "package_mc.ps1").read_text(encoding="utf-8")

    assert script_text.index('Assert-PackageArtifacts -PackagePath $builtPackage') < script_text.index('Update-PackageOutput -BuiltPackage $builtPackage')
    assert script_text.index('Assert-PackageArtifacts -PackagePath $finalPackage') < script_text.index('Start-Process -FilePath')
    assert "Get-Process" in script_text
    assert "mc.exe is still running" in script_text
    assert "Get-ChildItem -LiteralPath $item.FullName -Force" in script_text
    assert "-Recurse" not in script_text
    assert script_text.count("Start-Process -FilePath") == 1
    assert "-WorkingDirectory $finalPackage" in script_text
    assert "-Wait" not in script_text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell packaging on Windows")
@pytest.mark.parametrize("mode", [
    "success", "build_only", "build_fail", "missing_worker", "empty_worker", "missing_skill", "empty_skill",
    "missing_kimi", "bad_kimi_hash", "bad_kimi_version", "linked_kimi",
    "update_fail", "final_invalid", "final_kimi_corrupt", "start_fail",
])
def test_package_script_isolated_fake_build_and_launch_matrix(tmp_path, mode):
    import codex_client

    root = Path(__file__).resolve().parents[1]
    dist = tmp_path / "dist"
    package = dist / "mc"
    internal = package / "_internal"
    home = internal / ".codex-home"
    home.mkdir(parents=True)
    (home / "rollout.db").write_bytes(b"keep-session")
    bundled_history = internal / "history"
    bundled_history.mkdir()
    (bundled_history / "keep.db").write_bytes(b"keep-old-history")
    (package / "mc.exe").write_bytes(b"old-gui")
    (package / "mc_worker.exe").write_bytes(b"old-worker")
    (package / "user-data.txt").write_bytes(b"keep-local")
    kimi_relative = "_internal/runtimes/kimi/2.5.3/kimi.exe"
    kimi_target = package / kimi_relative
    kimi_target.parent.mkdir(parents=True)
    kimi_target.write_bytes(b"old-kimi")
    history = dist / "history"
    history.mkdir()
    from chat_store import ChatStore
    preserved_store = ChatStore(history / "chat_history.db")
    preserved_store.initialize()
    preserved_store.upsert_chat({"id": "preserved", "title": "User history"})
    feed_before = preserved_store.get_meta("notification_feed_id")
    database_before = (history / "chat_history.db").read_bytes()
    (history / "remote_machine.json").write_bytes(b'identity-and-token')
    nats_data = history / "nats" / "jetstream" / "stream.blk"
    nats_data.parent.mkdir(parents=True)
    nats_data.write_bytes(b'broker-data')
    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    (onedrive / "notes.db").write_bytes(b"outside-notes")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "SKILL.md"
    sentinel.write_bytes(b"\xef\xbb\xbfglobal-unchanged")
    sentinel.chmod(0o444)
    attributes = sentinel.stat().st_file_attributes
    cache = internal / "old-cache"
    cache.mkdir()
    codex_client._create_directory_link(outside, cache / "remote")
    codex_client._create_directory_link(outside, home / "skills")

    # The production script is copied into the fixture; only the environmental
    # administrator check is stubbed. All operations receive this fixture's paths.
    script = tmp_path / "package_mc.ps1"
    text = (root / "package_mc.ps1").read_text(encoding="utf-8")
    guard = 'return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'
    assert text.count(guard) == 1
    script.write_text(text.replace(guard, 'return $false'), encoding="utf-8-sig")
    # Use the real validator CLI with a synthetic pinned file and a controlled
    # version probe so its hash, path and exit-code checks all run.
    kimi_bytes = b"fixture-pinned-kimi"
    runtime_module = (root / "kimi_runtime.py").read_text(encoding="utf-8")
    test_hash = hashlib.sha256(kimi_bytes).hexdigest()
    from kimi_runtime import SUPPORTED_SHA256
    assert runtime_module.count(SUPPORTED_SHA256) == 1
    assert runtime_module.count("result = subprocess.run(") == 1
    runtime_module = runtime_module.replace(SUPPORTED_SHA256, test_hash)
    runtime_module = runtime_module.replace("result = subprocess.run(", "result = fixture_version_probe(")
    version = b"9.9.9" if mode == "bad_kimi_version" else b"2.5.3"
    version_fixture = (
        "def fixture_version_probe(argv, **kwargs):\n"
        "    assert argv[1:] == ['--version']\n"
        "    assert kwargs['env']['KIMI_CODE_NO_AUTO_UPDATE'] == '1'\n"
        "    with Path(__file__).with_name('probes.txt').open('a', encoding='utf-8') as probes:\n"
        "        probes.write(argv[0] + '\\n')\n"
        f"    return subprocess.CompletedProcess(argv, 0, {version!r})\n\n\n"
    )
    runtime_module = runtime_module.replace('if __name__ == "__main__":', version_fixture + 'if __name__ == "__main__":')
    (tmp_path / "kimi_runtime.py").write_text(runtime_module, encoding="utf-8")
    shutil.copyfile(root / "browser_open.py", tmp_path / "browser_open.py")
    external_kimi = outside / "kimi.exe"
    external_kimi.write_bytes(kimi_bytes)
    nats_source = tmp_path / "tools" / "nats-server" / "nats-server.exe"
    nats_source.parent.mkdir(parents=True)
    nats_source.write_bytes(b"fake-nats-source")
    spec = tmp_path / "fake.spec"
    spec.write_text("fake only", encoding="utf-8")
    excluded_dirs = [".dev", "tests", "test", "screenshots", "screenshot", "reverse-engineering",
        "reverse_engineering", "apktool", "jadx", "android-reverse", "jetstream", ".git", ".pytest_cache",
        "_bmad-output", ".codex-home", ".codex-home-other", "history"]
    excluded_files = ["new.apk", "new.aab", "app_state.json", "remote_machine.json", "output.log",
        "notes.db", "cache.sqlite", "cache.sqlite3", "chat_history.db", "chat_history.db-wal", "chat_history.db-shm"]
    artifact_paths = [base + name + "/unwanted-build-artifact" for base in ("", "_internal/nested/") for name in excluded_dirs]
    artifact_paths += [base + name for base in ("", "_internal/nested/") for name in excluded_files]
    runtime_paths = ["ZDSRAPI.dll", "ZDSRAPI_x64.dll", "_internal/python311.dll", "_internal/sound/done.wav"]
    builder = tmp_path / "fake-builder.py"
    builder.write_text(
        "import pathlib, subprocess, sys\n"
        "if pathlib.Path(sys.argv[1]).name == 'kimi_runtime.py':\n"
        "    sys.exit(subprocess.call([sys.executable, *sys.argv[1:]]))\n"
        "assert sys.argv[1:3] == ['-m', 'PyInstaller']\n"
        f"mode = {mode!r}\n"
        f"fixture = pathlib.Path({str(tmp_path)!r}).resolve()\n"
        "dist = pathlib.Path(sys.argv[sys.argv.index('--distpath') + 1]).resolve()\n"
        "assert fixture in dist.parents and dist.name.startswith('.mc-build-')\n"
        "if mode == 'build_fail': sys.exit(7)\n"
        "package = dist / 'mc'\n"
        "(package / '_internal' / 'assets').mkdir(parents=True)\n"
        "nats = package / '_internal' / 'nats-server' / 'nats-server.exe'\n"
        "nats.parent.mkdir()\n"
        "nats.write_bytes(b'fake-nats-package')\n"
        "(package / 'mc.exe').write_bytes(b'new-gui')\n"
        "if mode != 'missing_worker': (package / 'mc_worker.exe').write_bytes(b'' if mode == 'empty_worker' else b'new-worker')\n"
        "(package / '_internal' / 'assets' / 'chat_title_rules.json').write_text('{}')\n"
        "skill = package / '_internal' / 'assets' / 'codex-skills' / 'mc-browser-open' / 'SKILL.md'\n"
        "skill.parent.mkdir(parents=True)\n"
        "if mode != 'missing_skill': skill.write_bytes(b'' if mode == 'empty_skill' else b'skill')\n"
        f"kimi = package / {kimi_relative!r}\n"
        "if mode == 'linked_kimi':\n"
        "    kimi.parent.parent.mkdir(parents=True)\n"
        "    subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(kimi.parent), str(fixture / 'outside')], check=True)\n"
        "elif mode != 'missing_kimi':\n"
        "    kimi.parent.mkdir(parents=True)\n"
        f"    kimi.write_bytes(b'wrong-hash' if mode == 'bad_kimi_hash' else {kimi_bytes!r})\n"
        f"for relative in {artifact_paths + runtime_paths!r}:\n"
        "    artifact = package / relative\n"
        "    artifact.parent.mkdir(parents=True, exist_ok=True)\n"
        "    artifact.write_bytes(b'fixture-artifact')\n"
        "for name in ['history', '.codex-home']:\n"
        "    folder = package / '_internal' / name\n"
        "    folder.mkdir()\n"
        "    (folder / 'unwanted-build-state').write_bytes(b'do-not-import')\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text(f'@"{sys.executable}" "{builder}" %*\r\n', encoding="utf-8")
    launches = tmp_path / "launches.jsonl"
    wrapper = tmp_path / "run.ps1"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    wrapper.write_text(
        "function Get-Process { param($Name, $ErrorAction) }\n"
        "function Start-Process { param($FilePath, $WorkingDirectory, $ErrorAction)\n"
        + ("throw 'fake launch failure'\n" if mode == "start_fail" else
           f"@{{file=$FilePath; cwd=$WorkingDirectory}} | ConvertTo-Json -Compress | Add-Content -LiteralPath {quote(launches)}\n")
        + "}\n"
        + ("function Copy-Item { param($LiteralPath, $Destination, [switch]$Force)\n"
           "if ($Destination.EndsWith('mc.exe')) { throw 'fake update failure' }\n"
           "Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters\n}\n" if mode == "update_fail" else "")
        + ("function Copy-Item { param($LiteralPath, $Destination, [switch]$Force)\n"
           "Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters\n"
           "if ($Destination.EndsWith('mc_worker.exe')) { [IO.File]::WriteAllBytes($Destination, [byte[]]@()) }\n}\n"
           if mode == "final_invalid" else "")
        + ("function Copy-Item { param($LiteralPath, $Destination, [switch]$Force)\n"
           "Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters\n"
           "if ($Destination.EndsWith('kimi.exe')) { [IO.File]::WriteAllText($Destination, 'corrupted-after-copy') }\n}\n"
           if mode == "final_kimi_corrupt" else "")
        + f"& {quote(script)} -DistPath {quote(dist)} -WorkPath {quote(tmp_path / 'work')} -SpecPath {quote(spec)} -PythonExe {quote(fake_python)}{' -BuildOnly' if mode == 'build_only' else ''}\n"
        + "exit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "KIMI_BIN": str(external_kimi), "KIMI_CODE_NO_AUTO_UPDATE": "0"},
    )
    assert result.returncode == (0 if mode in {"success", "build_only"} else 1), result.stdout + result.stderr
    launch_rows = [json.loads(line) for line in launches.read_text(encoding="utf-8-sig").splitlines()] if launches.exists() else []
    assert launch_rows == ([{"file": str(package / "mc.exe"), "cwd": str(package)}] if mode == "success" else [])
    assert (home / "rollout.db").read_bytes() == b"keep-session"
    assert (home / "skills" / "SKILL.md").read_bytes() == b"\xef\xbb\xbfglobal-unchanged"
    assert (bundled_history / "keep.db").read_bytes() == b"keep-old-history"
    assert (history / "chat_history.db").read_bytes() == database_before
    assert ChatStore(history / "chat_history.db").get_meta("notification_feed_id") == feed_before
    assert ChatStore(history / "chat_history.db").load_chat("preserved")["title"] == "User history"
    assert (history / "remote_machine.json").read_bytes() == b'identity-and-token'
    assert nats_data.read_bytes() == b'broker-data'
    assert (onedrive / "notes.db").read_bytes() == b"outside-notes"
    assert (package / "user-data.txt").read_bytes() == b"keep-local"
    assert sentinel.read_bytes() == b"\xef\xbb\xbfglobal-unchanged"
    assert sentinel.stat().st_file_attributes == attributes
    assert not list(dist.glob(".mc-build-*"))
    assert not (home / "unwanted-build-state").exists()
    if mode in {"build_fail", "missing_worker", "empty_worker", "missing_skill", "empty_skill",
                "missing_kimi", "bad_kimi_hash", "bad_kimi_version", "linked_kimi"}:
        assert (package / "mc.exe").read_bytes() == b"old-gui"
        assert (package / "mc_worker.exe").read_bytes() == b"old-worker"
        assert kimi_target.read_bytes() == b"old-kimi"
        assert cache.exists()
    probes_path = tmp_path / "probes.txt"
    probes = probes_path.read_text(encoding="utf-8").splitlines() if probes_path.exists() else []
    if mode == "missing_kimi":
        assert "Kimi runtime missing" in result.stderr
        assert not probes
    if mode == "bad_kimi_hash":
        assert "SHA256 mismatch" in result.stderr
        assert not probes
    if mode == "bad_kimi_version":
        assert "version validation failed" in result.stderr and "9.9.9" in result.stderr
        assert len(probes) == 1 and ".mc-build-" in probes[0]
    if mode == "linked_kimi":
        assert "Kimi packaged runtime path contains a link" in result.stderr
        assert external_kimi.read_bytes() == kimi_bytes
        assert not probes
    if mode == "final_kimi_corrupt":
        assert "SHA256 mismatch" in result.stderr
        assert str(kimi_target) in result.stderr
        assert len(probes) == 1 and ".mc-build-" in probes[0]
        assert kimi_target.read_bytes() == b"corrupted-after-copy"
    if mode in {"success", "build_only"}:
        assert (package / "mc.exe").read_bytes() == b"new-gui"
        assert (package / "mc_worker.exe").read_bytes() == b"new-worker"
        assert not cache.exists()
        for base in ("", "_internal/nested/"):
            for name in excluded_dirs + excluded_files:
                assert not (package / base / name).exists(), base + name
        for relative in runtime_paths:
            assert (package / relative).read_bytes() == b"fixture-artifact"
        assert kimi_target.read_bytes() == kimi_bytes
        assert len(probes) == 2 and ".mc-build-" in probes[0]
        assert Path(probes[1]) == kimi_target



def test_pyinstaller_specs_bundle_websocket_client_for_kimi_server_client():
    """kimi_server_client imports websocket-client lazily; keep it in hiddenimports."""
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("ZhugeQA_A11y.spec", "zgwd.spec"):
        text = (root / spec_name).read_text(encoding="utf-8")

        assert "'websocket'" in text, f"{spec_name} must include the websocket hiddenimport"


def test_main_imports_kimi_server_client_module():
    root = Path(__file__).resolve().parents[1]
    main_text = (root / "main.py").read_text(encoding="utf-8")

    assert "from kimi_server_client import" in main_text


def test_package_collected_inputs_exclude_development_and_user_data(tmp_path):
    from package_inputs import filter_package_toc
    required = ["ZDSRAPI.dll", "ZDSRAPI_x64.dll", "nats-server/nats-server.exe",
                "assets/chat_title_rules.json", "assets/codex-skills/mc-browser-open/SKILL.md",
                "sound/done.wav", "python311.dll", "wx/msw/core.pyd"]
    excluded = ["new.apk", "screenshots/screen.png", "tests/fixture.json",
                "reverse-engineering/decompiled.txt", "jadx/sources/Main.java",
                "history/chat_history.db", ".codex-home/session.json", ".dev/run/out.txt"]
    entries = []
    for name in required + excluded:
        source = tmp_path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"fixture")
        entries.append((name, str(source), "DATA"))
    assert [entry[0] for entry in filter_package_toc(entries)] == required
    spec = (Path(__file__).resolve().parents[1] / "zgwd.spec").read_text(encoding="utf-8")
    assert "a.datas = filter_package_toc(a.datas)" in spec
    assert "b.datas = filter_package_toc(b.datas)" in spec


@pytest.mark.parametrize("ancestor", ["test", "tests"])
def test_package_source_filter_uses_collection_relative_boundary(tmp_path, monkeypatch, ancestor):
    import package_inputs
    collection = tmp_path / ancestor / "mc"
    monkeypatch.setattr(package_inputs, "__file__", str(collection / "package_inputs.py"))
    required = [("python311.dll", str(collection / ".venv/Lib/python311.dll"), "BINARY"),
                ("assets/chat_title_rules.json", str(collection / "assets/chat_title_rules.json"), "DATA"),
                ("wx/core.pyd", str(tmp_path / ancestor / "external-python/wx/core.pyd"), "BINARY")]
    test_artifacts = [("assets/renamed-fixture.json", str(collection / "tests/fixture.json"), "DATA"),
                      ("assets/other-fixture.json", str(collection / "test/fixture.json"), "DATA")]
    assert package_inputs.filter_package_toc(required + test_artifacts) == required
