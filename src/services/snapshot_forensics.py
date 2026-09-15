"""What an exchange changed each time it republished a day.

Phase 5 step 4.  The owner chose to keep republish forensics in the EOD
database once ``.state/raw`` becomes optional, and a kept revision nobody can
read is not a kept capability.  This is the reading half.  It only reads, and it
reports what changed rather than leaving two files for a person to diff by eye.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd

from .eod_store import ReadOnlyEodStore
from .snapshot_source import SNAPSHOT_SEGMENTS, DatabaseSnapshots, SnapshotEntry

#: The exchanges' own clock, which is how a person thinks about when a
#: bhavcopy was replaced.
IST = timezone(timedelta(hours=5, minutes=30))

#: How many rows each kind of change names before it only counts the rest.
NAMED = 10


@dataclass(frozen=True)
class RevisionChange:
    """One superseded snapshot, and how it differs from what replaced it."""

    sha256: str
    superseded_at: float
    rows: int
    compared: bool
    withdrawn: tuple[str, ...]
    added: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class RevisionReport:
    """Every kept revision of one date, kept apart from how it is printed."""

    entry: SnapshotEntry
    current_rows: Optional[int]
    current_sha256: Optional[str]
    revisions: tuple[RevisionChange, ...]
    written: tuple[Path, ...] = ()

    def render(self) -> str:
        name = (
            f"{self.entry.exchange}_{self.entry.segment} "
            f"{self.entry.target_date.isoformat()}"
        )
        if not self.revisions:
            return (
                f"{name}: no superseded snapshots are kept for this date.\n"
                "The EOD database keeps one whenever a re-download changes a "
                "date's bytes, so this date was never republished with "
                "different content, or its revisions have aged out."
            )
        lines = [
            f"{name}: {len(self.revisions)} superseded snapshot(s) kept in "
            "the EOD database."
        ]
        if self.current_rows is None:
            lines.append(
                "There is no current snapshot for this date to compare with."
            )
        else:
            lines.append(
                f"Current snapshot: {self.current_rows:,} rows, "
                f"sha256 {(self.current_sha256 or '')[:12]}"
            )
        for number, revision in enumerate(self.revisions, 1):
            when = datetime.fromtimestamp(
                revision.superseded_at, IST
            ).strftime("%Y-%m-%d %H:%M IST")
            lines.append("")
            lines.append(
                f"{number}. Replaced {when} -- it held {revision.rows:,} rows, "
                f"sha256 {revision.sha256[:12]}"
            )
            if not revision.compared:
                lines.append("   (nothing newer is kept to compare it with)")
                continue
            against = (
                "the current snapshot" if number == 1
                else f"revision {number - 1}, which replaced it"
            )
            lines.append(f"   Compared with {against}:")
            if not (revision.withdrawn or revision.added or revision.changed):
                lines.append(
                    "   no row's values differ; only the order of the rows "
                    "changed"
                )
            for label, items in (
                ("withdrawn", revision.withdrawn),
                ("added", revision.added),
                ("changed", revision.changed),
            ):
                if not items:
                    continue
                lines.append(f"   {len(items):,} row(s) {label}:")
                lines.extend(f"     {item}" for item in items[:NAMED])
                if len(items) > NAMED:
                    lines.append(f"     ... and {len(items) - NAMED:,} more")
        if self.written:
            lines.append("")
            lines.append(f"Wrote {len(self.written)} revision file(s):")
            lines.extend(f"  {path}" for path in self.written)
        return "\n".join(lines)


def _rows(text: str) -> int:
    # Written by ``to_csv``, so every line including the last ends in a
    # newline and the first line is the header.
    return max(text.count("\n") - 1, 0)


def _keyed(text: str) -> dict[str, dict[str, str]]:
    """Rows keyed by symbol, as their exact text.

    Symbols were measured unique within every date, but a key collision must
    not silently fold two rows into one, so a repeat is named by its series
    and then by its occurrence.
    """

    frame = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    keyed: dict[str, dict[str, str]] = {}
    for record in frame.to_dict(orient="records"):
        base = str(record.get("SYMBOL", ""))
        key = base
        if key in keyed:
            key = f"{base} ({record.get('SERIES', '')})"
        occurrence = 2
        while key in keyed:
            key = f"{base} #{occurrence}"
            occurrence += 1
        keyed[key] = {str(name): str(value) for name, value in record.items()}
    return keyed


def _compare(
    older: str, newer: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    before, after = _keyed(older), _keyed(newer)
    withdrawn = tuple(sorted(set(before) - set(after)))
    added = tuple(sorted(set(after) - set(before)))
    changed = []
    for key in sorted(set(before) & set(after)):
        differences = [
            f"{column} {before[key][column] or '(blank)'} -> "
            f"{after[key].get(column, '') or '(blank)'}"
            for column in before[key]
            if column != "SYMBOL"
            and before[key][column] != after[key].get(column, "")
        ]
        if differences:
            changed.append(f"{key}: " + ", ".join(differences))
    return withdrawn, added, tuple(changed)


def report_revisions(
    base_data_path: Path,
    exchange: str,
    segment: str,
    target_date: date,
    output: Optional[Path] = None,
) -> RevisionReport:
    """Read every kept revision of one date and describe each change.

    Each revision is compared with the version that replaced it: the newest
    with the current snapshot, each older one with the next newer revision
    still kept.  With ``output``, every kept revision is also written there,
    named by exchange, segment, date and the start of its sha256.
    """

    exchange, segment = exchange.upper(), segment.upper()
    if segment not in SNAPSHOT_SEGMENTS:
        raise ValueError(
            f"{exchange}_{segment} keeps no snapshot revisions; only the "
            f"{' and '.join(SNAPSHOT_SEGMENTS)} segments do"
        )
    base = Path(base_data_path)
    database = base / ".state" / "eod.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(
            "this data root has no EOD database yet; it fills as dates are "
            "downloaded"
        )
    if output is not None:
        state = (base / ".state").resolve()
        target = Path(output).resolve()
        if target == state or state in target.parents:
            raise ValueError(
                "revision files must not be written inside .state, which "
                "holds the application's own records"
            )

    entry = SnapshotEntry(exchange, segment, target_date)
    stamp = int(target_date.strftime("%Y%m%d"))
    store = ReadOnlyEodStore(database)
    with store.session():
        kept = store.snapshot_revisions(exchange, segment, stamp)
        current = (
            DatabaseSnapshots(store).text(entry)
            if store.published_frame(exchange, segment, stamp) is not None
            else None
        )

    changes = []
    newer = current
    for revision in kept:
        text = revision["text"]
        if newer is None:
            withdrawn: tuple[str, ...] = ()
            added: tuple[str, ...] = ()
            changed: tuple[str, ...] = ()
        else:
            withdrawn, added, changed = _compare(text, newer)
        changes.append(RevisionChange(
            revision["sha256"], revision["superseded_at"], _rows(text),
            newer is not None, withdrawn, added, changed,
        ))
        newer = text

    written: list[Path] = []
    if output is not None and kept:
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        for revision in kept:
            path = folder / (
                f"{exchange}_{segment}_{target_date.isoformat()}_"
                f"{revision['sha256'][:12]}.csv"
            )
            path.write_text(revision["text"], encoding="utf-8")
            written.append(path)

    return RevisionReport(
        entry,
        None if current is None else _rows(current),
        None if current is None
        else hashlib.sha256(current.encode("utf-8")).hexdigest(),
        tuple(changes),
        tuple(written),
    )
