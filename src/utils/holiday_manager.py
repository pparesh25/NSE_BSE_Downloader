"""Official NSE trading-calendar retrieval with bounded local caching."""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from . import holiday_calendar
from .http_client import HTTPStatusError, fetch_text_sync


class HolidayManager:
    """Fetch and cache capital-market trading holidays by calendar year.

    Three layers answer one question, in order of authority: the official API,
    the on-disk cache it fills, and the calendar bundled with the application.
    The bundled layer exists because the first two can both be unavailable --
    NSE blocks unfamiliar clients regularly -- and the old behaviour in that
    case was to answer "this year has no holidays", which is indistinguishable
    from a real answer and turns every holiday into a permanently failed date.
    """

    SOURCE_TEMPLATE = (
        "https://www.nseindia.com/api/holiday-master"
        "?type=trading&year={year}"
    )
    SOURCE_PAGE = (
        "https://www.nseindia.com/resources/exchange-communication-holidays"
    )
    SOURCE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/126 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": SOURCE_PAGE,
    }

    def __init__(
        self,
        cache_dir: Path,
        cache_ttl: timedelta = timedelta(hours=24),
        now_provider: Optional[Callable[[], datetime]] = None,
        start_year: int = 2025,
    ):
        self.logger = logging.getLogger(__name__)
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "market_holidays.json"
        self.cache_ttl = cache_ttl
        self.start_year = start_year
        self._now_provider = now_provider
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._holidays_cache: Set[date] = set()
        self._cache_years: Set[int] = set()
        self._cache_loaded = False
        self._cache_loaded_at: Optional[datetime] = None
        self._refresh_attempted_at: Optional[datetime] = None
        self._attempted_years: Set[int] = set()
        self._last_refresh_ok = True

    def _now(self) -> datetime:
        current = (
            self._now_provider()
            if self._now_provider is not None
            else datetime.now(timezone.utc)
        )
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _default_years(self) -> Set[int]:
        current_year = self._now().year
        start = min(self.start_year, current_year)
        return set(range(start, current_year + 1))

    def fetch_holidays_for_year(self, year: int) -> Optional[Set[date]]:
        """Return the official NSE CM calendar, or none when refresh fails."""

        url = self.SOURCE_TEMPLATE.format(year=year)
        try:
            content = fetch_text_sync(
                url,
                timeout=10,
                headers=self.SOURCE_HEADERS,
            )
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise ValueError("holiday response must contain an object")
            records = payload.get("CM")
            if not isinstance(records, list):
                raise ValueError("holiday response has no CM calendar")
            holidays: Set[date] = set()
            for record in records:
                if not isinstance(record, dict):
                    continue
                raw_date = record.get("tradingDate")
                if not raw_date:
                    continue
                parsed = datetime.strptime(str(raw_date), "%d-%b-%Y").date()
                if parsed.year == year:
                    holidays.add(parsed)
            if not holidays:
                # No NSE year has ever had zero capital-market holidays, so an
                # empty calendar means the API has no data for this year -- as
                # it does for every year before 2013.  Returning an empty set
                # here would record that as a successful refresh and answer
                # "not a holiday" for every date in the year.
                raise ValueError("holiday response has an empty CM calendar")
            self.logger.info(
                "Fetched %s official NSE holidays for %s",
                len(holidays),
                year,
            )
            return holidays
        except (HTTPStatusError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            self.logger.error(
                "Failed to fetch official NSE holidays for %s: %s",
                year,
                error,
            )
            return None
        except Exception as error:
            self.logger.error(
                "Unexpected holiday refresh error for %s: %s",
                year,
                error,
            )
            return None

    def parse_holiday_dates(self, holiday_lines: List[str]) -> Set[date]:
        """Parse legacy line-oriented calendars retained in old caches/tests."""

        holidays: Set[date] = set()
        formats = (
            "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
            "%d %b %Y", "%d %B %Y",
        )
        patterns = (
            r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
            r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b",
            r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
        )
        for line in holiday_lines:
            candidates = [line.strip()]
            for pattern in patterns:
                candidates.extend(re.findall(pattern, line))
            parsed = None
            for candidate in candidates:
                for value_format in formats:
                    try:
                        parsed = datetime.strptime(candidate, value_format).date()
                        break
                    except ValueError:
                        continue
                if parsed is not None:
                    holidays.add(parsed)
                    break
        return holidays

    def save_holidays_to_cache(
        self,
        holidays: Set[date],
        years: Optional[Set[int]] = None,
    ) -> None:
        """Atomically save a year-tagged calendar cache."""

        try:
            covered_years = years or {value.year for value in holidays}
            cache_data = {
                "holidays": sorted(value.isoformat() for value in holidays),
                "years": sorted(covered_years),
                "last_updated": self._now().isoformat(),
                "source": self.SOURCE_PAGE,
            }
            temporary = self.cache_file.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(cache_data, handle, indent=2)
            temporary.replace(self.cache_file)
        except Exception as error:
            self.logger.error("Failed to save holiday cache: %s", error)

    def _load_cache_record(
        self,
    ) -> Tuple[Set[date], Optional[datetime], Set[int]]:
        if not self.cache_file.exists():
            return set(), None, set()
        with self.cache_file.open("r", encoding="utf-8") as handle:
            cache_data = json.load(handle)
        if not isinstance(cache_data, dict):
            raise ValueError("holiday cache must contain an object")
        raw_holidays = cache_data.get("holidays", [])
        if not isinstance(raw_holidays, list):
            raise ValueError("holiday cache list is invalid")
        holidays = {date.fromisoformat(str(value)) for value in raw_holidays}
        raw_years = cache_data.get("years")
        years = (
            {int(value) for value in raw_years}
            if isinstance(raw_years, list)
            else {value.year for value in holidays}
        )
        raw_updated = cache_data.get("last_updated")
        updated_at = (
            datetime.fromisoformat(str(raw_updated)) if raw_updated else None
        )
        if updated_at is not None:
            updated_at = (
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None
                else updated_at.astimezone(timezone.utc)
            )
        return holidays, updated_at, years

    def cached_calendar(self) -> Tuple[Set[date], Set[int]]:
        """Holidays already on disk, and the years they actually cover.

        ``get_holidays`` reaches the network when a year is missing or stale,
        which a read-only diagnostic such as ``--audit`` must not do: an audit
        that hangs on a broken TLS stack is no use for diagnosing one.  This
        reports only what is already known, and says which years that is, so a
        caller can decline to judge a year whose calendar it does not have
        rather than mistake every holiday in it for a missing trading day.
        """

        try:
            holidays, _updated_at, years = self._load_cache_record()
        except Exception as error:
            self.logger.error("Failed to load holiday cache: %s", error)
            return set(), set()
        return holidays, years

    def load_holidays_from_cache(self) -> Set[date]:
        try:
            holidays, _, _ = self._load_cache_record()
            return holidays
        except Exception as error:
            self.logger.error("Failed to load holiday cache: %s", error)
            return set()

    def _is_fresh(self, updated_at: Optional[datetime]) -> bool:
        return bool(
            updated_at is not None
            and timedelta(0) <= self._now() - updated_at <= self.cache_ttl
        )

    def get_holidays(
        self,
        force_refresh: bool = False,
        required_year: Optional[int] = None,
    ) -> Set[date]:
        requested_years = (
            {required_year} if required_year is not None
            else self._default_years()
        )
        if (
            self._cache_loaded
            and not force_refresh
            and requested_years.issubset(
                self._cache_years.union(self._attempted_years)
            )
            and (
                self._is_fresh(self._cache_loaded_at)
                or self._is_fresh(self._refresh_attempted_at)
            )
        ):
            return set(self._holidays_cache)

        try:
            cached, cached_at, cached_years = self._load_cache_record()
        except Exception as error:
            self.logger.error("Failed to load holiday cache: %s", error)
            cached, cached_at, cached_years = set(), None, set()

        fresh = self._is_fresh(cached_at)
        if not force_refresh and fresh and requested_years.issubset(cached_years):
            self._holidays_cache = cached
            self._cache_years = cached_years
            self._cache_loaded = True
            self._cache_loaded_at = cached_at
            return set(cached)

        years_to_fetch = (
            requested_years
            if force_refresh or not fresh
            else requested_years.difference(cached_years)
        )
        combined = set(cached)
        covered = set(cached_years)
        all_refreshed = True
        loaded_at: Optional[datetime]
        if years_to_fetch:
            self._refresh_attempted_at = self._now()
            self._attempted_years = set(years_to_fetch)
        for year in sorted(years_to_fetch):
            fetched = self.fetch_holidays_for_year(year)
            if fetched is None:
                all_refreshed = False
                continue
            combined = {value for value in combined if value.year != year}
            combined.update(fetched)
            covered.add(year)

        if years_to_fetch and all_refreshed:
            self.save_holidays_to_cache(combined, covered)
            loaded_at = self._now()
        else:
            loaded_at = cached_at
            if cached:
                self.logger.warning(
                    "Using cached holidays because one or more refreshes failed"
                )

        # Bundled calendar last, and only for years neither the API nor the
        # cache answered.  It is applied after the save above so the cache file
        # only ever claims years that were actually fetched.
        for year in sorted(requested_years.difference(covered)):
            bundled = holiday_calendar.holidays_for_year(year)
            if bundled is None:
                self.logger.warning(
                    "No trading calendar available for %s; holidays in that "
                    "year cannot be skipped in advance",
                    year,
                )
                continue
            combined.update(bundled)
            covered.add(year)
            self.logger.info(
                "Using the bundled trading calendar for %s (%s holidays)",
                year,
                len(bundled),
            )

        self._last_refresh_ok = all_refreshed
        self._holidays_cache = combined
        self._cache_years = covered
        self._cache_loaded = True
        self._cache_loaded_at = loaded_at
        return set(combined)

    def is_holiday(self, check_date: date) -> bool:
        return check_date in self.get_holidays(required_year=check_date.year)

    def has_calendar(self, year: int) -> bool:
        """Whether ``year``'s calendar is known rather than assumed empty.

        False means holidays in that year cannot be skipped in advance, so the
        exchange's own 404 is the only evidence a date was not traded.
        """

        self.get_holidays(required_year=year)
        return year in self._cache_years

    def get_holiday_count(self) -> int:
        return len(self.get_holidays())

    def refresh_holidays(self) -> bool:
        """Whether the live refresh succeeded, not whether any holiday is known.

        The bundled calendar means holidays are always known, so answering
        ``bool(holidays)`` here could no longer report a failed refresh.
        """

        self.get_holidays(force_refresh=True)
        return self._last_refresh_ok
