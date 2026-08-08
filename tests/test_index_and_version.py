from datetime import date
import logging
from pathlib import Path
import re

import pandas as pd
import pytest

from src.downloaders.bse_index_downloader import BSEIndexDownloader
from src.downloaders.nse_index_downloader import NSEIndexDownloader
from src.services.canonical_data import INDEX_DAILY_COLUMNS
from src.core.exceptions import DataProcessingError
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


def test_nse_index_does_not_replace_a_wrong_source_date():
    day = date(2026, 7, 30)
    raw = pd.DataFrame([{
        "Index Name": "NIFTY 50", "Index Date": "29-07-2026",
        "Open Index Value": 25000, "High Index Value": 25100,
        "Low Index Value": 24900, "Closing Index Value": 25050,
    }])
    with pytest.raises(DataProcessingError, match="date mismatch"):
        _bare_downloader(NSEIndexDownloader).transform_data(raw, day)


def test_nse_index_allows_blank_optional_ohlc_but_rejects_bad_text():
    day = date(2026, 7, 30)
    close_only = pd.DataFrame([{
        "Index Name": "NIFTY TEST", "Index Date": "30-07-2026",
        "Open Index Value": None, "High Index Value": None,
        "Low Index Value": None, "Closing Index Value": 100,
    }])
    result = _bare_downloader(NSEIndexDownloader).transform_data(close_only, day)
    assert result.loc[0, "CLOSE"] == 100

    close_only.loc[0, "Open Index Value"] = "-"
    result = _bare_downloader(NSEIndexDownloader).transform_data(close_only, day)
    assert pd.isna(result.loc[0, "OPEN"])

    close_only.loc[0, "Open Index Value"] = "not-a-number"
    with pytest.raises(DataProcessingError, match="numeric Open Index Value"):
        _bare_downloader(NSEIndexDownloader).transform_data(close_only, day)


def test_version_history_drives_update_notification():
    assert get_version() == "1.1.0"
    notes = VERSION_HISTORY["1.1.0"]
    assert notes["release_date"] == "2026-08-09"
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


def test_version_metadata_remains_readable_by_v1_0_1_clients():
    content = Path("version.py").read_text(encoding="utf-8")

    version = re.search(
        r'__version__\s*=\s*["\']([^"\']+)["\']', content
    )
    build_date = re.search(
        r'__build_date__\s*=\s*["\']([^"\']+)["\']', content
    )
    history = re.search(
        r'VERSION_HISTORY\s*=\s*({.*?})\s*(?=\n\w|\ndef|\Z)',
        content,
        re.DOTALL,
    )

    assert version is not None and version.group(1) == "1.1.0"
    assert build_date is not None
    assert history is not None
    assert '"1.1.0"' in history.group(1)
