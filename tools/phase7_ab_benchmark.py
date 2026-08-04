"""Reproducible staged publication scale and golden-output benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.services.canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from src.services.date_join_coordinator import DateJoinCoordinator


CACHE_DATE_CAP = 4
START = date(2025, 1, 2)


def _config(root: Path):
    return SimpleNamespace(
        base_data_path=root,
        get_data_path=lambda exchange, segment: root / exchange / segment,
    )


def _equity(day: date, prefix: str, count: int = 4) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                f"{prefix}{index:03d}",
                day.strftime("%Y%m%d"),
                "10",
                "11",
                "9",
                "10",
                "100",
                "50",
                "50",
            ]
            for index in range(count)
        ],
        columns=EQUITY_DAILY_COLUMNS,
    )


def _index(day: date, prefix: str, count: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                f"{prefix}{index:03d}",
                day.strftime("%Y%m%d"),
                "10",
                "11",
                "9",
                "10",
                "100",
            ]
            for index in range(count)
        ],
        columns=INDEX_DAILY_COLUMNS,
    )


def _frames(exchange: str, day: date) -> dict[str, pd.DataFrame]:
    if exchange == "NSE":
        return {
            "EQ": _equity(day, "NSE_EQ"),
            "SME": _equity(day, "NSE_SME", 2),
            "INDEX": _index(day, "NIFTY"),
        }
    return {
        "EQ": _equity(day, "BSE_EQ"),
        "INDEX": _index(day, "SENSEX"),
    }


def _output_evidence(root: Path) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for path in sorted(root.glob("*/EQ/*-EQ.txt")):
        frame = pd.read_csv(
            path,
            header=None,
            names=EQUITY_DAILY_COLUMNS,
            dtype=str,
            keep_default_na=False,
        )
        evidence[str(path.relative_to(root))] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": len(frame),
            "columns": list(frame.columns),
        }
    return evidence


def _expected_evidence(
    days: list[date], mode: str
) -> dict[str, dict[str, object]]:
    exchanges = ("NSE",) if mode == "single_segment" else ("NSE", "BSE")
    dependencies: dict[str, tuple[str, ...]] = {
        "NSE": () if mode == "single_segment" else ("SME", "INDEX"),
        "BSE": ("INDEX",),
    }
    evidence: dict[str, dict[str, object]] = {}
    for exchange in exchanges:
        for target_date in days:
            frames = _frames(exchange, target_date)
            combined = pd.concat([
                frames[segment].reindex(columns=EQUITY_DAILY_COLUMNS)
                for segment in ("EQ", *dependencies[exchange])
            ], ignore_index=True)
            payload = combined.to_csv(index=False, header=False).encode()
            relative = (
                f"{exchange}/EQ/{target_date.isoformat()}-{exchange}-EQ.txt"
            )
            evidence[relative] = {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "rows": len(combined),
                "columns": list(EQUITY_DAILY_COLUMNS),
            }
    return evidence


def _run_staged(root: Path, days: list[date], mode: str) -> dict[str, Any]:
    config = _config(root)
    exchanges = ("NSE",) if mode == "single_segment" else ("NSE", "BSE")
    dependencies: dict[str, tuple[str, ...]] = {
        "NSE": () if mode == "single_segment" else ("SME", "INDEX"),
        "BSE": ("INDEX",),
    }
    coordinator = DateJoinCoordinator(
        config,
        dependencies,
        max_cache_dates=CACHE_DATE_CAP,
    )
    builder = coordinator.builder

    tracemalloc.start()
    started = time.perf_counter()
    for exchange in exchanges:
        segments = ("EQ", *dependencies[exchange])
        if not dependencies[exchange]:
            for target_date in days:
                frame = _frames(exchange, target_date)["EQ"]
                builder.save_component(exchange, "EQ", target_date, frame)
                result = builder.reconcile(exchange, target_date, ())
                if not result.ok:
                    raise RuntimeError(result.error)
            continue
        # Segment-major arrival intentionally holds many dates open and
        # demonstrates that prepared DataFrames stay at the configured cap.
        for segment in segments:
            for target_date in days:
                frame = _frames(exchange, target_date)[segment]
                builder.save_component(exchange, segment, target_date, frame)
                offered = coordinator.offer(
                    exchange, segment, target_date, frame
                )
                if offered is not None and not offered.ok:
                    raise RuntimeError(offered.error)
        unresolved = [result for result in coordinator.finalize() if not result.ok]
        if unresolved:
            raise RuntimeError(unresolved[0].error)
    wall_seconds = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "wall_seconds": wall_seconds,
        "tracemalloc_peak_bytes": peak_bytes,
        "peak_cached_dates": coordinator.peak_cached_dates,
        "outputs": _output_evidence(root),
    }


def run_case(output: Path, day_count: int, mode: str) -> dict[str, Any]:
    days = [START + timedelta(days=offset) for offset in range(day_count)]
    case_root = output / f"{mode}-{day_count}"
    staged = _run_staged(case_root / "staged", days, mode)
    expected = _expected_evidence(days, mode)
    parity = expected == staged["outputs"]
    return {
        "days": day_count,
        "mode": mode,
        "staged": staged,
        "golden_outputs": expected,
        "golden_output_parity": parity,
        "output_count": len(staged["outputs"]),
        "cache_within_cap": staged["peak_cached_dates"] <= CACHE_DATE_CAP,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    temporary = None
    if args.output is None:
        temporary = tempfile.TemporaryDirectory(prefix="phase7-staged-")
        output = Path(temporary.name)
    else:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
    cases = [
        run_case(output, day_count, mode)
        for day_count in (1, 20, 101)
        for mode in ("single_segment", "select_all")
    ]
    report = {
        "cache_date_cap": CACHE_DATE_CAP,
        "cases": cases,
        "acceptance": {
            "golden_output_parity_100_percent": all(
                case["golden_output_parity"] for case in cases
            ),
            "memory_cache_within_cap": all(
                case["cache_within_cap"] for case in cases
            ),
        },
    }
    report_path = output / "staged-benchmark-results.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(report_path)
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
