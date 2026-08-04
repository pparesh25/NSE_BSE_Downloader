"""Reproducible offline Phase 7.0 baseline harness.

Usage: python tools/phase7_benchmark.py --output .phase7-baseline
The harness uses an isolated temporary-like output root and the existing
CombinedFileBuilder, so it measures current persistence/reconciliation bytes
without touching ~/NSE_BSE_Data or making network requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.services.canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from src.services.combined_file_builder import CombinedFileBuilder


def _config(root: Path):
    return SimpleNamespace(
        base_data_path=root,
        get_data_path=lambda exchange, segment: root / exchange / segment,
    )


def _equity(day: date, prefix: str, count: int = 4) -> pd.DataFrame:
    return pd.DataFrame([
        [f"{prefix}{i:03d}", day.strftime("%Y%m%d"), "10", "11", "9", "10", "100", "50", "50"]
        for i in range(count)
    ], columns=EQUITY_DAILY_COLUMNS)


def _index(day: date, count: int = 2) -> pd.DataFrame:
    return pd.DataFrame([
        [f"INDEX{i:03d}", day.strftime("%Y%m%d"), "10", "11", "9", "10", "100"]
        for i in range(count)
    ], columns=INDEX_DAILY_COLUMNS)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(days: int, mode: str, output: Path) -> dict:
    root = output / f"{mode}-{days}"
    root.mkdir(parents=True, exist_ok=True)
    builder = CombinedFileBuilder(_config(root))
    started = time.perf_counter()
    rows = 0
    for offset in range(days):
        day = date(2026, 1, 1) + timedelta(days=offset)
        segment = "EQ" if mode == "single_segment" else "EQ"
        snapshot = builder.save_component("NSE", segment, day, _equity(day, "EQ"))
        rows += snapshot.rows
        if mode == "select_all":
            builder.save_component("NSE", "SME", day, _equity(day, "SME", 2))
            builder.save_component("NSE", "INDEX", day, _index(day))
            result = builder.reconcile("NSE", day, ("SME", "INDEX"))
            rows += result.rows
        else:
            builder.reconcile("NSE", day, ())
    files = sorted(root.rglob("*.txt"))
    parity = []
    for path in files:
        frame = pd.read_csv(path, header=None, names=EQUITY_DAILY_COLUMNS)
        parity.append({"sha256": _digest(path), "rows": len(frame), "columns": list(frame.columns)})
    return {
        "days": days, "mode": mode, "wall_seconds": time.perf_counter() - started,
        "component_rows": rows, "outputs": parity,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="phase7-baseline-")
        output = Path(temporary.name)
    results = [run_case(days, mode, output) for days in (1, 20, 101) for mode in ("single_segment", "select_all")]
    report = output / "benchmark-results.json"
    report.write_text(json.dumps({"cases": results}, indent=2, sort_keys=True), encoding="utf-8")
    print(report)
    if temporary:
        temporary.cleanup()


if __name__ == "__main__":
    main()
