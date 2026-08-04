"""Reproducible Phase 7.5 legacy-versus-batched history benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.services.symbol_history import HistoryBatchItem, SymbolHistoryStore


class CountingStore(SymbolHistoryStore):
    def __init__(self, base_data_path: Path):
        super().__init__(base_data_path)
        self.read_calls = 0
        self.write_calls = 0

    def _read_history(self, path: Path) -> pd.DataFrame:
        self.read_calls += 1
        return super()._read_history(path)

    def _write_history(self, path: Path, frame: pd.DataFrame) -> None:
        self.write_calls += 1
        super()._write_history(path, frame)


def _rows(target_date: date, renamed: bool) -> pd.DataFrame:
    rows = []
    for index, original in enumerate(("AAA", "BBB", "CCC")):
        symbol = "AAA-NEW" if renamed and original == "AAA" else original
        rows.append({
            "SYMBOL": symbol,
            "DATE": target_date.strftime("%Y%m%d"),
            "OPEN": 100 + index,
            "HIGH": 101 + index,
            "LOW": 99 + index,
            "CLOSE": 100 + index,
            "VOLUME": 1000 + index,
            "DELIVERY_QTY": 500 + index,
            "DELIVERY_PERCENT": 50,
            "SERIES": "EQ",
            "TOTAL_TRADES": 10,
            "QTY_PER_TRADE": 100,
            "ISIN": f"INE{index}",
            "SECURITY_ID": f"50000{index}",
        })
    return pd.DataFrame(rows)


def _evidence(root: Path) -> dict[str, dict[str, object]]:
    result = {}
    for path in sorted((root / "NSE" / "SYMBOLS").glob("*.txt")):
        frame = pd.read_csv(path, dtype=str)
        result[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": len(frame),
            "columns": list(frame.columns),
        }
    return result


def run_case(output: Path, day_count: int) -> dict[str, object]:
    case = output / f"days-{day_count}"
    legacy_root = case / "legacy"
    staged_root = case / "staged"
    legacy = CountingStore(legacy_root)
    staged = CountingStore(staged_root)
    start = date(2025, 1, 1)
    seed = _rows(start, False)
    legacy.upsert("NSE", "EQ", start, seed)
    staged.upsert("NSE", "EQ", start, seed)
    legacy.read_calls = legacy.write_calls = 0
    staged.read_calls = staged.write_calls = 0
    items = []
    started = time.perf_counter()
    for offset in range(1, day_count + 1):
        target_date = start + timedelta(days=offset)
        rows = _rows(target_date, offset >= day_count // 2)
        legacy.upsert("NSE", "EQ", target_date, rows)
        items.append(HistoryBatchItem("NSE", "EQ", target_date, rows))
    legacy_seconds = time.perf_counter() - started
    started = time.perf_counter()
    batch_result = staged.upsert_batch(items)
    staged_seconds = time.perf_counter() - started
    legacy_evidence = _evidence(legacy_root)
    staged_evidence = _evidence(staged_root)
    return {
        "days": day_count,
        "symbols": len(staged_evidence),
        "legacy_seconds": legacy_seconds,
        "staged_seconds": staged_seconds,
        "legacy_history_reads": legacy.read_calls,
        "legacy_history_writes": legacy.write_calls,
        "staged_history_reads": staged.read_calls,
        "staged_history_writes": staged.write_calls,
        "batch_reported_reads": batch_result.history_reads,
        "batch_reported_writes": batch_result.history_writes,
        "parity": legacy_evidence == staged_evidence,
        "outputs": staged_evidence,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    temporary = None
    if args.output is None:
        temporary = tempfile.TemporaryDirectory(prefix="phase7-history-")
        output = Path(temporary.name)
    else:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
    report = {
        "cases": [run_case(output, days) for days in (20, 100)]
    }
    report_path = output / "history-benchmark-results.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(report_path)
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
