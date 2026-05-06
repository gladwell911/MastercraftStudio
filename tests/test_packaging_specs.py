from pathlib import Path


def test_pyinstaller_specs_exclude_zdsr_dlls_from_upx():
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("ZhugeQA_A11y.spec", "zgwd.spec"):
        text = (root / spec_name).read_text(encoding="utf-8")

        assert "'ZDSRAPI.dll'" in text
        assert "'ZDSRAPI_x64.dll'" in text
        assert "upx_exclude=['ZDSRAPI.dll', 'ZDSRAPI_x64.dll']" in text
