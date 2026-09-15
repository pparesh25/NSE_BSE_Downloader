"""Phase 5 step 4: --rebuild-* can take its snapshots from the database."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

import pandas as pd

from src.services.eod_store import EodStore
from src.services.rebuild_service import SymbolHistoryRebuilder
from src.services.symbol_history import SymbolHistoryStore


def _rows(stamp: str, close: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "SYMBOL": "ABC", "DATE": stamp, "OPEN": close, "HIGH": close,
        "LOW": close, "CLOSE": close, "VOLUME": 10, "DELIVERY_QTY": 5,
        "DELIVERY_PERCENT": 50, "SERIES": "EQ", "TOTAL_TRADES": 1,
        "QTY_PER_TRADE": 10, "ISIN": "INE111111111", "SECURITY_ID": "500001",
    }])


def _day(stamp: str) -> date:
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def _publish(root: Path, stamp: str, close: int, mirror: bool = True) -> None:
    rows = _rows(stamp, close)
    SymbolHistoryStore(root).upsert("NSE", "EQ", _day(stamp), rows.copy())
    if mirror:
        EodStore(
            root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
        ).upsert_frame("NSE", "EQ", rows.copy(), published=rows.copy())


def _histories(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted((root / "NSE" / "SYMBOLS").glob("*.txt"))
    }


def _damage(root: Path) -> None:
    for path in (root / "NSE" / "SYMBOLS").glob("*.txt"):
        path.write_bytes(b"damaged history")


def test_a_rebuild_from_the_database_writes_what_one_from_files_writes(tmp_path):
    """Proved with .state/raw deleted: a rebuild that still works read the DB."""

    for name in ("files", "database"):
        for stamp, close in (("20250101", 100), ("20250102", 101)):
            _publish(tmp_path / name, stamp, close)
        _damage(tmp_path / name)

    SymbolHistoryRebuilder(tmp_path / "files").rebuild_exchange("NSE")
    shutil.rmtree(tmp_path / "database" / ".state" / "raw")
    SymbolHistoryRebuilder(
        tmp_path / "database", snapshots_from_database=True
    ).rebuild_exchange("NSE")

    assert _histories(tmp_path / "files")
    assert _histories(tmp_path / "database") == _histories(tmp_path / "files")


def test_a_date_only_the_files_hold_makes_the_rebuild_use_the_files(tmp_path):
    """The database fills forward; rebuilding from it alone would drop history."""

    _publish(tmp_path, "20250101", 100, mirror=False)
    _publish(tmp_path, "20250102", 101)
    _damage(tmp_path)

    SymbolHistoryRebuilder(
        tmp_path, snapshots_from_database=True
    ).rebuild_symbol("NSE", "ABC")

    history = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    assert history["DATE"].astype(str).tolist() == ["20250101", "20250102"]


def test_a_rebuild_reads_its_snapshots_once_whatever_the_source(tmp_path):
    """A rebuild asked for every snapshot again for every symbol.

    Measured on copies of the owner's tree: 42-78 ms a call from files and
    223-475 ms from the database, twice per symbol, which projected a full
    rebuild of 8,124 symbols past nine hours.
    """

    for name, from_database in (("files", False), ("database", True)):
        root = tmp_path / name
        for stamp, close in (("20250101", 100), ("20250102", 101)):
            _publish(root, stamp, close)
        rebuilder = SymbolHistoryRebuilder(
            root, snapshots_from_database=from_database
        )
        loads = []
        original = rebuilder._load_snapshots
        rebuilder._load_snapshots = lambda: loads.append(1) or original()

        rebuilder.rebuild_exchange("NSE")

        assert len(loads) == 1, name
