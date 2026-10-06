from pathlib import Path
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
@pytest.mark.parametrize("mode", ["success", "build_fail", "missing_worker", "empty_worker", "update_fail", "final_invalid", "start_fail"])
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
    history = dist / "history"
    history.mkdir()
    (history / "chat_history.db").write_bytes(b"outside-history")
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
    spec = tmp_path / "fake.spec"
    spec.write_text("fake only", encoding="utf-8")
    builder = tmp_path / "fake-builder.py"
    builder.write_text(
        "import pathlib, sys\n"
        "assert sys.argv[1:3] == ['-m', 'PyInstaller']\n"
        f"mode = {mode!r}\n"
        f"fixture = pathlib.Path({str(tmp_path)!r}).resolve()\n"
        "dist = pathlib.Path(sys.argv[sys.argv.index('--distpath') + 1]).resolve()\n"
        "assert fixture in dist.parents and dist.name.startswith('.mc-build-')\n"
        "if mode == 'build_fail': sys.exit(7)\n"
        "package = dist / 'mc'\n"
        "(package / '_internal' / 'assets').mkdir(parents=True)\n"
        "(package / 'mc.exe').write_bytes(b'new-gui')\n"
        "if mode != 'missing_worker': (package / 'mc_worker.exe').write_bytes(b'' if mode == 'empty_worker' else b'new-worker')\n"
        "(package / '_internal' / 'assets' / 'chat_title_rules.json').write_text('{}')\n"
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
        + f"& {quote(script)} -DistPath {quote(dist)} -WorkPath {quote(tmp_path / 'work')} -SpecPath {quote(spec)} -PythonExe {quote(fake_python)}\n"
        + "exit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == (0 if mode == "success" else 1), result.stdout + result.stderr
    launch_rows = [json.loads(line) for line in launches.read_text(encoding="utf-8-sig").splitlines()] if launches.exists() else []
    assert launch_rows == ([{"file": str(package / "mc.exe"), "cwd": str(package)}] if mode == "success" else [])
    assert (home / "rollout.db").read_bytes() == b"keep-session"
    assert (home / "skills" / "SKILL.md").read_bytes() == b"\xef\xbb\xbfglobal-unchanged"
    assert (bundled_history / "keep.db").read_bytes() == b"keep-old-history"
    assert (history / "chat_history.db").read_bytes() == b"outside-history"
    assert (onedrive / "notes.db").read_bytes() == b"outside-notes"
    assert (package / "user-data.txt").read_bytes() == b"keep-local"
    assert sentinel.read_bytes() == b"\xef\xbb\xbfglobal-unchanged"
    assert sentinel.stat().st_file_attributes == attributes
    assert not list(dist.glob(".mc-build-*"))
    assert not (home / "unwanted-build-state").exists()
    if mode in {"build_fail", "missing_worker", "empty_worker"}:
        assert (package / "mc.exe").read_bytes() == b"old-gui"
        assert (package / "mc_worker.exe").read_bytes() == b"old-worker"
    if mode == "success":
        assert (package / "mc.exe").read_bytes() == b"new-gui"
        assert (package / "mc_worker.exe").read_bytes() == b"new-worker"
        assert not cache.exists()


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
