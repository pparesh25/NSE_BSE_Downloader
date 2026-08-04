#!/usr/bin/env python3
"""Validate and optionally build the desktop application with Nuitka.

The default operation is a no-write dry run.  A real compilation requires the
explicit ``--build`` flag; previous output is removed only with ``--clean``.
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from app_metadata import (
    APP_NAME,
    MACOS_BUNDLE_ID,
    ORGANIZATION_NAME,
    PRODUCT_NAME,
)
from version import __version__


ENTRY_POINT = "main.py"
PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_MODULES = {
    "PySide6.QtWidgets": "PySide6",
    "aiohttp": "aiohttp",
    "pandas": "pandas",
    "yaml": "PyYAML",
}
REQUIRED_RESOURCES = (
    Path("config.yaml"),
    Path("src/gui/resources/QR_UPI.jpeg"),
)


def current_platform() -> str:
    names = {"darwin": "darwin", "windows": "windows", "linux": "linux"}
    return names.get(platform.system().lower(), platform.system().lower())


def validate_project(project_root: Path = PROJECT_ROOT) -> List[str]:
    """Return packaging errors without changing the project or environment."""

    errors: List[str] = []
    entry = project_root / ENTRY_POINT
    if not entry.is_file():
        errors.append(f"Missing entry point: {entry}")
    for relative in REQUIRED_RESOURCES:
        path = project_root / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Missing or empty bundled resource: {path}")

    config_path = project_root / "config.yaml"
    if config_path.is_file():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            for section in ("data_paths", "download_settings", "exchange_config"):
                if section not in (config or {}):
                    errors.append(f"config.yaml is missing section: {section}")
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"config.yaml cannot be parsed: {error}")

    qr_path = project_root / "src/gui/resources/QR_UPI.jpeg"
    if qr_path.is_file():
        signature = qr_path.read_bytes()[:8]
        if not (
            signature.startswith(b"\x89PNG\r\n\x1a\n")
            or signature.startswith(b"\xff\xd8\xff")
        ):
            errors.append(f"Unsupported QR image format: {qr_path}")

    if re.fullmatch(r"\d+(?:\.\d+){1,3}", __version__) is None:
        errors.append(
            f"Version {__version__!r} is not valid for Nuitka metadata"
        )
    return errors


def missing_runtime_dependencies() -> List[str]:
    missing = []
    for module, distribution in REQUIRED_MODULES.items():
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            available = False
        if not available:
            missing.append(distribution)
    return missing


def optional_icon_args(project_root: Path, target_platform: str) -> List[str]:
    resources = project_root / "src/gui/resources"
    if target_platform == "darwin" and (resources / "icon.icns").is_file():
        return [f"--macos-app-icon={resources / 'icon.icns'}"]
    if target_platform == "windows" and (resources / "icon.ico").is_file():
        return [f"--windows-icon-from-ico={resources / 'icon.ico'}"]
    if target_platform == "linux" and (resources / "icon.png").is_file():
        return [f"--linux-icon={resources / 'icon.png'}"]
    return []


def get_nuitka_command(
    *,
    project_root: Path = PROJECT_ROOT,
    target_platform: Optional[str] = None,
    output_dir: Optional[Path] = None,
    standalone_folder: bool = False,
) -> List[str]:
    """Create a platform-specific command without executing Nuitka."""

    target = target_platform or current_platform()
    destination = (output_dir or project_root / "dist").resolve()
    if target == "darwin":
        mode = "app-dist" if standalone_folder else "app"
    else:
        mode = "standalone" if standalone_folder else "onefile"

    command = [
        sys.executable,
        "-m",
        "nuitka",
        f"--mode={mode}",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        "--file-reference-choice=runtime",
        f"--include-data-files={project_root / 'config.yaml'}=config.yaml",
        (
            f"--include-data-dir={project_root / 'src/gui/resources'}="
            "src/gui/resources"
        ),
        f"--output-dir={destination}",
        f"--output-filename={APP_NAME}",
        f"--company-name={ORGANIZATION_NAME}",
        f"--product-name={PRODUCT_NAME}",
        f"--file-version={__version__}",
        f"--product-version={__version__}",
        "--file-description=NSE and BSE daily market data downloader",
        "--copyright=Copyright (c) 2026 Paresh Patel",
        f"--report={destination / 'nuitka-compilation-report.xml'}",
    ]
    if target == "darwin":
        command.extend([
            f"--macos-app-name={PRODUCT_NAME}",
            f"--macos-app-version={__version__}",
            "--macos-app-mode=gui",
            f"--macos-signed-app-name={MACOS_BUNDLE_ID}",
            "--macos-prohibit-multiple-instances",
        ])
    elif target == "windows":
        command.append("--windows-console-mode=disable")
    command.extend(optional_icon_args(project_root, target))
    command.append(str(project_root / ENTRY_POINT))
    return command


def clean_outputs(project_root: Path, output_dir: Path) -> None:
    """Remove only explicit Nuitka output locations after user opt-in."""

    resolved_root = project_root.resolve()
    resolved_output = output_dir.resolve()
    try:
        resolved_output.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            "--clean output directory must remain inside the project root"
        ) from error

    targets = (
        resolved_output,
        project_root / "main.build",
        project_root / "main.dist",
        project_root / "main.onefile-build",
        project_root / f"{APP_NAME}.build",
        project_root / f"{APP_NAME}.dist",
        project_root / f"{APP_NAME}.onefile-build",
    )
    protected = {resolved_root, project_root.parent.resolve(), Path("/")}
    for target in targets:
        resolved = target.resolve()
        if resolved in protected:
            raise ValueError(f"Refusing to clean broad path: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.exists():
            resolved.unlink()


def verify_build(output_dir: Path, target_platform: str) -> bool:
    """Verify the expected binary or app bundle after an explicit build."""

    if target_platform == "darwin":
        return any(
            path.is_dir()
            for path in (
                output_dir / f"{APP_NAME}.app",
                output_dir / "main.app",
            )
        )
    suffix = ".exe" if target_platform == "windows" else ""
    return (output_dir / f"{APP_NAME}{suffix}").is_file()


def _print_errors(label: str, errors: Iterable[str]) -> None:
    print(label)
    for error in errors:
        print(f"  - {error}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or explicitly build the Nuitka desktop package"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Run Nuitka; without this flag the command is only printed",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete explicit prior outputs before --build",
    )
    parser.add_argument(
        "--standalone-folder",
        action="store_true",
        help="Produce a folder/app-dist package instead of onefile/app",
    )
    parser.add_argument(
        "--target-platform",
        choices=("darwin", "windows", "linux"),
        default=current_platform(),
        help="Target used for command generation; real builds must match host",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    project_errors = validate_project(PROJECT_ROOT)
    if project_errors:
        _print_errors("Packaging validation failed:", project_errors)
        return 1
    missing = missing_runtime_dependencies()
    if missing:
        _print_errors("Missing runtime dependencies:", missing)
        return 1

    output_dir = args.output_dir.resolve()
    command = get_nuitka_command(
        project_root=PROJECT_ROOT,
        target_platform=args.target_platform,
        output_dir=output_dir,
        standalone_folder=args.standalone_folder,
    )
    print(f"Packaging validation passed for {args.target_platform}.")
    print(shlex.join(command))

    if not args.build:
        if args.clean:
            print("--clean was ignored because --build was not requested.")
        print("DRY RUN: no Nuitka build was started.")
        return 0
    if args.target_platform != current_platform():
        print("A real build target must match the current host platform.")
        return 1
    if importlib.util.find_spec("nuitka") is None:
        print(
            "Nuitka is not installed. Install requirements-build.txt in the "
            "dedicated build environment."
        )
        return 1
    if args.clean:
        clean_outputs(PROJECT_ROOT, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        return result.returncode
    if not verify_build(output_dir, args.target_platform):
        print("Nuitka returned success but the expected package was not found.")
        return 1
    print(f"Build verified under: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
