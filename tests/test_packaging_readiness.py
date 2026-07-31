import os
from pathlib import Path

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


def test_runtime_resources_do_not_depend_on_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert default_config_path().is_file()
    qr_path = resource_path("src", "gui", "resources", "QR_UPI.jpeg")
    assert qr_path.is_file()
    assert not QImage(str(qr_path)).isNull()
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
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
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
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
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
    assert "--mode=standalone" in linux


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
