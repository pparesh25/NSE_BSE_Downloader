from datetime import date
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.core.base_downloader import BaseDownloader
from src.core.data_manager import DataManager
from src.core.exceptions import DataProcessingError
from src.services.pipeline_state import PipelineManifest
from src.services.canonical_data import EQUITY_DAILY_COLUMNS


def test_pipeline_manifest_resumes_partial_date_and_returns_structured_result(
    tmp_path,
):
    day = date(2026, 7, 30)
    manifest = PipelineManifest(tmp_path)
    manifest.begin(
        "NSE",
        "EQ",
        day,
        ("downloaded", "validated", "daily", "delivery"),
        ("symbols", "actions", "combined"),
    )
    manifest.mark("NSE", "EQ", day, "downloaded", "complete")
    manifest.mark("NSE", "EQ", day, "validated", "complete")
    manifest.mark(
        "NSE", "EQ", day, "daily", "complete", path="daily.txt"
    )

    partial = manifest.segment_result("NSE", "EQ", [day])
    assert not partial.ok
    assert partial.partial_count == 1
    assert manifest.incomplete_dates("NSE", "EQ") == [day]

    resumed = PipelineManifest(tmp_path)
    resumed.mark("NSE", "EQ", day, "delivery", "complete")
    complete = resumed.segment_result("NSE", "EQ", [day])
    assert complete.ok
    assert complete.success_count == 1
    assert complete.dates[0].output_path == "daily.txt"
    assert resumed.incomplete_dates("NSE", "EQ") == []


def test_a_temporarily_skipped_date_can_complete_on_a_later_run(tmp_path):
    day = date(2026, 7, 31)
    manifest = PipelineManifest(tmp_path)
    manifest.skip_date("NSE", "INDEX", day, "Not published yet")
    assert manifest.date_result("NSE", "INDEX", day).status == "skipped"

    manifest.begin(
        "NSE",
        "INDEX",
        day,
        ("downloaded", "validated", "daily"),
        ("symbols", "delivery", "actions", "combined"),
    )
    for stage in ("downloaded", "validated", "daily"):
        manifest.mark("NSE", "INDEX", day, stage, "complete")
    assert manifest.date_result("NSE", "INDEX", day).status == "success"


class _NoHolidays:
    @staticmethod
    def is_holiday(target_date):
        return False


class _DataConfig:
    def __init__(self, base_path: Path):
        self.base_data_path = base_path
        self.date_settings = SimpleNamespace(
            weekend_skip=True,
            base_start_date="2026-07-01",
        )
        self.holiday_manager = _NoHolidays()

    @staticmethod
    def get_available_exchanges():
        return ["NSE_EQ"]

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


def _write_daily(path: Path, day: date):
    path.write_text(
        f"ABC,{day:%Y%m%d},10,12,9,11,100,50,50\n",
        encoding="utf-8",
    )


def test_data_manager_ignores_suffix_tricks_and_detects_gaps_and_invalid_files(
    tmp_path,
):
    manager = DataManager(_DataConfig(tmp_path))
    folder = tmp_path / "NSE" / "EQ"
    monday = date(2026, 7, 27)
    tuesday = date(2026, 7, 28)
    wednesday = date(2026, 7, 29)
    thursday = date(2026, 7, 30)

    _write_daily(folder / f"{monday}-NSE-EQ.txt", monday)
    _write_daily(folder / f"{wednesday}-NSE-EQ.txt", wednesday)
    (folder / f"{thursday}-NSE-EQ.txt").write_text(
        "ABC,wrong-date,10,12,9,11,100,50,50\n", encoding="utf-8"
    )
    _write_daily(folder / f"{thursday}-NSE-EQ.txt.bak", thursday)

    assert manager.get_last_file_date("NSE", "EQ") == wednesday
    assert manager.get_file_count("NSE", "EQ") == 2
    assert manager.get_invalid_file_dates("NSE", "EQ") == [thursday]
    assert manager.get_missing_file_dates("NSE", "EQ") == [tuesday, thursday]
    assert manager.get_repair_dates("NSE", "EQ") == [tuesday, thursday]


class _InvalidPriceDownloader(BaseDownloader):
    def __init__(self, base_path):
        self.exchange = "NSE"
        self.segment = "INDEX"
        self.exchange_segment = "NSE_INDEX"
        self.config = SimpleNamespace(
            base_data_path=Path(base_path),
            download_settings=SimpleNamespace(timeout_seconds=5),
        )
        self.logger = logging.getLogger("test.invalid.price")
        self.completed_files = 0
        self.total_files = 0
        self.progress_callback = None
        self.errors = []

    def build_url(self, target_date):
        return "https://example.test/report.csv"

    def process_downloaded_data(self, file_data, file_date):
        raise DataProcessingError("unexpected schema")

    def transform_data(self, df, file_date):
        return pd.DataFrame()

    async def _download_implementation(self, working_days):
        return await self._download_price_implementation(working_days)

    def save_processed_data(self, df, target_date):
        raise AssertionError("invalid report must never be saved")

    def get_download_option(self, name, default=None):
        return default

    async def update_async_session_timeout(self, manager, timeout):
        return None

    def _report_error(self, error):
        self.errors.append(error)


class _DeferredEQDownloader(BaseDownloader):
    def __init__(self, base_path):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = _DataConfig(Path(base_path))
        self.logger = logging.getLogger("test.deferred.eq")
        self.data_path = self.config.get_data_path("NSE", "EQ")
        self.exchange_config = SimpleNamespace(file_suffix="-NSE-EQ")
        self.combined_required = True
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


def test_combined_rerun_defers_publication_and_resets_manifest(tmp_path):
    day = date(2026, 7, 31)
    downloader = _DeferredEQDownloader(tmp_path)
    public = downloader.data_path / f"{day}-NSE-EQ.txt"
    public.write_bytes(b"previous-combined-output\n")
    downloader.pipeline_manifest.begin(
        "NSE", "EQ", day, ("downloaded", "validated", "daily", "combined")
    )
    for stage in ("downloaded", "validated", "daily", "combined"):
        downloader.pipeline_manifest.mark("NSE", "EQ", day, stage, "complete")

    downloader._begin_pipeline_date(day)
    assert downloader.pipeline_manifest.date_result(
        "NSE", "EQ", day
    ).status == "partial"
    frame = pd.DataFrame([[
        "FRESH", "20260731", 1, 2, 1, 2, 100, 50, 50, 200, 1,
    ]], columns=EQUITY_DAILY_COLUMNS)
    downloader.save_processed_data(frame, day)

    assert public.read_bytes() == b"previous-combined-output\n"
    component = (
        tmp_path / ".state" / "components" / "NSE" / "EQ"
        / "2026-07-31.csv"
    )
    assert component.is_file()
    assert downloader.pipeline_manifest.date_result(
        "NSE", "EQ", day
    ).status != "success"


class _InvalidPayloadManager:
    def __init__(self, config):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def download_multiple(self, tasks):
        return [SimpleNamespace(
            success=True,
            file_data=b"wrong,shape\n1,2\n",
            error_message="",
        )]


def test_invalid_download_is_quarantined_without_publishing_daily_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "src.utils.async_downloader.AsyncDownloadManager",
        _InvalidPayloadManager,
    )
    day = date(2026, 7, 30)
    downloader = _InvalidPriceDownloader(tmp_path)
    assert not asyncio.run(downloader._download_implementation([day]))

    result = downloader.last_segment_result.dates[0]
    assert result.failed_stages == ("validated",)
    assert "unexpected schema" in result.error
    quarantined = list(
        (tmp_path / ".state" / "quarantine" / "source_reports").glob(
            "NSE_INDEX-2026-07-30-price-*.bin"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"wrong,shape\n1,2\n"


def test_integrity_scan_accepts_documented_close_only_combined_index_row(
    tmp_path,
):
    manager = DataManager(_DataConfig(tmp_path))
    day = date(2026, 7, 31)
    path = tmp_path / "NSE" / "EQ" / f"{day}-NSE-EQ.txt"
    path.write_text(
        "ABC,20260731,10,12,9,11,100,50,50\n"
        "NIFTY TEST,20260731,,,,110,0,,\n",
        encoding="utf-8",
    )
    assert manager.validate_daily_output("NSE", "EQ", day, path)


def test_integrity_scan_accepts_existing_v1_0_1_seven_column_daily_file(
    tmp_path,
):
    manager = DataManager(_DataConfig(tmp_path))
    day = date(2025, 8, 7)
    path = tmp_path / "NSE" / "EQ" / f"{day}-NSE-EQ.txt"
    original = b"ABC,20250807,10,12,9,11,100\n"
    path.write_bytes(original)

    assert manager.validate_daily_output("NSE", "EQ", day, path)
    assert manager.get_available_file_dates("NSE", "EQ") == [day]
    assert path.read_bytes() == original


def test_integrity_scan_rejects_nonfinite_and_fake_close_only_rows(tmp_path):
    manager = DataManager(_DataConfig(tmp_path))
    day = date(2026, 7, 31)
    path = tmp_path / "NSE" / "EQ" / f"{day}-NSE-EQ.txt"
    path.write_text(
        "ABC,20260731,10,12,9,nan,100,50,50\n",
        encoding="utf-8",
    )
    assert not manager.validate_daily_output("NSE", "EQ", day, path)

    path.write_text(
        "FAKE INDEX,20260731,,,,110,0,1,2\n",
        encoding="utf-8",
    )
    assert not manager.validate_daily_output("NSE", "EQ", day, path)
