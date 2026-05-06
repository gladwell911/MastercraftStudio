import zdsr_tts


def test_zdsr_tts_adds_dll_directory_while_loading_candidate(monkeypatch, tmp_path):
    dll_dir = tmp_path / "bundle"
    dll_dir.mkdir()
    dll_path = dll_dir / "ZDSRAPI_x64.dll"
    dll_path.write_bytes(b"fake")
    added = []
    removed = []
    loaded = []

    class _Cookie:
        def close(self):
            removed.append("closed")

    monkeypatch.setattr(zdsr_tts.ZDSRTTSClient, "_candidate_dirs", lambda self: [dll_dir])
    monkeypatch.setattr(zdsr_tts.ZDSRTTSClient, "_candidate_names", lambda self: ["ZDSRAPI_x64.dll"])
    monkeypatch.setattr(zdsr_tts.os, "add_dll_directory", lambda path: added.append(path) or _Cookie(), raising=False)
    monkeypatch.setattr(zdsr_tts.ctypes, "WinDLL", lambda path: loaded.append(path) or object())

    api = zdsr_tts.ZDSRTTSClient()._load_api()

    assert api is not None
    assert added == [str(dll_dir)]
    assert removed == ["closed"]
    assert loaded == [str(dll_path)]
