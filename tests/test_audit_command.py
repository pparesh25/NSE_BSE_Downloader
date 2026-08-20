"""The read-only audit: what it verifies, and that it verifies it read-only.

`--audit` exists because every sha256 written into the pipeline manifest used
to be write-only.  Its one hard constraint is that answering "can I trust this
database?" must not change the database, so the last test here fingerprints the
whole data root around a full run.  That test is not decoration: it is what
caught SQLite's own `mode=ro` rewriting `pipeline_state.sqlite3-shm`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

import main
from src.core.config import Config
from src.services.audit_service import AuditError, DatabaseAudit
from src.services.canonical_data import (
    INTERNAL_EQUITY_COLUMNS,
    LEGACY_SYMBOL_HISTORY_COLUMNS,
    SYMBOL_HISTORY_COLUMNS,
)
from src.services.pipeline_state import PipelineManifest

DAY = date(2026, 7, 30)
EARLIER = date(2026, 7, 29)


def _pin_clock(monkeypatch, day: date) -> None:
    monkeypatch.setattr(
        "src.utils.date_utils.DateUtils.today_ist", classmethod(lambda cls: day)
    )
    monkeypatch.setattr(
        "src.utils.date_utils.DateUtils.is_data_available_time",
        staticmethod(lambda now=None: True),
    )


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Pin "the last session the exchanges published" to the fixture date.

    Coverage is judged up to the newest session that exists, so without this
    every test would grow a stale-tail finding as the real calendar moved on.
    """

    _pin_clock(monkeypatch, DAY)


def _config(start: date = DAY) -> Config:
    """A configuration whose data root is this test's temporary home."""

    config = Config("config.yaml")
    config.date_settings.base_start_date = start.isoformat()
    return config


def _config_file(tmp_path: Path, start: date = DAY) -> str:
    """The shipped configuration, re-pointed at this test's start date."""

    shipped = Path("config.yaml").read_text(encoding="utf-8")
    target = tmp_path / "audit-config.yaml"
    target.write_text(
        shipped.replace(
            'base_start_date: "2025-07-15"', f'base_start_date: "{start}"'
        ),
        encoding="utf-8",
    )
    return str(target)


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
        rows=len(path.read_text(encoding="utf-8").splitlines()),
    )
    return manifest


@pytest.fixture
def healthy(tmp_path):
    """A data root holding one recorded, verifiable NSE_FO date."""

    config = _config()
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

    config = _config()
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

    assert {finding.category for finding in report.findings} == {
        "unpublished-date",
        "incomplete-date",
    }
    assert report.failed


def test_an_unknown_segment_is_refused_before_anything_is_read(tmp_path):
    config = _config()

    with pytest.raises(AuditError) as error:
        DatabaseAudit(config, ["NSE_COMMODITY"])

    assert "NSE_COMMODITY" in str(error.value)


def test_a_root_with_no_pipeline_database_says_so(tmp_path):
    config = _config()

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert report.findings == ()
    assert any("no pipeline database" in note for note in report.notes)


def test_the_audit_writes_nothing_to_the_data_root(healthy, tmp_path):
    """The constraint the whole command exists under, measured end to end."""

    config, _ = healthy
    root = config.base_data_path
    before = _fingerprint(root)

    assert main.run_audit_mode(_config_file(tmp_path), []) == 0

    assert _fingerprint(root) == before


def test_the_command_separates_a_broken_database_from_broken_data(
    healthy, tmp_path
):
    config, published = healthy
    config_file = _config_file(tmp_path)
    assert main.run_audit_mode(config_file, ["NSE_FO"]) == 0

    published.write_text("SYMBOL,9,9,9\n", encoding="utf-8")
    assert main.run_audit_mode(config_file, ["NSE_FO"]) == 1

    database = config.base_data_path / ".state" / "pipeline_state.sqlite3"
    database.write_bytes(b"not a database")
    assert main.run_audit_mode(config_file, []) == 2


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
    config = _config()
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "FO",
        DAY,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark("NSE", "FO", DAY, "downloaded", "complete")
    manifest.mark("NSE", "FO", DAY, "validated", "complete", rows=1)
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
    config = _config()
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
    manifest.mark("NSE", "FO", DAY, "downloaded", "complete")
    manifest.mark("NSE", "FO", DAY, "validated", "complete", rows=1)
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
    config = _config()

    manifest = PipelineManifest(config.base_data_path)
    manifest.begin(
        "NSE",
        "FO",
        DAY,
        ("downloaded", "validated", "daily"),
        ("delivery", "symbols", "actions", "combined"),
    )
    manifest.mark("NSE", "FO", DAY, "downloaded", "complete")
    manifest.mark("NSE", "FO", DAY, "validated", "complete", rows=1)
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


def _categories(report, category: str) -> list:
    return [
        finding for finding in report.findings if finding.category == category
    ]


def test_a_truncated_head_is_reported_against_the_configured_start(tmp_path):
    # The defect this replaces: `get_missing_file_dates` looks only between
    # the first and last filename, so a database that never went back far
    # enough looks complete to it.
    config = _config(date(2026, 7, 27))
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")
    _record_simple(config.base_data_path, "FO", DAY, published)

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    head = _categories(report, "head-gap")
    assert len(head) == 1
    assert "2026-07-27 to 2026-07-29" in head[0].message
    assert report.failed


def test_a_stale_tail_is_visible_without_failing_the_audit(
    monkeypatch, tmp_path
):
    config = _config()
    published = _publish(config, "FO", DAY, "SYMBOL,1,2,3\n")
    _record_simple(config.base_data_path, "FO", DAY, published)
    _pin_clock(monkeypatch, date(2026, 8, 7))

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    tail = _categories(report, "stale-tail")
    assert len(tail) == 1
    # Freshness is the owner's business; only damage fails the command.
    assert tail[0].severity == "notice"
    assert "2026-07-31 to 2026-08-07" in tail[0].message
    assert not report.failed


def test_a_missing_day_inside_the_published_range_is_reported(tmp_path):
    config = _config(date(2026, 7, 28))
    for day in (date(2026, 7, 28), DAY):
        published = _publish(config, "FO", day, "SYMBOL,1,2,3\n")
        _record_simple(config.base_data_path, "FO", day, published)

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    missing = _categories(report, "missing-dates")
    assert len(missing) == 1
    assert "2026-07-29" in missing[0].message
    assert report.failed


def test_a_holiday_is_not_mistaken_for_a_missing_day(monkeypatch, tmp_path):
    # 26 June 2026 is a trading holiday in the bundled calendar, with a
    # weekend behind it.  Judging the gap without a calendar would invent
    # three missing sessions here.
    _pin_clock(monkeypatch, date(2026, 6, 29))
    config = _config(date(2026, 6, 25))
    for day in (date(2026, 6, 25), date(2026, 6, 29)):
        published = _publish(config, "FO", day, "SYMBOL,1,2,3\n")
        _record_simple(config.base_data_path, "FO", day, published)

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert report.findings == ()


def test_a_date_the_exchange_never_published_is_not_a_gap(tmp_path):
    config = _config(date(2026, 7, 28))
    for day in (date(2026, 7, 28), DAY):
        published = _publish(config, "FO", day, "SYMBOL,1,2,3\n")
        _record_simple(config.base_data_path, "FO", day, published)
    PipelineManifest(config.base_data_path).skip_date(
        "NSE", "FO", date(2026, 7, 29), "The exchange published no report"
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert _categories(report, "missing-dates") == []
    assert not report.failed


def test_a_year_with_no_trading_calendar_is_declined_not_guessed(
    monkeypatch, tmp_path
):
    # The bundled calendar starts in 2013.  Treating an unknown year as
    # holiday-free would report every Diwali in it as a missing session.
    _pin_clock(monkeypatch, date(2005, 6, 30))
    config = _config(date(2005, 6, 1))
    published = _publish(config, "FO", date(2005, 6, 30), "SYMBOL,1,2,3\n")
    _record_simple(config.base_data_path, "FO", date(2005, 6, 30), published)

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert _categories(report, "missing-dates") == []
    assert _categories(report, "head-gap") == []
    assert any("no trading calendar is known for 2005" in note
               for note in report.notes)


def test_a_truncated_day_is_caught_by_neighbours_when_no_digest_can_be(
    tmp_path,
):
    """The only plausibility check data written before the manifest can get."""

    config = _config(date(2026, 7, 21))
    days = [
        date(2026, 7, day) for day in (21, 22, 23, 24, 27, 28, 29, 30)
    ]
    for day in days:
        rows = 5 if day == date(2026, 7, 28) else 100
        _publish(config, "FO", day, "SYMBOL,1,2,3\n" * rows)

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    outliers = _categories(report, "row-count-outlier")
    assert len(outliers) == 1
    assert outliers[0].target_date == date(2026, 7, 28)
    assert "5 rows is 5% of the 100-row median" in outliers[0].message


def test_a_file_that_gained_rows_since_publication_says_how(healthy):
    config, published = healthy

    published.write_text(
        "SYMBOL,1,2,3\nSYMBOL,4,5,6\n", encoding="utf-8"
    )

    report = DatabaseAudit(config, ["NSE_FO"]).run()

    assert {finding.category for finding in report.findings} == {
        "digest-mismatch",
        "row-count-drift",
    }
    drift = _categories(report, "row-count-drift")[0]
    assert "holds 2 rows but the pipeline recorded 1" in drift.message


# --------------------------------------------------------------------------
# Symbol histories, raw snapshots and orphans
# --------------------------------------------------------------------------

ACME = ("ACME", "INE000A01001", "1")
BOLT = ("BOLT", "INE000B01002", "2")


def _write_snapshot(config, exchange, segment, day, securities) -> Path:
    """One checksummed raw snapshot, written the way the store writes it."""

    folder = config.base_data_path / ".state" / "raw" / exchange / segment
    folder.mkdir(parents=True, exist_ok=True)
    lines = [",".join(INTERNAL_EQUITY_COLUMNS)]
    for symbol, isin, security_id in securities:
        values = {
            "SYMBOL": symbol,
            "DATE": day.strftime("%Y%m%d"),
            "ISIN": isin,
            "SECURITY_ID": security_id,
            "SERIES": "EQ",
        }
        lines.append(
            ",".join(values.get(column, "1") for column in
                     INTERNAL_EQUITY_COLUMNS)
        )
    body = "\n".join(lines) + "\n"
    path = folder / f"{day.isoformat()}.csv"
    path.write_text(body, encoding="utf-8")
    path.with_suffix(".csv.meta.json").write_text(
        json.dumps({
            "version": 1,
            "exchange": exchange,
            "segment": segment,
            "target_date": day.isoformat(),
            "row_count": len(securities),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }),
        encoding="utf-8",
    )
    return path


def _write_history(config, exchange, filename, days, *, legacy=False) -> Path:
    columns = (
        LEGACY_SYMBOL_HISTORY_COLUMNS if legacy else SYMBOL_HISTORY_COLUMNS
    )
    folder = config.base_data_path / exchange / "SYMBOLS"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    for day in days:
        lines.append(
            ",".join([day.strftime("%Y%m%d")] + ["1"] * (len(columns) - 1))
        )
    path = folder / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def symbols_root(tmp_path):
    """Two NSE EQ sessions, published, recorded, snapshotted and historied."""

    config = _config(EARLIER)
    for day in (EARLIER, DAY):
        published = _publish(config, "EQ", day, "ACME,1\nBOLT,1\n")
        _record_simple(config.base_data_path, "EQ", day, published)
        _write_snapshot(config, "NSE", "EQ", day, [ACME, BOLT])
    _write_history(config, "NSE", "acme.txt", [EARLIER, DAY])
    _write_history(config, "NSE", "bolt.txt", [EARLIER, DAY])
    return config


def test_a_consistent_symbol_tree_reports_nothing(symbols_root):
    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    assert report.findings == ()


def test_a_symbol_history_missing_a_snapshot_date_is_caught(symbols_root):
    # Nothing enumerated `<EX>/SYMBOLS/` before this, so a history could lose
    # a session and only a rebuild would ever notice.
    _write_history(symbols_root, "NSE", "bolt.txt", [EARLIER])

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    gaps = _categories(report, "symbol-coverage-gap")
    assert len(gaps) == 1
    assert gaps[0].path.name == "bolt.txt"
    assert DAY.isoformat() in gaps[0].message
    assert report.failed


def test_a_symbol_file_the_snapshots_require_is_reported_when_absent(
    symbols_root,
):
    (symbols_root.base_data_path / "NSE" / "SYMBOLS" / "bolt.txt").unlink()

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    missing = _categories(report, "missing-symbol-file")
    assert len(missing) == 1
    assert "bolt.txt" in missing[0].message


def test_a_renamed_security_is_followed_by_identity_not_by_ticker(
    symbols_root,
):
    """A rename merges the history into the new name's file.

    Resolving an old snapshot row by its own ticker would look for a file the
    merge deleted, and report a healthy database as missing thousands of them.
    """

    history = symbols_root.base_data_path / "NSE" / "SYMBOLS"
    (history / "acme.txt").rename(history / "acmecorp.txt")
    registry = symbols_root.base_data_path / ".state" / "symbol_registry.json"
    registry.write_text(
        json.dumps({
            "version": 3,
            "exchanges": {"NSE": {"ISIN:INE000A01001": "ACMECORP"}},
            "files": {"NSE": {"ACMECORP": "acmecorp.txt"}},
            "identities": {},
        }),
        encoding="utf-8",
    )

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    assert report.findings == ()


def test_a_damaged_raw_snapshot_is_caught_by_its_own_metadata(symbols_root):
    # These digests are written and then never read until a `--rebuild-*`
    # needs them, which is the worst moment to find the snapshot damaged.
    snapshot = (
        symbols_root.base_data_path / ".state" / "raw" / "NSE" / "EQ"
        / f"{DAY.isoformat()}.csv"
    )
    snapshot.write_text(
        snapshot.read_text(encoding="utf-8").replace("BOLT", "BOLTX"),
        encoding="utf-8",
    )

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    assert [
        finding.category for finding in _categories(
            report, "raw-digest-mismatch"
        )
    ] == ["raw-digest-mismatch"]
    assert report.failed


def test_a_raw_snapshot_without_metadata_cannot_be_rebuilt_from(symbols_root):
    (
        symbols_root.base_data_path / ".state" / "raw" / "NSE" / "EQ"
        / f"{DAY.isoformat()}.csv.meta.json"
    ).unlink()

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    assert len(_categories(report, "raw-metadata-missing")) == 1


def test_metadata_left_without_its_snapshot_is_reported(symbols_root):
    (
        symbols_root.base_data_path / ".state" / "raw" / "NSE" / "EQ"
        / f"{DAY.isoformat()}.csv"
    ).unlink()

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    orphans = _categories(report, "orphan-working-file")
    assert any("metadata" in finding.message for finding in orphans)


def test_repeated_dates_in_a_symbol_history_are_reported(symbols_root):
    _write_history(symbols_root, "NSE", "acme.txt", [EARLIER, DAY, DAY])

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    duplicates = _categories(report, "duplicate-symbol-dates")
    assert len(duplicates) == 1
    assert DAY.isoformat() in duplicates[0].message


def test_a_symbol_history_with_an_unknown_header_is_refused(symbols_root):
    path = symbols_root.base_data_path / "NSE" / "SYMBOLS" / "acme.txt"
    path.write_text("WHO,KNOWS\n20260730,1\n", encoding="utf-8")

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    assert len(_categories(report, "symbol-schema-unknown")) == 1


def test_the_older_symbol_schema_is_noted_rather_than_failed(symbols_root):
    _write_history(
        symbols_root, "NSE", "acme.txt", [EARLIER, DAY], legacy=True
    )

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    legacy = _categories(report, "legacy-symbol-schema")
    assert len(legacy) == 1
    assert legacy[0].severity == "notice"
    assert not report.failed


def test_a_symbol_file_no_snapshot_covers_is_a_notice(symbols_root):
    _write_history(symbols_root, "NSE", "ghost.txt", [EARLIER])

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    unaccounted = _categories(report, "unaccounted-symbol-file")
    assert len(unaccounted) == 1
    assert "ghost.txt" in unaccounted[0].message
    assert not report.failed


def test_a_working_file_for_an_unpublished_date_is_an_orphan(symbols_root):
    stray = date(2026, 7, 28)
    _write_snapshot(symbols_root, "NSE", "EQ", stray, [ACME])

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    orphans = [
        finding for finding in _categories(report, "orphan-working-file")
        if "raw snapshot" in finding.message
    ]
    assert len(orphans) == 1
    assert stray.isoformat() in orphans[0].message


def test_a_registry_naming_a_file_that_does_not_exist_is_reported(
    symbols_root,
):
    registry = symbols_root.base_data_path / ".state" / "symbol_registry.json"
    registry.write_text(
        json.dumps({
            "version": 3,
            "exchanges": {},
            "files": {"NSE": {"GONE": "gone.txt"}},
            "identities": {},
        }),
        encoding="utf-8",
    )

    report = DatabaseAudit(symbols_root, ["NSE_EQ"]).run()

    missing = _categories(report, "missing-registered-file")
    assert len(missing) == 1
    assert "gone.txt" in missing[0].message
