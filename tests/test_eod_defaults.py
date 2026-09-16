"""The database mirror ships on; the two settings that read it ship off.

Decided by the owner on 2026-09-16, after testing v1.2.0: a fresh install should start
collecting `.state/eod.sqlite3` from its first download, because the settings that read
the database can only read what the mirror has already written, and a user who turned
one of them on months later would find nothing there. It costs about 450 MB a year for
all six segments.

These pin each default where it is decided -- the shipped configuration, the built-in
preferences and the call site that asks -- because changing only one of them would
leave another in charge.
"""

from __future__ import annotations

from datetime import date
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.core.base_downloader import BaseDownloader
from src.core.config import Config
from src.services.canonical_data import EQUITY_DAILY_COLUMNS
from src.services.pipeline_state import PipelineManifest
from src.services.settings import SettingsService
from src.utils.user_preferences import UserPreferences

DAY = date(2026, 7, 31)


def test_the_shipped_configuration_mirrors_but_still_reads_the_files():
    options = SettingsService(Config("config.yaml")).download_options()

    assert options.get("dual_write_eod_database") is True
    assert options.get("publish_histories_from_database") is False
    assert options.get("read_snapshots_from_database") is False


def test_a_fresh_settings_file_starts_with_the_mirror_on(tmp_path):
    """What a fresh install, or a deleted settings folder, gives a user.

    The file is written from these defaults the first time it is saved, and a value
    already in the file wins over the default afterwards -- so this is the only moment
    at which the shipped default decides what a user ends up with.
    """

    preferences = UserPreferences()

    assert preferences.get_download_options()["dual_write_eod_database"] is True
    assert preferences.save_preferences()
    written = json.loads(
        (tmp_path / ".nse_bse_downloader" / "user_preferences.json").read_text(
            encoding="utf-8"
        )
    )
    assert written["download_options"]["dual_write_eod_database"] is True


class _Config:
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


class _Downloader(BaseDownloader):
    """The real option lookup: nothing here overrides get_download_option."""

    def __init__(self, base_path):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = _Config(Path(base_path))
        self.logger = logging.getLogger("test.eod.defaults")
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


def test_publishing_with_default_settings_writes_the_database(tmp_path):
    """The first download of a fresh install has to create the mirror itself."""

    downloader = _Downloader(tmp_path)
    downloader._begin_pipeline_date(DAY)
    frame = pd.DataFrame(
        [["ABC", "20260731", 10.0, 12.0, 9.0, 11.0, 100, 50, 50.0, 1100.0, 9.5]],
        columns=EQUITY_DAILY_COLUMNS,
    )

    downloader.save_processed_data(frame, DAY)

    assert (downloader.data_path / f"{DAY}-NSE-EQ.txt").is_file()
    assert (tmp_path / ".state" / "eod.sqlite3").is_file()
    assert downloader.config.eod_store.row_count() == 1
