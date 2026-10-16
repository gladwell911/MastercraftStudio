import main


def test_instance_key_is_bound_to_normalized_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path / "cz" / "history")
    first = main.app_instance_key()
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path / "cz" / "mc" / ".." / "history")
    assert main.app_instance_key() == first
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path / "cx" / "history")
    assert main.app_instance_key() != first
    assert str(tmp_path) not in first


def test_packaged_instance_scope_follows_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(tmp_path / "cz" / "mc" / "mc.exe"))
    first = main.app_instance_key()
    assert main.resolve_app_data_dir() == tmp_path / "cz" / "history"
    monkeypatch.setattr(main.sys, "executable", str(tmp_path / "cx" / "mc" / "mc.exe"))
    assert main.app_instance_key() != first
