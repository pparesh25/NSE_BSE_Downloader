import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

import package_release_artifact as release
from app_metadata import APP_NAME, PRODUCT_NAME
from version import __version__


def test_architecture_names_are_stable():
    assert release.normalized_architecture("AMD64") == "x64"
    assert release.normalized_architecture("x86_64") == "x64"
    assert release.normalized_architecture("aarch64") == "arm64"


def test_find_build_output_for_each_platform(tmp_path):
    app = tmp_path / "main.app"
    app.mkdir()
    assert release.find_build_output(tmp_path, "darwin") == app

    app.rmdir()
    windows = tmp_path / f"{APP_NAME}.exe"
    windows.write_bytes(b"windows")
    assert release.find_build_output(tmp_path, "windows") == windows

    windows.unlink()
    linux = tmp_path / APP_NAME
    linux.write_bytes(b"linux")
    assert release.find_build_output(tmp_path, "linux") == linux


def test_missing_build_output_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError, match="No linux Nuitka package"):
        release.find_build_output(tmp_path, "linux")


def test_release_zip_has_one_root_metadata_modes_and_checksum(
    tmp_path, monkeypatch
):
    build = tmp_path / "build"
    package = build / "main.app"
    executable = package / "Contents" / "MacOS" / APP_NAME
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    resource = package / "Contents" / "MacOS" / "config.yaml"
    resource.write_text("data_paths: {}\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("SOURCE_COMMIT", "b" * 40)

    archive_path, checksum_path = release.create_release_archive(
        package,
        target_platform="darwin",
        architecture="arm64",
        output_dir=tmp_path / "release",
        trust_status="notarized",
    )
    release.validate_release_archive(archive_path)

    expected_root = f"{APP_NAME}-{__version__}-darwin-arm64"
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert {Path(name).parts[0] for name in names} == {expected_root}
        metadata = json.loads(
            archive.read(f"{expected_root}/BUILD-METADATA.json")
        )
        assert metadata["application"] == PRODUCT_NAME
        assert metadata["git_commit"] == "b" * 40
        assert metadata["trust_status"] == "notarized"
        executable_name = (
            f"{expected_root}/{PRODUCT_NAME}.app/Contents/MacOS/{APP_NAME}"
        )
        mode = archive.getinfo(executable_name).external_attr >> 16
        assert stat.S_IMODE(mode) == 0o755

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert checksum_path.read_bytes() == (
        f"{digest}  {archive_path.name}\n".encode("ascii")
    )


def test_git_commit_falls_back_to_github_sha(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    assert release._git_commit(tmp_path) == "c" * 40


def test_smoke_test_uses_isolated_gui_config(tmp_path, monkeypatch):
    executable = tmp_path / release.APP_NAME
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "--smoke-gui" in command:
            config_path = Path(command[command.index("--config") + 1])
            config = release.yaml.safe_load(config_path.read_text(encoding="utf-8"))
            Path(config["data_paths"]["base_folder"]).mkdir(parents=True)
        return type("Result", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    release.smoke_test(executable, "linux")

    assert calls[0][0][-1] == "--help"
    assert calls[1][0][-1] == "--verify-tls"
    gui_command, gui_options = next(
        call for call in calls if "--smoke-gui" in call[0]
    )
    assert gui_options["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert gui_options["env"]["HOME"] == gui_options["env"]["USERPROFILE"]


def test_release_zip_rejects_symlinks(tmp_path):
    package = tmp_path / APP_NAME
    package.mkdir()
    target = package / "target"
    target.write_text("target", encoding="utf-8")
    (package / "link").symlink_to(target)

    with pytest.raises(ValueError, match="cannot contain symlinks"):
        release.create_release_archive(
            package,
            target_platform="linux",
            architecture="x64",
            output_dir=tmp_path / "release",
        )


def test_release_zip_rejects_directory_symlinks(tmp_path):
    package = tmp_path / APP_NAME
    package.mkdir()
    target = tmp_path / "outside-directory"
    target.mkdir()
    (package / "linked-directory").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot contain symlinks"):
        release.create_release_archive(
            package,
            target_platform="linux",
            architecture="x64",
            output_dir=tmp_path / "release",
        )
