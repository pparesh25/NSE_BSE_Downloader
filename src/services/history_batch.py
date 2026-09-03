"""Run-scoped, crash-resumable batching for optional symbol histories."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Iterator, Optional

from .pipeline_state import PipelineManifest, StageUpdate
from .pipeline_telemetry import PipelineTelemetry
from .state_store import StateStoreError, file_sha256
from .symbol_history import (
    HistoryBatchItem,
    HistoryBatchResult,
    SymbolHistoryStore,
)


@dataclass(frozen=True)
class HistoryJournalEntry:
    entry_key: str
    exchange: str
    segment: str
    target_date: date
    snapshot_path: str
    sha256: str


@dataclass(frozen=True)
class HistoryActionWindow:
    exchange: str
    segment: str
    dates: tuple[date, ...]
    add_sme_suffix: bool
    timeout: int


@dataclass(frozen=True)
class HistoryBatchOutcome:
    batch_id: str
    entries: tuple[HistoryJournalEntry, ...]
    result: HistoryBatchResult


class HistoryBatchJournal:
    """Small WAL journal sharing the transactional pipeline database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                raise StateStoreError("History batch journal requires SQLite WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_pending_entries (
                    entry_key TEXT PRIMARY KEY,
                    exchange TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_batches (
                    batch_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status='prepared'),
                    prepared_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_batch_entries (
                    batch_id TEXT NOT NULL REFERENCES history_batches(batch_id)
                        ON DELETE CASCADE,
                    entry_key TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY(batch_id, entry_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_batch_symbols (
                    batch_id TEXT NOT NULL REFERENCES history_batches(batch_id)
                        ON DELETE CASCADE,
                    symbol_path TEXT NOT NULL,
                    completed_at REAL NOT NULL,
                    PRIMARY KEY(batch_id, symbol_path)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_batch_entry "
                "ON history_batch_entries(entry_key)"
            )

    @staticmethod
    def _entry(row: sqlite3.Row) -> HistoryJournalEntry:
        return HistoryJournalEntry(
            entry_key=row["entry_key"],
            exchange=row["exchange"],
            segment=row["segment"],
            target_date=date.fromisoformat(row["target_date"]),
            snapshot_path=row["snapshot_path"],
            sha256=row["sha256"],
        )

    def enqueue(self, entry: HistoryJournalEntry) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                INSERT INTO history_pending_entries(
                    entry_key, exchange, segment, target_date,
                    snapshot_path, sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_key) DO UPDATE SET
                    exchange=excluded.exchange,
                    segment=excluded.segment,
                    target_date=excluded.target_date,
                    snapshot_path=excluded.snapshot_path,
                    sha256=excluded.sha256
                """,
                (
                    entry.entry_key,
                    entry.exchange,
                    entry.segment,
                    entry.target_date.isoformat(),
                    entry.snapshot_path,
                    entry.sha256,
                ),
            )

    def active_contains(self, entry_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM history_batch_entries WHERE entry_key=? LIMIT 1",
                (entry_key,),
            ).fetchone()
        return row is not None

    def prepare(
        self, limit: Optional[int] = None
    ) -> Optional[tuple[str, tuple[HistoryJournalEntry, ...]]]:
        """Claim the open batch, or cut a new one of at most ``limit`` entries.

        The limit is what keeps a multi-year backfill inside memory. The caller
        loads every entry of a batch at once, so an unbounded batch makes peak
        memory a function of how long the run has been collecting rather than
        of anything the machine can bound.

        Entries are cut in date order, so a batch is always a contiguous window
        and rows still merge in the order they would have if the whole run were
        one batch.
        """

        bound = limit if limit is not None and limit > 0 else -1
        selection = (
            "SELECT entry_key FROM history_pending_entries "
            "ORDER BY target_date, entry_key LIMIT ?"
        )
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT batch_id FROM history_batches ORDER BY prepared_at LIMIT 1"
            ).fetchone()
            if active is not None:
                batch_id = active["batch_id"]
            else:
                pending = connection.execute(
                    "SELECT * FROM history_pending_entries "
                    f"WHERE entry_key IN ({selection}) "
                    "ORDER BY target_date, entry_key",
                    (bound,),
                ).fetchall()
                if not pending:
                    return None
                material = "|".join(
                    f"{row['entry_key']}:{row['sha256']}" for row in pending
                )
                batch_id = hashlib.sha256(material.encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO history_batches(batch_id, status, prepared_at) "
                    "VALUES (?, 'prepared', ?)",
                    (batch_id, time.time()),
                )
                connection.execute(
                    f"""
                    INSERT INTO history_batch_entries(
                        batch_id, entry_key, exchange, segment, target_date,
                        snapshot_path, sha256
                    )
                    SELECT ?, entry_key, exchange, segment, target_date,
                           snapshot_path, sha256
                    FROM history_pending_entries
                    WHERE entry_key IN ({selection})
                    """,
                    (batch_id, bound),
                )
                connection.execute(
                    "DELETE FROM history_pending_entries "
                    f"WHERE entry_key IN ({selection})",
                    (bound,),
                )
            rows = connection.execute(
                "SELECT * FROM history_batch_entries WHERE batch_id=? "
                "ORDER BY target_date, entry_key",
                (batch_id,),
            ).fetchall()
            return batch_id, tuple(self._entry(row) for row in rows)

    def completed_paths(self, batch_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol_path FROM history_batch_symbols WHERE batch_id=?",
                (batch_id,),
            ).fetchall()
        return {row["symbol_path"] for row in rows}

    def complete_symbol(self, batch_id: str, symbol_path: str) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "INSERT OR IGNORE INTO history_batch_symbols("
                "batch_id, symbol_path, completed_at) VALUES (?, ?, ?)",
                (batch_id, symbol_path, time.time()),
            )

    def finish(self, batch_id: str) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "DELETE FROM history_batches WHERE batch_id=?", (batch_id,)
            )


def _publish_from_database(config: Any) -> bool:
    """Whether symbol files come out of the EOD database this run."""

    try:
        from .settings import SettingsService

        return bool(
            SettingsService(config).get_download_option(
                "publish_histories_from_database", False
            )
        )
    except Exception:
        # A preference that cannot be read is not a reason to change how
        # publication works.
        return False


class HistoryBatchCoordinator:
    """Collect raw dates and publish derived symbol files after core outputs."""

    _lock = Lock()

    # How many date/segment entries one batch may hold.  Every entry in a batch
    # is loaded at once, so this is the knob that trades peak memory against
    # how often each touched symbol file is rewritten.  Fifty dates keeps a
    # Select All backfill under roughly a gigabyte while still amortizing each
    # rewrite over fifty trading days.
    DEFAULT_BATCH_DATES = 50

    def __init__(
        self,
        config: Any,
        telemetry: Optional[PipelineTelemetry] = None,
    ):
        self.config = config
        self.base_path = Path(config.base_data_path)
        self.histories = SymbolHistoryStore(self.base_path)
        self.pipeline = PipelineManifest(self.base_path)
        self.journal = HistoryBatchJournal(self.pipeline.database_path)
        self.telemetry = telemetry or PipelineTelemetry()
        self.batch_dates = self._resolve_batch_dates(config)
        self._action_windows: dict[tuple[str, str], HistoryActionWindow] = {}
        # Phase 5 step 3.  When the EOD database publishes the histories, this
        # batch still does everything else it does -- registry, renames,
        # retirement, action windows -- and simply does not write the files.
        self.publish_files = not _publish_from_database(config)

    @classmethod
    def _resolve_batch_dates(cls, config: Any) -> int:
        settings = getattr(config, "download_settings", None)
        try:
            configured = int(
                getattr(settings, "history_batch_dates", None)
                or cls.DEFAULT_BATCH_DATES
            )
        except (TypeError, ValueError):
            return cls.DEFAULT_BATCH_DATES
        return max(1, configured)

    @staticmethod
    def _entry_key(exchange: str, segment: str, target_date: date) -> str:
        return f"{exchange.upper()}_{segment.upper()}:{target_date.isoformat()}"

    def offer(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        rows: Any,
    ) -> Path:
        """Checkpoint canonical rows and queue them without rewriting symbols."""

        exchange = exchange.upper()
        segment = segment.upper()
        key = self._entry_key(exchange, segment, target_date)
        with self._lock:
            if self.journal.active_contains(key):
                self._finalize_locked()
            snapshot = self.histories.save_internal_snapshot(
                exchange, segment, target_date, rows
            )
            relative = str(snapshot.relative_to(self.base_path))
            self.journal.enqueue(HistoryJournalEntry(
                key,
                exchange,
                segment,
                target_date,
                relative,
                file_sha256(snapshot),
            ))
        self.telemetry.record(
            "history_queued",
            exchange=exchange,
            segment=segment,
            exchange_segment=f"{exchange}_{segment}",
            target_date=target_date.isoformat(),
            rows=len(rows),
        )
        return snapshot

    def register_action_window(
        self,
        exchange: str,
        segment: str,
        dates: Iterable[date],
        *,
        add_sme_suffix: bool,
        timeout: int,
    ) -> None:
        key = (exchange.upper(), segment.upper())
        incoming = set(dates)
        previous = self._action_windows.get(key)
        if previous is not None:
            incoming.update(previous.dates)
        self._action_windows[key] = HistoryActionWindow(
            key[0],
            key[1],
            tuple(sorted(incoming)),
            add_sme_suffix,
            timeout,
        )

    def action_windows(self) -> tuple[HistoryActionWindow, ...]:
        return tuple(
            self._action_windows[key] for key in sorted(self._action_windows)
        )

    @staticmethod
    def _held_back_dates(
        result: HistoryBatchResult,
    ) -> dict[tuple[str, str, date], str]:
        """Map each date a failed symbol held back to why it was held back."""

        held_back: dict[tuple[str, str, date], list[str]] = {}
        for failure in result.failures:
            for identity in failure.entries:
                held_back.setdefault(identity, []).append(
                    f"{failure.path}: {failure.error}"
                )
        summaries: dict[tuple[str, str, date], str] = {}
        for identity, reasons in held_back.items():
            summary = "; ".join(reasons[:3])
            if len(reasons) > 3:
                summary += f"; and {len(reasons) - 3} more symbols"
            summaries[identity] = f"Symbol history not published -- {summary}"
        return summaries

    def finalize(
        self, on_progress: Optional[Callable[[int, int], None]] = None
    ) -> tuple[HistoryBatchOutcome, ...]:
        with self._lock:
            return self._finalize_locked(on_progress)

    def _finalize_locked(
        self, on_progress: Optional[Callable[[int, int], None]] = None
    ) -> tuple[HistoryBatchOutcome, ...]:
        outcomes: list[HistoryBatchOutcome] = []
        while True:
            prepared = self.journal.prepare(self.batch_dates)
            if prepared is None:
                return tuple(outcomes)
            batch_id, entries = prepared
            started = time.monotonic_ns()
            try:
                items = []
                for entry in entries:
                    relative = Path(entry.snapshot_path)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise StateStoreError(
                            "History journal contains an unsafe snapshot path"
                        )
                    snapshot = self.base_path / relative
                    if file_sha256(snapshot) != entry.sha256:
                        raise StateStoreError(
                            f"History snapshot checksum mismatch: {snapshot}"
                        )
                    items.append(HistoryBatchItem(
                        entry.exchange,
                        entry.segment,
                        entry.target_date,
                        self.histories.read_internal_snapshot(snapshot),
                    ))
                result = self.histories.upsert_batch(
                    items,
                    snapshots_saved=True,
                    completed_paths=self.journal.completed_paths(batch_id),
                    on_symbol_written=lambda path: self.journal.complete_symbol(
                        batch_id, path
                    ),
                    publish_files=self.publish_files,
                    on_progress=on_progress,
                )
                held_back = self._held_back_dates(result)
                updates: list[StageUpdate] = []
                for entry in entries:
                    identity = (
                        entry.exchange, entry.segment, entry.target_date
                    )
                    if identity in held_back:
                        updates.append((
                            *identity,
                            "symbols",
                            "failed",
                            {
                                "batch_id": batch_id,
                                "error": held_back[identity],
                            },
                        ))
                    else:
                        updates.append((
                            *identity,
                            "symbols",
                            "complete",
                            {
                                "batch_id": batch_id,
                                "symbols": result.symbols,
                                "history_reads": result.history_reads,
                                "history_writes": result.history_writes,
                            },
                        ))
                self.pipeline.mark_many(updates)
                # The batch is closed either way. Every date a failed symbol
                # held back is now marked failed, so it returns through the
                # ordinary repair path with a fresh download instead of
                # blocking the batches queued behind it.
                self.journal.finish(batch_id)
            except Exception as error:
                try:
                    self.pipeline.mark_many([
                        (
                            entry.exchange,
                            entry.segment,
                            entry.target_date,
                            "symbols",
                            "failed",
                            {"batch_id": batch_id, "error": str(error)},
                        )
                        for entry in entries
                    ])
                except Exception:
                    pass
                self.telemetry.record(
                    "history_batch_finished",
                    batch_id=batch_id,
                    outcome="error",
                    error_type=type(error).__name__,
                    duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                )
                raise
            self.telemetry.record(
                "history_batch_finished",
                batch_id=batch_id,
                outcome="partial" if result.failures else "success",
                failed_symbols=len(result.failures),
                refused_merges=len(result.refused_merges),
                entries=result.entries,
                rows=result.rows,
                symbols=result.symbols,
                history_reads=result.history_reads,
                history_writes=result.history_writes,
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            outcomes.append(HistoryBatchOutcome(batch_id, entries, result))
