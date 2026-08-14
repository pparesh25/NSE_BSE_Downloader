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
import struct
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
    "certifi": "certifi",
    "pandas": "pandas",
    "yaml": "PyYAML",
}
# A CA bundle is a few hundred kilobytes of PEM blocks.  Anything much smaller
# is a stub or a truncated file, and would compile into a build that trusts
# almost nothing while looking correctly configured.
MINIMUM_CA_BUNDLE_BYTES = 50_000
REQUIRED_RESOURCES = (
    Path("config.yaml"),
    Path("src/gui/resources/QR_UPI.jpeg"),
    Path("src/gui/resources/icon.png"),
    Path("src/gui/resources/icon.ico"),
    Path("src/gui/resources/icon.icns"),
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

    errors.extend(validate_icon_resources(project_root / "src/gui/resources"))

    if re.fullmatch(r"\d+(?:\.\d+){1,3}", __version__) is None:
        errors.append(f"Version {__version__!r} is not valid for Nuitka metadata")
    return errors


def validate_icon_resources(resources: Path) -> List[str]:
    """Validate committed cross-platform icon containers without image tools."""

    errors: List[str] = []
    png_path = resources / "icon.png"
    ico_path = resources / "icon.ico"
    icns_path = resources / "icon.icns"
    try:
        header = png_path.read_bytes()[:26]
        if not header.startswith(b"\x89PNG\r\n\x1a\n") or len(header) < 26:
            errors.append(f"Invalid PNG application icon: {png_path}")
        else:
            width, height = struct.unpack(">II", header[16:24])
            if (width, height) != (1024, 1024) or header[25] != 6:
                errors.append(
                    f"PNG application icon must be 1024x1024 RGBA: {png_path}"
                )
    except OSError as error:
        errors.append(f"Application icon cannot be read: {error}")

    try:
        header = ico_path.read_bytes()[:6]
        if len(header) != 6:
            errors.append(f"Invalid Windows application icon: {ico_path}")
        else:
            reserved, image_type, count = struct.unpack("<HHH", header)
            if reserved != 0 or image_type != 1 or count < 7:
                errors.append(
                    "Windows application icon must contain at least seven "
                    f"sizes: {ico_path}"
                )
    except OSError as error:
        errors.append(f"Windows application icon cannot be read: {error}")

    try:
        if icns_path.read_bytes()[:4] != b"icns":
            errors.append(f"Invalid macOS application icon: {icns_path}")
    except OSError as error:
        errors.append(f"macOS application icon cannot be read: {error}")
    return errors


def certificate_bundle_errors() -> List[str]:
    """Check that there is a real trust store to bundle into the build.

    The v1.1.0 artifacts compiled and passed every other gate while carrying no
    certificates at all, so they could not open a single HTTPS connection on a
    user's machine.  This runs in the dry run, before a 20-minute build.
    """

    errors: List[str] = []
    try:
        import certifi
    except ImportError:
        # missing_runtime_dependencies() names the distribution to install.
        return errors

    bundle = Path(certifi.where())
    if not bundle.is_file():
        errors.append(f"CA certificate bundle is missing: {bundle}")
    elif bundle.stat().st_size < MINIMUM_CA_BUNDLE_BYTES:
        errors.append(
            f"CA certificate bundle is implausibly small "
            f"({bundle.stat().st_size} bytes): {bundle}"
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
    macos_sign_identity: Optional[str] = None,
    macos_sign_notarization: bool = False,
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
        (f"--include-data-dir={project_root / 'src/gui/resources'}=src/gui/resources"),
        # Without this the compiled binary has no certificates to verify
        # against, and every HTTPS request fails.  Nuitka compiles certifi's
        # module but treats cacert.pem as data it does not need.
        "--include-package-data=certifi",
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
        command.extend(
            [
                f"--macos-app-name={PRODUCT_NAME}",
                f"--macos-app-version={__version__}",
                "--macos-app-mode=gui",
                f"--macos-signed-app-name={MACOS_BUNDLE_ID}",
                "--macos-prohibit-multiple-instances",
            ]
        )
        if macos_sign_identity:
            command.append(f"--macos-sign-identity={macos_sign_identity}")
        if macos_sign_notarization:
            if not macos_sign_identity:
                raise ValueError(
                    "macOS notarization signing requires --macos-sign-identity"
                )
            command.append("--macos-sign-notarization")
    elif macos_sign_identity or macos_sign_notarization:
        raise ValueError("macOS signing options require a darwin target")
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


def find_macos_app(output_dir: Path) -> Path:
    """Return the macOS app bundle produced by Nuitka."""

    for path in (
        output_dir / f"{APP_NAME}.app",
        output_dir / f"{PRODUCT_NAME}.app",
        output_dir / "main.app",
    ):
        if path.is_dir():
            return path
    raise FileNotFoundError(f"No macOS app bundle found under {output_dir}")


def macos_qt_link_replacements(
    dependency_output: str,
    executable_dir: Path,
) -> List[tuple[str, str]]:
    """Map framework-style PySide plugin links to Nuitka's flat Qt layout."""

    replacements: List[tuple[str, str]] = []
    pattern = re.compile(r"(@rpath/(Qt[A-Za-z0-9_]+)\.framework/Versions/A/\2)")
    for old_path, library_name in pattern.findall(dependency_output):
        if (executable_dir / library_name).is_file():
            replacements.append((old_path, f"@executable_path/{library_name}"))
    return replacements


def _resolve_macos_sign_identity(requested_identity: Optional[str]) -> str:
    if requested_identity and requested_identity != "auto":
        return requested_identity
    if not requested_identity:
        return "-"

    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    identities = re.findall(
        r'^\s*\d+\)\s+[0-9A-Fa-f]+\s+"([^"]+)"',
        result.stdout,
        re.M,
    )
    developer_ids = [
        identity
        for identity in identities
        if identity.startswith("Developer ID Application:")
    ]
    candidates = developer_ids or identities
    if len(candidates) != 1:
        raise RuntimeError(
            "--macos-sign-identity=auto requires exactly one usable "
            f"code-signing identity; found {len(candidates)}"
        )
    return candidates[0]


def repair_macos_qt_plugin_links(
    output_dir: Path,
    *,
    sign_identity: Optional[str] = None,
    hardened_runtime: bool = False,
) -> int:
    """Repair PySide plugin links, then restore valid bundle signatures.

    PyPI PySide6 plugins reference Qt frameworks through ``@rpath`` while
    Nuitka places the corresponding Qt libraries beside the app executable.
    The repair is deterministic and only rewrites dependencies whose flat
    library is present in the generated bundle.
    """

    app_path = find_macos_app(output_dir)
    executable_dir = app_path / "Contents" / "MacOS"
    plugin_root = executable_dir / "PySide6" / "qt-plugins"
    if not plugin_root.is_dir():
        raise FileNotFoundError(
            f"Bundled PySide6 plugin directory is missing: {plugin_root}"
        )

    modified: List[Path] = []
    for plugin_path in sorted(plugin_root.rglob("*.dylib")):
        dependency_output = subprocess.run(
            ["otool", "-L", str(plugin_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout
        replacements = macos_qt_link_replacements(
            dependency_output,
            executable_dir,
        )
        for old_path, new_path in replacements:
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    old_path,
                    new_path,
                    str(plugin_path),
                ],
                check=True,
            )
        if replacements:
            modified.append(plugin_path)

    if not modified:
        return 0

    identity = _resolve_macos_sign_identity(sign_identity)
    signing_options = (
        ["--options", "runtime", "--timestamp"] if hardened_runtime else []
    )
    for plugin_path in modified:
        subprocess.run(
            [
                "codesign",
                "--force",
                *signing_options,
                "--sign",
                identity,
                str(plugin_path),
            ],
            check=True,
        )
    subprocess.run(
        [
            "codesign",
            "--force",
            *signing_options,
            "--sign",
            identity,
            str(app_path),
        ],
        check=True,
    )
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app_path)],
        check=True,
    )
    return len(modified)


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
    parser.add_argument(
        "--macos-sign-identity",
        help="Developer ID identity or 'auto'; darwin builds only",
    )
    parser.add_argument(
        "--macos-sign-notarization",
        action="store_true",
        help="Enable hardened-runtime signing required by Apple notarization",
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
    bundle_errors = certificate_bundle_errors()
    if bundle_errors:
        _print_errors("Certificate bundle validation failed:", bundle_errors)
        return 1

    output_dir = args.output_dir.resolve()
    command = get_nuitka_command(
        project_root=PROJECT_ROOT,
        target_platform=args.target_platform,
        output_dir=output_dir,
        standalone_folder=args.standalone_folder,
        macos_sign_identity=args.macos_sign_identity,
        macos_sign_notarization=args.macos_sign_notarization,
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
    if args.target_platform == "darwin":
        repaired_count = repair_macos_qt_plugin_links(
            output_dir,
            sign_identity=args.macos_sign_identity,
            hardened_runtime=args.macos_sign_notarization,
        )
        print(f"Repaired {repaired_count} bundled macOS Qt plugins.")
    print(f"Build verified under: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
