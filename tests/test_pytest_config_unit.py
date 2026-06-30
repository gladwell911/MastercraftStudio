import configparser
from pathlib import Path


def test_pytest_default_collection_covers_all_project_test_roots():
    config = configparser.ConfigParser()
    config.read(Path(__file__).resolve().parents[1] / "pytest.ini", encoding="utf-8")

    raw_paths = config.get("pytest", "testpaths")
    collected_paths = [line.strip() for line in raw_paths.splitlines() if line.strip()]

    assert collected_paths == [
        "tests",
        "test",
        "test_program_runner.py",
    ]
