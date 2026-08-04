"""Run an isolated exact-date Select All live soak and record typed outcomes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import Config
from src.gui.main_window import DownloadWorker


SEGMENTS = ("NSE_EQ", "NSE_FO", "NSE_SME", "NSE_INDEX", "BSE_EQ", "BSE_INDEX")
APPEND_OPTIONS = {
    "sme_append_to_eq": True,
    "index_append_to_eq": True,
    "bse_index_append_to_eq": True,
}


def _isolated_config(source: Path, root: Path, engine: str) -> Config:
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data["data_paths"]["base_folder"] = str(root)
    data.setdefault("download_options", {})["pipeline_engine"] = engine
    config_path = root.parent / f"{root.name}-config.yaml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config(str(config_path))


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(0.95 * (len(ordered) - 1))]


def _telemetry_summary(root: Path) -> dict[str, object]:
    path = root / ".state" / "transport_events.jsonl"
    events = []
    if path.is_file():
        events = [json.loads(line) for line in path.read_text().splitlines()]
    lag = [
        float(event["fields"]["lag_ms"])
        for event in events
        if event["kind"] == "event_loop_lag"
    ]
    return {
        "event_count": len(events),
        "attempts": sum(
            event["kind"] == "download_attempt_started" for event in events
        ),
        "retries": sum(event["kind"] == "retry_scheduled" for event in events),
        "event_loop_lag_p95_ms": _percentile_95(lag),
    }


def _public_outputs(root: Path) -> dict[str, dict[str, object]]:
    outputs = {}
    for path in sorted(root.glob("*/*/*.txt")):
        if "SYMBOLS" in path.parts:
            continue
        outputs[str(path.relative_to(root))] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    return outputs


async def _run_once(
    config_path: Path,
    output: Path,
    target_date: date,
    engine: str,
    run_number: int,
) -> dict[str, Any]:
    root = output / f"run-{run_number:02d}"
    config = _isolated_config(config_path, root, engine)
    worker = DownloadWorker(
        config,
        list(SEGMENTS),
        timeout_seconds=config.download_settings.timeout_seconds,
        append_options=APPEND_OPTIONS,
        retry_dates={name: [target_date] for name in SEGMENTS},
    )
    visible_status: list[tuple[str, str]] = []
    visible_errors: list[tuple[str, str]] = []
    worker.status_updated.connect(
        lambda segment, message: visible_status.append((segment, message))
    )
    worker.error_occurred.connect(
        lambda segment, message: visible_errors.append((segment, message))
    )
    started = time.perf_counter()
    returned_success = await worker._run_downloads()
    elapsed = time.perf_counter() - started

    outcomes = {}
    silent_missing = []
    for name in SEGMENTS:
        downloader = worker.downloaders.get(name)
        result = getattr(downloader, "last_segment_result", None)
        matching = [
            item for item in getattr(result, "dates", ())
            if item.target_date == target_date
        ]
        if not matching:
            silent_missing.append(name)
            continue
        item = matching[0]
        outcomes[name] = {
            "status": item.status,
            "completed_stages": list(item.completed_stages),
            "failed_stages": list(item.failed_stages),
            "error": item.error,
        }
    return {
        "run": run_number,
        "engine": engine,
        "date": target_date.isoformat(),
        "wall_seconds": elapsed,
        "returned_success": returned_success,
        "overall_outcome": worker.final_outcome.value,
        "typed_outcomes": outcomes,
        "silent_missing_segments": silent_missing,
        "visible_status_count": len(visible_status),
        "visible_errors": visible_errors,
        "telemetry": _telemetry_summary(root),
        "public_outputs": _public_outputs(root),
    }


async def _run(args: argparse.Namespace, output: Path) -> Path:
    results = []
    report_path = output / "live-soak-results.json"
    for run_number in range(1, args.runs + 1):
        result = await _run_once(
            args.config.resolve(),
            output,
            args.date,
            args.engine,
            run_number,
        )
        results.append(result)
        report_path.write_text(
            json.dumps({"runs": results}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            f"run {run_number}/{args.runs}: {result['overall_outcome']} "
            f"silent={len(result['silent_missing_segments'])}"
        )
    output_sets = [result["public_outputs"] for result in results]
    lags = [
        value
        for result in results
        if (value := result["telemetry"]["event_loop_lag_p95_ms"]) is not None
    ]
    report = {
        "date": args.date.isoformat(),
        "engine": args.engine,
        "runs": results,
        "acceptance": {
            "run_count": len(results),
            "zero_silent_skips": all(
                not result["silent_missing_segments"] for result in results
            ),
            "all_runs_success": all(
                result["returned_success"] for result in results
            ),
            "output_sha_parity": bool(output_sets) and all(
                outputs == output_sets[0] for outputs in output_sets[1:]
            ),
            "max_run_lag_p95_ms": max(lags) if lags else None,
            "median_wall_seconds": statistics.median(
                result["wall_seconds"] for result in results
            ),
        },
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--engine", choices=("legacy", "staged"), default="staged")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    temporary = None
    if args.output is None:
        temporary = tempfile.TemporaryDirectory(prefix="phase7-live-soak-")
        output = Path(temporary.name)
    else:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
    report_path = asyncio.run(_run(args, output))
    print(report_path)
    if temporary is not None:
        temporary.cleanup()


if __name__ == "__main__":
    main()
