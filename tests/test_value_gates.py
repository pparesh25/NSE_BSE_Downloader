"""Phase 3.4: a day only publishes if its size and its prices are plausible.

The thresholds here are measured, not chosen: every constant is justified
against the owner's real tree, and the tests pin both directions -- what the
gates must reject, and the real market shapes they must keep accepting.
"""

from datetime import date
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.base_downloader import BaseDownloader
from src.core.data_manager import DataManager
from src.core.exceptions import FileOperationError
from src.services.canonical_data import (
    EQUITY_DAILY_COLUMNS,
    FO_DAILY_COLUMNS,
    INTERNAL_EQUITY_COLUMNS,
    drop_unpriced_rows,
    incoherent_ohlc,
    normalize_nse_equity,
    row_count_band_error,
    validate_canonical_data,
)
from src.core.exceptions import DataProcessingError
from src.services.pipeline_state import PipelineManifest


DAY = date(2026, 7, 31)


def _equity_source(rows):
    """A legacy NSE bhavcopy frame, which needs no ISIN to normalize."""

    return pd.DataFrame([
        {
            "SYMBOL": symbol,
            "SERIES": "EQ",
            "OPEN": open_,
            "HIGH": high,
            "LOW": low,
            "CLOSE": close,
            "TOTTRDQTY": 100,
            "TOTALTRADES": 10,
            "ISIN": "INE000000000",
            "TIMESTAMP": "31-JUL-2026",
            "TOTTRDVAL": close * 100,
            "PREVCLOSE": close,
        }
        for symbol, open_, high, low, close in rows
    ])


def _canonical(rows, columns=EQUITY_DAILY_COLUMNS):
    return pd.DataFrame(
        [
            [symbol, "20260731", open_, high, low, close, 100, *(
                [50, 50] if columns is EQUITY_DAILY_COLUMNS else [0, 0]
            ), close * 100, close]
            for symbol, open_, high, low, close in rows
        ],
        columns=columns,
    )


# --- unpriced rows ------------------------------------------------------------


def test_an_all_zero_row_is_dropped_and_a_settlement_only_row_survives(caplog):
    frame = _canonical([
        ("TRADED", 10.0, 12.0, 9.0, 11.0),
        ("SETTLED", 0.0, 0.0, 0.0, 1209.5),
        ("EMPTY", 0.0, 0.0, 0.0, 0.0),
    ], FO_DAILY_COLUMNS)

    with caplog.at_level(logging.WARNING):
        result = drop_unpriced_rows(frame, "nse-fo-udiff")

    # 1,515 real NSE futures rows carry 0,0,0 OHL with a real settlement
    # CLOSE, so only the row with no price at all may be dropped.
    assert result["SYMBOL"].tolist() == ["TRADED", "SETTLED"]
    assert "dropped 1 row(s)" in caplog.text


def test_a_report_of_only_unpriced_rows_is_refused():
    frame = _canonical([("A", 0, 0, 0, 0), ("B", 0, 0, 0, 0)])
    with pytest.raises(DataProcessingError, match="no priced rows"):
        drop_unpriced_rows(frame, "nse-equity-legacy")


def test_normalizing_drops_an_all_zero_row_before_it_can_publish():
    result = normalize_nse_equity(
        _equity_source([
            ("GOOD", 10, 12, 9, 11),
            ("DEAD", 0, 0, 0, 0),
        ]),
        DAY,
    )
    assert result["SYMBOL"].tolist() == ["GOOD"]


# --- OHLC coherence -----------------------------------------------------------


def test_coherence_judges_only_rows_with_a_real_traded_range():
    frame = _canonical([
        ("INSIDE", 10.0, 12.0, 9.0, 11.0),
        ("SETTLED", 0.0, 0.0, 0.0, 1209.5),   # no range to be judged against
        ("NOOPEN", 0.0, 12.0, 9.0, 11.0),     # absent, not a price of zero
        ("ABOVE", 10.0, 12.0, 9.0, 12.5),     # close above the day's high
        ("INVERTED", 10.0, 9.0, 12.0, 11.0),  # high below low
    ])
    flagged = frame.loc[incoherent_ohlc(frame), "SYMBOL"].tolist()
    assert flagged == ["ABOVE", "INVERTED"]


def test_a_few_odd_rows_publish_but_a_misaligned_report_does_not():
    # BSE really does publish a settlement close above the day's high on a
    # one-share trade (NCC# on 2026-01-27, IRFC# on 2026-02-23), and the worst
    # real day measured is 3 of 640 NSE FO rows.
    odd = _canonical(
        [(f"OK{index}", 10.0, 12.0, 9.0, 11.0) for index in range(200)]
        + [("NCC#", 141.0, 141.0, 141.0, 141.7)]
    )
    validate_canonical_data(odd, DAY, EQUITY_DAILY_COLUMNS)

    broken = _canonical(
        [(f"BAD{index}", 10.0, 9.0, 12.0, 11.0) for index in range(20)]
        + [(f"OK{index}", 10.0, 12.0, 9.0, 11.0) for index in range(80)]
    )
    with pytest.raises(DataProcessingError, match="outside their own"):
        validate_canonical_data(broken, DAY, EQUITY_DAILY_COLUMNS)


# --- row-count band -----------------------------------------------------------


def test_row_count_band_only_judges_with_enough_neighbours():
    assert row_count_band_error(5, [1000, 1010, 1020]) is None
    assert row_count_band_error(1000, []) is None


def test_row_count_band_rejects_a_placeholder_and_a_doubled_report():
    neighbours = [1000, 1010, 1020, 990, 1005]
    assert row_count_band_error(1000, neighbours) is None
    assert row_count_band_error(600, neighbours) is None       # 60%, inside
    assert row_count_band_error(1400, neighbours) is None      # 139%, inside
    assert "outside" in (row_count_band_error(5, neighbours) or "")
    assert "outside" in (row_count_band_error(2500, neighbours) or "")


def test_neighbouring_row_counts_look_both_ways_and_stay_local_in_time(tmp_path):
    manifest = PipelineManifest(tmp_path)

    def publish(day, rows, status="complete"):
        manifest.begin("NSE", "EQ", day, ("daily",))
        manifest.mark("NSE", "EQ", day, "daily", status, rows=rows)

    publish(date(2026, 7, 29), 1000)
    publish(date(2026, 7, 30), 1010)
    publish(date(2026, 8, 3), 1020)          # a later session still counts
    publish(date(2026, 8, 4), 0, "failed")   # an incomplete one does not
    publish(date(1995, 1, 2), 300)           # a different era does not

    counts = manifest.neighbouring_row_counts("NSE", "EQ", date(2026, 7, 31))
    assert sorted(counts) == [1000, 1010, 1020]


class _BandConfig:
    def __init__(self, base_path: Path):
        self.base_data_path = base_path
        self.date_settings = SimpleNamespace(
            weekend_skip=True, base_start_date="2026-07-01"
        )
        self.holiday_manager = SimpleNamespace(is_holiday=lambda _day: False)

    @staticmethod
    def get_available_exchanges():
        return ["NSE_EQ"]

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


class _BandDownloader(BaseDownloader):
    def __init__(self, base_path):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = _BandConfig(Path(base_path))
        self.logger = logging.getLogger("test.band")
        self.data_path = self.config.get_data_path("NSE", "EQ")
        self.exchange_config = SimpleNamespace(file_suffix="-NSE-EQ")
        self.combined_required = False
        self.pipeline_manifest = PipelineManifest(base_path)

    def build_url(self, target_date):
        return "https://example.test/report.csv"

    def process_downloaded_data(self, file_data, file_date):
        return None

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return True

    def get_download_option(self, name, default=None):
        return False


def _band_history(downloader, count=6, rows=1000):
    for index in range(count):
        day = date(2026, 7, 1) + pd.Timedelta(days=index).to_pytimedelta()
        downloader.pipeline_manifest.begin("NSE", "EQ", day, ("daily",))
        downloader.pipeline_manifest.mark(
            "NSE", "EQ", day, "daily", "complete", rows=rows
        )


def test_a_placeholder_day_is_refused_and_marked_for_repair(tmp_path):
    downloader = _BandDownloader(tmp_path)
    _band_history(downloader)
    downloader._begin_pipeline_date(DAY)
    frame = _canonical([("ABC", 10, 12, 9, 11)])

    with pytest.raises(FileOperationError):
        downloader.save_processed_data(frame, DAY)

    assert not (downloader.data_path / f"{DAY}-NSE-EQ.txt").exists()
    result = downloader.pipeline_manifest.date_result("NSE", "EQ", DAY)
    assert result.status == "failed"
    assert "outside" in (result.error or "")


def test_a_normal_day_publishes_through_the_band(tmp_path):
    downloader = _BandDownloader(tmp_path)
    _band_history(downloader, rows=3)
    downloader._begin_pipeline_date(DAY)
    frame = _canonical([
        ("ABC", 10, 12, 9, 11), ("DEF", 10, 12, 9, 11), ("GHI", 10, 12, 9, 11),
    ])

    downloader.save_processed_data(frame, DAY)

    assert (downloader.data_path / f"{DAY}-NSE-EQ.txt").is_file()


# --- published files are counted, and written with one line terminator --------


def test_a_truncated_daily_file_is_invalid_and_returns_for_repair(tmp_path):
    downloader = _BandDownloader(tmp_path)
    _band_history(downloader, rows=3)
    downloader._begin_pipeline_date(DAY)
    frame = _canonical([
        ("ABC", 10, 12, 9, 11), ("DEF", 10, 12, 9, 11), ("GHI", 10, 12, 9, 11),
    ])
    downloader.save_processed_data(frame, DAY)
    published = downloader.data_path / f"{DAY}-NSE-EQ.txt"

    manager = DataManager(_BandConfig(tmp_path))
    assert manager.validate_daily_output("NSE", "EQ", DAY, published)

    # Both ends still parse; only the count betrays the loss.
    lines = published.read_text().splitlines()
    published.write_text(f"{lines[0]}\n{lines[-1]}\n")
    manager = DataManager(_BandConfig(tmp_path))
    assert not manager.validate_daily_output("NSE", "EQ", DAY, published)
    assert DAY in manager.get_invalid_file_dates("NSE", "EQ")


def test_daily_files_written_by_an_older_build_are_left_alone(tmp_path):
    config = _BandConfig(tmp_path)
    folder = config.get_data_path("NSE", "EQ")
    path = folder / f"{DAY}-NSE-EQ.txt"
    path.write_text(
        f"ABC,{DAY:%Y%m%d},10,12,9,11,100,50,50\n"
        f"DEF,{DAY:%Y%m%d},10,12,9,11,100,50,50\n"
    )
    # No manifest record exists, so no expectation is invented for it.
    assert DataManager(config).validate_daily_output("NSE", "EQ", DAY, path)


def test_published_files_use_one_line_terminator_on_every_platform(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "linesep", "\r\n")
    downloader = _BandDownloader(tmp_path)
    downloader._begin_pipeline_date(DAY)
    downloader.save_processed_data(_canonical([("ABC", 10, 12, 9, 11)]), DAY)

    published = (downloader.data_path / f"{DAY}-NSE-EQ.txt").read_bytes()
    component = (
        tmp_path / ".state" / "components" / "NSE" / "EQ" / f"{DAY}.csv"
    ).read_bytes()
    assert b"\r\n" not in published
    assert b"\r\n" not in component


def test_symbol_histories_use_one_line_terminator_on_every_platform(
    tmp_path, monkeypatch
):
    from src.services.symbol_history import SymbolHistoryStore

    monkeypatch.setattr(os, "linesep", "\r\n")
    rows = pd.DataFrame([{
        column: value for column, value in zip(
            INTERNAL_EQUITY_COLUMNS,
            ["ABC", "20260731", 10, 12, 9, 11, 100, 50, 50,
             "EQ", 10, 10, "INE000000000", "500001"],
        )
    }])
    SymbolHistoryStore(tmp_path).upsert("NSE", "EQ", DAY, rows)

    history = (tmp_path / "NSE" / "SYMBOLS" / "abc.txt").read_bytes()
    snapshot = (
        tmp_path / ".state" / "raw" / "NSE" / "EQ" / f"{DAY}.csv"
    ).read_bytes()
    assert b"\r\n" not in history
    assert b"\r\n" not in snapshot
