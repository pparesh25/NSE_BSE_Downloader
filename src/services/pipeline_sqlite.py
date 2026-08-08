"""Transactional SQLite/WAL storage for per-date pipeline records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NoReturn, Optional

from .state_store import StateCorruptionError, quarantine_copy


class SQLitePipelineStore:
    """Store one validated JSON record per indexed pipeline date."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: Path,
        validator: Callable[[dict[str, Any]], None],
        quarantine_root: Path,
    ):
        self.path = Path(path)
        self.validator = validator
        self.quarantine_root = Path(quarantine_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    def _raise_corruption(self, error: Exception) -> NoReturn:
        backup = quarantine_copy(
            self.path, self.quarantine_root, "pipeline_sqlite"
        )
        for suffix in ("-wal", "-shm"):
            quarantine_copy(
                Path(str(self.path) + suffix),
                self.quarantine_root,
                "pipeline_sqlite",
            )
        raise StateCorruptionError(self.path, backup, error) from error

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
            journal_mode = connection.execute(
                "PRAGMA journal_mode=WAL"
            ).fetchone()
            if journal_mode is None or journal_mode[0].lower() != "wal":
                raise sqlite3.DatabaseError("pipeline SQLite WAL is unavailable")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_dates (
                    record_key TEXT PRIMARY KEY,
                    exchange TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
                    skipped_reason TEXT,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pipeline_incomplete
                ON pipeline_dates(exchange, segment, complete, target_date)
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
                ("schema_version", str(self.SCHEMA_VERSION)),
            )
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            try:
                current_version = (
                    int(version["value"]) if version is not None else None
                )
            except (TypeError, ValueError) as error:
                raise sqlite3.DatabaseError(
                    "invalid pipeline SQLite schema version"
                ) from error
            if current_version != self.SCHEMA_VERSION:
                raise sqlite3.DatabaseError("unsupported pipeline SQLite schema")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise sqlite3.DatabaseError("pipeline SQLite integrity check failed")

    @staticmethod
    def _payload(record: dict[str, Any]) -> str:
        return json.dumps(record, sort_keys=True, separators=(",", ":"))

    def _validated_record(self, key: str, record: dict[str, Any]) -> None:
        self.validator({"version": 1, "dates": {key: record}})

    def legacy_imported(self) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM metadata WHERE key='legacy_json_imported'"
                ).fetchone()
            return row is not None
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    def get_record(self, key: str) -> Optional[dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT record_json FROM pipeline_dates WHERE record_key=?",
                    (key,),
                ).fetchone()
            if row is None:
                return None
            record = json.loads(row["record_json"])
            self._validated_record(key, record)
            return record
        except (json.JSONDecodeError, sqlite3.DatabaseError, ValueError) as error:
            self._raise_corruption(error)

    def has_record(self, key: str) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM pipeline_dates WHERE record_key=?", (key,)
                ).fetchone()
            return row is not None
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    @staticmethod
    def _record_values(
        key: str, record: dict[str, Any]
    ) -> tuple[Any, ...]:
        return (
            key,
            record["exchange"],
            record["segment"],
            record["date"],
            int(record["complete"]),
            record.get("skipped_reason"),
            str(record.get("updated_at", "")),
            SQLitePipelineStore._payload(record),
        )

    def update_records(
        self,
        keys: Iterable[str],
        updater: Callable[
            [dict[str, Optional[dict[str, Any]]]], None
        ],
    ) -> None:
        """Read, mutate and write selected records in one locked transaction."""

        selected = list(dict.fromkeys(keys))
        if not selected:
            return
        placeholders = ",".join("?" for _ in selected)
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT record_key, record_json FROM pipeline_dates "
                f"WHERE record_key IN ({placeholders})",
                selected,
            ).fetchall()
            records: dict[str, Optional[dict[str, Any]]] = {
                key: None for key in selected
            }
            try:
                for row in rows:
                    key = row["record_key"]
                    record = json.loads(row["record_json"])
                    self._validated_record(key, record)
                    records[key] = record
            except (json.JSONDecodeError, ValueError) as error:
                connection.rollback()
                self._raise_corruption(error)
            updater(records)
            prepared = [
                (key, record)
                for key, record in records.items()
                if record is not None
            ]
            for key, record in prepared:
                self._validated_record(key, record)
            connection.executemany(
                """
                INSERT INTO pipeline_dates(
                    record_key, exchange, segment, target_date, complete,
                    skipped_reason, updated_at, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    exchange=excluded.exchange,
                    segment=excluded.segment,
                    target_date=excluded.target_date,
                    complete=excluded.complete,
                    skipped_reason=excluded.skipped_reason,
                    updated_at=excluded.updated_at,
                    record_json=excluded.record_json
                """,
                [self._record_values(key, record) for key, record in prepared],
            )

    def import_manifest(self, data: dict[str, Any]) -> bool:
        """Import legacy JSON once and verify parity inside one transaction."""

        self.validator(data)
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM pipeline_dates LIMIT 1"
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    ("legacy_json_imported", "existing_sqlite"),
                )
                return False
            for key, record in data["dates"].items():
                self._validated_record(key, record)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO pipeline_dates(
                        record_key, exchange, segment, target_date, complete,
                        skipped_reason, updated_at, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._record_values(key, record),
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("legacy_json_imported", "1"),
            )
            rows = connection.execute(
                "SELECT record_key, record_json FROM pipeline_dates "
                "ORDER BY record_key"
            ).fetchall()
            imported = {
                "version": 1,
                "dates": {
                    row["record_key"]: json.loads(row["record_json"])
                    for row in rows
                },
            }
            self.validator(imported)
            if imported != data:
                raise ValueError("SQLite pipeline import parity verification failed")
            return True

    def export_manifest(self) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT record_key, record_json FROM pipeline_dates "
                    "ORDER BY record_key"
                ).fetchall()
            data = {
                "version": 1,
                "dates": {
                    row["record_key"]: json.loads(row["record_json"])
                    for row in rows
                },
            }
            self.validator(data)
            return data
        except (json.JSONDecodeError, sqlite3.DatabaseError, ValueError) as error:
            self._raise_corruption(error)

    def segment_records(
        self, exchange: str, segment: str
    ) -> list[dict[str, Any]]:
        """Return every record for one exchange/segment, oldest first."""

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT record_json FROM pipeline_dates
                    WHERE exchange=? AND segment=?
                    ORDER BY target_date
                    """,
                    (exchange.upper(), segment.upper()),
                ).fetchall()
            return [json.loads(row["record_json"]) for row in rows]
        except (json.JSONDecodeError, sqlite3.DatabaseError, ValueError) as error:
            self._raise_corruption(error)

    def neighbouring_records(
        self,
        exchange: str,
        segment: str,
        target_date: date,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return records for the sessions closest in date to ``target_date``.

        Nearest in *either* direction, because a backfill walks backwards: for
        1995 the only comparable sessions are the ones already fetched around
        it, and comparing 1995 against 2026 would reject the whole era.
        """

        query = """
            SELECT record_json FROM pipeline_dates
            WHERE exchange=? AND segment=? AND target_date{operator}?
            ORDER BY target_date {order}
            LIMIT ?
        """
        try:
            with self._connect() as connection:
                rows = [
                    *connection.execute(
                        query.format(operator="<", order="DESC"),
                        (exchange.upper(), segment.upper(),
                         target_date.isoformat(), limit),
                    ).fetchall(),
                    *connection.execute(
                        query.format(operator=">", order="ASC"),
                        (exchange.upper(), segment.upper(),
                         target_date.isoformat(), limit),
                    ).fetchall(),
                ]
            return [json.loads(row["record_json"]) for row in rows]
        except (json.JSONDecodeError, sqlite3.DatabaseError, ValueError) as error:
            self._raise_corruption(error)

    def incomplete_dates(self, exchange: str, segment: str) -> list[date]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT target_date FROM pipeline_dates
                    WHERE exchange=? AND segment=? AND complete=0
                      AND skipped_reason IS NULL
                    ORDER BY target_date
                    """,
                    (exchange.upper(), segment.upper()),
                ).fetchall()
            return [date.fromisoformat(row["target_date"]) for row in rows]
        except (sqlite3.DatabaseError, ValueError) as error:
            self._raise_corruption(error)
