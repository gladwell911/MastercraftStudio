"""Explicit local preparation and validation of MC's isolated Kimi runtime."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from browser_open import resource_root


SUPPORTED_VERSION = "2.5.3"
SUPPORTED_PROTOCOL = 2
SUPPORTED_SHA256 = "3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d"


def runtime_path():
    return resource_root() / "runtimes" / "kimi" / SUPPORTED_VERSION / ("kimi.exe" if os.name == "nt" else "kimi")


def child_environment(source=None):
    return {**(os.environ if source is None else source), "KIMI_CODE_NO_AUTO_UPDATE": "1"}


def validate_runtime(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Kimi runtime missing at {path}; prepare explicitly: python kimi_runtime.py --source <local-kimi.exe> --destination {path.parent}")
    with path.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    if digest != SUPPORTED_SHA256:
        raise RuntimeError(f"Kimi runtime validation failed: expected {SUPPORTED_VERSION} / protocol {SUPPORTED_PROTOCOL}, SHA256 mismatch")
    result = subprocess.run([str(path), "--version"], capture_output=True, timeout=10,
                            env=child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    version = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode or version != SUPPORTED_VERSION:
        raise RuntimeError(f"Kimi runtime version validation failed: local {version!r}, expected {SUPPORTED_VERSION}")
    return path


def prepare_runtime(source, destination):
    source = validate_runtime(source)
    destination = Path(destination)
    target = destination / ("kimi.exe" if os.name == "nt" else "kimi")
    if target.exists():
        raise RuntimeError("Kimi destination already exists; select an empty version directory")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
        return validate_runtime(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(prepare_runtime(args.source, args.destination))
