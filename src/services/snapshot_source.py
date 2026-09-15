"""Where the readers of canonical daily snapshots get them from.

Phase 5 step 4.  Five things read ``.state/raw`` -- the history batch journal,
the ``--rebuild-*`` commands, the rebuild prompt, ``--audit`` and the republish
forensics -- and the snapshots cannot become optional until each has a source
that yields the same thing.  This module is that seam: one interface, backed
either by the files or by the EOD database.

The two are interchangeable only because of what was measured first: every one
of the owner's twelve real snapshots regenerates from the database byte for
byte, with sha256 matching its recorded metadata.  Equal bytes parsed the same
way give equal frames, so nothing downstream can tell the sources apart --
which is the property every reader needs, and the one the tests pin.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from .eod_export import snapshot_text
from .eod_store import EodReader

#: The segments snapshots exist for: the ones symbol histories are built from.
#: Index and futures rows are published and mirrored but never replayed.
SNAPSHOT_SEGMENTS: tuple[str, ...] = ("EQ", "SME")


@dataclass(frozen=True, order=True)
class SnapshotEntry:
    """One canonical daily snapshot, named the way ``.state/raw`` names it.

    Ordered by exchange, segment and date, which is the order sorting the
    snapshot paths produces.  The order is not cosmetic: a rebuild replays
    snapshots in it, and the registry names colliding symbol files by first
    arrival.
    """

    exchange: str
    segment: str
    target_date: date

    @property
    def relative_path(self) -> str:
        return (
            f"{self.exchange}/{self.segment}/"
            f"{self.target_date.isoformat()}.csv"
        )


def _stamp_date(stamp: int) -> date:
    text = f"{int(stamp):08d}"
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


class RawFileSnapshots:
    """Snapshots as ``.state/raw`` holds them today."""

    def __init__(self, raw_root: Path):
        self.root = Path(raw_root)

    def entries(self, exchange: Optional[str] = None) -> list[SnapshotEntry]:
        wanted = exchange.upper() if exchange else None
        found: list[SnapshotEntry] = []
        if not self.root.is_dir():
            return found
        for path in sorted(self.root.rglob("*.csv")):
            parts = path.relative_to(self.root).parts
            if len(parts) != 3:
                continue
            try:
                target_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            entry = SnapshotEntry(
                parts[0].upper(), parts[1].upper(), target_date
            )
            if wanted is None or entry.exchange == wanted:
                found.append(entry)
        return found

    def path(self, entry: SnapshotEntry) -> Path:
        return self.root / entry.relative_path

    def text(self, entry: SnapshotEntry) -> str:
        return self.path(entry).read_text(encoding="utf-8")

    def frame(self, entry: SnapshotEntry) -> pd.DataFrame:
        return pd.read_csv(self.path(entry), dtype=str)

    def digest(self, entry: SnapshotEntry) -> str:
        return hashlib.sha256(self.path(entry).read_bytes()).hexdigest()


class DatabaseSnapshots:
    """The same snapshots, regenerated from the EOD database."""

    def __init__(
        self, store: EodReader, exchanges: Sequence[str] = ("NSE", "BSE")
    ):
        self.store = store
        self.exchanges = tuple(value.upper() for value in exchanges)

    def entries(self, exchange: Optional[str] = None) -> list[SnapshotEntry]:
        wanted = [exchange.upper()] if exchange else list(self.exchanges)
        return sorted(
            SnapshotEntry(name, segment, _stamp_date(stamp))
            for name in wanted
            for segment in SNAPSHOT_SEGMENTS
            for stamp in self.store.published_dates(name, segment)
        )

    def text(self, entry: SnapshotEntry) -> str:
        return snapshot_text(
            self.store, entry.exchange, entry.segment, entry.target_date
        )

    def frame(self, entry: SnapshotEntry) -> pd.DataFrame:
        # Parsed exactly as ``read_internal_snapshot`` parses the file, so
        # equal text is an equal frame.
        return pd.read_csv(StringIO(self.text(entry)), dtype=str)

    def digest(self, entry: SnapshotEntry) -> str:
        return hashlib.sha256(self.text(entry).encode("utf-8")).hexdigest()


def missing_from(
    database: DatabaseSnapshots,
    raw: RawFileSnapshots,
    exchange: Optional[str] = None,
) -> list[SnapshotEntry]:
    """Snapshot dates ``.state/raw`` holds that the database does not.

    The database fills forward from the day dual-write was switched on, so a
    tree older than that has snapshots the database never saw.  Reading such a
    tree from the database alone would silently drop those dates from a
    rebuild or an audit -- so every database-backed reader checks this first,
    and falls back to the files when it is not empty.
    """

    have = set(database.entries(exchange))
    return [entry for entry in raw.entries(exchange) if entry not in have]
