"""Read-only verification of a published NSE/BSE data root.

The database this application builds is meant to accumulate for years and be
traded off.  Until now "is it complete and self-consistent?" could not be
answered without changing it: every sha256 written into the pipeline manifest
was never read back, and the only repair tooling was the destructive
``--rebuild-*``.

This module answers that question and changes nothing.  "Changes nothing" is a
requirement, not a courtesy -- a report produced by code that had just written
to the database would not be evidence about the database the user has -- so
none of the ordinary routes into the data root are used here:

* ``Config.get_data_path`` creates the folder it is asked about, so
  ``Config.resolve_data_path`` is used instead;
* ``DataManager.__init__`` creates the whole folder structure, so the published
  filename contract is imported from ``DAILY_FILE_PATTERNS`` directly;
* ``PipelineManifest`` initialises its SQLite schema and imports the legacy
  JSON in its constructor, so ``ReadOnlyPipelineStore`` is used instead;
* ``VersionedJSONStore.read`` and ``read_internal_snapshot`` copy a file they
  cannot parse into ``.state/quarantine``, so neither is called.  A file this
  command cannot parse is reported, not moved.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

from ..core.config import Config
from ..core.data_manager import DAILY_FILE_PATTERNS
from ..utils import holiday_calendar
from ..utils.date_utils import DateUtils
from .canonical_data import (
    BAND_SESSIONS,
    LEGACY_SYMBOL_HISTORY_COLUMNS,
    SYMBOL_HISTORY_COLUMNS,
    row_count_band_error,
    valid_isin,
    valid_security_id,
)
from .instance_lock import SingleInstanceLock
from .pipeline_sqlite import ReadOnlyPipelineStore
from .symbol_history import SymbolHistoryStore

ERROR = "error"
WARNING = "warning"
NOTICE = "notice"

#: Worst first, so the rendered report opens with what matters.
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, NOTICE: 2}

#: Findings at or above this severity make the command exit non-zero.  A
#: notice describes something the audit cannot vouch for rather than something
#: it found wrong -- data published before the manifest existed, for instance
#: -- and a database full of those is not a failing database.
FAILING_SEVERITIES = frozenset({ERROR, WARNING})

_DIGEST = re.compile(r"[0-9a-f]{64}")

#: How far a session may be from its neighbours and still be judged against
#: them.  The same bound the writer uses: a market grows over decades, so a
#: 1995 session must not be measured against a 2026 one merely because nothing
#: closer has been downloaded yet.
BAND_MAX_DISTANCE_DAYS = 180

#: Read size for the single pass that produces both a digest and a row count.
_READ_BLOCK = 1024 * 1024

#: The longest run of closed days the calendar walk will step back over.  The
#: exchanges have never closed for anything near this long.
_MAX_CLOSED_RUN = 60

#: Segments whose rows become symbol histories, and therefore the ones whose
#: selection makes the ``SYMBOLS`` cross-check relevant.
SYMBOL_BEARING_SEGMENTS = frozenset({"EQ", "SME"})

#: How many individual dates or filenames one aggregated finding names before
#: it falls back to a count.  A finding nobody can read is not a finding.
_LISTED = 5


def _describe(values: Sequence[Any], total: Optional[int] = None) -> str:
    """Name the first few of ``values`` and count the rest."""

    total = len(values) if total is None else total
    listed = ", ".join(str(value) for value in values[:_LISTED])
    if total > len(values[:_LISTED]):
        listed += f", and {total - len(values[:_LISTED])} more"
    return listed


@dataclass(frozen=True)
class FileMeasurement:
    """What one pass over a published file establishes."""

    sha256: str
    rows: int


def measure_file(path: Path) -> FileMeasurement:
    """Return the digest and row count of ``path`` from a single read.

    Both checks need the whole file, and an audit of a multi-year database
    reads every published file already; reading each one twice would double
    the only expensive part of the command.
    """

    digest = hashlib.sha256()
    rows = 0
    trailing_newline = True
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_READ_BLOCK), b""):
            digest.update(block)
            rows += block.count(b"\n")
            trailing_newline = block.endswith(b"\n")
    # A final line without its newline is still a row.
    return FileMeasurement(
        digest.hexdigest(), rows if trailing_newline else rows + 1
    )


class OfflineTradingCalendar:
    """Which days the exchanges traded, decided without a network call.

    ``DataManager.is_trading_day`` asks ``HolidayManager.is_holiday``, which
    fetches a year it does not have.  A diagnostic must not do that -- the
    defect that withdrew v1.1.0 was a TLS failure, and an audit that hangs
    trying to reach NSE cannot help diagnose one.

    So this reads the holiday cache the application has already saved, falls
    back to the bundled calendar, and reports a year it has neither of as
    *unknown* rather than as holiday-free.  Guessing that a year had no
    holidays would turn every Diwali into a missing trading day.
    """

    def __init__(self, config: Config):
        settings = config.date_settings
        self.weekend_skip = getattr(settings, "weekend_skip", True)
        self.holiday_skip = getattr(settings, "holiday_skip", True)
        cached, cached_years = config.holiday_manager.cached_calendar()
        self._cached = cached
        self._cached_years = set(cached_years)

    def knows_year(self, year: int) -> bool:
        return (
            not self.holiday_skip
            or year in self._cached_years
            or holiday_calendar.holidays_for_year(year) is not None
        )

    def is_trading_day(self, target_date: date) -> bool:
        if self.weekend_skip and target_date.weekday() >= 5:
            return False
        if not self.holiday_skip:
            return True
        if target_date.year in self._cached_years:
            return target_date not in self._cached
        bundled = holiday_calendar.holidays_for_year(target_date.year)
        if bundled is None:
            # Unknown rather than empty; ``knows_year`` keeps such a year out
            # of the expected set entirely.
            return True
        return target_date not in bundled

    def trading_days(self, first: date, last: date) -> list[date]:
        days = []
        current = first
        while current <= last:
            if self.knows_year(current.year) and self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return days

    def expected_last_trading_date(self) -> date:
        """The most recent session whose reports the exchanges have published.

        Mirrors ``DataManager.get_expected_last_trading_date``: today counts
        only after the 6 p.m. IST publication time.
        """

        today = DateUtils.today_ist()
        current = today
        if self.is_trading_day(today) and DateUtils.is_data_available_time():
            return today
        if self.is_trading_day(today):
            current = today - timedelta(days=1)
        # Bounded: a holiday cache that somehow marked every day a holiday
        # would otherwise walk backwards for ever inside a diagnostic.
        for _ in range(_MAX_CLOSED_RUN):
            if self.is_trading_day(current):
                return current
            current -= timedelta(days=1)
        return current


class AuditError(RuntimeError):
    """The audit could not run, as distinct from the audit finding a problem."""


@dataclass(frozen=True)
class AuditFinding:
    """One thing the audit could not vouch for."""

    severity: str
    category: str
    message: str
    exchange_segment: str = ""
    target_date: Optional[date] = None
    path: Optional[Path] = None

    def _location(self) -> str:
        parts = [self.exchange_segment]
        if self.target_date is not None:
            parts.append(self.target_date.isoformat())
        return " ".join(part for part in parts if part)

    def render(self) -> str:
        location = self._location()
        head = f"{self.severity.upper():<7} {self.category}"
        if location:
            head = f"{head}  {location}"
        lines = [head, f"        {self.message}"]
        if self.path is not None:
            lines.append(f"        {self.path}")
        return "\n".join(lines)

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (
            SEVERITY_ORDER.get(self.severity, len(SEVERITY_ORDER)),
            self.exchange_segment,
            self.target_date.isoformat() if self.target_date else "",
            self.category,
        )


@dataclass
class SegmentSummary:
    """What the audit actually looked at for one exchange segment."""

    exchange_segment: str
    records: int = 0
    files: int = 0
    digests_verified: int = 0

    def render(self) -> str:
        return (
            f"  {self.exchange_segment:<10} {self.records:>6} recorded dates, "
            f"{self.files:>6} published files, "
            f"{self.digests_verified:>6} digests verified"
        )


@dataclass
class SymbolSummary:
    """What the symbol cross-check looked at for one exchange."""

    exchange: str
    histories: int = 0
    snapshots: int = 0
    pairs: int = 0

    def render(self) -> str:
        return (
            f"  {self.exchange + ' SYMBOLS':<10} {self.histories:>6} history "
            f"files, {self.snapshots:>6} raw snapshots, {self.pairs:>7} "
            "(symbol, date) pairs checked"
        )


@dataclass
class AuditReport:
    """Everything one ``--audit`` run established."""

    base_data_path: Path
    summaries: tuple[SegmentSummary, ...] = ()
    symbols: tuple[SymbolSummary, ...] = ()
    findings: tuple[AuditFinding, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        counts = {ERROR: 0, WARNING: 0, NOTICE: 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    @property
    def failed(self) -> bool:
        return any(
            finding.severity in FAILING_SEVERITIES for finding in self.findings
        )

    def render(self) -> str:
        lines = [f"Audit of {self.base_data_path}", ""]
        for note in self.notes:
            lines.append(f"Note: {note}")
        if self.notes:
            lines.append("")
        for summary in self.summaries:
            lines.append(summary.render())
        for symbol_summary in self.symbols:
            lines.append(symbol_summary.render())
        lines.append("")

        if not self.findings:
            lines.append("No problems found.")
            return "\n".join(lines)

        counts = self.counts
        described = ", ".join(
            f"{counts[severity]} {severity}{'s' if counts[severity] != 1 else ''}"
            for severity in (ERROR, WARNING, NOTICE)
            if counts[severity]
        )
        lines.append(f"{len(self.findings)} finding(s): {described}.")
        lines.append("")
        for finding in sorted(self.findings, key=lambda item: item.sort_key):
            lines.append(finding.render())
        return "\n".join(lines)


class DatabaseAudit:
    """Verify a data root against its own pipeline records, writing nothing."""

    def __init__(
        self, config: Config, segments: Optional[Sequence[str]] = None
    ):
        self.config = config
        self.base_data_path = Path(config.base_data_path)
        available = list(config.get_available_exchanges())
        if segments:
            requested = [str(segment).upper() for segment in segments]
            unknown = sorted(
                set(requested).difference(available)
            )
            if unknown:
                raise AuditError(
                    f"unknown exchange segment(s): {', '.join(unknown)}. "
                    f"Known segments: {', '.join(available)}"
                )
            self.segments = [
                segment for segment in available if segment in set(requested)
            ]
        else:
            self.segments = available
        self.calendar = OfflineTradingCalendar(config)
        self._findings: list[AuditFinding] = []
        self._notes: list[str] = []
        self._relocated_reported = False
        self._unknown_calendar_years: set[int] = set()
        self._registry_cache: Optional[dict[str, Any]] = None
        # One read per file, however many checks want to look at it.
        self._measurements: dict[Path, Optional[FileMeasurement]] = {}

    # ---------------------------------------------------------------- helpers

    def _add(
        self,
        severity: str,
        category: str,
        message: str,
        *,
        exchange_segment: str = "",
        target_date: Optional[date] = None,
        path: Optional[Path] = None,
    ) -> None:
        self._findings.append(
            AuditFinding(
                severity=severity,
                category=category,
                message=message,
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=path,
            )
        )

    def _measure(
        self,
        path: Path,
        kind: str,
        *,
        exchange_segment: str = "",
        target_date: Optional[date] = None,
    ) -> Optional[FileMeasurement]:
        """Digest and row count for one file, read once and remembered."""

        if path in self._measurements:
            return self._measurements[path]
        try:
            measurement: Optional[FileMeasurement] = measure_file(path)
        except OSError as error:
            measurement = None
            self._add(
                ERROR,
                "unreadable-file",
                f"the {kind} could not be read: {error}",
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=path,
            )
        self._measurements[path] = measurement
        return measurement

    def _note_running_instance(self) -> None:
        """Say so if a download may be writing while the audit reads.

        The lock is deliberately not acquired: this command is read-only, and
        taking the writer's lock would stop a running GUI to answer a question
        about it.
        """

        holder = SingleInstanceLock(self.base_data_path).holder()
        if not holder:
            return
        pid = holder.get("pid")
        if (
            os.name == "posix"
            and isinstance(pid, int)
            and holder.get("host") == socket.gethostname()
        ):
            # Only on POSIX.  On Windows ``os.kill`` with signal 0 does not
            # probe the process, it calls TerminateProcess -- a read-only
            # command must never take that path.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except OSError:
                pass  # Alive, owned by another user.
        self._notes.append(
            f"another copy of the application holds the data-root lock "
            f"(pid {pid}); anything it is writing now may read as a problem."
        )

    # ----------------------------------------------------------- record loading

    def _load_records(self) -> dict[str, list[tuple[date, dict[str, Any]]]]:
        """Group valid pipeline records by exchange segment, oldest first."""

        store = ReadOnlyPipelineStore(
            self.base_data_path / ".state" / "pipeline_state.sqlite3"
        )
        if not store.exists():
            self._notes.append(
                "there is no pipeline database in this data root, so no "
                "digest recorded at download time could be checked."
            )
            return {}
        try:
            rows = store.raw_records()
        except sqlite3.DatabaseError as error:
            raise AuditError(
                f"the pipeline database could not be read: {error}"
            ) from error

        grouped: dict[str, list[tuple[date, dict[str, Any]]]] = {}
        for key, payload in rows:
            record = self._parse_record(key, payload)
            if record is None:
                continue
            exchange_segment = f"{record['exchange']}_{record['segment']}"
            target_date = date.fromisoformat(record["date"])
            grouped.setdefault(exchange_segment, []).append(
                (target_date, record)
            )
        for entries in grouped.values():
            entries.sort(key=lambda entry: entry[0])
        return grouped

    def _parse_record(self, key: str, payload: str) -> Optional[dict[str, Any]]:
        """Return one record, or report why it cannot be used and return None."""

        try:
            record = json.loads(payload)
        except ValueError as error:
            self._add(
                ERROR,
                "unreadable-record",
                f"pipeline record '{key}' is not valid JSON: {error}",
            )
            return None
        if not isinstance(record, dict):
            self._add(
                ERROR,
                "unreadable-record",
                f"pipeline record '{key}' is not an object",
            )
            return None
        exchange = record.get("exchange")
        segment = record.get("segment")
        raw_date = record.get("date")
        stages = record.get("stages")
        if (
            not isinstance(exchange, str)
            or not isinstance(segment, str)
            or not isinstance(raw_date, str)
            or not isinstance(stages, dict)
        ):
            self._add(
                ERROR,
                "unreadable-record",
                f"pipeline record '{key}' does not identify one date's stages",
            )
            return None
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            self._add(
                ERROR,
                "unreadable-record",
                f"pipeline record '{key}' has an invalid date '{raw_date}'",
            )
            return None
        return record

    # ------------------------------------------------------------- digest check

    def _resolve(self, recorded: str, fallback: Path) -> Optional[Path]:
        """Find a recorded file, allowing for a data root that has moved.

        Recorded paths are absolute and were written on the machine that
        produced them, so a restored backup or a copied data root would
        otherwise report every single file as missing.
        """

        path = Path(recorded)
        if path.is_file():
            return path
        if fallback.is_file():
            if not self._relocated_reported:
                self._relocated_reported = True
                self._notes.append(
                    f"recorded paths point outside {self.base_data_path}, so "
                    "files were matched by their place in this data root "
                    "instead; the database was written under a different path."
                )
            return fallback
        return None

    def _verify_digest(
        self,
        recorded_path: Any,
        recorded_digest: Any,
        fallback: Path,
        kind: str,
        *,
        exchange_segment: str,
        target_date: date,
        summary: SegmentSummary,
    ) -> None:
        if not isinstance(recorded_path, str) or not recorded_path:
            self._add(
                ERROR,
                "incomplete-record",
                f"the {kind} is recorded complete but with no path",
                exchange_segment=exchange_segment,
                target_date=target_date,
            )
            return
        if not isinstance(recorded_digest, str) or not _DIGEST.fullmatch(
            recorded_digest
        ):
            self._add(
                ERROR,
                "incomplete-record",
                f"the {kind} is recorded complete but with no usable sha256",
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=Path(recorded_path),
            )
            return

        path = self._resolve(recorded_path, fallback)
        if path is None:
            self._add(
                ERROR,
                "missing-file",
                f"the {kind} recorded for this date is not on disk",
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=Path(recorded_path),
            )
            return
        measurement = self._measure(
            path,
            kind,
            exchange_segment=exchange_segment,
            target_date=target_date,
        )
        if measurement is None:
            return
        actual = measurement.sha256
        summary.digests_verified += 1
        if actual != recorded_digest:
            self._add(
                ERROR,
                "digest-mismatch",
                f"the {kind} does not match the sha256 recorded when it was "
                f"written (recorded {recorded_digest[:12]}, "
                f"found {actual[:12]})",
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=path,
            )

    def _check_record_digests(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        record: dict[str, Any],
        summary: SegmentSummary,
    ) -> None:
        exchange_segment = f"{exchange}_{segment}"
        stages = record.get("stages", {})
        daily = stages.get("daily")
        combined = stages.get("combined")
        daily = daily if isinstance(daily, dict) else {}
        combined = combined if isinstance(combined, dict) else {}

        if daily.get("status") == "complete":
            component_path = daily.get("component_path")
            component_digest = daily.get("component_sha256")
            if component_path or component_digest:
                self._verify_digest(
                    component_path,
                    component_digest,
                    self.base_data_path / ".state" / "components" / exchange
                    / segment / f"{target_date.isoformat()}.csv",
                    "reconciliation component",
                    exchange_segment=exchange_segment,
                    target_date=target_date,
                    summary=summary,
                )

            published_fallback = (
                self.config.resolve_data_path(exchange, segment)
                / f"{target_date.isoformat()}-{exchange}-{segment}.txt"
            )
            if daily.get("publication_deferred"):
                # For a deferred publication the daily ``sha256`` describes the
                # component, not the file at ``path``: the published EQ file is
                # written later by the combined stage, after SME and Index rows
                # have been appended, and that stage records its own digest.
                # Comparing here would report every combined date as corrupt.
                if combined.get("status") != "complete":
                    self._add(
                        WARNING,
                        "unpublished-date",
                        "this date was prepared but its combined publication "
                        "did not complete, so no digest vouches for the "
                        "published file",
                        exchange_segment=exchange_segment,
                        target_date=target_date,
                        path=published_fallback,
                    )
            else:
                self._verify_digest(
                    daily.get("path"),
                    daily.get("sha256"),
                    published_fallback,
                    "published file",
                    exchange_segment=exchange_segment,
                    target_date=target_date,
                    summary=summary,
                )

        if combined.get("status") == "complete":
            self._verify_digest(
                combined.get("path"),
                combined.get("sha256"),
                self.config.resolve_data_path(exchange, "EQ")
                / f"{target_date.isoformat()}-{exchange}-EQ.txt",
                "combined file",
                exchange_segment=exchange_segment,
                target_date=target_date,
                summary=summary,
            )

    # -------------------------------------------------------------- file scan

    def _published_files(
        self, exchange: str, segment: str
    ) -> dict[date, Path]:
        """Every file in one segment folder that matches the naming contract.

        Anything else in the folder is reported here, because the folder is a
        published interface: a leftover ``.tmp`` is an interrupted write, and
        a stray file is something no consumer will ever read.
        """

        exchange_segment = f"{exchange}_{segment}"
        folder = self.config.resolve_data_path(exchange, segment)
        published: dict[date, Path] = {}
        if not folder.is_dir():
            return published
        pattern = re.compile(DAILY_FILE_PATTERNS[exchange_segment])

        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            match = pattern.fullmatch(path.name)
            if match is None:
                if path.name.endswith(".tmp"):
                    self._add(
                        WARNING,
                        "interrupted-write",
                        "a temporary file was left behind, so a write did not "
                        "finish",
                        exchange_segment=exchange_segment,
                        path=path,
                    )
                else:
                    self._add(
                        NOTICE,
                        "unrecognised-file",
                        "this file does not match the published naming "
                        "contract, so nothing reads it",
                        exchange_segment=exchange_segment,
                        path=path,
                    )
                continue
            try:
                file_date = date.fromisoformat(match.group(1))
            except ValueError:
                self._add(
                    WARNING,
                    "unrecognised-file",
                    "this filename carries a date that does not exist",
                    exchange_segment=exchange_segment,
                    path=path,
                )
                continue
            if file_date in published:
                self._add(
                    WARNING,
                    "duplicate-published-file",
                    "two files claim this date, and which one a consumer "
                    f"reads depends on the directory order: {path.name} and "
                    f"{published[file_date].name}",
                    exchange_segment=exchange_segment,
                    target_date=file_date,
                    path=path,
                )
            published[file_date] = path
        return published

    def _report_unrecorded(
        self,
        exchange_segment: str,
        published: dict[date, Path],
        recorded_dates: set[date],
    ) -> None:
        earliest = min(recorded_dates) if recorded_dates else None
        for file_date, path in sorted(published.items()):
            if file_date in recorded_dates:
                continue
            if earliest is not None and file_date < earliest:
                self._add(
                    NOTICE,
                    "unrecorded-file",
                    "this file predates every pipeline record in this data "
                    "root, so no digest can vouch for it",
                    exchange_segment=exchange_segment,
                    target_date=file_date,
                    path=path,
                )
            else:
                self._add(
                    WARNING,
                    "unrecorded-file",
                    "this file is not recorded in the pipeline database, so "
                    "nothing states where it came from",
                    exchange_segment=exchange_segment,
                    target_date=file_date,
                    path=path,
                )

    # --------------------------------------------------------------- coverage

    def _configured_start(self) -> Optional[date]:
        raw = getattr(self.config.date_settings, "base_start_date", None)
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            self._add(
                ERROR,
                "unusable-configuration",
                f"base_start_date '{raw}' is not a date, so coverage cannot "
                "be judged",
            )
            return None

    @staticmethod
    def _runs(expected: list[date], present: set[date]) -> list[list[date]]:
        """Group absent dates into stretches of consecutive trading days.

        One finding per stretch rather than per date: a head gap of a year is
        one fact about the database, not two hundred and fifty of them.
        """

        runs: list[list[date]] = []
        current: list[date] = []
        for day in expected:
            if day in present:
                if current:
                    runs.append(current)
                    current = []
                continue
            current.append(day)
        if current:
            runs.append(current)
        return runs

    def _check_coverage(
        self,
        exchange_segment: str,
        published: dict[date, Path],
        entries: list[tuple[date, dict[str, Any]]],
    ) -> None:
        """Judge coverage against the configured start, not the first file.

        ``get_missing_file_dates`` looks only *between* the first and last
        filename on disk, so a truncated head and a stale tail are both
        invisible to it.  This starts from ``base_start_date`` -- or from the
        oldest file, when the owner has deliberately gone back further -- and
        ends at the last session the exchanges have actually published.
        """

        if not published and not entries:
            # A segment the owner does not download.  The audit cannot know
            # which segments are wanted, so silence is the only honest answer.
            return
        configured = self._configured_start()
        if configured is None:
            return

        floor = min([configured, *published]) if published else configured
        ceiling = self.calendar.expected_last_trading_date()
        if floor > ceiling:
            return

        for year in range(floor.year, ceiling.year + 1):
            if not self.calendar.knows_year(year):
                self._unknown_calendar_years.add(year)

        expected = self.calendar.trading_days(floor, ceiling)
        if not expected:
            return

        # A date the pipeline recorded is not a coverage question, whatever
        # became of it: a settled absence is not a gap, a complete date is the
        # digest check's to judge, and an unfinished one is reported below.
        # Counting them here would report the same date twice.
        accounted = set(published).union(
            target_date for target_date, _record in entries
        )
        first_published = min(accounted) if accounted else None
        last_published = max(accounted) if accounted else None

        for run in self._runs(expected, accounted):
            span = (
                run[0].isoformat() if len(run) == 1
                else f"{run[0].isoformat()} to {run[-1].isoformat()}"
            )
            if first_published is None:
                self._add(
                    WARNING,
                    "missing-dates",
                    f"{len(run)} expected trading day(s) ({span}) have no "
                    "published file, and this segment has none at all",
                    exchange_segment=exchange_segment,
                )
            elif run[-1] < first_published:
                self._add(
                    WARNING,
                    "head-gap",
                    f"the database starts at {first_published.isoformat()}, "
                    f"leaving {len(run)} trading day(s) ({span}) between the "
                    f"configured start and the oldest published file",
                    exchange_segment=exchange_segment,
                )
            elif last_published is not None and run[0] > last_published:
                # Freshness, not damage: the owner decides when to download.
                self._add(
                    NOTICE,
                    "stale-tail",
                    f"{len(run)} trading day(s) ({span}) since the newest "
                    f"published file have not been downloaded",
                    exchange_segment=exchange_segment,
                )
            else:
                self._add(
                    WARNING,
                    "missing-dates",
                    f"{len(run)} trading day(s) ({span}) inside the published "
                    "range have no file",
                    exchange_segment=exchange_segment,
                )

        unfinished = sorted(
            target_date for target_date, record in entries
            if not record.get("complete") and not record.get("skipped_reason")
        )
        if unfinished:
            listed = ", ".join(day.isoformat() for day in unfinished[:5])
            if len(unfinished) > 5:
                listed += f", and {len(unfinished) - 5} more"
            self._add(
                WARNING,
                "incomplete-date",
                f"{len(unfinished)} date(s) never finished every required "
                f"stage: {listed}",
                exchange_segment=exchange_segment,
            )

    # -------------------------------------------------------------- row counts

    @staticmethod
    def _recorded_rows(record: dict[str, Any]) -> Optional[int]:
        """The row count the writer recorded for the file it published.

        The combined stage wins where it ran: for a deferred publication the
        daily count describes the component, and the file on disk carries the
        appended SME and Index rows as well.
        """

        stages = record.get("stages", {})
        combined = stages.get("combined")
        daily = stages.get("daily")
        combined = combined if isinstance(combined, dict) else {}
        daily = daily if isinstance(daily, dict) else {}
        if combined.get("status") == "complete" and isinstance(
            combined.get("rows"), int
        ):
            return int(combined["rows"])
        if (
            daily.get("status") == "complete"
            and not daily.get("publication_deferred")
            and isinstance(daily.get("rows"), int)
        ):
            return int(daily["rows"])
        return None

    def _check_row_counts(
        self,
        exchange_segment: str,
        published: dict[date, Path],
        entries: list[tuple[date, dict[str, Any]]],
    ) -> None:
        """Size every published file against its record and its neighbours.

        The writer already bands a day against whatever sessions existed when
        it was written, which for a backfill walking backwards is very few.
        Judging again now, against the finished neighbourhood, is what makes a
        truncated or placeholder day visible after the fact.  It is also the
        only plausibility check available for files written before the
        manifest existed, which carry no digest to compare.
        """

        recorded = {
            target_date: rows
            for target_date, record in entries
            if (rows := self._recorded_rows(record)) is not None
        }
        on_disk: dict[date, int] = {}
        for target_date, path in sorted(published.items()):
            measurement = self._measure(
                path,
                "published file",
                exchange_segment=exchange_segment,
                target_date=target_date,
            )
            if measurement is None:
                continue
            on_disk[target_date] = measurement.rows
            expected = recorded.get(target_date)
            if expected is not None and expected != measurement.rows:
                self._add(
                    ERROR,
                    "row-count-drift",
                    f"the file holds {measurement.rows} rows but the pipeline "
                    f"recorded {expected} when it published this date",
                    exchange_segment=exchange_segment,
                    target_date=target_date,
                    path=path,
                )

        for target_date, rows in sorted(on_disk.items()):
            neighbours = [
                count for _distance, count in sorted(
                    (abs((other - target_date).days), count)
                    for other, count in on_disk.items()
                    if other != target_date
                    and abs((other - target_date).days)
                    <= BAND_MAX_DISTANCE_DAYS
                )[:BAND_SESSIONS]
            ]
            reason = row_count_band_error(rows, neighbours)
            if reason is not None:
                self._add(
                    WARNING,
                    "row-count-outlier",
                    reason,
                    exchange_segment=exchange_segment,
                    target_date=target_date,
                    path=published[target_date],
                )

    # ----------------------------------------------------------- symbol files

    def _registry(self) -> dict[str, Any]:
        """The symbol registry, read without the store's quarantine behaviour."""

        if self._registry_cache is not None:
            return self._registry_cache
        empty: dict[str, Any] = {
            "exchanges": {}, "files": {}, "identities": {}
        }
        path = self.base_data_path / ".state" / "symbol_registry.json"
        if not path.is_file():
            self._registry_cache = empty
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("the registry must be an object")
        except (OSError, ValueError) as error:
            self._add(
                ERROR,
                "unreadable-registry",
                f"the symbol registry could not be read: {error}",
                path=path,
            )
            self._registry_cache = empty
            return empty
        for section in ("exchanges", "files", "identities"):
            if not isinstance(data.get(section), dict):
                data[section] = {}
        self._registry_cache = data
        return data

    def _snapshot_paths(self, exchange: str) -> list[Path]:
        """Every checksummed raw snapshot for one exchange, oldest first."""

        root = self.base_data_path / ".state" / "raw" / exchange
        if not root.is_dir():
            return []
        paths = [
            path
            for segment in sorted(root.iterdir()) if segment.is_dir()
            for path in sorted(segment.glob("*.csv"))
        ]
        return sorted(paths, key=lambda path: path.stem)

    def _snapshot_filename(
        self,
        exchange: str,
        registry: dict[str, Any],
        row: Sequence[str],
        columns: dict[str, int],
    ) -> Optional[str]:
        """The symbol file one raw row belongs in, resolved as the store does.

        Identity first, then the ticker.  A renamed security keeps its file,
        so an old row's own symbol would point at a filename that was merged
        away; asking the registry which ticker owns that ISIN or security code
        follows the rename instead of reporting it as a missing file.
        """

        def value(name: str) -> str:
            position = columns.get(name)
            if position is None or position >= len(row):
                return ""
            return str(row[position]).strip().upper()

        symbol = value("SYMBOL")
        isin = value("ISIN")
        security_id = value("SECURITY_ID")
        owners = registry["exchanges"].get(exchange, {})
        for key in (
            f"ISIN:{isin}" if valid_isin(isin) else None,
            f"ID:{security_id}" if valid_security_id(security_id) else None,
        ):
            if key is not None and key in owners:
                symbol = owners[key]
                break
        if not symbol:
            return None
        return registry["files"].get(exchange, {}).get(
            symbol, f"{SymbolHistoryStore.safe_filename(symbol)}.txt"
        )

    def _verify_snapshot(self, path: Path) -> Optional[bytes]:
        """Check one raw snapshot against its own metadata and return it.

        Nothing reads these digests back until a `--rebuild-*` needs the
        snapshot, which is the worst moment to discover it is damaged: the
        rebuild is the repair.
        """

        try:
            payload = path.read_bytes()
        except OSError as error:
            self._add(
                ERROR,
                "unreadable-file",
                f"the raw snapshot could not be read: {error}",
                path=path,
            )
            return None
        metadata_path = path.with_suffix(".csv.meta.json")
        if not metadata_path.is_file():
            self._add(
                ERROR,
                "raw-metadata-missing",
                "this raw snapshot has no metadata, so no digest vouches for "
                "it and a rebuild will refuse to read it",
                path=path,
            )
            return payload
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("the metadata must be an object")
        except (OSError, ValueError) as error:
            self._add(
                ERROR,
                "raw-metadata-unreadable",
                f"the raw snapshot metadata could not be read: {error}",
                path=metadata_path,
            )
            return payload
        digest = hashlib.sha256(payload).hexdigest()
        if metadata.get("sha256") != digest:
            self._add(
                ERROR,
                "raw-digest-mismatch",
                "the raw snapshot does not match the sha256 its metadata "
                "records, so a rebuild would refuse it",
                path=path,
            )
            return payload
        rows = payload.count(b"\n")
        if payload and not payload.endswith(b"\n"):
            rows += 1
        rows = max(rows - 1, 0)  # the header is not a row
        if metadata.get("row_count") != rows:
            self._add(
                ERROR,
                "raw-row-count-mismatch",
                f"the raw snapshot holds {rows} rows but its metadata records "
                f"{metadata.get('row_count')}",
                path=path,
            )
        return payload

    def _expected_symbol_coverage(
        self, exchange: str, snapshots: list[Path]
    ) -> tuple[dict[str, int], list[date]]:
        """Which dates each symbol file owes, as one bit per snapshot date.

        A bitmask rather than a set of dates: a finished multi-year backfill
        holds tens of millions of (symbol, date) pairs, which no audit should
        need gigabytes to check.  One bit each keeps the whole expectation for
        a 4,000-symbol, 7,500-session database inside a few megabytes.
        """

        index: dict[date, int] = {}
        ordered: list[date] = []
        for path in snapshots:
            try:
                snapshot_date = date.fromisoformat(path.stem)
            except ValueError:
                self._add(
                    WARNING,
                    "unrecognised-file",
                    "this raw snapshot is not named for a date, so nothing "
                    "can rebuild from it",
                    path=path,
                )
                continue
            if snapshot_date not in index:
                index[snapshot_date] = len(ordered)
                ordered.append(snapshot_date)

        expected: dict[str, int] = {}
        registry = self._registry()
        for path in snapshots:
            try:
                snapshot_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            payload = self._verify_snapshot(path)
            if payload is None:
                continue
            bit = 1 << index[snapshot_date]
            try:
                decoded = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                self._add(
                    ERROR,
                    "raw-schema-unknown",
                    f"this raw snapshot is not readable as text: {error}",
                    path=path,
                )
                continue
            reader = csv.reader(decoded.splitlines())
            header = next(reader, None)
            if header is None:
                continue
            # Resolved from the header rather than by position: a snapshot
            # written by a different column order must be refused, not
            # silently read as if SYMBOL were somewhere else.
            columns = {name: position for position, name in enumerate(header)}
            if "SYMBOL" not in columns:
                self._add(
                    ERROR,
                    "raw-schema-unknown",
                    "this raw snapshot has no SYMBOL column, so nothing can "
                    "rebuild a symbol history from it",
                    path=path,
                )
                continue
            for row in reader:
                if not row:
                    continue
                filename = self._snapshot_filename(
                    exchange, registry, row, columns
                )
                if filename is None:
                    continue
                expected[filename] = expected.get(filename, 0) | bit
        return expected, ordered

    def _symbol_file_dates(
        self, path: Path, index: dict[date, int]
    ) -> Optional[tuple[int, list[date], bool]]:
        """One symbol file's dates as a bitmask, its duplicates, its schema."""

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            self._add(
                ERROR,
                "unreadable-file",
                f"the symbol history could not be read: {error}",
                path=path,
            )
            return None
        if not lines:
            self._add(
                WARNING,
                "empty-symbol-file",
                "this symbol history is empty, so it carries no history at all",
                path=path,
            )
            return None
        header = lines[0].split(",")
        current_schema = header == SYMBOL_HISTORY_COLUMNS
        if not current_schema and header != LEGACY_SYMBOL_HISTORY_COLUMNS:
            self._add(
                ERROR,
                "symbol-schema-unknown",
                "this symbol history's header matches neither the current nor "
                "the previous column set, so a consumer cannot read it",
                path=path,
            )
            return None

        mask = 0
        seen: set[date] = set()
        duplicates: list[date] = []
        malformed = 0
        for line in lines[1:]:
            if not line:
                continue
            raw = line.split(",", 1)[0].strip()
            try:
                row_date = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                malformed += 1
                continue
            if row_date in seen:
                duplicates.append(row_date)
            seen.add(row_date)
            position = index.get(row_date)
            if position is not None:
                mask |= 1 << position
        if malformed:
            self._add(
                ERROR,
                "symbol-date-unreadable",
                f"{malformed} row(s) carry a date this file's own format "
                "cannot parse",
                path=path,
            )
        return mask, duplicates, current_schema

    def _check_symbol_histories(self, exchange: str) -> Optional[SymbolSummary]:
        """Cross-check every ``SYMBOLS/*.txt`` against the raw snapshots.

        Nothing enumerated ``<EX>/SYMBOLS/`` at all before this: a symbol file
        could lose a year of history and only a rebuild would notice, which is
        no use to someone asking whether a rebuild is needed.
        """

        snapshots = self._snapshot_paths(exchange)
        folder = self.base_data_path / exchange / "SYMBOLS"
        if not snapshots and not folder.is_dir():
            return None

        expected, ordered = self._expected_symbol_coverage(exchange, snapshots)
        index = {value: position for position, value in enumerate(ordered)}
        summary = SymbolSummary(
            exchange,
            snapshots=len(snapshots),
            pairs=sum(mask.bit_count() for mask in expected.values()),
        )

        on_disk = {
            path.name: path
            for path in (sorted(folder.iterdir()) if folder.is_dir() else [])
            if path.is_file() and path.suffix == ".txt"
        }
        summary.histories = len(on_disk)
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.is_file() and path.name.endswith(".tmp"):
                    self._add(
                        WARNING,
                        "interrupted-write",
                        "a temporary symbol file was left behind, so a write "
                        "did not finish",
                        exchange_segment=exchange,
                        path=path,
                    )

        missing_files: list[str] = []
        legacy_schema = 0
        for filename in sorted(expected):
            history = on_disk.get(filename)
            if history is None:
                missing_files.append(filename)
                continue
            result = self._symbol_file_dates(history, index)
            if result is None:
                continue
            mask, duplicates, current_schema = result
            if not current_schema:
                legacy_schema += 1
            if duplicates:
                self._add(
                    ERROR,
                    "duplicate-symbol-dates",
                    f"{len(duplicates)} date(s) appear more than once: "
                    f"{_describe(sorted(set(duplicates)))}",
                    exchange_segment=exchange,
                    path=history,
                )
            absent = expected[filename] & ~mask
            if absent:
                gaps = [
                    ordered[position]
                    for position in range(len(ordered))
                    if absent >> position & 1
                ]
                self._add(
                    ERROR,
                    "symbol-coverage-gap",
                    f"{len(gaps)} date(s) present in the raw snapshots are "
                    f"missing from this history: {_describe(gaps)}",
                    exchange_segment=exchange,
                    path=history,
                )

        if missing_files:
            self._add(
                ERROR,
                "missing-symbol-file",
                f"{len(missing_files)} symbol file(s) the raw snapshots "
                f"require do not exist: {_describe(missing_files)}",
                exchange_segment=exchange,
            )

        unaccounted = sorted(set(on_disk).difference(expected))
        if unaccounted:
            self._add(
                NOTICE,
                "unaccounted-symbol-file",
                f"{len(unaccounted)} symbol file(s) appear in no raw snapshot, "
                "so their history cannot be verified or rebuilt: "
                f"{_describe(unaccounted)}",
                exchange_segment=exchange,
            )
        if legacy_schema:
            self._add(
                NOTICE,
                "legacy-symbol-schema",
                f"{legacy_schema} symbol file(s) still carry the pre-ISIN "
                "column set, so two schema generations coexist here",
                exchange_segment=exchange,
            )

        registered = self._registry()["files"].get(exchange, {})
        absent_registered = sorted(
            filename for filename in set(registered.values())
            if filename not in on_disk
        )
        if absent_registered:
            self._add(
                ERROR,
                "missing-registered-file",
                f"{len(absent_registered)} file(s) the registry claims to own "
                f"do not exist: {_describe(absent_registered)}",
                exchange_segment=exchange,
            )
        return summary

    # ------------------------------------------------------------- state trees

    def _check_state_orphans(
        self,
        grouped: dict[str, list[tuple[date, dict[str, Any]]]],
        published: dict[str, dict[date, Path]],
    ) -> None:
        """Find working files no published date accounts for.

        Both trees are written per date and read only by a repair.  One left
        behind for a date that was never published means a run stopped in the
        middle, and nothing else would ever say so.
        """

        state = self.base_data_path / ".state"
        for category, kind in (
            ("components", "reconciliation component"),
            ("raw", "raw snapshot"),
        ):
            root = state / category
            if not root.is_dir():
                continue
            for exchange_dir in sorted(root.iterdir()):
                if not exchange_dir.is_dir():
                    continue
                for segment_dir in sorted(exchange_dir.iterdir()):
                    if not segment_dir.is_dir():
                        continue
                    exchange_segment = (
                        f"{exchange_dir.name}_{segment_dir.name}"
                    )
                    if exchange_segment not in self.segments:
                        continue
                    known = {
                        target_date for target_date, _ in
                        grouped.get(exchange_segment, [])
                    }
                    known.update(published.get(exchange_segment, {}))
                    orphans = []
                    for path in sorted(segment_dir.glob("*.csv")):
                        try:
                            snapshot_date = date.fromisoformat(path.stem)
                        except ValueError:
                            continue
                        if snapshot_date not in known:
                            orphans.append(snapshot_date)
                    if orphans:
                        self._add(
                            WARNING,
                            "orphan-working-file",
                            f"{len(orphans)} {kind}(s) exist for dates with "
                            "no published file and no pipeline record: "
                            f"{_describe(orphans)}",
                            exchange_segment=exchange_segment,
                        )
                    for path in sorted(segment_dir.glob("*.tmp")):
                        self._add(
                            WARNING,
                            "interrupted-write",
                            f"a temporary {kind} was left behind, so a write "
                            "did not finish",
                            exchange_segment=exchange_segment,
                            path=path,
                        )

        raw_root = state / "raw"
        if raw_root.is_dir():
            # ``with_suffix`` cannot undo a two-part suffix: it would turn
            # ``2026-07-01.csv.meta.json`` into ``2026-07-01.csv.csv`` and
            # report every healthy snapshot as widowed.
            widowed = [
                path for path in sorted(raw_root.rglob("*.csv.meta.json"))
                if not path.with_name(
                    path.name[: -len(".meta.json")]
                ).is_file()
            ]
            if widowed:
                self._add(
                    WARNING,
                    "orphan-working-file",
                    f"{len(widowed)} raw-snapshot metadata file(s) have no "
                    "snapshot beside them: "
                    f"{_describe([path.name for path in widowed])}",
                )

    # ------------------------------------------------------------------- entry

    def run(self) -> AuditReport:
        """Verify the selected segments and return what was found."""

        self._findings = []
        self._notes = []
        self._relocated_reported = False
        self._unknown_calendar_years = set()
        self._measurements = {}
        self._registry_cache = None

        self._note_running_instance()
        grouped = self._load_records()

        for exchange_segment in sorted(grouped):
            if exchange_segment in self.segments:
                continue
            if exchange_segment in self.config.get_available_exchanges():
                continue
            self._add(
                NOTICE,
                "unknown-segment",
                f"the pipeline database holds {len(grouped[exchange_segment])} "
                f"record(s) for '{exchange_segment}', which this version does "
                "not publish",
            )

        summaries = []
        published_by_segment: dict[str, dict[date, Path]] = {}
        for exchange_segment in self.segments:
            exchange, segment = exchange_segment.split("_", 1)
            entries = grouped.get(exchange_segment, [])
            summary = SegmentSummary(exchange_segment, records=len(entries))
            published = self._published_files(exchange, segment)
            published_by_segment[exchange_segment] = published
            summary.files = len(published)

            for target_date, record in entries:
                self._check_record_digests(
                    exchange, segment, target_date, record, summary
                )
            self._report_unrecorded(
                exchange_segment,
                published,
                {target_date for target_date, _ in entries},
            )
            self._check_coverage(exchange_segment, published, entries)
            self._check_row_counts(exchange_segment, published, entries)
            summaries.append(summary)

        # Every raw snapshot for the exchange is used, whichever of its
        # segments was asked for -- EQ and SME write into one ``SYMBOLS``
        # folder, so judging it from half the snapshots would report the other
        # half's securities as unaccounted for.  Only the *trigger* narrows:
        # someone auditing NSE_FO did not ask about symbol histories.
        symbol_exchanges = sorted({
            exchange for exchange, segment in (
                value.split("_", 1) for value in self.segments
            )
            if segment in SYMBOL_BEARING_SEGMENTS
        })
        symbol_summaries = [
            summary for summary in (
                self._check_symbol_histories(exchange)
                for exchange in symbol_exchanges
            )
            if summary is not None
        ]
        self._check_state_orphans(grouped, published_by_segment)

        if self._unknown_calendar_years:
            years = ", ".join(
                str(year) for year in sorted(self._unknown_calendar_years)
            )
            self._notes.append(
                "no trading calendar is known for "
                f"{years}, so holidays in those years cannot be told apart "
                "from missing files; they were left out of the expected set."
            )

        return AuditReport(
            base_data_path=self.base_data_path,
            summaries=tuple(summaries),
            symbols=tuple(symbol_summaries),
            findings=tuple(self._findings),
            notes=tuple(self._notes),
        )
