"""The read-only audit: what it verifies, and that it verifies it read-only.

`--audit` exists because every sha256 written into the pipeline manifest used
to be write-only.  Its one hard constraint is that answering "can I trust this
database?" must not change the database, so the last test here fingerprints the
whole data root around a full run.  That test is not decoration: it is what
caught SQLite's own `mode=ro` rewriting `pipeline_state.sqlite3-shm`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

import main
from src.core.config import Config
from src.services.audit_service import AuditError, DatabaseAudit
from src.services.pipeline_state import PipelineManifest

DAY = date(2026, 7, 30)
EARLIER = date(2026, 7, 29)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(root: Path) -> tuple[tuple[str, int, int, str], ...]:
    """Path, mtime, size and content digest of everything under ``root``."""

    entries = []
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        content = _digest(path) if path.is_file() else ""
        entries.append((str(path), stat.st_mtime_ns, stat.st_size, content))
    return tuple(entries)


def _publish(config: Config, segment: str, target_date: date, body: str) -> Path:
    """Write one published file the way a completed download leaves it."""

    folder = config.get_data_path("NSE", segment)
    path = folder / f"{target_date.isoformat()}-NSE-{segment}.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _record_simple(base: Path, segment: str, target_date: date, path: Path):
    """Record one date whose published file carries its own digest."""

    manifest = PipelineManifest(base)
    manifest.begin(
        "NSE",
        segment,
        target_date,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark("NSE", segment, target_date, "downloaded", "complete")
    manifest.mark("NSE", segment, target_date, "validated", "complete", rows=1)
    manifest.mark(
        "NSE",
        segment,
        target_date,
        "daily",
        "complete",
        path=str(path),
        sha256=_digest(path),
        rows=1,
    )
    return manifest


@pytest.fixture
def healthy(tmp_path):
    """A data root holding one recorded, verifiable NSE_FO date."""

    config = Config("config.yaml")
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")
    _record_simple(config.base_data_path, "FO", DAY, published)
    return config, published


def test_a_healthy_segment_verifies_and_reports_nothing(healthy):
    config, _ = healthy

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert report.findings == ()
    assert not report.failed
    assert report.summaries[0].records == 1
    assert report.summaries[0].files == 1
    assert report.summaries[0].digests_verified == 1
    assert "No problems found." in report.render()


def test_a_rewritten_file_is_caught_by_its_recorded_digest(healthy):
    config, published = healthy

    published.write_text("SYMBOL,9,9,9\n", encoding="utf-8")

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "digest-mismatch"
    ]
    assert report.findings[0].severity == "error"
    assert report.findings[0].target_date == DAY
    assert report.failed


def test_a_deleted_file_is_caught_even_though_the_record_survives(healthy):
    config, published = healthy

    published.unlink()

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == ["missing-file"]
    assert report.summaries[0].digests_verified == 0
    assert report.failed


def test_a_file_no_record_vouches_for_is_reported(healthy):
    config, _ = healthy

    _publish(config, "FO", date(2026, 7, 31), "SYMBOL,1,2,3\n")

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "unrecorded-file"
    ]
    assert report.findings[0].severity == "warning"
    assert report.findings[0].target_date == date(2026, 7, 31)


def test_data_older_than_the_manifest_is_a_notice_rather_than_a_failure(
    healthy,
):
    # A user upgrading from v1.0.1 has years of files predating the manifest.
    # Nothing can vouch for them, but they are not evidence of damage.
    config, _ = healthy

    _publish(config, "FO", EARLIER, "SYMBOL,1,2,3\n")

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.severity for finding in report.findings] == ["notice"]
    assert report.findings[0].category == "unrecorded-file"
    assert not report.failed


def test_a_leftover_temporary_file_shows_an_interrupted_write(healthy):
    config, published = healthy

    (published.parent / f"{published.name}.tmp").write_text(
        "half", encoding="utf-8"
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "interrupted-write"
    ]
    assert report.failed


def _record_deferred(config: Config, component: Path, published: Path) -> None:
    """Record an EQ date published by the combined stage, as NSE EQ is."""

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "EQ",
        DAY,
        ("downloaded", "validated", "daily", "combined"),
        ("delivery", "symbols", "actions"),
    )
    manifest.mark("NSE", "EQ", DAY, "downloaded", "complete")
    manifest.mark("NSE", "EQ", DAY, "validated", "complete", rows=1)
    manifest.mark(
        "NSE",
        "EQ",
        DAY,
        "daily",
        "complete",
        path=str(published),
        # Deferred publication records the *component's* digest here.
        sha256=_digest(component),
        component_path=str(component),
        component_sha256=_digest(component),
        publication_deferred=True,
        rows=1,
    )
    manifest.mark(
        "NSE",
        "EQ",
        DAY,
        "combined",
        "complete",
        path=str(published),
        sha256=_digest(published),
        rows=3,
        components=["EQ", "SME", "INDEX"],
    )


@pytest.fixture
def deferred(tmp_path):
    """An NSE EQ date whose published file legitimately outgrew its component.

    The daily stage stores the component digest under ``sha256`` and the
    combined stage appends SME and Index rows afterwards.  Comparing the daily
    digest against the file on disk would report every such date as corrupt,
    which is the false positive this fixture exists to pin down.
    """

    config = Config("config.yaml")
    component_dir = config.base_data_path / ".state" / "components" / "NSE" / "EQ"
    component_dir.mkdir(parents=True)
    component = component_dir / f"{DAY.isoformat()}.csv"
    component.write_text("EQ\n", encoding="utf-8")

    published = _publish(config, "EQ", DAY, "EQ\nSME\nINDEX\n")
    _record_deferred(config, component, published)
    return config, component, published


def test_an_appended_combined_file_is_verified_not_rejected(deferred):
    config, _, _ = deferred

    report = DatabaseAudit(config, ["NSE_EQ"]).run()

    assert report.findings == ()
    # The component and the combined file, each against its own digest.
    assert report.summaries[0].digests_verified == 2


def test_corrupting_the_combined_file_is_still_caught(deferred):
    config, _, published = deferred

    published.write_text("EQ\nSME\nTAMPERED\n", encoding="utf-8")

    report = DatabaseAudit(config, ["NSE_EQ"]).run()

    assert [finding.category for finding in report.findings] == [
        "digest-mismatch"
    ]
    assert "combined file" in report.findings[0].message


def test_a_date_that_never_reached_publication_is_reported(deferred):
    config, component, published = deferred
    published.unlink()

    manifest = PipelineManifest(config.base_data_path)
    manifest.mark("NSE", "EQ", DAY, "combined", "failed", error="interrupted")

    report = DatabaseAudit(config, ["NSE_EQ"]).run()

    assert [finding.category for finding in report.findings] == [
        "unpublished-date"
    ]
    assert report.failed


def test_an_unknown_segment_is_refused_before_anything_is_read(tmp_path):
    config = Config("config.yaml")

    with pytest.raises(AuditError) as error:
        DatabaseAudit(config, ["NSE_COMMODITY"])

    assert "NSE_COMMODITY" in str(error.value)


def test_a_root_with_no_pipeline_database_says_so(tmp_path):
    config = Config("config.yaml")

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert report.findings == ()
    assert any("no pipeline database" in note for note in report.notes)


def test_the_audit_writes_nothing_to_the_data_root(healthy):
    """The constraint the whole command exists under, measured end to end."""

    config, _ = healthy
    root = config.base_data_path
    before = _fingerprint(root)

    assert main.run_audit_mode("config.yaml", []) == 0

    assert _fingerprint(root) == before


def test_the_command_separates_a_broken_database_from_broken_data(healthy):
    config, published = healthy
    assert main.run_audit_mode("config.yaml", ["NSE_FO"]) == 0

    published.write_text("SYMBOL,9,9,9\n", encoding="utf-8")
    assert main.run_audit_mode("config.yaml", ["NSE_FO"]) == 1

    database = config.base_data_path / ".state" / "pipeline_state.sqlite3"
    database.write_bytes(b"not a database")
    assert main.run_audit_mode("config.yaml", []) == 2


def test_the_parser_distinguishes_no_audit_from_a_whole_root_audit():
    parser = main.setup_argument_parser()

    assert parser.parse_args([]).audit is None
    assert parser.parse_args(["--audit"]).audit == []
    assert parser.parse_args(["--audit", "NSE_EQ", "BSE_EQ"]).audit == [
        "NSE_EQ",
        "BSE_EQ",
    ]


def test_a_record_that_cannot_be_parsed_is_reported_not_quarantined(healthy):
    # The ordinary state readers copy anything they cannot parse into
    # `.state/quarantine`.  That is a write, so the audit reports instead.
    import sqlite3

    config, _ = healthy
    database = config.base_data_path / ".state" / "pipeline_state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO pipeline_dates(record_key, exchange, segment, "
        "target_date, complete, skipped_reason, updated_at, record_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "NSE_FO:2026-07-28",
            "NSE",
            "FO",
            "2026-07-28",
            0,
            None,
            "2026-07-28T00:00:00+00:00",
            "{ this is not json",
        ),
    )
    connection.commit()
    connection.close()

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "unreadable-record"
    ]
    assert not (config.base_data_path / ".state" / "quarantine").exists()


def test_a_complete_stage_with_no_digest_is_a_finding_in_itself(tmp_path):
    config = Config("config.yaml")
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "FO",
        DAY,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark(
        "NSE", "FO", DAY, "daily", "complete", path=str(published), rows=1
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "incomplete-record"
    ]
    assert "no usable sha256" in report.findings[0].message


def test_a_data_root_that_moved_is_matched_by_layout_not_by_path(tmp_path):
    # Recorded paths are absolute and were written wherever the data root was
    # at the time.  A restored backup must not report every file as missing.
    config = Config("config.yaml")
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")
    elsewhere = Path("/somewhere/else/NSE_BSE_Data/NSE/FO") / published.name

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "FO",
        DAY,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark(
        "NSE",
        "FO",
        DAY,
        "daily",
        "complete",
        path=str(elsewhere),
        sha256=_digest(published),
        rows=1,
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert report.findings == ()
    assert report.summaries[0].digests_verified == 1
    assert any("different path" in note for note in report.notes)


def test_a_file_outside_the_naming_contract_is_noted(healthy):
    config, published = healthy
    (published.parent / "notes.txt").write_text("mine", encoding="utf-8")

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "unrecognised-file"
    ]
    assert not report.failed


def test_a_running_download_is_flagged_as_making_findings_transient(healthy):
    import json
    import os
    import socket

    config, _ = healthy
    lock = config.base_data_path / ".state" / "app.lock"
    lock.write_text(
        json.dumps({"pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert any("holds the data-root lock" in note for note in report.notes)
    assert "Note: another copy of the application" in report.render()


def test_a_complete_stage_with_no_path_is_a_finding_in_itself(tmp_path):
    config = Config("config.yaml")

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "FO",
        DAY,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark(
        "NSE", "FO", DAY, "daily", "complete", component_sha256="a" * 64
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    # One for the component the record half-describes, one for the published
    # file it claims to have written: neither can be checked against anything.
    assert {finding.category for finding in report.findings} == {
        "incomplete-record"
    }
    assert [
        finding.message for finding in report.findings
    ] == [
        "the reconciliation component is recorded complete but with no path",
        "the published file is recorded complete but with no path",
    ]


def test_a_filename_carrying_an_impossible_date_is_reported(healthy):
    config, published = healthy
    (published.parent / "2026-02-30-NSE-FO.txt").write_text(
        "x", encoding="utf-8"
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert [finding.category for finding in report.findings] == [
        "unrecognised-file"
    ]
    assert report.findings[0].severity == "warning"


def test_records_for_a_segment_this_version_does_not_publish_are_noted(healthy):
    import json
    import sqlite3

    config, _ = healthy
    record = {
        "exchange": "NSE",
        "segment": "COMMODITY",
        "date": DAY.isoformat(),
        "required_stages": ["downloaded"],
        "stages": {"downloaded": {"status": "complete"}},
        "complete": True,
    }
    database = config.base_data_path / ".state" / "pipeline_state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO pipeline_dates(record_key, exchange, segment, "
        "target_date, complete, skipped_reason, updated_at, record_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            f"NSE_COMMODITY:{DAY.isoformat()}",
            "NSE",
            "COMMODITY",
            DAY.isoformat(),
            1,
            None,
            "2026-07-30T00:00:00+00:00",
            json.dumps(record),
        ),
    )
    connection.commit()
    connection.close()

    report = DatabaseAudit(config).run()

    unknown = [
        finding for finding in report.findings
        if finding.category == "unknown-segment"
    ]
    assert len(unknown) == 1
    assert not report.failed
