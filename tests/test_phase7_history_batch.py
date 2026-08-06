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
from src.services.history_batch import (
    HistoryBatchCoordinator,
    HistoryBatchJournal,
    HistoryJournalEntry,
)
from src.services.pipeline_state import PipelineManifest
from src.services.pipeline_telemetry import PipelineTelemetry
from src.services.state_store import StateStoreError
from src.services.symbol_history import (
    MAX_ISOLATED_SYMBOL_FAILURES,
    HistoryBatchItem,
    SymbolHistoryStore,
)


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


def test_new_single_date_batch_fast_path_matches_incremental_bytes(tmp_path):
    day = date(2025, 1, 2)
    reference_root = tmp_path / "incremental-single"
    batch_root = tmp_path / "batch-single"
    rows = _rows(day)

    SymbolHistoryStore(reference_root).upsert("NSE", "EQ", day, rows)
    result = SymbolHistoryStore(batch_root).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, rows)
    ])

    assert result.history_writes == 3
    assert _symbol_bytes(batch_root) == _symbol_bytes(reference_root)


def test_new_single_date_batch_fast_path_keeps_numeric_validation(tmp_path):
    """The fast path still refuses the row; it now reports instead of raising."""

    day = date(2025, 1, 2)
    rows = _rows(day).iloc[[0]].astype(object).copy()
    rows.loc[:, "CLOSE"] = "invalid"

    result = SymbolHistoryStore(tmp_path).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, rows)
    ])

    assert result.history_writes == 0
    assert not (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.path == str(Path("NSE") / "SYMBOLS" / "aaa.txt")
    assert "invalid CLOSE" in failure.error
    assert failure.entries == (("NSE", "EQ", day),)


def test_one_unwritable_symbol_does_not_hold_back_the_rest(tmp_path):
    """A single bad symbol must not cost a backfill every other symbol."""

    day = date(2025, 1, 2)
    rows = _rows(day).astype(object).copy()
    rows.loc[rows["SYMBOL"] == "BBB", "CLOSE"] = "invalid"

    result = SymbolHistoryStore(tmp_path).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, rows)
    ])

    symbols = tmp_path / "NSE" / "SYMBOLS"
    assert sorted(path.name for path in symbols.glob("*.txt")) == [
        "aaa.txt", "ccc.txt"
    ]
    assert result.history_writes == 2
    assert [failure.path for failure in result.failures] == [
        str(Path("NSE") / "SYMBOLS" / "bbb.txt")
    ]


def test_batch_raises_once_failures_stop_looking_per_symbol(tmp_path):
    """Past the cap the store itself is suspect, so the batch fails closed."""

    day = date(2025, 1, 2)
    count = MAX_ISOLATED_SYMBOL_FAILURES + 5
    template = _rows(day).iloc[[0]].astype(object)
    rows = pd.concat(
        [
            template.assign(
                SYMBOL=f"SYM{index:04d}",
                ISIN=f"INE{index:04d}",
                SECURITY_ID=f"6{index:05d}",
                CLOSE="invalid",
            )
            for index in range(count)
        ],
        ignore_index=True,
    )

    with pytest.raises(ValueError, match="invalid CLOSE"):
        SymbolHistoryStore(tmp_path).upsert_batch([
            HistoryBatchItem("NSE", "EQ", day, rows)
        ])


def test_batch_isolates_up_to_the_cap_before_failing_closed(tmp_path):
    """The cap counts failures, so anything under it still publishes."""

    day = date(2025, 1, 2)
    template = _rows(day).iloc[[0]].astype(object)
    frames = []
    for index in range(MAX_ISOLATED_SYMBOL_FAILURES + 1):
        frames.append(template.assign(
            SYMBOL=f"SYM{index:04d}",
            ISIN=f"INE{index:04d}",
            SECURITY_ID=f"6{index:05d}",
            # One good symbol past the last tolerated failure proves the batch
            # kept going rather than stopping at the first bad row.
            CLOSE=100.0 if index == MAX_ISOLATED_SYMBOL_FAILURES else "invalid",
        ))
    rows = pd.concat(frames, ignore_index=True)

    result = SymbolHistoryStore(tmp_path).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, rows)
    ])

    assert len(result.failures) == MAX_ISOLATED_SYMBOL_FAILURES
    assert result.history_writes == 1
    assert (
        tmp_path / "NSE" / "SYMBOLS"
        / f"sym{MAX_ISOLATED_SYMBOL_FAILURES:04d}.txt"
    ).exists()


@pytest.mark.parametrize("day_count", [20, 100])
def test_multi_day_batch_is_byte_equivalent_with_constant_symbol_io(
    tmp_path, monkeypatch, day_count
):
    reference_root = tmp_path / "incremental-reference"
    batch_root = tmp_path / "batch"
    start = date(2025, 1, 1)
    reference = SymbolHistoryStore(reference_root)
    batched = SymbolHistoryStore(batch_root)
    reference.upsert("NSE", "EQ", start, _rows(start))
    batched.upsert("NSE", "EQ", start, _rows(start))

    reference_reads = 0
    reference_writes = 0
    original_read = reference._read_history
    original_write = reference._write_history

    def counted_read(path):
        nonlocal reference_reads
        reference_reads += 1
        return original_read(path)

    def counted_write(path, frame):
        nonlocal reference_writes
        reference_writes += 1
        return original_write(path, frame)

    monkeypatch.setattr(reference, "_read_history", counted_read)
    monkeypatch.setattr(reference, "_write_history", counted_write)
    items = []
    for offset in range(1, day_count + 1):
        day = start + timedelta(days=offset)
        rows = _rows(day, renamed=offset >= day_count // 2)
        reference.upsert("NSE", "EQ", day, rows)
        items.append(HistoryBatchItem("NSE", "EQ", day, rows))

    result = batched.upsert_batch(items)

    assert _symbol_bytes(batch_root) == _symbol_bytes(reference_root)
    assert reference_reads >= day_count * 3
    assert reference_writes >= day_count * 3
    assert result.history_reads <= 4
    assert result.history_writes == 3
    assert not (batch_root / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert (batch_root / "NSE" / "SYMBOLS" / "aaa-new.txt").exists()


def test_batch_holds_one_symbol_history_at_a_time(tmp_path, monkeypatch):
    """Peak memory must follow the batch's rows, not the symbols it touches.

    Reading every history up front and writing at the end is what made a
    multi-year backfill die at the very end of a long run, so the interleaving
    itself is the contract: each symbol is read immediately before its own
    write and is not held past it.
    """

    store = SymbolHistoryStore(tmp_path)
    days = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(3)]
    store.upsert("NSE", "EQ", days[0], _rows(days[0]))

    sequence: list[tuple[str, str]] = []
    original_read = store._read_history
    original_write = store._write_history

    def traced_read(path):
        sequence.append(("read", path.name))
        return original_read(path)

    def traced_write(path, frame):
        sequence.append(("write", path.name))
        return original_write(path, frame)

    monkeypatch.setattr(store, "_read_history", traced_read)
    monkeypatch.setattr(store, "_write_history", traced_write)
    store.upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, _rows(day)) for day in days[1:]
    ])

    assert sequence == [
        ("read", "aaa.txt"), ("write", "aaa.txt"),
        ("read", "bbb.txt"), ("write", "bbb.txt"),
        ("read", "ccc.txt"), ("write", "ccc.txt"),
    ]


def test_history_journal_resumes_only_remaining_symbols_after_interrupt(
    tmp_path, monkeypatch
):
    """A kill mid-batch must not re-do the symbols already published.

    ``KeyboardInterrupt`` is deliberate: it is a ``BaseException``, so it
    models a real interrupt rather than a per-symbol error, and it leaves the
    batch open exactly as terminating the process would.
    """

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

    def interrupt_second(path, frame):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise KeyboardInterrupt("simulated process kill")
        return original_write(path, frame)

    monkeypatch.setattr(
        coordinator.histories, "_write_history", interrupt_second
    )
    with pytest.raises(KeyboardInterrupt):
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


def _seeded_coordinator(root: Path, days, batch_dates=None, renamed_from=None):
    """Offer ``days`` to a coordinator whose daily stage is already complete."""

    settings = (
        SimpleNamespace(history_batch_dates=batch_dates)
        if batch_dates is not None else None
    )
    config = SimpleNamespace(base_data_path=root, download_settings=settings)
    manifest = PipelineManifest(root)
    coordinator = HistoryBatchCoordinator(config)
    for offset, day in enumerate(days):
        manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
        manifest.mark("NSE", "EQ", day, "daily", "complete")
        coordinator.offer("NSE", "EQ", day, _rows(
            day, renamed=renamed_from is not None and offset >= renamed_from
        ))
    return coordinator, manifest


def test_batch_dates_defaults_when_settings_do_not_configure_it(tmp_path):
    coordinator = HistoryBatchCoordinator(
        SimpleNamespace(base_data_path=tmp_path)
    )
    assert coordinator.batch_dates == HistoryBatchCoordinator.DEFAULT_BATCH_DATES

    configured = HistoryBatchCoordinator(SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=SimpleNamespace(history_batch_dates=7),
    ))
    assert configured.batch_dates == 7


def test_history_batch_dates_reaches_the_coordinator_from_config_yaml(
    tmp_path,
):
    """The knob has to survive the whole path, not just exist in the file.

    ``DownloadSettings`` is built key by key, so a new YAML key that nobody
    forwards is silently inert while still looking configurable.
    """

    import yaml

    from src.core.config import Config

    values = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert values["download_settings"]["history_batch_dates"] == 50
    values["download_settings"]["history_batch_dates"] = 17
    values["data_paths"] = {"base_path": str(tmp_path / "market_data")}
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")

    config = Config(str(path))
    assert config.download_settings.history_batch_dates == 17
    assert HistoryBatchCoordinator(config).batch_dates == 17


def test_journal_prepare_cuts_a_contiguous_window_in_date_order(tmp_path):
    """A bounded batch must still be a contiguous window, not an arbitrary cut.

    Date order is what keeps rows merging in the same sequence they would have
    if the whole run were one batch.
    """

    journal = HistoryBatchJournal(tmp_path / "pipeline.db")
    days = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(4)]
    for day in reversed(days):
        for segment in ("SME", "EQ"):
            journal.enqueue(HistoryJournalEntry(
                f"NSE_{segment}:{day.isoformat()}",
                "NSE", segment, day, f"raw/{segment}/{day}.csv", "0" * 64,
            ))

    batch_id, entries = journal.prepare(3)
    assert [(entry.segment, entry.target_date) for entry in entries] == [
        ("EQ", days[0]), ("SME", days[0]), ("EQ", days[1]),
    ]

    # An open batch is claimed again rather than re-cut, so a resumed run
    # finishes the window it interrupted before starting a new one.
    assert journal.prepare(3)[0] == batch_id
    journal.finish(batch_id)

    _, following = journal.prepare(3)
    assert [(entry.segment, entry.target_date) for entry in following] == [
        ("SME", days[1]), ("EQ", days[2]), ("SME", days[2]),
    ]


@pytest.mark.parametrize("batch_dates", [1, 2, 3, 5, 7, 12])
def test_bucketed_batches_match_one_unbounded_batch_byte_for_byte(
    tmp_path, batch_dates
):
    """Splitting a run into buckets must not change a single published byte.

    The bucket sizes walk the boundary across the rename at day 7, so at least
    one of them makes the merge read the file an earlier bucket already wrote
    rather than a frame still held in memory.
    """

    days = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(12)]
    bucketed_root = tmp_path / "bucketed"
    single_root = tmp_path / "single"

    coordinator, manifest = _seeded_coordinator(
        bucketed_root, days, batch_dates=batch_dates, renamed_from=7
    )
    outcomes = coordinator.finalize()

    assert len(outcomes) == -(-len(days) // batch_dates)
    assert all(
        outcome.result.entries <= batch_dates for outcome in outcomes
    )
    assert manifest.incomplete_dates("NSE", "EQ") == []

    SymbolHistoryStore(single_root).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, _rows(day, renamed=offset >= 7))
        for offset, day in enumerate(days)
    ])

    assert _symbol_bytes(bucketed_root) == _symbol_bytes(single_root)
    assert not (bucketed_root / "NSE" / "SYMBOLS" / "aaa.txt").exists()


def test_a_kill_in_a_later_bucket_keeps_the_finished_ones(tmp_path):
    """Each bucket commits on its own, so a kill costs one bucket at most."""

    days = [date(2025, 1, 1) + timedelta(days=offset) for offset in range(12)]
    root = tmp_path / "interrupted"
    coordinator, manifest = _seeded_coordinator(root, days, batch_dates=5)

    calls = 0
    original = coordinator.histories.upsert_batch

    def kill_in_second_bucket(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("simulated process kill")
        return original(*args, **kwargs)

    coordinator.histories.upsert_batch = kill_in_second_bucket
    with pytest.raises(KeyboardInterrupt):
        coordinator.finalize()

    assert [
        manifest.date_result("NSE", "EQ", day).status for day in days[:5]
    ] == ["success"] * 5
    assert manifest.incomplete_dates("NSE", "EQ") == days[5:]

    resumed = HistoryBatchCoordinator(SimpleNamespace(
        base_data_path=root,
        download_settings=SimpleNamespace(history_batch_dates=5),
    ))
    # Two buckets remain, not three: the first was committed and dropped.
    assert len(resumed.finalize()) == 2
    assert manifest.incomplete_dates("NSE", "EQ") == []

    clean_root = tmp_path / "clean"
    SymbolHistoryStore(clean_root).upsert_batch([
        HistoryBatchItem("NSE", "EQ", day, _rows(day)) for day in days
    ])
    assert _symbol_bytes(root) == _symbol_bytes(clean_root)


def test_a_failed_symbol_fails_only_the_dates_it_holds_back(tmp_path):
    """One bad symbol must cost its own dates, not the whole batch."""

    good_day = date(2025, 1, 1)
    bad_day = date(2025, 1, 2)
    root = tmp_path / "isolated"
    config = SimpleNamespace(base_data_path=root)
    manifest = PipelineManifest(root)
    coordinator = HistoryBatchCoordinator(config)

    for day in (good_day, bad_day):
        manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
        manifest.mark("NSE", "EQ", day, "daily", "complete")
    coordinator.offer("NSE", "EQ", good_day, _rows(good_day))

    # DDD trades only on the second day, so only that day is held back.
    second = _rows(bad_day).astype(object)
    broken = second.iloc[[0]].copy()
    broken.loc[:, ["SYMBOL", "ISIN", "SECURITY_ID"]] = ["DDD", "INE9", "509"]
    broken.loc[:, "CLOSE"] = "invalid"
    coordinator.offer(
        "NSE", "EQ", bad_day, pd.concat([second, broken], ignore_index=True)
    )

    outcomes = coordinator.finalize()

    assert manifest.date_result("NSE", "EQ", good_day).status == "success"
    held_back = manifest.date_result("NSE", "EQ", bad_day)
    # Partial, not failed: the daily file is published and only the symbol
    # stage is outstanding.  What matters is that the date is not complete, so
    # it returns through the ordinary repair path.
    assert held_back.status == "partial"
    assert held_back.failed_stages == ("symbols",)
    assert "ddd.txt" in (held_back.error or "")
    assert manifest.incomplete_dates("NSE", "EQ") == [bad_day]
    assert [failure.path for failure in outcomes[0].result.failures] == [
        str(Path("NSE") / "SYMBOLS" / "ddd.txt")
    ]
    # The good symbols published, and the batch closed rather than blocking
    # everything queued behind it.
    assert not (root / "NSE" / "SYMBOLS" / "ddd.txt").exists()
    for name in ("aaa.txt", "bbb.txt", "ccc.txt"):
        assert (root / "NSE" / "SYMBOLS" / name).exists()
    with coordinator.journal._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM history_batches"
        ).fetchone()[0] == 0


def test_a_failed_rename_merge_is_not_isolated(tmp_path):
    """A merge failure must abort: its rows live in a file this batch deletes.

    Isolating it would drop the retired file and publish neither side, so this
    one case keeps the older fail-closed behaviour.
    """

    days = (date(2025, 1, 1), date(2025, 1, 2))
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", days[0], _rows(days[0]))

    original_write = store._write_history

    def fail_the_merge(path, frame):
        if path.name == "aaa-new.txt":
            raise OSError("simulated merge failure")
        return original_write(path, frame)

    store._write_history = fail_the_merge
    with pytest.raises(OSError, match="simulated merge failure"):
        store.upsert_batch([
            HistoryBatchItem("NSE", "EQ", days[1], _rows(days[1], renamed=True))
        ])

    # The rows the merge would have carried are still on disk under the old
    # name, so nothing was lost and a repeat run can complete the rename.
    assert (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert not (tmp_path / "NSE" / "SYMBOLS" / "aaa-new.txt").exists()


def test_a_corrupt_snapshot_fails_the_batch_and_keeps_it_open(tmp_path):
    """A tampered snapshot is a store problem, so the batch fails closed."""

    day = date(2025, 1, 2)
    manifest = PipelineManifest(tmp_path)
    manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
    manifest.mark("NSE", "EQ", day, "daily", "complete")
    telemetry = PipelineTelemetry()
    coordinator = HistoryBatchCoordinator(
        SimpleNamespace(base_data_path=tmp_path), telemetry=telemetry
    )
    snapshot = coordinator.offer("NSE", "EQ", day, _rows(day))
    snapshot.write_text(snapshot.read_text() + "\n")

    with pytest.raises(StateStoreError, match="checksum mismatch"):
        coordinator.finalize()

    assert manifest.date_result("NSE", "EQ", day).failed_stages == ("symbols",)
    assert not list((tmp_path / "NSE" / "SYMBOLS").glob("*.txt"))
    # Left open on purpose: nothing was published, so the batch is still the
    # unit of work a later run should retry.
    with coordinator.journal._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM history_batches"
        ).fetchone()[0] == 1
    assert [
        event.fields["outcome"] for event in telemetry.events
        if event.kind == "history_batch_finished"
    ] == ["error"]


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


def test_worker_reports_held_back_symbols_without_failing_the_run(tmp_path):
    """A partial batch must be visible, not silently swallowed as success."""

    day = date(2025, 1, 2)
    manifest = PipelineManifest(tmp_path)
    manifest.begin("NSE", "EQ", day, ("daily", "symbols"))
    manifest.mark("NSE", "EQ", day, "daily", "complete")
    config = SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )
    coordinator = HistoryBatchCoordinator(config)
    rows = _rows(day).astype(object)
    rows.loc[rows["SYMBOL"] == "BBB", "CLOSE"] = "invalid"
    coordinator.offer("NSE", "EQ", day, rows)
    config.history_batch_coordinator = coordinator

    worker = DownloadWorker(config, [])
    errors: list[tuple[str, str]] = []
    statuses: list[tuple[str, str]] = []
    worker.error_occurred.connect(lambda *args: errors.append(args))
    worker.status_updated.connect(lambda *args: statuses.append(args))

    asyncio.run(worker._finalize_staged_histories())

    assert [name for name, _message in errors] == ["Symbol histories"]
    assert "1 symbol histories were not published" in errors[0][1]
    assert "bbb.txt" in errors[0][1]
    # The published half is still reported, so the run reads as partial rather
    # than as a failure that lost everything.
    assert statuses and "published 2 symbols" in statuses[0][1]


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


def test_staged_action_timeout_retries_once_and_reports_success(
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
    coordinator.pipeline.begin("BSE", "EQ", day, ("actions",))
    coordinator.register_action_window(
        "BSE", "EQ", (day,), add_sme_suffix=False, timeout=5
    )
    config.history_batch_coordinator = coordinator
    calls = 0

    async def flaky_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.TimeoutError
        return []

    monkeypatch.setattr(CorporateActionClient, "fetch", flaky_fetch)
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

    assert calls == 2
    retries = [
        event for event in telemetry.events
        if event.kind == "corporate_action_retry_scheduled"
    ]
    finished = [
        event for event in telemetry.events
        if event.kind == "corporate_action_fetch_finished"
    ]
    assert len(retries) == 1
    assert retries[0].fields["error_type"] == "TimeoutError"
    assert len(finished) == 1
    assert finished[0].fields["outcome"] == "success"
    assert finished[0].fields["attempts"] == 2
    assert coordinator.pipeline.date_result("BSE", "EQ", day).status == "success"
