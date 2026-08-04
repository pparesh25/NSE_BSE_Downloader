"""Run-scoped, crash-resumable batching for optional symbol histories."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Optional

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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

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

    def prepare(self) -> Optional[tuple[str, tuple[HistoryJournalEntry, ...]]]:
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
                    "SELECT * FROM history_pending_entries ORDER BY entry_key"
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
                    """
                    INSERT INTO history_batch_entries(
                        batch_id, entry_key, exchange, segment, target_date,
                        snapshot_path, sha256
                    )
                    SELECT ?, entry_key, exchange, segment, target_date,
                           snapshot_path, sha256
                    FROM history_pending_entries
                    """,
                    (batch_id,),
                )
                connection.execute("DELETE FROM history_pending_entries")
            rows = connection.execute(
                "SELECT * FROM history_batch_entries WHERE batch_id=? "
                "ORDER BY entry_key",
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


class HistoryBatchCoordinator:
    """Collect raw dates and publish derived symbol files after core outputs."""

    _lock = Lock()

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
        self._action_windows: dict[tuple[str, str], HistoryActionWindow] = {}

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

    def finalize(self) -> tuple[HistoryBatchOutcome, ...]:
        with self._lock:
            return self._finalize_locked()

    def _finalize_locked(self) -> tuple[HistoryBatchOutcome, ...]:
        outcomes: list[HistoryBatchOutcome] = []
        while True:
            prepared = self.journal.prepare()
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
                )
                updates: list[StageUpdate] = [
                    (
                        entry.exchange,
                        entry.segment,
                        entry.target_date,
                        "symbols",
                        "complete",
                        {
                            "batch_id": batch_id,
                            "symbols": result.symbols,
                            "history_reads": result.history_reads,
                            "history_writes": result.history_writes,
                        },
                    )
                    for entry in entries
                ]
                self.pipeline.mark_many(updates)
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
                outcome="success",
                entries=result.entries,
                rows=result.rows,
                symbols=result.symbols,
                history_reads=result.history_reads,
                history_writes=result.history_writes,
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            outcomes.append(HistoryBatchOutcome(batch_id, entries, result))
