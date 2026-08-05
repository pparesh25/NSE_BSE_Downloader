import hashlib
import os
from pathlib import Path
import stat
import zipfile

from src.utils import update_checker as update_checker_module
from src.utils.update_checker import UpdateChecker, release_platform_key


def _zip_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project-1.2.0/main.py", "print('safe')\n")
    return path.read_bytes()


def test_update_checker_targets_canonical_community_repository():
    checker = UpdateChecker(current_version="1.1.0")

    assert checker.GITHUB_REPOSITORY == "pparesh25/NSE_BSE_Downloader"
    assert checker.version_info_url == (
        "https://raw.githubusercontent.com/pparesh25/"
        "NSE_BSE_Downloader/main/version.py"
    )


def test_update_requires_immutable_url_and_matching_hash(tmp_path, monkeypatch):
    source = tmp_path / "source.zip"
    payload = _zip_bytes(source)
    expected = hashlib.sha256(payload).hexdigest()

    checker = UpdateChecker(current_version="1.1.0")
    configured, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/"
        "releases/download/v1.2.0/app.zip",
        expected,
        "1.2.0",
    )
    assert configured, message

    def fake_download(url, file_path, **kwargs):
        Path(file_path).write_bytes(payload)

    monkeypatch.setattr("src.utils.update_checker.download_to_file_sync", fake_download)
    destination = tmp_path / "update.zip"
    success, result = checker.download_update(destination)

    assert success, result
    assert destination.read_bytes() == payload
    assert not destination.with_suffix(".zip.part").exists()


def test_update_rejects_mutable_branch_and_hash_mismatch(tmp_path, monkeypatch):
    source = tmp_path / "source.zip"
    payload = _zip_bytes(source)
    checker = UpdateChecker(current_version="1.1.0")

    success, message = checker.configure_update_artifact(
        "https://codeload.github.com/pparesh25/NSE_BSE_Downloader/"
        "zip/refs/heads/main",
        hashlib.sha256(payload).hexdigest(),
        "1.2.0",
    )
    assert not success
    assert "immutable" in message.lower()

    success, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/"
        "releases/download/v1.2.0/app.zip",
        "0" * 64,
        "1.2.0",
    )
    assert success, message

    def fake_download(url, file_path, **kwargs):
        Path(file_path).write_bytes(payload)

    monkeypatch.setattr("src.utils.update_checker.download_to_file_sync", fake_download)
    destination = tmp_path / "update.zip"
    success, message = checker.download_update(destination)

    assert not success
    assert "checksum" in message.lower()
    assert not destination.exists()
    assert not destination.with_suffix(".zip.part").exists()


def test_invalid_metadata_clears_previously_valid_artifact():
    checker = UpdateChecker(current_version="1.1.0")
    success, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/"
        "releases/download/v1.2.0/app.zip",
        "1" * 64,
        "1.2.0",
    )
    assert success, message

    success, _ = checker.configure_update_artifact(
        "https://github.com/other/project/releases/download/v9/app.zip",
        "2" * 64,
        "9.0.0",
    )

    assert not success
    assert checker.download_url is None
    assert checker.expected_sha256 is None


def test_version_without_verified_metadata_clears_artifact():
    checker = UpdateChecker(current_version="1.1.0")
    success, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/"
        "releases/download/v1.2.0/app.zip",
        "1" * 64,
        "1.2.0",
    )
    assert success, message

    result = checker._parse_github_version_file('__version__ = "1.3.0"')

    assert result["artifact_verified"] is False
    assert checker.download_url is None
    assert checker.expected_sha256 is None


def test_update_release_tag_must_match_announced_version():
    checker = UpdateChecker(current_version="1.1.0")

    success, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/"
        "releases/download/v1.2.0/app.zip",
        "1" * 64,
        "1.3.0",
    )

    assert not success
    assert "announced version" in message.lower()
    assert checker.download_url is None


def test_update_extraction_rejects_parent_traversal(tmp_path):
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project-1.2.0/main.py", "safe")
        archive.writestr("../outside.txt", "unsafe")

    checker = UpdateChecker(current_version="1.1.0")
    extract_to = tmp_path / "extract"
    extract_to.mkdir()
    sentinel = extract_to / "keep.txt"
    sentinel.write_text("existing installation")
    success, message = checker.extract_update(archive_path, extract_to)

    assert not success
    assert "unsafe" in message.lower()
    assert not (tmp_path / "outside.txt").exists()
    assert sentinel.read_text() == "existing installation"
    assert not (tmp_path / "extract.tmp").exists()


def test_update_extraction_replaces_existing_staging_atomically(tmp_path):
    archive_path = tmp_path / "valid.zip"
    _zip_bytes(archive_path)
    extract_to = tmp_path / "extract"
    extract_to.mkdir()
    (extract_to / "old.txt").write_text("old")

    checker = UpdateChecker(current_version="1.1.0")
    success, result = checker.extract_update(archive_path, extract_to)

    assert success, result
    assert (extract_to / "project-1.2.0" / "main.py").is_file()
    assert not (extract_to / "old.txt").exists()
    assert not (tmp_path / "extract.previous").exists()


def test_update_extraction_restores_only_archived_executable_bits(tmp_path):
    archive_path = tmp_path / "executable.zip"
    executable = zipfile.ZipInfo("project-1.2.0/NSE_BSE_Downloader")
    executable.create_system = 3
    executable.external_attr = (stat.S_IFREG | 0o755) << 16
    regular = zipfile.ZipInfo("project-1.2.0/config.yaml")
    regular.create_system = 3
    regular.external_attr = (stat.S_IFREG | 0o666) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(executable, b"binary")
        archive.writestr(regular, b"settings")

    checker = UpdateChecker(current_version="1.1.0")
    success, result = checker.extract_update(archive_path, tmp_path / "extract")

    assert success, result
    extracted_root = Path(result)
    executable_mode = stat.S_IMODE(
        (extracted_root / "NSE_BSE_Downloader").stat().st_mode
    )
    regular_mode = stat.S_IMODE((extracted_root / "config.yaml").stat().st_mode)
    assert executable_mode & 0o111 == 0o111
    assert os.access(extracted_root / "NSE_BSE_Downloader", os.X_OK)
    assert regular_mode & 0o111 == 0
    assert regular_mode & 0o022 == 0


def _version_file(artifacts: str) -> str:
    return (
        '__version__ = "1.2.0"\n'
        '__build_date__ = "2026-08-06"\n'
        f"__update_artifacts__ = {artifacts}\n"
        'VERSION_HISTORY = {"1.2.0": {"features": ["x"], "bug_fixes": ["y"]}}\n'
    )


def test_platform_key_matches_release_archive_names():
    # These are the exact suffixes package_release_artifact.py builds into each
    # archive name, so a published asset and the running client agree.
    assert release_platform_key("Darwin", "arm64") == "darwin-arm64"
    assert release_platform_key("Windows", "AMD64") == "windows-x64"
    assert release_platform_key("Linux", "x86_64") == "linux-x64"
    assert release_platform_key("Darwin", "aarch64") == "darwin-arm64"


def test_artifact_selected_for_running_platform(monkeypatch):
    checker = UpdateChecker(current_version="1.1.0")
    monkeypatch.setattr(
        update_checker_module, "release_platform_key", lambda: "linux-x64"
    )
    content = _version_file(
        '{\n'
        '    "darwin-arm64": {"url": "https://github.com/pparesh25/'
        'NSE_BSE_Downloader/releases/download/v1.2.0/mac.zip",\n'
        '                     "sha256": "' + "a" * 64 + '"},\n'
        '    "linux-x64": {"url": "https://github.com/pparesh25/'
        'NSE_BSE_Downloader/releases/download/v1.2.0/linux.zip",\n'
        '                  "sha256": "' + "b" * 64 + '"},\n'
        '}'
    )

    result = checker._parse_github_version_file(content)

    assert result["artifact_verified"] is True
    assert checker.download_url.endswith("linux.zip")
    assert checker.expected_sha256 == "b" * 64


def test_platform_without_published_artifact_stays_notification_only(monkeypatch):
    checker = UpdateChecker(current_version="1.1.0")
    monkeypatch.setattr(
        update_checker_module, "release_platform_key", lambda: "darwin-x64"
    )
    content = _version_file(
        '{"linux-x64": {"url": "https://github.com/pparesh25/NSE_BSE_Downloader'
        '/releases/download/v1.2.0/linux.zip", "sha256": "' + "b" * 64 + '"}}'
    )

    result = checker._parse_github_version_file(content)

    assert result["latest_version"] == "1.2.0"
    assert result["artifact_verified"] is False
    assert checker.download_url is None
    assert checker.expected_sha256 is None


def test_empty_artifact_mapping_is_notification_only():
    checker = UpdateChecker(current_version="1.1.0")

    result = checker._parse_github_version_file(_version_file("{}"))

    assert result["latest_version"] == "1.2.0"
    assert result["artifact_verified"] is False
    assert checker.download_url is None


def test_malformed_artifact_mapping_does_not_execute_or_configure(
    tmp_path, monkeypatch
):
    checker = UpdateChecker(current_version="1.1.0")
    monkeypatch.setattr(
        update_checker_module, "release_platform_key", lambda: "linux-x64"
    )
    # version.py arrives over the network, so a hostile or corrupted response
    # must be read as data and never evaluated as code.
    marker = tmp_path / "executed"
    content = _version_file(
        '{"linux-x64": {"url": __import__("pathlib").Path('
        f"{str(marker)!r}"
        ').write_text("x"), "sha256": "' + "b" * 64 + '"}}'
    )

    result = checker._parse_github_version_file(content)

    assert not marker.exists(), "remote metadata must never be executed"
    assert result["latest_version"] == "1.2.0"
    assert result["artifact_verified"] is False
    assert checker.download_url is None


def test_artifact_url_rejects_relative_path_segments():
    checker = UpdateChecker(current_version="1.1.0")

    success, message = checker.configure_update_artifact(
        "https://github.com/pparesh25/NSE_BSE_Downloader/releases/download/"
        "v1.2.0/../../../../other/repo/evil.zip",
        "c" * 64,
        "1.2.0",
    )

    assert not success
    assert "relative path" in message
    assert checker.download_url is None
