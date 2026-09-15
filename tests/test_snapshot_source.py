"""Phase 5 step 4: the database can stand in for .state/raw.

Every frame is built by the real normalizer and written by the real snapshot
writer, because what is being proven is that a reader cannot tell the two
sources apart -- and a hand-built frame would only prove the test agrees with
itself.
"""

from __future__ import annotations

from datetime import date
import json

import pandas as pd

from src.services.canonical_data import normalize_nse_equity, public_equity
from src.services.eod_store import EodStore, ReadOnlyEodStore
from src.services.snapshot_source import (
    DatabaseSnapshots,
    RawFileSnapshots,
    SnapshotEntry,
    missing_from,
)
from src.services.symbol_history import SymbolHistoryStore

DAYS = (date(2026, 8, 17), date(2026, 8, 18))
LAYOUT = (("BSE", "EQ"), ("NSE", "EQ"), ("NSE", "SME"))


def _source(symbols, target_date):
    stamp = target_date.strftime("%d-%b-%Y").upper()
    return pd.DataFrame([
        {
            "SYMBOL": symbol, "SERIES": "EQ",
            "OPEN": 10.0 + index, "HIGH": 12.0 + index,
            "LOW": 9.0 + index, "CLOSE": 11.0 + index,
            "TOTTRDQTY": 100 * (index + 1), "TOTALTRADES": 10,
            "ISIN": f"INE00000{index:04d}", "TIMESTAMP": stamp,
            "TOTTRDVAL": (11.0 + index) * 100 * (index + 1),
            "PREVCLOSE": 10.5 + index,
        }
        for index, symbol in enumerate(symbols)
    ])


def _publish(root, exchange, segment, day, symbols, mirror=True):
    internal = normalize_nse_equity(_source(symbols, day), day)
    SymbolHistoryStore(root).save_internal_snapshot(
        exchange, segment, day, internal
    )
    if mirror:
        store = EodStore(
            root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
        )
        store.upsert_frame(
            exchange, segment, internal, published=public_equity(internal)
        )


def _tree(root):
    for day in DAYS:
        for exchange, segment in LAYOUT:
            _publish(root, exchange, segment, day, ["AAA", "BBB", "CCC"])
    raw = RawFileSnapshots(root / ".state" / "raw")
    database = DatabaseSnapshots(
        ReadOnlyEodStore(root / ".state" / "eod.sqlite3")
    )
    return raw, database


def test_both_sources_list_the_same_snapshots_in_the_same_order(tmp_path):
    """A rebuild replays in this order; colliding files are named by it."""

    raw, database = _tree(tmp_path)

    assert len(raw.entries()) == 6
    assert database.entries() == raw.entries()
    assert database.entries("nse") == raw.entries("NSE")


def test_both_sources_yield_identical_frames_and_checksums(tmp_path):
    raw, database = _tree(tmp_path)
    histories = SymbolHistoryStore(tmp_path)

    for entry in raw.entries():
        pd.testing.assert_frame_equal(
            database.frame(entry),
            histories.read_internal_snapshot(raw.path(entry)),
        )
        metadata = json.loads(
            raw.path(entry).with_suffix(".csv.meta.json").read_text()
        )
        assert database.digest(entry) == raw.digest(entry)
        assert database.digest(entry) == metadata["sha256"]


def test_a_date_the_database_never_saw_is_named_not_dropped(tmp_path):
    """A tree older than dual-write has snapshots the database lacks."""

    raw, database = _tree(tmp_path)
    older = date(2026, 8, 14)
    _publish(tmp_path, "NSE", "EQ", older, ["AAA"], mirror=False)

    assert missing_from(database, raw) == [SnapshotEntry("NSE", "EQ", older)]
    assert missing_from(database, raw, "BSE") == []


def test_no_database_means_no_snapshots_and_nothing_created(tmp_path):
    database = DatabaseSnapshots(
        ReadOnlyEodStore(tmp_path / ".state" / "eod.sqlite3")
    )

    assert database.entries() == []
    assert not (tmp_path / ".state" / "eod.sqlite3").exists()
