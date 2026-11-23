"""Resolve and validate the local Kimi CLI when the Kimi client starts."""
import hashlib
import os
from pathlib import Path
import subprocess


# Hashes identify the Windows builds with recorded protocol compatibility.
SUPPORTED_BUILDS = {
    "3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d": "2.5.3",
    "b50c7ce3bed69e9be58f9a29aa090c0e7c61f7a1887503c4943267384a71ef30": "2.6.3",
}


def runtime_path():
    override = os.getenv("KIMI_BIN", "").strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise RuntimeError(f"KIMI_BIN must be an absolute path: {override}")
        return path
    return Path.home() / ".kimi-code" / "bin" / "kimi.exe"


def child_environment(source=None):
    return {**(os.environ if source is None else source), "KIMI_CODE_NO_AUTO_UPDATE": "1"}


def validate_runtime(path):
    path = Path(path)
    validate_runtime_version(path)
    return path


def validate_runtime_version(path):
    path = Path(path)
    try:
        with path.open("rb") as binary:
            digest = hashlib.file_digest(binary, "sha256").hexdigest()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RuntimeError(
            f"Kimi runtime missing at {path}. Install a supported Kimi CLI or set KIMI_BIN to its absolute path."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot read Kimi CLI at {path}: {exc}") from exc

    expected_version = SUPPORTED_BUILDS.get(digest)
    if expected_version is None:
        supported = ", ".join(sorted(set(SUPPORTED_BUILDS.values())))
        raise RuntimeError(
            f"Kimi CLI at {path} has an unverified SHA256 ({digest}). "
            f"Use a verified build ({supported}) via KIMI_BIN or wait for MC to support this build."
        )

    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, timeout=10,
            env=child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Kimi CLI version probe failed at {path}: {exc}") from exc
    version = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode or version != expected_version:
        raise RuntimeError(
            f"Kimi CLI version validation failed at {path}: local {version!r}, "
            f"expected {expected_version}, exit code {result.returncode}."
        )
    return version
