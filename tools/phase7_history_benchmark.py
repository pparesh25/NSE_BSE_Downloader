"""Reproducible incremental-reference versus batched history benchmark."""

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
    reference_root = case / "incremental-reference"
    batched_root = case / "batched"
    reference = CountingStore(reference_root)
    batched = CountingStore(batched_root)
    start = date(2025, 1, 1)
    seed = _rows(start, False)
    reference.upsert("NSE", "EQ", start, seed)
    batched.upsert("NSE", "EQ", start, seed)
    reference.read_calls = reference.write_calls = 0
    batched.read_calls = batched.write_calls = 0
    items = []
    started = time.perf_counter()
    for offset in range(1, day_count + 1):
        target_date = start + timedelta(days=offset)
        rows = _rows(target_date, offset >= day_count // 2)
        reference.upsert("NSE", "EQ", target_date, rows)
        items.append(HistoryBatchItem("NSE", "EQ", target_date, rows))
    reference_seconds = time.perf_counter() - started
    started = time.perf_counter()
    batch_result = batched.upsert_batch(items)
    batched_seconds = time.perf_counter() - started
    reference_evidence = _evidence(reference_root)
    batched_evidence = _evidence(batched_root)
    return {
        "days": day_count,
        "symbols": len(batched_evidence),
        "reference_seconds": reference_seconds,
        "batched_seconds": batched_seconds,
        "reference_history_reads": reference.read_calls,
        "reference_history_writes": reference.write_calls,
        "batched_history_reads": batched.read_calls,
        "batched_history_writes": batched.write_calls,
        "batch_reported_reads": batch_result.history_reads,
        "batch_reported_writes": batch_result.history_writes,
        "parity": reference_evidence == batched_evidence,
        "outputs": batched_evidence,
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
