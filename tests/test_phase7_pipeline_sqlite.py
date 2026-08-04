"""Phase 7.4 transactional pipeline-state acceptance tests."""

from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from src.services.pipeline_state import PipelineManifest
from src.services.state_store import StateCorruptionError


def _legacy_record(target_date: date) -> dict[str, object]:
    return {
        "exchange": "NSE",
        "segment": "EQ",
        "date": target_date.isoformat(),
        "required_stages": ["downloaded", "validated"],
        "stages": {
            "downloaded": {"status": "complete"},
            "validated": {"status": "pending"},
        },
        "complete": False,
        "updated_at": "2026-08-04T00:00:00+00:00",
    }


def _write_legacy_manifest(root: Path, target_date: date) -> tuple[Path, dict]:
    key = f"NSE_EQ:{target_date.isoformat()}"
    payload = {"version": 1, "dates": {key: _legacy_record(target_date)}}
    path = root / ".state" / "pipeline_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path, payload


def test_legacy_json_import_is_exact_read_only_and_wal_enabled(tmp_path):
    day = date(2026, 7, 30)
    legacy_path, expected = _write_legacy_manifest(tmp_path, day)
    original = legacy_path.read_bytes()

    manifest = PipelineManifest(tmp_path)

    assert manifest.manifest_data() == expected
    assert legacy_path.read_bytes() == original
    assert manifest.legacy_backup_path.read_bytes() == original
    with closing(sqlite3.connect(manifest.database_path)) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        imported = connection.execute(
            "SELECT value FROM metadata WHERE key='legacy_json_imported'"
        ).fetchone()
    assert journal_mode == ("wal",)
    assert imported == ("1",)

    manifest.mark("NSE", "EQ", day, "validated", "complete")
    assert legacy_path.read_bytes() == original
    assert manifest.date_result("NSE", "EQ", day).status == "success"


def test_stage_batch_is_atomic_across_dates(tmp_path):
    first = date(2026, 7, 29)
    second = date(2026, 7, 30)
    missing = date(2026, 7, 31)
    manifest = PipelineManifest(tmp_path)
    for day in (first, second):
        manifest.begin("NSE", "EQ", day, ("downloaded",))

    with pytest.raises(ValueError, match="was not started"):
        manifest.mark_many((
            ("NSE", "EQ", first, "downloaded", "complete", {}),
            ("NSE", "EQ", missing, "downloaded", "complete", {}),
        ))
    assert manifest.date_result("NSE", "EQ", first).status == "failed"

    manifest.mark_many((
        ("NSE", "EQ", first, "downloaded", "complete", {"attempt": 1}),
        ("NSE", "EQ", second, "downloaded", "complete", {"attempt": 1}),
    ))
    assert manifest.incomplete_dates("NSE", "EQ") == []


def test_incomplete_dates_use_index_and_survive_restart(tmp_path):
    start = date(2026, 1, 1)
    manifest = PipelineManifest(tmp_path)
    days = [start + timedelta(days=offset) for offset in range(120)]
    for day in days:
        manifest.begin("BSE", "EQ", day, ("downloaded",))
    manifest.mark_many(tuple(
        ("BSE", "EQ", day, "downloaded", "complete", {})
        for day in days[:100]
    ))

    with closing(sqlite3.connect(manifest.database_path)) as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT target_date FROM pipeline_dates
            WHERE exchange=? AND segment=? AND complete=0
              AND skipped_reason IS NULL
            ORDER BY target_date
            """,
            ("BSE", "EQ"),
        ).fetchall()
    assert any("idx_pipeline_incomplete" in row[-1] for row in plan)
    assert PipelineManifest(tmp_path).incomplete_dates("BSE", "EQ") == days[100:]


def test_abrupt_process_exit_rolls_back_uncommitted_stage_change(tmp_path):
    day = date(2026, 7, 30)
    manifest = PipelineManifest(tmp_path)
    manifest.begin("NSE", "EQ", day, ("downloaded",))
    script = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "UPDATE pipeline_dates SET complete=1 WHERE record_key=?",
    (sys.argv[2],),
)
os._exit(23)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(manifest.database_path),
            f"NSE_EQ:{day}",
        ],
        check=False,
    )

    assert result.returncode == 23
    resumed = PipelineManifest(tmp_path)
    assert resumed.incomplete_dates("NSE", "EQ") == [day]
    assert resumed.date_result("NSE", "EQ", day).status == "failed"


def test_corrupt_database_is_preserved_and_legacy_can_recover(tmp_path):
    day = date(2026, 7, 30)
    legacy_path, expected = _write_legacy_manifest(tmp_path, day)
    manifest = PipelineManifest(tmp_path)
    with closing(sqlite3.connect(manifest.database_path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    manifest.database_path.with_name(
        manifest.database_path.name + "-wal"
    ).unlink(missing_ok=True)
    manifest.database_path.with_name(
        manifest.database_path.name + "-shm"
    ).unlink(missing_ok=True)
    corrupted = b"not-a-sqlite-database\x00phase-7.4"
    manifest.database_path.write_bytes(corrupted)

    with pytest.raises(StateCorruptionError) as caught:
        PipelineManifest(tmp_path)
    assert manifest.database_path.read_bytes() == corrupted
    quarantine_path = caught.value.quarantine_path
    assert quarantine_path is not None
    assert quarantine_path.read_bytes() == corrupted
    assert legacy_path.is_file()

    manifest.database_path.unlink()
    manifest.database_path.with_name(
        manifest.database_path.name + "-wal"
    ).unlink(missing_ok=True)
    manifest.database_path.with_name(
        manifest.database_path.name + "-shm"
    ).unlink(missing_ok=True)
    recovered = PipelineManifest(tmp_path)
    assert recovered.manifest_data() == expected
