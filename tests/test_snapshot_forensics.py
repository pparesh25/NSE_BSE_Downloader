"""Phase 5 step 4: reading back what an exchange changed when it republished.

The owner chose to keep republish forensics in the database.  A revision nobody
can read is not a kept capability, so these pin the reading half: what it
reports, that it names the change rather than dumping two files, and that it
changes nothing it reads.
"""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

import main
from src.core.config import Config
from src.services.canonical_data import normalize_nse_equity, public_equity
from src.services.eod_store import EodStore
from src.services.snapshot_forensics import report_revisions
from src.services.symbol_history import SymbolHistoryStore

DAY = date(2026, 8, 18)
IDS = {"AAA": 0, "BBB": 1, "CCC": 2, "DDD": 3}


def _frame(symbols, closes=None) -> pd.DataFrame:
    closes = closes or {}
    stamp = DAY.strftime("%d-%b-%Y").upper()
    frame = normalize_nse_equity(pd.DataFrame([
        {
            "SYMBOL": symbol, "SERIES": "EQ",
            "OPEN": 10.0 + IDS[symbol], "HIGH": 120.0,
            "LOW": 1.0, "CLOSE": 11.0 + IDS[symbol],
            "TOTTRDQTY": 100 * (IDS[symbol] + 1), "TOTALTRADES": 10,
            "ISIN": f"INE00000{IDS[symbol]:04d}", "TIMESTAMP": stamp,
            "TOTTRDVAL": 1000.0 * (IDS[symbol] + 1), "PREVCLOSE": 10.5,
        }
        for symbol in symbols
    ]), DAY)
    for symbol, close in closes.items():
        frame.loc[frame["SYMBOL"] == symbol, "CLOSE"] = close
    return frame


def _publish_versions(root: Path, *frames) -> EodStore:
    histories = SymbolHistoryStore(root)
    store = EodStore(root / ".state" / "eod.sqlite3", root / ".state" / "quarantine")
    for moment, frame in enumerate(frames, 1):
        histories.save_internal_snapshot("NSE", "EQ", DAY, frame)
        store._clock = lambda moment=moment: 1_787_000_000.0 + moment * 3600
        store.upsert_frame("NSE", "EQ", frame, published=public_equity(frame))
    return store


def _fingerprint(root: Path) -> tuple:
    return tuple(
        (str(path), path.stat().st_mtime_ns,
         hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "")
        for path in sorted(root.rglob("*"))
    )


def test_a_republish_is_described_as_rows_withdrawn_added_and_changed(tmp_path):
    _publish_versions(
        tmp_path,
        _frame(["AAA", "BBB", "CCC"]),
        _frame(["AAA", "CCC", "DDD"], closes={"AAA": 99.5}),
    )

    report = report_revisions(tmp_path, "nse", "eq", DAY)

    assert report.current_rows == 3
    assert len(report.revisions) == 1
    revision = report.revisions[0]
    assert revision.rows == 3
    assert revision.withdrawn == ("BBB",)
    assert revision.added == ("DDD",)
    assert revision.changed == ("AAA: CLOSE 11.0 -> 99.5",)
    rendered = report.render()
    assert "1 row(s) withdrawn" in rendered and "BBB" in rendered
    assert "AAA: CLOSE 11.0 -> 99.5" in rendered


def test_each_revision_is_compared_with_the_version_that_replaced_it(tmp_path):
    _publish_versions(
        tmp_path,
        _frame(["AAA", "BBB"]),
        _frame(["AAA", "BBB"], closes={"AAA": 50.0}),
        _frame(["AAA", "BBB"], closes={"AAA": 50.0, "BBB": 70.0}),
    )

    report = report_revisions(tmp_path, "NSE", "EQ", DAY)

    newest, oldest = report.revisions
    assert newest.changed == ("BBB: CLOSE 12.0 -> 70.0",)
    assert oldest.changed == ("AAA: CLOSE 11.0 -> 50.0",)
    assert "revision 1, which replaced it" in report.render()


def test_a_written_revision_is_the_superseded_snapshot_byte_for_byte(tmp_path):
    original = _frame(["AAA", "BBB", "CCC"])
    _publish_versions(tmp_path, original, _frame(["AAA", "CCC"]))
    raw = tmp_path / ".state" / "raw_revisions" / "NSE" / "EQ" / DAY.isoformat()
    [kept_file] = list(raw.glob("*.csv"))

    report = report_revisions(tmp_path, "NSE", "EQ", DAY, tmp_path / "out")

    [written] = report.written
    assert written.read_bytes() == kept_file.read_bytes()
    assert kept_file.stem.startswith(written.stem.rsplit("_", 1)[-1])


def test_a_date_never_republished_says_so_plainly(tmp_path):
    _publish_versions(tmp_path, _frame(["AAA"]))

    report = report_revisions(tmp_path, "NSE", "EQ", DAY)

    assert report.revisions == ()
    assert "no superseded snapshots are kept" in report.render()


def test_a_database_older_than_revisions_reads_as_none_kept(tmp_path):
    _publish_versions(tmp_path, _frame(["AAA"]), _frame(["AAA"], closes={"AAA": 5.0}))
    with sqlite3.connect(tmp_path / ".state" / "eod.sqlite3") as connection:
        connection.execute("DROP TABLE snapshot_revisions")

    assert report_revisions(tmp_path, "NSE", "EQ", DAY).revisions == ()


def test_segments_without_snapshots_and_writes_into_state_are_refused(tmp_path):
    _publish_versions(tmp_path, _frame(["AAA"]))

    with pytest.raises(ValueError, match="keeps no snapshot revisions"):
        report_revisions(tmp_path, "NSE", "INDEX", DAY)
    with pytest.raises(ValueError, match=".state"):
        report_revisions(tmp_path, "NSE", "EQ", DAY, tmp_path / ".state" / "out")


def test_the_command_reports_and_changes_nothing_it_reads(tmp_path, capsys):
    root = Config("config.yaml").base_data_path
    _publish_versions(
        root, _frame(["AAA", "BBB"]), _frame(["AAA"], closes={"AAA": 1.5})
    )
    before = _fingerprint(root)

    code = main.run_snapshot_revisions_mode("config.yaml", ["NSE", "EQ", DAY.isoformat()])

    assert code == 0
    output = capsys.readouterr().out
    assert "1 superseded snapshot(s) kept" in output and "BBB" in output
    assert _fingerprint(root) == before


def test_the_command_refuses_what_it_cannot_answer(tmp_path, capsys):
    root = Config("config.yaml").base_data_path

    assert main.run_snapshot_revisions_mode("config.yaml", ["NSE", "EQ"]) == 2
    assert main.run_snapshot_revisions_mode("config.yaml", ["NSE", "EQ", "18-08-2026"]) == 2
    assert main.run_snapshot_revisions_mode("config.yaml", ["NSE", "EQ", DAY.isoformat()]) == 2
    assert "no EOD database yet" in capsys.readouterr().out
    assert not (root / ".state" / "eod.sqlite3").exists()


def test_the_flag_takes_three_or_four_values_and_excludes_the_repairs():
    parser = main.setup_argument_parser()
    assert parser.parse_args(
        ["--snapshot-revisions", "NSE", "EQ", "2026-08-18"]
    ).snapshot_revisions == ["NSE", "EQ", "2026-08-18"]
    assert parser.parse_args([]).snapshot_revisions is None
    with pytest.raises(SystemExit):
        parser.parse_args(["--snapshot-revisions", "NSE", "--rebuild-all"])
