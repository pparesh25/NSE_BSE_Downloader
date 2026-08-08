from datetime import date
from itertools import permutations
from pathlib import Path

import pandas as pd

from src.services.canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from src.services.combined_file_builder import CombinedFileBuilder
from src.services.date_join_coordinator import DateJoinCoordinator


class _Config:
    def __init__(self, root: Path):
        self.base_data_path = root

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


DAY = date(2026, 8, 4)


def _equity(day, symbol):
    return pd.DataFrame([[
        symbol, day.strftime("%Y%m%d"), 10, 12, 9, 11, 100, 50, 50,
    ]], columns=EQUITY_DAILY_COLUMNS)


def _index(day, symbol="NIFTY"):
    return pd.DataFrame([[
        symbol, day.strftime("%Y%m%d"), 100, 120, 90, 110, 0,
    ]], columns=INDEX_DAILY_COLUMNS)


def _offer(coordinator, exchange, segment, day, frame):
    coordinator.builder.save_component(exchange, segment, day, frame)
    return coordinator.offer(exchange, segment, day, frame)


def test_staged_arrival_permutations_are_byte_identical(tmp_path):
    outputs = []
    frames = {
        "EQ": _equity(DAY, "EQ"),
        "SME": _equity(DAY, "SME"),
        "INDEX": _index(DAY),
    }
    for position, order in enumerate(permutations(frames)):
        coordinator = DateJoinCoordinator(
            _Config(tmp_path / str(position)), {"NSE": ("SME", "INDEX")}
        )
        result = None
        for segment in order:
            result = _offer(
                coordinator, "NSE", segment, DAY, frames[segment]
            )
        assert result is not None and result.ok
        outputs.append(result.output_path.read_bytes())
    assert len(set(outputs)) == 1


def test_bse_staged_join_matches_persisted_rebuild(tmp_path):
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(config, {"BSE": ("INDEX",)})
    _offer(coordinator, "BSE", "INDEX", DAY, _index(DAY, "SENSEX"))
    staged = _offer(coordinator, "BSE", "EQ", DAY, _equity(DAY, "BSE_EQ"))
    assert staged is not None and staged.ok
    staged_bytes = staged.output_path.read_bytes()
    restarted = CombinedFileBuilder(config).reconcile("BSE", DAY, ("INDEX",))
    assert restarted.ok
    assert restarted.output_path.read_bytes() == staged_bytes
    assert restarted.sha256 == staged.sha256


def test_ready_earlier_date_publishes_before_incomplete_later_date(tmp_path):
    coordinator = DateJoinCoordinator(
        _Config(tmp_path), {"NSE": ("SME", "INDEX")}
    )
    later = date(2026, 8, 5)
    _offer(coordinator, "NSE", "EQ", later, _equity(later, "LATER"))
    _offer(coordinator, "NSE", "EQ", DAY, _equity(DAY, "EARLY"))
    _offer(coordinator, "NSE", "INDEX", DAY, _index(DAY))
    result = _offer(
        coordinator, "NSE", "SME", DAY, _equity(DAY, "EARLY_SME")
    )
    assert result is not None and result.ok
    assert result.output_path.is_file()
    later_output = (
        tmp_path / "NSE" / "EQ" / f"{later.isoformat()}-NSE-EQ.txt"
    )
    assert not later_output.exists()


def test_cache_eviction_uses_durable_component_fallback(tmp_path):
    coordinator = DateJoinCoordinator(
        _Config(tmp_path),
        {"NSE": ("SME", "INDEX")},
        max_cache_dates=1,
    )
    later = date(2026, 8, 5)
    _offer(coordinator, "NSE", "EQ", DAY, _equity(DAY, "EARLY"))
    _offer(coordinator, "NSE", "EQ", later, _equity(later, "LATER"))
    _offer(coordinator, "NSE", "INDEX", DAY, _index(DAY))
    result = _offer(
        coordinator, "NSE", "SME", DAY, _equity(DAY, "EARLY_SME")
    )
    assert result is not None and result.ok
    assert result.rows == 3
    assert coordinator.cached_dates <= 1
    assert coordinator.peak_cached_dates == 1


def test_finalize_missing_dependency_keeps_existing_public_eq(tmp_path):
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(
        config, {"NSE": ("SME", "INDEX")}
    )
    output = config.get_data_path("NSE", "EQ") / f"{DAY}-NSE-EQ.txt"
    output.write_bytes(b"previous-validated-output\n")
    _offer(coordinator, "NSE", "EQ", DAY, _equity(DAY, "EQ"))
    _offer(coordinator, "NSE", "SME", DAY, _equity(DAY, "SME"))
    results = coordinator.finalize()
    assert len(results) == 1 and not results[0].ok
    assert "NSE_INDEX" in results[0].error
    assert output.read_bytes() == b"previous-validated-output\n"


def test_bse_eq_publishes_before_index_exists(tmp_path):
    # The shipped default enables bse_index_append_to_eq, but BSE INDEX only
    # starts on 2025-04-17.  Every earlier BSE EQ date used to end the run with
    # no published file at all and be re-downloaded on every future run.
    day = date(2015, 6, 15)
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(config, {"BSE": ("INDEX",)})
    equity = pd.DataFrame(
        [["ABB", "20150615", 1, 2, 1, 2, 100, 50, 50]],
        columns=EQUITY_DAILY_COLUMNS,
    )

    assert coordinator.dependencies_for("BSE", day) == ()

    published = coordinator.offer("BSE", "EQ", day, equity)

    assert published is not None, "EQ alone must publish before INDEX exists"
    assert published.ok
    assert published.components == ("EQ",)
    output = pd.read_csv(published.output_path, header=None)
    assert len(output) == 1


def test_bse_eq_still_waits_for_index_once_it_exists(tmp_path):
    day = date(2025, 6, 16)
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(config, {"BSE": ("INDEX",)})
    equity = pd.DataFrame(
        [["ABB", "20250616", 1, 2, 1, 2, 100, 50, 50]],
        columns=EQUITY_DAILY_COLUMNS,
    )
    index = pd.DataFrame(
        [["SENSEX", "20250616", 1, 2, 1, 2, 0]], columns=INDEX_DAILY_COLUMNS
    )

    assert coordinator.dependencies_for("BSE", day) == ("INDEX",)
    assert coordinator.offer("BSE", "EQ", day, equity) is None

    published = coordinator.offer("BSE", "INDEX", day, index)

    assert published is not None and published.ok
    assert published.components == ("EQ", "INDEX")


def test_finalize_publishes_when_nothing_is_genuinely_outstanding(tmp_path):
    day = date(2015, 6, 15)
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(config, {"BSE": ("INDEX",)})
    coordinator.offer(
        "BSE", "EQ", day,
        pd.DataFrame(
            [["ABB", "20150615", 1, 2, 1, 2, 100, 50, 50]],
            columns=EQUITY_DAILY_COLUMNS,
        ),
    )

    results = coordinator.finalize()

    assert len(results) == 1
    assert results[0].ok, results[0].error


def test_finalize_still_fails_a_transiently_missing_component(tmp_path):
    # A dependency that exists for this date but did not arrive is a real
    # failure and must keep the date queued for repair.
    day = date(2025, 6, 16)
    config = _Config(tmp_path)
    coordinator = DateJoinCoordinator(config, {"BSE": ("INDEX",)})
    coordinator.offer(
        "BSE", "EQ", day,
        pd.DataFrame(
            [["ABB", "20250616", 1, 2, 1, 2, 100, 50, 50]],
            columns=EQUITY_DAILY_COLUMNS,
        ),
    )

    results = coordinator.finalize()

    assert len(results) == 1
    assert not results[0].ok
    assert "BSE_INDEX" in (results[0].error or "")
