import asyncio
from datetime import date
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.core.base_downloader import BaseDownloader
from src.services.delivery_state import PendingDeliveryStore


def test_pending_delivery_store_is_idempotent(tmp_path):
    store = PendingDeliveryStore(tmp_path)
    day = date(2025, 10, 14)
    store.add("NSE", "EQ", day)
    store.add("NSE", "EQ", day)
    assert store.dates("NSE", "EQ") == [day]
    store.discard("NSE", "EQ", day)
    assert store.dates("NSE", "EQ") == []


class _CashDownloader(BaseDownloader):
    def __init__(self, base_path):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = SimpleNamespace(
            base_data_path=Path(base_path),
            download_settings=SimpleNamespace(timeout_seconds=5),
        )
        self.logger = logging.getLogger("test.delivery.workflow")
        self.completed_files = 0
        self.total_files = 0
        self.progress_callback = None
        self.saved_delivery = []
        self.notices = []

    def build_url(self, target_date):
        return f"https://example.test/{target_date}.zip"

    def process_downloaded_data(self, file_data, file_date, delivery_data=None):
        self.saved_delivery.append(delivery_data)
        return pd.DataFrame([{
            "SYMBOL": "ABC", "DATE": file_date.strftime("%Y%m%d"),
            "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": 1,
            "VOLUME": 1, "DELIVERY_QTY": pd.NA,
            "DELIVERY_PERCENT": pd.NA,
        }])

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return await self._download_equity_implementation(working_days)

    def save_processed_data(self, df, target_date):
        return self.config.base_data_path / f"{target_date}.txt"

    def get_download_option(self, name, default=None):
        return {
            "include_delivery_data": True,
            "generate_symbol_files": False,
            "apply_corporate_actions": False,
        }.get(name, default)

    async def update_async_session_timeout(self, manager, timeout):
        return None

    def _report_notice(self, notice):
        self.notices.append(notice)

    def _report_error(self, error):
        raise AssertionError(error)


class _FakeDownloadManager:
    delivery_available = False

    def __init__(self, config):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def download_multiple(self, tasks):
        price = SimpleNamespace(success=True, file_data=b"price", error_message="")
        delivery = SimpleNamespace(
            success=self.delivery_available,
            file_data=b"delivery" if self.delivery_available else None,
            error_message="404" if not self.delivery_available else "",
        )
        return [price, delivery]


def test_missing_delivery_saves_price_and_next_run_clears_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "src.utils.async_downloader.AsyncDownloadManager", _FakeDownloadManager
    )
    day = date(2025, 10, 14)
    downloader = _CashDownloader(tmp_path)
    store = PendingDeliveryStore(tmp_path)

    _FakeDownloadManager.delivery_available = False
    assert asyncio.run(downloader._download_equity_implementation([day]))
    assert downloader.saved_delivery == [None]
    assert store.dates("NSE", "EQ") == [day]

    _FakeDownloadManager.delivery_available = True
    assert asyncio.run(downloader._download_equity_implementation([]))
    assert downloader.saved_delivery[-1] == b"delivery"
    assert store.dates("NSE", "EQ") == []
