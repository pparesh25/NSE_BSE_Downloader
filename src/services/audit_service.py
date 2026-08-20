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

import json
import os
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from ..core.config import Config
from ..core.data_manager import DAILY_FILE_PATTERNS
from .instance_lock import SingleInstanceLock
from .pipeline_sqlite import ReadOnlyPipelineStore
from .state_store import file_sha256

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
class AuditReport:
    """Everything one ``--audit`` run established."""

    base_data_path: Path
    summaries: tuple[SegmentSummary, ...] = ()
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
        self._findings: list[AuditFinding] = []
        self._notes: list[str] = []
        self._relocated_reported = False

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
        try:
            actual = file_sha256(path)
        except OSError as error:
            self._add(
                ERROR,
                "unreadable-file",
                f"the {kind} could not be read: {error}",
                exchange_segment=exchange_segment,
                target_date=target_date,
                path=path,
            )
            return
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

    def _scan_published_folder(
        self,
        exchange: str,
        segment: str,
        recorded_dates: set[date],
        summary: SegmentSummary,
    ) -> None:
        exchange_segment = f"{exchange}_{segment}"
        folder = self.config.resolve_data_path(exchange, segment)
        if not folder.is_dir():
            return
        pattern = re.compile(DAILY_FILE_PATTERNS[exchange_segment])
        earliest = min(recorded_dates) if recorded_dates else None

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

            summary.files += 1
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

    # ------------------------------------------------------------------- entry

    def run(self) -> AuditReport:
        """Verify the selected segments and return what was found."""

        self._findings = []
        self._notes = []
        self._relocated_reported = False

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
        for exchange_segment in self.segments:
            exchange, segment = exchange_segment.split("_", 1)
            entries = grouped.get(exchange_segment, [])
            summary = SegmentSummary(exchange_segment, records=len(entries))
            for target_date, record in entries:
                self._check_record_digests(
                    exchange, segment, target_date, record, summary
                )
            self._scan_published_folder(
                exchange,
                segment,
                {target_date for target_date, _ in entries},
                summary,
            )
            summaries.append(summary)

        return AuditReport(
            base_data_path=self.base_data_path,
            summaries=tuple(summaries),
            findings=tuple(self._findings),
            notes=tuple(self._notes),
        )
