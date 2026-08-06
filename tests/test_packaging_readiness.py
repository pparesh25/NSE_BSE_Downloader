import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
import pytest
import yaml

import build_nuitka_cross_platform as packaging
import runtime_paths
from app_metadata import MACOS_BUNDLE_ID
from main import setup_argument_parser
from runtime_paths import default_config_path, resource_path
from src.core.config import Config
from src.gui import widgets
from src.utils.update_checker import UpdateChecker
from version import get_version


def test_runtime_resources_do_not_depend_on_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert default_config_path().is_file()
    qr_path = resource_path("src", "gui", "resources", "QR_UPI.jpeg")
    assert qr_path.is_file()
    assert not QImage(str(qr_path)).isNull()
    icon_path = resource_path("src", "gui", "resources", "icon.png")
    icon = QImage(str(icon_path))
    assert not icon.isNull()
    assert (icon.width(), icon.height()) == (1024, 1024)
    assert Path(setup_argument_parser().get_default("config")) == (
        default_config_path()
    )
    assert widgets.__all__ == []


def test_config_default_uses_bundle_root_outside_app_working_directory(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    values = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    values["data_paths"]["base_folder"] = str(tmp_path / "market_data")
    (bundle / "config.yaml").write_text(
        yaml.safe_dump(values),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_paths, "application_root", lambda: bundle)
    monkeypatch.chdir(tmp_path)

    config = Config()
    assert config.config_path == bundle / "config.yaml"
    assert config.base_data_path == (tmp_path / "market_data").resolve()


def test_resource_path_rejects_escape():
    try:
        resource_path("..", "outside")
    except ValueError as error:
        assert "application root" in str(error)
    else:
        raise AssertionError("resource path traversal was accepted")


def test_update_checker_uses_compiled_version_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checker = UpdateChecker()
    assert checker.get_current_version() == get_version()


def test_packaging_manifest_and_macos_command_are_deterministic(tmp_path):
    assert packaging.validate_project() == []
    command = packaging.get_nuitka_command(
        target_platform="darwin",
        output_dir=tmp_path / "dist",
    )
    joined = "\n".join(command)
    assert "--mode=app" in command
    assert "--enable-plugin=pyside6" in command
    assert "config.yaml=config.yaml" in joined
    assert "src/gui/resources" in joined
    assert "--macos-app-mode=gui" in command
    assert f"--macos-signed-app-name={MACOS_BUNDLE_ID}" in command
    assert "--macos-prohibit-multiple-instances" in command
    assert any(value.endswith("/icon.icns") for value in command)
    assert not any("Market Holidays" in value for value in command)
    assert not any("version.py=" in value for value in command)


def test_cross_platform_commands_disable_windows_console(tmp_path):
    windows = packaging.get_nuitka_command(
        target_platform="windows",
        output_dir=tmp_path / "windows",
    )
    linux = packaging.get_nuitka_command(
        target_platform="linux",
        output_dir=tmp_path / "linux",
        standalone_folder=True,
    )
    assert "--mode=onefile" in windows
    assert "--windows-console-mode=disable" in windows
    assert any(value.endswith("/icon.ico") for value in windows)
    assert "--mode=standalone" in linux
    assert any(value.endswith("/icon.png") for value in linux)


def test_macos_notarization_signing_requires_identity(tmp_path):
    with pytest.raises(ValueError, match="requires --macos-sign-identity"):
        packaging.get_nuitka_command(
            target_platform="darwin",
            output_dir=tmp_path,
            macos_sign_notarization=True,
        )

    command = packaging.get_nuitka_command(
        target_platform="darwin",
        output_dir=tmp_path,
        macos_sign_identity="auto",
        macos_sign_notarization=True,
    )
    assert "--macos-sign-identity=auto" in command
    assert "--macos-sign-notarization" in command


def test_release_icons_have_valid_cross_platform_containers():
    resources = Path("src/gui/resources")
    assert packaging.validate_icon_resources(resources) == []
    assert setup_argument_parser().parse_args(["--smoke-gui"]).smoke_gui is True


def test_macos_qt_link_replacements_only_target_bundled_libraries(tmp_path):
    (tmp_path / "QtCore").write_bytes(b"library")
    dependencies = """
        @rpath/QtCore.framework/Versions/A/QtCore
        @rpath/QtSvg.framework/Versions/A/QtSvg
        /usr/lib/libSystem.B.dylib
    """

    assert packaging.macos_qt_link_replacements(dependencies, tmp_path) == [
        (
            "@rpath/QtCore.framework/Versions/A/QtCore",
            "@executable_path/QtCore",
        )
    ]


def test_explicit_macos_sign_identity_does_not_query_keychain():
    assert packaging._resolve_macos_sign_identity("Developer ID") == ("Developer ID")
    assert packaging._resolve_macos_sign_identity(None) == "-"


def test_macos_qt_repair_rewrites_and_resigns_bundle(tmp_path, monkeypatch):
    app = tmp_path / "main.app"
    executable_dir = app / "Contents" / "MacOS"
    plugin = executable_dir / "PySide6" / "qt-plugins" / "platforms" / "libq.dylib"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"plugin")
    (executable_dir / "QtCore").write_bytes(b"library")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        stdout = ""
        if command[:2] == ["otool", "-L"]:
            stdout = "@rpath/QtCore.framework/Versions/A/QtCore\n"
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(packaging.subprocess, "run", fake_run)

    assert packaging.repair_macos_qt_plugin_links(tmp_path) == 1
    assert [
        "install_name_tool",
        "-change",
        "@rpath/QtCore.framework/Versions/A/QtCore",
        "@executable_path/QtCore",
        str(plugin),
    ] in commands
    assert ["codesign", "--force", "--sign", "-", str(plugin)] in commands
    assert ["codesign", "--force", "--sign", "-", str(app)] in commands
    assert [
        "codesign",
        "--verify",
        "--deep",
        "--strict",
        str(app),
    ] in commands


def test_packaging_cli_defaults_to_no_build(capsys):
    assert packaging.main(["--target-platform=darwin"]) == 0
    output = capsys.readouterr().out
    assert "Packaging validation passed" in output
    assert "DRY RUN: no Nuitka build was started." in output


def test_clean_refuses_output_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="inside the project root"):
        packaging.clean_outputs(project, tmp_path / "external-output")
