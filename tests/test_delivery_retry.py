"""Phase 2.4 gates: a delivery report that will never exist stops being asked for.

NSE published no separate delivery report before 2019-09-30 and BSE none before
2006-01-02, while both price archives go back much further.  A backfill below
those floors used to queue a download that can never succeed, hold the date
below ``complete`` forever, and re-queue it from *two* places on every run --
the pending-delivery store and ``incomplete_dates``.  Both have to release the
date together, which is most of what is pinned here.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.base_downloader import BaseDownloader
from src.services.delivery_state import (
    MAX_DELIVERY_ATTEMPTS,
    MAX_PENDING_DELIVERY_DAYS,
    PendingDeliveryStore,
)
from src.services.pipeline_state import PipelineManifest
from src.services.source_resolver import (
    delivery_available,
    delivery_first_available,
)
from src.utils.date_utils import DateUtils


TODAY = date(2026, 8, 6)
NSE_FLOOR = date(2019, 9, 30)


# --------------------------------------------------------------------------
# The floors themselves
# --------------------------------------------------------------------------


def test_delivery_floors_match_what_the_archives_answer():
    """Pinned by request; see the era note in source_resolver.py."""

    assert delivery_first_available("NSE") == date(2019, 9, 30)
    assert delivery_first_available("BSE") == date(2006, 1, 2)
    assert delivery_first_available("MCX") is None

    # 2019-09-27 (Fri) and 2019-09-30 (Mon) are consecutive trading days.
    assert delivery_available("NSE", date(2019, 9, 27)) is False
    assert delivery_available("NSE", date(2019, 9, 30)) is True
    assert delivery_available("BSE", date(2005, 12, 30)) is False
    assert delivery_available("BSE", date(2006, 1, 2)) is True
    # An exchange with no known floor is never held back by one.
    assert delivery_available("MCX", date(1995, 1, 2)) is True


# --------------------------------------------------------------------------
# The pending store
# --------------------------------------------------------------------------


def _seed_v1(tmp_path: Path, key: str, dates: list[str]) -> None:
    state = tmp_path / ".state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "pending_delivery.json").write_text(
        json.dumps({"version": 1, "pending": {key: dates}}), encoding="utf-8"
    )


def test_a_version_one_state_file_migrates_and_retires_at_once(tmp_path):
    """Old entries carry no queue date, so they must not get a fresh lease."""

    _seed_v1(tmp_path, "NSE_EQ", ["2020-01-02", "2020-01-03"])
    store = PendingDeliveryStore(tmp_path)

    assert store.dates("NSE", "EQ") == [date(2020, 1, 2), date(2020, 1, 3)]
    retired = store.expire("NSE", "EQ", today=TODAY)

    assert [day for day, _ in retired] == [date(2020, 1, 2), date(2020, 1, 3)]
    assert all("30 days" in reason for _, reason in retired)
    assert store.dates("NSE", "EQ") == []


def test_a_date_below_the_floor_is_retired_with_the_reason_that_fits(tmp_path):
    store = PendingDeliveryStore(tmp_path)
    store.add("NSE", "EQ", date(2015, 6, 10), today=TODAY)

    retired = store.expire(
        "NSE", "EQ", today=TODAY, first_available=NSE_FLOOR
    )

    assert [day for day, _ in retired] == [date(2015, 6, 10)]
    assert "no delivery report before 2019-09-30" in retired[0][1]


def test_a_genuinely_late_report_is_still_waited_for(tmp_path):
    """The feature this store exists for must survive the new bound."""

    store = PendingDeliveryStore(tmp_path)
    yesterday = TODAY - timedelta(days=1)
    store.add("NSE", "EQ", yesterday, today=TODAY)

    assert store.expire(
        "NSE", "EQ", today=TODAY, first_available=NSE_FLOOR
    ) == []
    assert store.dates("NSE", "EQ") == [yesterday]


def test_the_age_bound_fires_on_the_day_it_says(tmp_path):
    store = PendingDeliveryStore(tmp_path)
    queued = TODAY - timedelta(days=MAX_PENDING_DELIVERY_DAYS)
    store.add("NSE", "EQ", date(2026, 7, 1), today=queued)

    day_before = TODAY - timedelta(days=1)
    assert store.expire("NSE", "EQ", today=day_before) == []
    retired = store.expire("NSE", "EQ", today=TODAY)
    assert len(retired) == 1
    assert f"{MAX_PENDING_DELIVERY_DAYS} days" in retired[0][1]


def test_the_attempt_bound_catches_what_the_age_bound_would_not(tmp_path):
    """Someone running many times a day should not wait a month."""

    store = PendingDeliveryStore(tmp_path)
    target = date(2026, 8, 5)
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        store.add("NSE", "EQ", target, today=TODAY)

    assert store.attempts("NSE", "EQ", target) == MAX_DELIVERY_ATTEMPTS
    retired = store.expire("NSE", "EQ", today=TODAY)
    assert len(retired) == 1
    assert "attempts" in retired[0][1]


def test_a_delivered_report_clears_its_attempts(tmp_path):
    store = PendingDeliveryStore(tmp_path)
    target = date(2026, 8, 5)
    store.add("NSE", "EQ", target, today=TODAY)
    store.add("NSE", "EQ", target, today=TODAY)
    assert store.attempts("NSE", "EQ", target) == 2

    store.discard("NSE", "EQ", target)
    assert store.dates("NSE", "EQ") == []
    assert store.attempts("NSE", "EQ", target) == 0

    store.add("NSE", "EQ", target, today=TODAY)
    assert store.attempts("NSE", "EQ", target) == 1


def test_segments_keep_separate_queues(tmp_path):
    store = PendingDeliveryStore(tmp_path)
    store.add("NSE", "EQ", date(2026, 8, 5), today=TODAY)
    store.add("NSE", "SME", date(2015, 6, 10), today=TODAY)

    store.expire("NSE", "SME", today=TODAY, first_available=NSE_FLOOR)

    assert store.dates("NSE", "EQ") == [date(2026, 8, 5)]
    assert store.dates("NSE", "SME") == []


# --------------------------------------------------------------------------
# The download loop
# --------------------------------------------------------------------------


class _FakeManager:
    """Records every task requested and answers each URL from a script."""

    requested: list[str] = []
    delivery_succeeds = True

    def __init__(self, config):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def download_multiple(self, tasks):
        results = []
        for task in tasks:
            self.requested.append(task.url)
            is_delivery = "bhavdata" in task.url or "SCBSEALL" in task.url
            if is_delivery and not self.delivery_succeeds:
                results.append(SimpleNamespace(
                    success=False,
                    file_data=None,
                    error_message="File not available",
                    outcome="file_not_found",
                ))
            else:
                results.append(SimpleNamespace(
                    success=True,
                    file_data=b"payload",
                    error_message="",
                    outcome="success",
                ))
        return results


class _ProbeEQDownloader(BaseDownloader):
    """Minimal EQ downloader exercising the real delivery bookkeeping."""

    def __init__(self, base_path: Path):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = SimpleNamespace(
            base_data_path=Path(base_path),
            download_settings=SimpleNamespace(timeout_seconds=5),
        )
        self.logger = logging.getLogger("test.delivery")
        self.completed_files = 0
        self.total_files = 0
        self.progress_callback = None
        self.combined_required = False
        self.errors: list[str] = []
        self.notices: list[str] = []
        self.pipeline_manifest = PipelineManifest(base_path)

    def build_url(self, target_date):
        return f"https://example.test/price-{target_date.isoformat()}.csv"

    def process_downloaded_data(self, file_data, file_date, delivery_data=None):
        return pd.DataFrame([{"SYMBOL": "ABC"}])

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return await self._download_equity_implementation(working_days)

    def save_processed_data(self, df, target_date):
        self._mark_pipeline(target_date, "daily", "complete", path="daily.txt")
        return Path("daily.txt")

    def get_download_option(self, name, default=None):
        # Delivery on, staged history off: this probe is about one stage.
        return name == "include_delivery_data"

    async def update_async_session_timeout(self, manager, timeout):
        return None

    def _report_error(self, error):
        self.errors.append(error)

    def _report_notice(self, notice):
        self.notices.append(notice)


@pytest.fixture
def frozen_today(monkeypatch):
    monkeypatch.setattr(DateUtils, "today_ist", classmethod(lambda cls: TODAY))
    return TODAY


def _run(downloader, days, monkeypatch, *, delivery_succeeds=True):
    monkeypatch.setattr(
        "src.utils.async_downloader.AsyncDownloadManager", _FakeManager
    )
    _FakeManager.requested = []
    _FakeManager.delivery_succeeds = delivery_succeeds
    return asyncio.run(downloader._download_equity_implementation(days))


def test_a_date_below_the_floor_never_asks_for_a_delivery_report(
    tmp_path, monkeypatch, frozen_today
):
    day = date(2015, 6, 10)
    downloader = _ProbeEQDownloader(tmp_path)

    assert _run(downloader, [day], monkeypatch) is True

    assert not any("bhavdata" in url for url in _FakeManager.requested)
    result = downloader.pipeline_manifest.date_result("NSE", "EQ", day)
    assert result.status == "success"
    assert "delivery" in result.disabled_stages
    assert downloader.pipeline_manifest.incomplete_dates("NSE", "EQ") == []
    assert not (tmp_path / ".state" / "pending_delivery.json").exists()


def test_a_date_above_the_floor_still_requests_and_queues_delivery(
    tmp_path, monkeypatch, frozen_today
):
    """The late-report feature must survive: this is what the store is for."""

    day = TODAY - timedelta(days=1)
    downloader = _ProbeEQDownloader(tmp_path)

    _run(downloader, [day], monkeypatch, delivery_succeeds=False)

    assert any("bhavdata" in url for url in _FakeManager.requested)
    store = PendingDeliveryStore(tmp_path)
    assert store.dates("NSE", "EQ") == [day]
    assert store.attempts("NSE", "EQ", day) == 1
    assert downloader.pipeline_manifest.incomplete_dates("NSE", "EQ") == [day]


def test_an_expired_pending_date_is_released_by_both_queues(
    tmp_path, monkeypatch, frozen_today
):
    """The whole point: one queue releasing alone just moves the loop."""

    day = date(2020, 1, 2)
    _seed_v1(tmp_path, "NSE_EQ", [day.isoformat()])
    downloader = _ProbeEQDownloader(tmp_path)
    manifest = downloader.pipeline_manifest
    manifest.begin(
        "NSE", "EQ", day, ("downloaded", "validated", "daily", "delivery")
    )
    for stage in ("downloaded", "validated", "daily"):
        manifest.mark("NSE", "EQ", day, stage, "complete")
    manifest.mark("NSE", "EQ", day, "delivery", "failed", error="pending")
    assert manifest.incomplete_dates("NSE", "EQ") == [day]

    # No dates selected: the sweep alone must clear it.
    _run(downloader, [], monkeypatch)

    assert PendingDeliveryStore(tmp_path).dates("NSE", "EQ") == []
    assert manifest.incomplete_dates("NSE", "EQ") == []
    assert manifest.date_result("NSE", "EQ", day).status == "success"
    assert downloader.notices and "stopped retrying" in downloader.notices[0]


def test_a_retired_date_does_not_come_back_on_the_next_run(
    tmp_path, monkeypatch, frozen_today
):
    day = date(2020, 1, 2)
    _seed_v1(tmp_path, "NSE_EQ", [day.isoformat()])
    first = _ProbeEQDownloader(tmp_path)
    first.pipeline_manifest.begin(
        "NSE", "EQ", day, ("downloaded", "validated", "daily", "delivery")
    )
    for stage in ("downloaded", "validated", "daily"):
        first.pipeline_manifest.mark("NSE", "EQ", day, stage, "complete")
    _run(first, [], monkeypatch)

    second = _ProbeEQDownloader(tmp_path)
    _run(second, [], monkeypatch)

    assert second.pipeline_manifest.incomplete_dates("NSE", "EQ") == []
    assert PendingDeliveryStore(tmp_path).dates("NSE", "EQ") == []
    assert _FakeManager.requested == []


def test_a_segment_reports_success_despite_a_disabled_delivery_stage(
    tmp_path, monkeypatch, frozen_today
):
    """`_core_pipeline_ok` reads segment-level requirements; a stage the date
    itself disabled can never complete and must not hold the segment back."""

    old = date(2015, 6, 10)
    recent = TODAY - timedelta(days=2)
    downloader = _ProbeEQDownloader(tmp_path)

    assert _run(downloader, [old, recent], monkeypatch) is True

    result = downloader.pipeline_manifest.segment_result(
        "NSE", "EQ", [old, recent]
    )
    assert result.ok
    assert result.success_count == 2
