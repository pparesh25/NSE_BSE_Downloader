"""Phase 5 step 4: --audit can check symbol coverage against the database."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import shutil

import pandas as pd

from src.core.config import Config
from src.services.audit_service import DatabaseAudit
from src.services.eod_store import EodStore
from src.services.symbol_history import SymbolHistoryStore

STAMPS = ("20260728", "20260729", "20260730")


def _rows(stamp: str) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "SYMBOL": symbol, "DATE": stamp, "OPEN": 100 + index,
            "HIGH": 101 + index, "LOW": 99 + index, "CLOSE": 100 + index,
            "VOLUME": 1000, "DELIVERY_QTY": 500, "DELIVERY_PERCENT": 50,
            "SERIES": "EQ", "TOTAL_TRADES": 10, "QTY_PER_TRADE": 100,
            "ISIN": f"INE{index:03d}000{index:03d}",
            "SECURITY_ID": f"50000{index}",
        }
        for index, symbol in enumerate(("AAA", "BBB"))
    ])


def _tree(root: Path) -> None:
    histories = SymbolHistoryStore(root)
    store = EodStore(root / ".state" / "eod.sqlite3", root / ".state" / "quarantine")
    for stamp in STAMPS:
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        histories.upsert("NSE", "EQ", day, _rows(stamp))
        store.upsert_frame("NSE", "EQ", _rows(stamp), published=_rows(stamp))


def _categories(report) -> set[str]:
    return {finding.category for finding in report.findings}


def _coverage(report) -> list[tuple]:
    return sorted(
        (finding.category, finding.message)
        for finding in report.findings
        if "symbol" in finding.category or "raw" in finding.category
    )


def _fingerprint(root: Path) -> tuple:
    return tuple(
        (
            str(path), path.stat().st_mtime_ns, path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
        )
        for path in sorted(root.rglob("*"))
    )


def test_symbol_coverage_is_the_same_from_either_source(tmp_path):
    config = Config("config.yaml")
    _tree(config.base_data_path)

    files = DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=False).run()
    database = DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=True).run()

    assert _coverage(database) == _coverage(files)
    assert "database-snapshot-divergence" not in _categories(database)


def test_the_database_alone_still_catches_a_lost_symbol_history(tmp_path):
    """With .state/raw gone, only the database knows bbb.txt is owed."""

    config = Config("config.yaml")
    root = config.base_data_path
    _tree(root)
    shutil.rmtree(root / ".state" / "raw")
    (root / "NSE" / "SYMBOLS" / "bbb.txt").unlink()

    database = DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=True).run()
    files = DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=False).run()

    assert "missing-symbol-file" in _categories(database)
    assert "missing-symbol-file" not in _categories(files)


def test_a_database_that_disagrees_with_the_files_is_an_error(tmp_path):
    config = Config("config.yaml")
    root = config.base_data_path
    _tree(root)
    changed = _rows(STAMPS[-1])
    changed.loc[0, "CLOSE"] = 999
    EodStore(root / ".state" / "eod.sqlite3", root / ".state" / "quarantine").upsert_frame(
        "NSE", "EQ", changed, published=changed
    )

    report = DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=True).run()

    assert "database-snapshot-divergence" in _categories(report)


def test_reading_snapshots_from_the_database_changes_nothing(tmp_path):
    config = Config("config.yaml")
    root = config.base_data_path
    _tree(root)
    before = _fingerprint(root)

    DatabaseAudit(config, ["NSE_EQ"], snapshots_from_database=True).run()

    assert _fingerprint(root) == before
