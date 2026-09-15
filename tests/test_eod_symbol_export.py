"""Phase 5 step 2: symbol histories regenerated from the database.

A daily file is one frame written once.  A history accumulates rows from many
dates under a name the registry chose and may since have changed, and the
engine rescales the whole file when a corporate action lands -- so the raw rows
the database holds are not what the file says.  These tests pin the three
resolutions that follow: which rows belong to a file, what a column's dtype
becomes, and how an applied adjustment is replayed.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pandas as pd

from src.services.eod_export import (
    applied_actions,
    compare_symbol,
    symbol_keys,
    symbol_text,
)
from src.services.eod_store import EodStore
from src.services.symbol_history import SymbolHistoryStore


def _row(day, symbol="OLDNAME", close=100.0, volume=100,
         isin="INE111111111", security_id="123", delivery=80):
    return pd.DataFrame([{
        "SYMBOL": symbol, "DATE": day,
        "OPEN": close, "HIGH": close, "LOW": close, "CLOSE": close,
        "VOLUME": volume, "DELIVERY_QTY": delivery, "DELIVERY_PERCENT": 80.0,
        "SERIES": "EQ", "TOTAL_TRADES": 10, "QTY_PER_TRADE": 10.0,
        "ISIN": isin, "SECURITY_ID": security_id,
        "TURNOVER": close * volume, "PREV_CLOSE": close,
    }])


def _publish(root: Path, day: date, rows: pd.DataFrame, segment="EQ"):
    """Write the history the real way, and mirror the same frame."""

    SymbolHistoryStore(root).upsert("NSE", segment, day, rows)
    store = EodStore(
        root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
    )
    store.upsert_frame("NSE", segment, rows)
    return store


def _registry(root: Path) -> dict:
    return json.loads(
        (root / ".state" / "symbol_registry.json").read_text(encoding="utf-8")
    )


def _history(root: Path, name: str) -> Path:
    return root / "NSE" / "SYMBOLS" / name


def test_a_history_regenerates_byte_for_byte(tmp_path):
    store = _publish(tmp_path, date(2025, 1, 1), _row("20250101"))
    _publish(tmp_path, date(2025, 1, 2), _row("20250102", close=110.0))

    published = _history(tmp_path, "oldname.txt")
    assert compare_symbol(
        store, _registry(tmp_path), published, "NSE"
    ) is None
    assert symbol_text(
        store, _registry(tmp_path), "NSE", "oldname.txt"
    ) == published.read_text()


def test_a_rename_keeps_both_names_pointing_at_one_history(tmp_path):
    """The registry decides which rows share a file; the exporter follows it."""

    store = _publish(tmp_path, date(2025, 1, 1), _row("20250101"))
    _publish(tmp_path, date(2025, 1, 2), _row("20250102", symbol="NEWNAME"))
    registry = _registry(tmp_path)

    published = next((tmp_path / "NSE" / "SYMBOLS").glob("*.txt"))
    keys = symbol_keys(registry, "NSE", published.name)

    assert "ID:123" in keys and "ISIN:INE111111111" in keys
    assert len(pd.read_csv(published)) == 2
    assert compare_symbol(store, registry, published, "NSE") is None


def test_a_security_with_no_identifiers_is_keyed_by_its_name(tmp_path):
    """NSE SME publishes neither ISIN nor security code for any row."""

    store = _publish(
        tmp_path, date(2025, 1, 1),
        _row("20250101", symbol="TINY_SME", isin="", security_id=""),
        segment="SME",
    )
    registry = _registry(tmp_path)

    assert symbol_keys(registry, "NSE", "tiny_sme.txt") == ["SYM:TINY_SME"]
    assert compare_symbol(
        store, registry, _history(tmp_path, "tiny_sme.txt"), "NSE"
    ) is None


def test_a_missing_value_anywhere_makes_the_whole_column_float(tmp_path):
    store = _publish(tmp_path, date(2025, 1, 1), _row("20250101"))
    _publish(
        tmp_path, date(2025, 1, 2),
        _row("20250102", delivery=float("nan")),
    )

    published = _history(tmp_path, "oldname.txt")
    text = published.read_text()
    assert ",80.0," in text          # widened by the gap on the other date
    assert compare_symbol(store, _registry(tmp_path), published, "NSE") is None


def test_an_applied_split_is_replayed_onto_the_raw_rows(tmp_path):
    """The database holds what the exchange published; the file does not.

    When a split is recorded the engine rescales every earlier row, so a
    history with an applied action cannot be regenerated from raw rows alone.
    Without the replay the export writes the unadjusted price -- which is how
    the two real KIRLPNU files were found among 8,078.
    """

    store = _publish(tmp_path, date(2025, 1, 1), _row("20250101", close=200.0))
    _publish(tmp_path, date(2025, 1, 2), _row("20250102", close=100.0))
    published = _history(tmp_path, "oldname.txt")

    ledger = tmp_path / ".state" / "corporate_actions.json"
    ledger.write_text(json.dumps({
        "version": 1, "transactions": {},
        "actions": {"a": {
            "status": "applied", "action_type": "split", "factor": 2.0,
            "ex_date": "2025-01-02", "exchange": "NSE",
            "stable_id": "123", "symbol": "OLDNAME",
        }},
    }), encoding="utf-8")

    # Rescale the published history the way the engine would have.
    from src.services.corporate_actions import adjust_rows
    frame = pd.read_csv(published, dtype=str)
    for column in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
                   "DELIVERY_QTY", "QTY_PER_TRADE"):
        frame[column] = pd.to_numeric(frame[column])
    for column in ("VOLUME", "DELIVERY_QTY"):
        frame[column] = frame[column].astype(object)
    adjust_rows(frame, frame["DATE"] < "20250102", 2.0)
    frame.to_csv(published, index=False, lineterminator="\n")

    actions = applied_actions(tmp_path / ".state")
    assert len(actions) == 1
    assert compare_symbol(
        store, _registry(tmp_path), published, "NSE", actions
    ) is None
    # Without the replay the export would still be carrying the raw 200.0.
    assert compare_symbol(
        store, _registry(tmp_path), published, "NSE", ()
    ) is not None


def test_a_history_the_database_never_saw_is_reported_not_invented(tmp_path):
    _publish(tmp_path, date(2025, 1, 1), _row("20250101"))
    store = EodStore(
        tmp_path / ".state" / "eod.sqlite3", tmp_path / ".state" / "quarantine"
    )
    orphan = _history(tmp_path, "unknown.txt")
    orphan.write_text(
        "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,TOTAL_TRADES,QTY_PER_TRADE,"
        "DELIVERY_QTY,DELIVERY_PERCENT,ISIN,TURNOVER,PREV_CLOSE\n"
        "20250101,1,1,1,1,1,EQ,1,1,1,1,,1,1\n"
    )

    report = compare_symbol(store, _registry(tmp_path), orphan, "NSE")

    assert report is not None and "published 2 lines, exported 1" in report
