import hashlib
from pathlib import Path
import zipfile

from src.utils.update_checker import UpdateChecker


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
