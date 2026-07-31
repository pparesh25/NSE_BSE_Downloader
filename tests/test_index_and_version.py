from datetime import date
import logging
from pathlib import Path

import pandas as pd

from src.downloaders.bse_index_downloader import BSEIndexDownloader
from src.downloaders.nse_index_downloader import NSEIndexDownloader
from src.services.canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from src.services.memory_append_manager import MemoryAppendManager
from src.utils.memory_optimizer import MemoryOptimizer
from src.utils.update_checker import UpdateChecker
from version import VERSION_HISTORY, get_version


def _bare_downloader(downloader_class):
    downloader = object.__new__(downloader_class)
    downloader.memory_optimizer = MemoryOptimizer()
    downloader.logger = logging.getLogger("test.index")
    return downloader


def test_nse_and_bse_index_transform_to_named_seven_columns():
    day = date(2026, 7, 30)
    nse_raw = pd.DataFrame([{
        "Index Name": "NIFTY 50", "Index Date": "30-07-2026",
        "Open Index Value": 25000, "High Index Value": 25100,
        "Low Index Value": 24900, "Closing Index Value": 25050,
        "Volume": 123,
    }])
    bse_raw = pd.DataFrame([{
        "IndexName": "SENSEX", "OpenPrice": 80000, "HighPrice": 80100,
        "LowPrice": 79900, "ClosePrice": 80050,
    }])

    nse = _bare_downloader(NSEIndexDownloader).transform_data(nse_raw, day)
    bse = _bare_downloader(BSEIndexDownloader).transform_data(bse_raw, day)
    assert list(nse.columns) == INDEX_DAILY_COLUMNS
    assert list(bse.columns) == INDEX_DAILY_COLUMNS
    assert nse.loc[0, "DATE"] == bse.loc[0, "DATE"] == "20260730"


def test_index_append_uses_names_and_leaves_delivery_blank():
    manager = object.__new__(MemoryAppendManager)
    manager.logger = logging.getLogger("test.append")
    base = pd.DataFrame([["ABC", "20260730", 1, 2, 1, 2, 100, 50, 50]],
                        columns=EQUITY_DAILY_COLUMNS)
    index = pd.DataFrame([["NIFTY 50", "20260730", 1, 2, 1, 2, 0]],
                         columns=INDEX_DAILY_COLUMNS)
    aligned = manager._align_columns_for_append(index, base)
    assert list(aligned.columns) == EQUITY_DAILY_COLUMNS
    assert aligned.loc[0, "DELIVERY_QTY"] == ""
    assert aligned.loc[0, "DELIVERY_PERCENT"] == ""


def test_version_history_drives_update_notification():
    assert get_version() == "1.1.0"
    notes = VERSION_HISTORY["1.1.0"]
    assert notes["release_date"] == "2026-07-31"
    assert any("delivery" in item.lower() for item in notes["features"])
    assert any("open interest" in item.lower() for item in notes["features"])
    assert any("calendar" in item.lower() for item in notes["features"])

    checker = object.__new__(UpdateChecker)
    checker.logger = logging.getLogger("test.update")
    checker.download_url = "https://example.test/update.zip"
    parsed = checker._parse_github_version_file(
        Path("version.py").read_text(encoding="utf-8")
    )
    assert parsed["latest_version"] == "1.1.0"
    assert any(
        "delivery" in item.lower()
        for item in parsed["changelog"]["features"]
    )
    assert any(
        "calendar" in item.lower()
        for item in parsed["changelog"]["features"]
    )
