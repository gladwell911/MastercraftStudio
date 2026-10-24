"""Exclude development artifacts from PyInstaller collected inputs."""
from pathlib import PurePosixPath

_EXCLUDED_PARTS = frozenset({
    ".dev", "tests", "test", "screenshots", "screenshot", "reverse-engineering",
    "reverse_engineering", "apktool", "jadx", "android-reverse", ".codex-home",
    "history", "jetstream", ".git", ".pytest_cache", "_bmad-output",
})


def package_input_allowed(destination: str) -> bool:
    path = PurePosixPath(str(destination).replace("\\", "/").lower())
    return (path.name not in {"app_state.json", "remote_machine.json"}
            and not any(part in _EXCLUDED_PARTS for part in path.parts)
            and not any(part.startswith(".codex-home-") for part in path.parts)
            and path.suffix not in {".apk", ".aab", ".log", ".db", ".sqlite", ".sqlite3"})


def filter_package_toc(entries):
    source_artifacts = {"tests", "test", "screenshots", "screenshot", "reverse-engineering", "reverse_engineering", "apktool", "jadx"}
    def allowed(entry):
        source = PurePosixPath(str(entry[1]).replace("\\", "/").lower())
        return (package_input_allowed(entry[0]) and source.suffix not in {".apk", ".aab"}
                and not any(part in source_artifacts for part in source.parts))
    return [entry for entry in entries if allowed(entry)]
