#!/usr/bin/env python3
"""Smoke-test and package a Nuitka build as a checksum-bound ZIP archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Sequence

from app_metadata import APP_NAME, PRODUCT_NAME
from version import __version__


PROJECT_ROOT = Path(__file__).resolve().parent
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def normalized_architecture(machine: Optional[str] = None) -> str:
    """Return a stable release architecture name."""

    value = (machine or platform.machine()).strip().lower()
    aliases = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    return aliases.get(value, value.replace(" ", "-"))


def find_build_output(build_dir: Path, target_platform: str) -> Path:
    """Locate the verified package produced by the build helper."""

    build_dir = build_dir.resolve()
    if target_platform == "darwin":
        candidates = (
            build_dir / f"{APP_NAME}.app",
            build_dir / f"{PRODUCT_NAME}.app",
            build_dir / "main.app",
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
    else:
        suffix = ".exe" if target_platform == "windows" else ""
        candidate = build_dir / f"{APP_NAME}{suffix}"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"No {target_platform} Nuitka package found under {build_dir}"
    )


def executable_path(package: Path, target_platform: str) -> Path:
    """Return the executable used for a non-mutating CLI smoke test."""

    if target_platform == "darwin":
        executable = package / "Contents" / "MacOS" / APP_NAME
    else:
        executable = package
    if not executable.is_file():
        raise FileNotFoundError(f"Packaged executable is missing: {executable}")
    return executable


def smoke_test(package: Path, target_platform: str) -> None:
    """Load the packaged program and exit through argparse before data setup."""

    executable = executable_path(package, target_platform)
    result = subprocess.run(
        [str(executable), "--help"],
        cwd=package.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(
            f"Packaged --help smoke test failed with {result.returncode}: "
            f"{stderr}"
        )


def _iter_package_paths(package: Path) -> Iterable[Path]:
    if package.is_file():
        yield package
        return
    yield package
    yield from sorted(package.rglob("*"), key=lambda item: item.as_posix())


def _zip_info(archive_name: str, path: Optional[Path] = None) -> zipfile.ZipInfo:
    is_directory = archive_name.endswith("/")
    info = zipfile.ZipInfo(archive_name, FIXED_ZIP_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    if is_directory:
        mode = stat.S_IFDIR | 0o755
        info.external_attr = (mode << 16) | 0x10
    else:
        source_mode = path.stat().st_mode if path is not None else 0o644
        permissions = 0o755 if source_mode & 0o111 else 0o644
        info.external_attr = (stat.S_IFREG | permissions) << 16
    return info


def _write_path(
    archive: zipfile.ZipFile,
    path: Path,
    archive_name: PurePosixPath,
) -> None:
    name = archive_name.as_posix()
    if path.is_symlink():
        raise ValueError(f"Release archives cannot contain symlinks: {path}")
    if path.is_dir():
        archive.writestr(_zip_info(name.rstrip("/") + "/"), b"")
        return
    with path.open("rb") as source, archive.open(_zip_info(name, path), "w") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(project_root: Path = PROJECT_ROOT) -> str:
    environment_commit = os.environ.get("GITHUB_SHA", "").strip()
    if environment_commit:
        return environment_commit
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def create_release_archive(
    package: Path,
    *,
    target_platform: str,
    architecture: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Create a deterministic single-root ZIP and its SHA-256 sidecar."""

    output_dir.mkdir(parents=True, exist_ok=True)
    root_name = (
        f"{APP_NAME}-{__version__}-{target_platform}-{architecture}"
    )
    archive_path = output_dir / f"{root_name}.zip"
    checksum_path = archive_path.with_suffix(".zip.sha256")
    packaged_name = (
        f"{PRODUCT_NAME}.app" if target_platform == "darwin" else package.name
    )
    metadata = {
        "application": PRODUCT_NAME,
        "version": __version__,
        "platform": target_platform,
        "architecture": architecture,
        "git_commit": _git_commit(),
    }

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        root = PurePosixPath(root_name)
        archive.writestr(_zip_info(root.as_posix() + "/"), b"")
        metadata_name = root / "BUILD-METADATA.json"
        metadata_bytes = (
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        archive.writestr(_zip_info(metadata_name.as_posix()), metadata_bytes)

        package_root = root / packaged_name
        for path in _iter_package_paths(package):
            relative = Path(".") if path == package else path.relative_to(package)
            destination = package_root
            if relative != Path("."):
                destination /= PurePosixPath(relative.as_posix())
            _write_path(archive, path, destination)

    digest = _sha256(archive_path)
    checksum_path.write_text(
        f"{digest}  {archive_path.name}\n",
        encoding="ascii",
    )
    return archive_path, checksum_path


def validate_release_archive(archive_path: Path) -> None:
    """Reject malformed archives before they are uploaded by CI."""

    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        if not members:
            raise ValueError("Release archive is empty")
        top_levels = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe release archive path: {member.filename}")
            parts = [part for part in path.parts if part not in {"", "."}]
            if parts:
                top_levels.add(parts[0])
            file_type = (member.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise ValueError(f"Release archive contains a symlink: {path}")
        if len(top_levels) != 1:
            raise ValueError("Release archive must contain one top-level folder")
        if not any(member.filename.endswith("BUILD-METADATA.json") for member in members):
            raise ValueError("Release archive is missing build metadata")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test and package a Nuitka release build"
    )
    parser.add_argument(
        "--target-platform",
        required=True,
        choices=("darwin", "windows", "linux"),
    )
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "release-dist",
    )
    parser.add_argument("--architecture", default=normalized_architecture())
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="Package without execution; intended only for unit-test fixtures",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    package = find_build_output(args.build_dir, args.target_platform)
    if not args.skip_smoke_test:
        smoke_test(package, args.target_platform)
    archive_path, checksum_path = create_release_archive(
        package,
        target_platform=args.target_platform,
        architecture=args.architecture,
        output_dir=args.output_dir.resolve(),
    )
    validate_release_archive(archive_path)
    if not checksum_path.is_file():
        raise FileNotFoundError(f"Checksum was not created: {checksum_path}")
    print(f"Release archive: {archive_path}")
    print(f"SHA-256: {checksum_path.read_text(encoding='ascii').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
