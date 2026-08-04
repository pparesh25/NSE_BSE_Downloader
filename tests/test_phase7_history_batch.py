"""Phase 7.5 batched history, parity and crash-recovery gates."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.gui.main_window import DownloadWorker
from src.services.corporate_actions import (
    CorporateAction,
    CorporateActionClient,
    CorporateActionEngine,
)
from src.services.history_batch import HistoryBatchCoordinator
from src.services.pipeline_state import PipelineManifest
from src.services.pipeline_telemetry import PipelineTelemetry
from src.services.symbol_history import HistoryBatchItem, SymbolHistoryStore


def _rows(day: date, renamed: bool = False) -> pd.DataFrame:
    values = []
    for index, symbol in enumerate(("AAA", "BBB", "CCC")):
        if renamed and symbol == "AAA":
            symbol = "AAA-NEW"
        values.append({
            "SYMBOL": symbol,
            "DATE": day.strftime("%Y%m%d"),
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
    return pd.DataFrame(values)


def _symbol_bytes(root: Path) -> dict[str, bytes]:
    symbols = root / "NSE" / "SYMBOLS"
    return {
        path.name: path.read_bytes()
        for path in sorted(symbols.glob("*.txt"))
    }


def test_new_single_date_batch_fast_path_matches_legacy_bytes(tmp_path):
    day = date(2025, 1, 2)
    legacy_root = tmp_path / "legacy-single"
    batch_root = tmp_path / "batch-single"
    rows = _rows(day)

    SymbolHistoryStore(legacy_root).upsert("NSE", "EQ", day, rows)
    result = SymbolHistoryStore(batch_root).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, rows)
    ])

    assert result.history_writes == 3
    assert _symbol_bytes(batch_root) == _symbol_bytes(legacy_root)


def test_new_single_date_batch_fast_path_keeps_numeric_validation(tmp_path):
    day = date(2025, 1, 2)
    rows = _rows(day).iloc[[0]].astype(object).copy()
    rows.loc[:, "CLOSE"] = "invalid"

    with pytest.raises(ValueError, match="invalid CLOSE"):
        SymbolHistoryStore(tmp_path).upsert_batch([
            HistoryBatchItem("NSE", "EQ", day, rows)
        ])


@pytest.mark.parametrize("day_count", [20, 100])
def test_multi_day_batch_is_byte_equivalent_with_constant_symbol_io(
    tmp_path, monkeypatch, day_count
):
    legacy_root = tmp_path / "legacy"
    batch_root = tmp_path / "batch"
    start = date(2025, 1, 1)
    legacy = SymbolHistoryStore(legacy_root)
    batched = SymbolHistoryStore(batch_root)
    legacy.upsert("NSE", "EQ", start, _rows(start))
    batched.upsert("NSE", "EQ", start, _rows(start))

    legacy_reads = 0
    legacy_writes = 0
    original_read = legacy._read_history
    original_write = legacy._write_history

    def counted_read(path):
        nonlocal legacy_reads
        legacy_reads += 1
        return original_read(path)

    def counted_write(path, frame):
        nonlocal legacy_writes
        legacy_writes += 1
        return original_write(path, frame)

    monkeypatch.setattr(legacy, "_read_history", counted_read)
    monkeypatch.setattr(legacy, "_write_history", counted_write)
    items = []
    for offset in range(1, day_count + 1):
        day = start + timedelta(days=offset)
        rows = _rows(day, renamed=offset >= day_count // 2)
        legacy.upsert("NSE", "EQ", day, rows)
        items.append(HistoryBatchItem("NSE", "EQ", day, rows))

    result = batched.upsert_batch(items)

    assert _symbol_bytes(batch_root) == _symbol_bytes(legacy_root)
    assert legacy_reads >= day_count * 3
    assert legacy_writes >= day_count * 3
    assert result.history_reads <= 4
    assert result.history_writes == 3
    assert not (batch_root / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert (batch_root / "NSE" / "SYMBOLS" / "aaa-new.txt").exists()


def test_history_journal_resumes_only_remaining_symbols_after_failure(
    tmp_path, monkeypatch
):
    root = tmp_path / "recovery"
    clean_root = tmp_path / "clean"
    config = SimpleNamespace(base_data_path=root)
    manifest = PipelineManifest(root)
    days = (date(2025, 1, 1), date(2025, 1, 2))
    for day in days:
        manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
        manifest.mark("NSE", "EQ", day, "daily", "complete")

    coordinator = HistoryBatchCoordinator(config)
    for day in days:
        coordinator.offer("NSE", "EQ", day, _rows(day))

    original_write = coordinator.histories._write_history
    writes = 0

    def fail_second(path, frame):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated history batch crash")
        return original_write(path, frame)

    monkeypatch.setattr(coordinator.histories, "_write_history", fail_second)
    with pytest.raises(OSError, match="simulated history batch crash"):
        coordinator.finalize()
    assert writes == 2

    resumed = HistoryBatchCoordinator(config)
    resumed_writes = 0
    resumed_original = resumed.histories._write_history

    def count_resumed(path, frame):
        nonlocal resumed_writes
        resumed_writes += 1
        return resumed_original(path, frame)

    monkeypatch.setattr(resumed.histories, "_write_history", count_resumed)
    outcomes = resumed.finalize()
    assert len(outcomes) == 1
    assert resumed_writes == 2
    assert manifest.incomplete_dates("NSE", "EQ") == []

    clean = SymbolHistoryStore(clean_root)
    clean.upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, _rows(day)) for day in days
    ])
    assert _symbol_bytes(root) == _symbol_bytes(clean_root)
    with resumed.journal._connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM history_batches"
        ).fetchone()[0]
    assert active == 0


def test_daily_output_remains_partial_until_optional_history_batch_commits(
    tmp_path,
):
    day = date(2025, 1, 2)
    manifest = PipelineManifest(tmp_path)
    manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
    manifest.mark("NSE", "EQ", day, "daily", "complete")
    coordinator = HistoryBatchCoordinator(
        SimpleNamespace(base_data_path=tmp_path)
    )

    coordinator.offer("NSE", "EQ", day, _rows(day))
    before = manifest.date_result("NSE", "EQ", day)
    assert before.status == "partial"
    assert before.completed_stages == ("daily",)

    coordinator.finalize()
    after = manifest.date_result("NSE", "EQ", day)
    assert after.status == "success"
    assert "symbols" in after.completed_stages


def test_batch_delivery_revision_reapplies_audited_action_once(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    first = _rows(date(2025, 1, 1)).iloc[[0]].copy()
    second = _rows(date(2025, 1, 2)).iloc[[0]].copy()
    second.loc[:, ["OPEN", "HIGH", "LOW", "CLOSE"]] = 50
    store.upsert_batch([
        HistoryBatchItem("NSE", "EQ", date(2025, 1, 1), first),
        HistoryBatchItem("NSE", "EQ", date(2025, 1, 2), second),
    ])
    action = CorporateAction(
        "NSE", "AAA", "INE0", date(2025, 1, 2),
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )
    assert CorporateActionEngine(tmp_path).apply([action])["applied"] == 1

    revision = first.copy()
    revision.loc[:, "DELIVERY_QTY"] = 777
    store.upsert_batch([
        HistoryBatchItem("NSE", "EQ", date(2025, 1, 1), revision)
    ])

    history = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "aaa.txt")
    old_day = history.loc[history["DATE"] == 20250101].iloc[0]
    assert float(old_day["CLOSE"]) == 50.0
    assert int(old_day["DELIVERY_QTY"]) == 777
    ledger = CorporateActionEngine(tmp_path)._read_ledger()
    assert ledger["actions"][action.key]["status"] == "applied"


def test_staged_worker_applies_actions_only_after_history_commit(
    tmp_path, monkeypatch
):
    day_one = date(2025, 1, 1)
    day_two = date(2025, 1, 2)
    manifest = PipelineManifest(tmp_path)
    for day in (day_one, day_two):
        manifest.begin(
            "NSE", "EQ", day, ("daily", "symbols", "actions")
        )
        manifest.mark("NSE", "EQ", day, "daily", "complete")

    settings = SimpleNamespace(timeout_seconds=5)
    config = SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=settings,
        stage_executors={},
    )
    coordinator = HistoryBatchCoordinator(config)
    first = _rows(day_one).iloc[[0]].copy()
    second = _rows(day_two).iloc[[0]].copy()
    second.loc[:, ["OPEN", "HIGH", "LOW", "CLOSE"]] = 50
    coordinator.offer("NSE", "EQ", day_one, first)
    coordinator.offer("NSE", "EQ", day_two, second)
    coordinator.register_action_window(
        "NSE",
        "EQ",
        (day_one, day_two),
        add_sme_suffix=True,
        timeout=30,
    )
    config.history_batch_coordinator = coordinator
    action = CorporateAction(
        "NSE", "AAA", "INE0", day_two,
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )

    async def fake_fetch(*args, **kwargs):
        history = tmp_path / "NSE" / "SYMBOLS" / "aaa.txt"
        assert history.exists()
        return [action]

    monkeypatch.setattr(CorporateActionClient, "fetch", fake_fetch)
    worker = DownloadWorker(config, [])
    asyncio.run(worker._finalize_staged_histories())

    for day in (day_one, day_two):
        assert manifest.date_result("NSE", "EQ", day).status == "success"
    history = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "aaa.txt")
    assert float(history.loc[history["DATE"] == 20250101, "CLOSE"].iloc[0]) == 50


def test_staged_action_windows_fetch_concurrently_with_timing_telemetry(
    tmp_path, monkeypatch
):
    day = date(2025, 1, 2)
    telemetry = PipelineTelemetry()
    config = SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
        pipeline_telemetry=telemetry,
    )
    coordinator = HistoryBatchCoordinator(config, telemetry=telemetry)
    for exchange, segment in (("NSE", "EQ"), ("NSE", "SME"), ("BSE", "EQ")):
        coordinator.pipeline.begin(
            exchange, segment, day, ("actions",)
        )
        coordinator.register_action_window(
            exchange,
            segment,
            (day,),
            add_sme_suffix=segment == "SME",
            timeout=5,
        )
    config.history_batch_coordinator = coordinator
    active = 0
    peak_active = 0

    async def fake_fetch(*_args, **_kwargs):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return []

    monkeypatch.setattr(CorporateActionClient, "fetch", fake_fetch)
    monkeypatch.setattr(
        CorporateActionEngine,
        "apply",
        lambda *_args, **_kwargs: {
            "applied": 0,
            "skipped": 0,
            "manual_review": 0,
        },
    )

    asyncio.run(DownloadWorker(config, [])._finalize_staged_histories())

    fetch_events = [
        event for event in telemetry.events
        if event.kind == "corporate_action_fetch_finished"
    ]
    apply_events = [
        event for event in telemetry.events
        if event.kind == "corporate_action_apply_finished"
    ]
    assert peak_active == 3
    assert len(fetch_events) == 3
    assert all(event.fields["outcome"] == "success" for event in fetch_events)
    assert len(apply_events) == 2
