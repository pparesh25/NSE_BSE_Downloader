"""Phase 5 step 4: republish forensics kept in the database, and a faithful mirror.

The owner chose to keep the previous version of a republished bhavcopy rather than
drop it once .state/raw is optional.  Building that exposed a defect it depends
on: an upsert merged a date's rows and never removed one the new frame lacked.
Measured before the fix, after a correction withdrew a row, the published file
held two rows and the mirror three.
"""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.services.canonical_data import normalize_nse_equity, public_equity
from src.services.eod_export import snapshot_text
from src.services.eod_store import EodStore
from src.services.symbol_history import SymbolHistoryStore

DAY = date(2026, 8, 18)
STAMP = 20260818
IDS = {"AAA": 0, "BBB": 1, "CCC": 2}


def _frame(symbols) -> pd.DataFrame:
    stamp = DAY.strftime("%d-%b-%Y").upper()
    return normalize_nse_equity(pd.DataFrame([
        {
            "SYMBOL": symbol, "SERIES": "EQ",
            "OPEN": 10.0 + IDS[symbol], "HIGH": 12.0 + IDS[symbol],
            "LOW": 9.0 + IDS[symbol], "CLOSE": 11.0 + IDS[symbol],
            "TOTTRDQTY": 100 * (IDS[symbol] + 1), "TOTALTRADES": 10,
            "ISIN": f"INE00000{IDS[symbol]:04d}", "TIMESTAMP": stamp,
            "TOTTRDVAL": (11.0 + IDS[symbol]) * 100 * (IDS[symbol] + 1),
            "PREVCLOSE": 10.5 + IDS[symbol],
        }
        for symbol in symbols
    ]), DAY)


def _store(root: Path) -> EodStore:
    return EodStore(root / ".state" / "eod.sqlite3", root / ".state" / "quarantine")


def _publish(root: Path, frame: pd.DataFrame):
    path = SymbolHistoryStore(root).save_internal_snapshot("NSE", "EQ", DAY, frame)
    store = _store(root)
    store.upsert_frame("NSE", "EQ", frame, published=public_equity(frame))
    return store, path


def test_a_row_the_exchange_withdraws_leaves_the_mirror(tmp_path):
    _publish(tmp_path, _frame(["AAA", "BBB", "CCC"]))
    store, path = _publish(tmp_path, _frame(["AAA", "CCC"]))

    rows = store.daily_rows("NSE", "EQ", STAMP)
    assert [row["symbol"] for row in rows] == ["AAA", "CCC"]
    assert store.published_frame("NSE", "EQ", STAMP)["rows"] == len(rows)
    assert snapshot_text(store, "NSE", "EQ", DAY) == path.read_text(encoding="utf-8")


def test_a_republished_date_keeps_its_previous_snapshot(tmp_path):
    """The database twin of .state/raw_revisions, byte for byte."""

    original = _frame(["AAA", "BBB", "CCC"])
    _, path = _publish(tmp_path, original)
    original_bytes = path.read_bytes()
    revised = original.copy()
    revised.loc[0, "DELIVERY_QTY"] = 9.0
    store, _ = _publish(tmp_path, revised)

    revisions = store.snapshot_revisions("NSE", "EQ", STAMP)
    digest = hashlib.sha256(original_bytes).hexdigest()
    assert [revision["sha256"] for revision in revisions] == [digest]
    assert revisions[0]["text"].encode("utf-8") == original_bytes
    raw_copy = (
        tmp_path / ".state" / "raw_revisions" / "NSE" / "EQ" /
        DAY.isoformat() / f"{digest}.csv"
    )
    assert raw_copy.read_bytes() == original_bytes


def test_an_identical_redownload_records_nothing(tmp_path):
    frame = _frame(["AAA", "BBB"])
    _publish(tmp_path, frame)
    store, _ = _publish(tmp_path, frame.copy())

    assert store.snapshot_revisions("NSE", "EQ", STAMP) == []


def test_segments_without_snapshots_keep_no_revisions(tmp_path):
    """Scope matches .state/raw_revisions, which never covered index or futures."""

    def index(close):
        return pd.DataFrame({
            "SYMBOL": ["Nifty 50"], "DATE": ["20260818"], "OPEN": [1.0],
            "HIGH": [2.0], "LOW": [0.5], "CLOSE": [close], "VOLUME": [100.0],
            "TURNOVER": [150.0], "PREV_CLOSE": [1.0],
        })

    store = _store(tmp_path)
    store.upsert_frame("NSE", "INDEX", index(1.5), published=index(1.5))
    store.upsert_frame("NSE", "INDEX", index(1.7), published=index(1.7))

    assert store.snapshot_revisions("NSE", "INDEX", STAMP) == []
    assert store.daily_rows("NSE", "INDEX", STAMP)[0]["close"] == 1.7


def _three_revisions(root: Path) -> EodStore:
    store = _store(root)
    for quantity, moment in ((None, 0.0), (1.0, 1000.0), (2.0, 2000.0), (3.0, 3000.0)):
        frame = _frame(["AAA"])
        if quantity is not None:
            frame.loc[0, "DELIVERY_QTY"] = quantity
        store._clock = lambda moment=moment: moment
        store.upsert_frame("NSE", "EQ", frame, published=public_equity(frame))
    return store


def test_retention_keeps_the_newest_per_date_and_drops_the_expired(tmp_path):
    store = _three_revisions(tmp_path)
    assert len(store.snapshot_revisions("NSE", "EQ", STAMP)) == 3

    removed, removed_bytes, kept = store.prune_snapshot_revisions(
        max_age_days=-1, max_per_date=2, now=3000.0
    )
    assert (removed, kept) == (1, 2) and removed_bytes > 0
    assert [r["superseded_at"] for r in store.snapshot_revisions("NSE", "EQ", STAMP)] == [
        3000.0, 2000.0
    ]

    removed, _bytes, kept = store.prune_snapshot_revisions(
        max_age_days=0, max_per_date=None, now=3000.0
    )
    assert (removed, kept) == (2, 0)


def test_a_run_prunes_database_revisions_by_the_raw_revision_settings(tmp_path):
    from src.gui.main_window import DownloadWorker
    from src.services.pipeline_telemetry import PipelineTelemetry

    store = _three_revisions(tmp_path)
    telemetry = PipelineTelemetry()
    config = SimpleNamespace(
        base_data_path=tmp_path,
        eod_store=store,
        retention_settings=SimpleNamespace(
            raw_revision_days=-1, raw_revision_max_per_date=1
        ),
        pipeline_telemetry=telemetry,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )

    DownloadWorker(config, [])._prune_snapshot_revisions()

    assert len(store.snapshot_revisions("NSE", "EQ", STAMP)) == 1
    exported = tmp_path / "telemetry.jsonl"
    telemetry.export_jsonl(exported)
    assert "eod.sqlite3:snapshot_revisions" in exported.read_text()
