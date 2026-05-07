from pathlib import Path


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


def test_package_script_safely_cleans_packaged_output_before_build():
    root = Path(__file__).resolve().parents[1]
    script_text = (root / "package_mc.ps1").read_text(encoding="utf-8")

    assert "Clear-PackageOutput" in script_text
    assert "Resolve-Path -LiteralPath $DistPath" in script_text
    assert "Get-Process" in script_text
    assert "mc.exe is still running" in script_text
    assert "Remove-Item -LiteralPath $targetPath -Recurse -Force" in script_text
    assert "Remove-BundledRuntimeHistory" in script_text
    assert "_internal" in script_text
