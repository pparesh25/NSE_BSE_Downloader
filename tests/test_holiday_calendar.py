"""Phase 2.3 gates: a known calendar offline, and holidays that stop returning.

Two failure modes are pinned here. A holiday the application does not know
about is queued as a trading day, downloads a 404, and -- because the date
stays at ``complete=0`` -- is re-downloaded on every future run forever. And a
year the official API cannot answer for used to be indistinguishable from a
year with no holidays.
"""

from __future__ import annotations

import asyncio
from datetime import date
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.base_downloader import (
    ABSENT_REPORT_SETTLE_DAYS,
    AbsentReportLedger,
    BaseDownloader,
)
from src.services.pipeline_state import PipelineManifest
from src.utils import holiday_calendar
from src.utils.date_utils import DateUtils
from src.utils.holiday_manager import HolidayManager


REPUBLIC_DAY_2015 = date(2015, 1, 26)


def _manager(tmp_path: Path, **kwargs) -> HolidayManager:
    return HolidayManager(tmp_path / "cache", **kwargs)


def _blocked(monkeypatch) -> None:
    """Make every live holiday request fail, the way NSE blocking does."""

    def refuse(*_args, **_kwargs):
        raise TimeoutError("nseindia did not answer")

    monkeypatch.setattr(
        "src.utils.holiday_manager.fetch_text_sync", refuse
    )


def _answers(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        "src.utils.holiday_manager.fetch_text_sync",
        lambda *_args, **_kwargs: json.dumps(payload),
    )


def _answers_per_year(monkeypatch, day: int = 26, month: str = "Jan") -> None:
    """Answer every requested year with one holiday inside that year."""

    def respond(url, *_args, **_kwargs):
        year = url.rsplit("=", 1)[1]
        return json.dumps(
            {"CM": [{"tradingDate": f"{day:02d}-{month}-{year}"}]}
        )

    monkeypatch.setattr(
        "src.utils.holiday_manager.fetch_text_sync", respond
    )


# --------------------------------------------------------------------------
# The bundled calendar
# --------------------------------------------------------------------------


def test_the_bundle_covers_every_year_the_api_serves():
    years = sorted(holiday_calendar.covered_years())
    assert years == list(
        range(holiday_calendar.FIRST_YEAR, holiday_calendar.LAST_YEAR + 1)
    )
    assert holiday_calendar.FIRST_YEAR == 2013, (
        "the API answers {} for earlier years; see tools/generate_holiday_calendar.py"
    )
    for year in years:
        # NSE has run between 14 and 23 capital-market holidays a year. A count
        # outside that means a regeneration truncated or duplicated a year.
        assert 12 <= len(holiday_calendar.HOLIDAYS[year]) <= 30, year
        parsed = holiday_calendar.holidays_for_year(year)
        assert parsed is not None
        assert {value.year for value in parsed} == {year}


def test_an_uncovered_year_answers_none_rather_than_an_empty_set():
    """Empty and unknown must not look the same; no NSE year has 0 holidays."""

    assert holiday_calendar.holidays_for_year(2010) is None
    assert holiday_calendar.holidays_for_year(2100) is None


def test_the_bundled_calendar_answers_when_the_api_is_unreachable(
    tmp_path, monkeypatch
):
    _blocked(monkeypatch)
    manager = _manager(tmp_path)

    assert manager.is_holiday(REPUBLIC_DAY_2015) is True

    # Mirror: with the bundled layer removed, the same call answers "trading
    # day", which is the defect this phase exists to remove.
    monkeypatch.setattr(
        holiday_calendar, "holidays_for_year", lambda _year: None
    )
    assert _manager(tmp_path / "b").is_holiday(REPUBLIC_DAY_2015) is False


def test_an_empty_api_year_is_a_failure_not_an_empty_calendar(
    tmp_path, monkeypatch
):
    _answers(monkeypatch, {"CM": []})
    manager = _manager(tmp_path)

    assert manager.fetch_holidays_for_year(2015) is None
    # And the year still resolves, from the bundle rather than from silence.
    assert manager.is_holiday(REPUBLIC_DAY_2015) is True


def test_a_live_calendar_supersedes_the_bundled_one(tmp_path, monkeypatch):
    """NSE amends calendars; the live answer must win where it exists."""

    _answers(monkeypatch, {"CM": [{"tradingDate": "02-Jan-2015"}]})
    manager = _manager(tmp_path)

    assert manager.is_holiday(date(2015, 1, 2)) is True
    assert manager.is_holiday(REPUBLIC_DAY_2015) is False


def test_a_year_no_source_can_answer_is_reported_rather_than_assumed(
    tmp_path, monkeypatch, caplog
):
    _blocked(monkeypatch)
    manager = _manager(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert manager.has_calendar(2005) is False
    assert any("No trading calendar available for 2005" in record.message
               for record in caplog.records)
    assert manager.has_calendar(2015) is True


def test_the_bundle_is_never_written_into_the_on_disk_cache(
    tmp_path, monkeypatch
):
    """A cache that claims years it never fetched would hide a broken API."""

    _blocked(monkeypatch)
    manager = _manager(tmp_path)
    manager.is_holiday(REPUBLIC_DAY_2015)

    assert not manager.cache_file.exists()


def test_refresh_reports_the_live_outcome_not_the_bundle(
    tmp_path, monkeypatch
):
    _blocked(monkeypatch)
    assert _manager(tmp_path).refresh_holidays() is False

    _answers_per_year(monkeypatch)
    assert _manager(tmp_path / "ok").refresh_holidays() is True


# --------------------------------------------------------------------------
# A 404 the exchange means: dates that stop coming back
# --------------------------------------------------------------------------


class _FakeManager:
    """Stands in for AsyncDownloadManager with a scripted answer per date."""

    answers: dict[date, object] = {}

    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def download_multiple(self, tasks):
        return [self.answers[task.target_date] for task in tasks]


def _absent() -> SimpleNamespace:
    return SimpleNamespace(
        success=False,
        file_data=None,
        error_message="File not available - may not be published yet",
        outcome="file_not_found",
    )


def _blocked_result() -> SimpleNamespace:
    return SimpleNamespace(
        success=False,
        file_data=None,
        error_message="Access denied - server may be blocking requests",
        outcome="access_denied",
    )


def _published() -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        file_data=b"payload",
        error_message="",
        outcome="success",
    )


class _ProbeDownloader(BaseDownloader):
    """Minimal price-only downloader driving the real pipeline bookkeeping."""

    def __init__(self, base_path: Path):
        self.exchange = "NSE"
        self.segment = "INDEX"
        self.exchange_segment = "NSE_INDEX"
        self.config = SimpleNamespace(
            base_data_path=Path(base_path),
            download_settings=SimpleNamespace(timeout_seconds=5),
        )
        self.logger = logging.getLogger("test.absent")
        self.completed_files = 0
        self.total_files = 0
        self.progress_callback = None
        self.errors: list[str] = []
        self.notices: list[str] = []
        self.saved: list[date] = []
        self.pipeline_manifest = PipelineManifest(base_path)

    def build_url(self, target_date):
        return "https://example.test/report.csv"

    def process_downloaded_data(self, file_data, file_date):
        return pd.DataFrame([{"SYMBOL": "ABC"}])

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return await self._download_price_implementation(working_days)

    def save_processed_data(self, df, target_date):
        # Stands in for the real publish, whose only relevance here is that it
        # completes the daily stage; without it every date stays partial and
        # the retry-queue assertions below could not tell the cases apart.
        self.saved.append(target_date)
        self._mark_pipeline(target_date, "daily", "complete", path="daily.txt")
        return Path("daily.txt")

    def get_download_option(self, name, default=None):
        return default

    async def update_async_session_timeout(self, manager, timeout):
        return None

    def _report_error(self, error):
        self.errors.append(error)

    def _report_notice(self, notice):
        self.notices.append(notice)


@pytest.fixture
def frozen_today(monkeypatch):
    today = date(2026, 8, 6)
    monkeypatch.setattr(DateUtils, "today_ist", classmethod(lambda cls: today))
    return today


def _run(downloader, days, answers, monkeypatch):
    monkeypatch.setattr(
        "src.utils.async_downloader.AsyncDownloadManager", _FakeManager
    )
    _FakeManager.answers = answers
    return asyncio.run(downloader._download_price_implementation(days))


def test_a_settled_404_is_retired_once_the_year_proves_itself(
    tmp_path, monkeypatch, frozen_today
):
    holiday = date(2010, 1, 26)
    traded = date(2010, 1, 27)
    downloader = _ProbeDownloader(tmp_path)

    _run(
        downloader,
        [holiday, traded],
        {holiday: _absent(), traded: _published()},
        monkeypatch,
    )

    manifest = downloader.pipeline_manifest
    assert manifest.date_result("NSE", "INDEX", holiday).status == "skipped"
    assert manifest.date_result("NSE", "INDEX", traded).status == "success"
    # The point of the whole change: it is no longer queued for the next run.
    assert manifest.incomplete_dates("NSE", "INDEX") == []
    assert downloader.errors == []
    assert downloader.notices and "published no report" in downloader.notices[0]


def test_a_404_stays_queued_when_nothing_in_that_year_downloaded(
    tmp_path, monkeypatch, frozen_today
):
    """A wrong URL era 404s every date; retiring those would hide it."""

    first = date(2010, 1, 26)
    second = date(2010, 1, 27)
    downloader = _ProbeDownloader(tmp_path)

    _run(
        downloader,
        [first, second],
        {first: _absent(), second: _absent()},
        monkeypatch,
    )

    manifest = downloader.pipeline_manifest
    assert manifest.incomplete_dates("NSE", "INDEX") == [first, second]
    assert downloader.notices == []
    assert downloader.errors and "the source itself may be wrong" in (
        downloader.errors[0]
    )


def test_evidence_does_not_carry_across_years(
    tmp_path, monkeypatch, frozen_today
):
    absent_year = date(2010, 1, 26)
    other_year = date(2011, 1, 27)
    downloader = _ProbeDownloader(tmp_path)

    _run(
        downloader,
        [absent_year, other_year],
        {absent_year: _absent(), other_year: _published()},
        monkeypatch,
    )

    assert downloader.pipeline_manifest.incomplete_dates(
        "NSE", "INDEX"
    ) == [absent_year]


def test_a_recent_404_is_not_retired_yet(tmp_path, monkeypatch, frozen_today):
    """Reports can be late; only a settled date's silence is conclusive."""

    from datetime import timedelta

    recent = frozen_today - timedelta(days=ABSENT_REPORT_SETTLE_DAYS - 1)
    traded = frozen_today - timedelta(days=ABSENT_REPORT_SETTLE_DAYS + 1)
    downloader = _ProbeDownloader(tmp_path)

    _run(
        downloader,
        [traded, recent],
        {traded: _published(), recent: _absent()},
        monkeypatch,
    )

    assert downloader.pipeline_manifest.incomplete_dates(
        "NSE", "INDEX"
    ) == [recent]


def test_only_a_not_found_answer_counts_as_absent(
    tmp_path, monkeypatch, frozen_today
):
    """A block or a timeout is a failure to ask, not an answer about the date."""

    blocked = date(2010, 1, 26)
    traded = date(2010, 1, 27)
    downloader = _ProbeDownloader(tmp_path)

    _run(
        downloader,
        [blocked, traded],
        {blocked: _blocked_result(), traded: _published()},
        monkeypatch,
    )

    assert downloader.pipeline_manifest.incomplete_dates(
        "NSE", "INDEX"
    ) == [blocked]
    assert downloader.notices == []


def test_ledger_settles_only_what_the_run_can_explain():
    ledger = AbsentReportLedger()
    ledger.note_absent(date(2010, 1, 26), "no report")
    ledger.note_absent(date(2011, 1, 26), "no report")
    ledger.note_downloaded(date(2010, 3, 1))

    assert [day for day, _ in ledger.settled()] == [date(2010, 1, 26)]
