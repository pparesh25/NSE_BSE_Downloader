"""Phase 5 step 4: the history journal can replay snapshots from the database.

The journal checkpoints every date it queues and verifies each snapshot by
checksum before replaying it, so it is the reader where "the database yields the
same thing as the file" has to hold byte for byte rather than merely value for
value.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pandas as pd

from src.services.eod_store import EodStore
from src.services.history_batch import HistoryBatchCoordinator
from src.services.pipeline_state import PipelineManifest

DAY = date(2025, 1, 2)


def _rows(day: date) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "SYMBOL": symbol, "DATE": day.strftime("%Y%m%d"),
            "OPEN": 100 + index, "HIGH": 101 + index, "LOW": 99 + index,
            "CLOSE": 100 + index, "VOLUME": 1000 + index,
            "DELIVERY_QTY": 500 + index, "DELIVERY_PERCENT": 50,
            "SERIES": "EQ", "TOTAL_TRADES": 10, "QTY_PER_TRADE": 100,
            "ISIN": f"INE{index:03d}000{index:03d}",
            "SECURITY_ID": f"50000{index}",
        }
        for index, symbol in enumerate(("AAA", "BBB", "CCC"))
    ])


def _symbol_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted((root / "NSE" / "SYMBOLS").glob("*.txt"))
    }


def _coordinator(
    root: Path, mirror: Optional[pd.DataFrame], from_database: bool
) -> HistoryBatchCoordinator:
    manifest = PipelineManifest(root)
    manifest.begin("NSE", "EQ", DAY, ("daily", "symbols"))
    manifest.mark("NSE", "EQ", DAY, "daily", "complete")
    config = SimpleNamespace(
        base_data_path=root,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )
    if mirror is not None:
        # Before the offer, as in a real run: dual-write precedes the queue.
        config.eod_store = EodStore(
            root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
        )
        config.eod_store.upsert_frame(
            "NSE", "EQ", mirror.copy(), published=mirror.copy()
        )
    coordinator = HistoryBatchCoordinator(config)
    coordinator.snapshots_from_database = from_database
    return coordinator


def test_a_queued_snapshot_replays_from_the_database_when_it_agrees(tmp_path):
    """Proved by damaging the file after it is journaled.

    A replay that still succeeds, and writes the bytes a file-backed replay
    writes, can only have read the database.
    """

    rows = _rows(DAY)
    control = _coordinator(tmp_path / "files", rows, from_database=False)
    control.offer("NSE", "EQ", DAY, rows.copy())
    control.finalize()

    database = _coordinator(tmp_path / "database", rows, from_database=True)
    snapshot = database.offer("NSE", "EQ", DAY, rows.copy())
    snapshot.write_bytes(snapshot.read_bytes() + b"damaged")
    database.finalize()

    assert database.snapshot_divergences == []
    assert _symbol_bytes(tmp_path / "files")
    assert _symbol_bytes(tmp_path / "database") == _symbol_bytes(
        tmp_path / "files"
    )


def test_a_database_that_disagrees_is_reported_and_the_file_replayed(tmp_path):
    """Divergence is the evidence the setting exists to collect: never silent."""

    rows = _rows(DAY)
    changed = rows.copy()
    changed.loc[0, "CLOSE"] = 999
    coordinator = _coordinator(tmp_path, changed, from_database=True)
    coordinator.offer("NSE", "EQ", DAY, rows.copy())
    coordinator.finalize()

    assert len(coordinator.snapshot_divergences) == 1
    assert "differs" in coordinator.snapshot_divergences[0]
    history = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "aaa.txt")
    assert float(history.loc[0, "CLOSE"]) == 100


def test_without_a_database_the_setting_quietly_replays_the_files(tmp_path):
    rows = _rows(DAY)
    coordinator = _coordinator(tmp_path, None, from_database=True)
    coordinator.offer("NSE", "EQ", DAY, rows.copy())
    coordinator.finalize()

    assert coordinator.snapshot_divergences == []
    assert _symbol_bytes(tmp_path)
