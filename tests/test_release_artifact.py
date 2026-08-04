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

    archive_path, checksum_path = release.create_release_archive(
        package,
        target_platform="darwin",
        architecture="arm64",
        output_dir=tmp_path / "release",
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
        assert metadata["git_commit"] == "a" * 40
        executable_name = (
            f"{expected_root}/{PRODUCT_NAME}.app/Contents/MacOS/{APP_NAME}"
        )
        mode = archive.getinfo(executable_name).external_attr >> 16
        assert stat.S_IMODE(mode) == 0o755

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert checksum_path.read_text(encoding="ascii") == (
        f"{digest}  {archive_path.name}\n"
    )


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
