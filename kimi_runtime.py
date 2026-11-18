"""Explicit local preparation and validation of MC's isolated Kimi runtime."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess

from browser_open import resource_root


SUPPORTED_VERSION = "2.5.3"
SUPPORTED_PROTOCOL = 2
SUPPORTED_SHA256 = "3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d"


def runtime_relative_path():
    executable = "kimi.exe" if os.name == "nt" else "kimi"
    return Path("runtimes", "kimi", SUPPORTED_VERSION, executable)


def runtime_path():
    return resource_root() / runtime_relative_path()


def child_environment(source=None):
    return {**(os.environ if source is None else source), "KIMI_CODE_NO_AUTO_UPDATE": "1"}


def validate_runtime(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Kimi runtime missing at {path}; prepare explicitly: python kimi_runtime.py --source <local-kimi.exe> --destination {path.parent}")
    with path.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    if digest != SUPPORTED_SHA256:
        raise RuntimeError(f"Kimi runtime validation failed at {path}: expected {SUPPORTED_VERSION} / protocol {SUPPORTED_PROTOCOL}, SHA256 mismatch")
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, timeout=10,
                                env=child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Kimi runtime version probe failed at {path}: {exc}") from exc
    version = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode or version != SUPPORTED_VERSION:
        raise RuntimeError(f"Kimi runtime version validation failed at {path}: local {version!r}, expected {SUPPORTED_VERSION}, exit code {result.returncode}")
    return path


def validate_packaged_runtime(package_root):
    package_root = Path(package_root)
    target = package_root / "_internal" / runtime_relative_path()
    # Reject links before reading or running an executable outside the package.
    node = package_root
    for part in ("", *target.relative_to(package_root).parts):
        node /= part
        try:
            metadata = node.lstat()
        except (FileNotFoundError, NotADirectoryError):
            break
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError(f"Kimi packaged runtime path contains a link: {node}")
    return validate_runtime(target)


def prepare_runtime(source, destination):
    source = validate_runtime(source)
    destination = Path(destination)
    target = destination / runtime_relative_path().name
    if target.exists():
        raise RuntimeError("Kimi destination already exists; select an empty version directory")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
        return validate_runtime(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source")
    mode.add_argument("--package-root", help="validate the fixed onedir runtime under this package")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.source is not None and args.destination is None:
        parser.error("--source requires --destination")
    if args.package_root is not None and args.destination is not None:
        parser.error("--package-root cannot be combined with --destination")
    try:
        if args.package_root is not None:
            print(validate_packaged_runtime(args.package_root))
        else:
            print(prepare_runtime(args.source, args.destination))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
