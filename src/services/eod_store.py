"""SQLite storage for end-of-day rows, written beside the published text files.

This is step 1 of Phase 5 in
[CODE_DEFECT_REMEDIATION_PLAN.md](../../docs/engineering/CODE_DEFECT_REMEDIATION_PLAN.md):
*dual-write*.  Rows are inserted from the same canonical frames the text files
are written from, in the same transaction-per-date shape the rest of the
pipeline already uses, and **nothing reads this database yet**.  Publication,
symbol histories, rebuilds and corporate actions all still run entirely off the
text files, so the store can be deleted at any time with no loss and the whole
step is revertible by turning one setting off.

Why a separate database file from ``pipeline_state.sqlite3``:

* That store is bookkeeping -- two small tables and a history journal -- and it
  runs ``PRAGMA integrity_check`` on every open.  This table grows to millions
  of rows, and checking it on every application start would make startup scale
  with the size of the archive.
* The two have different lifecycles.  ``.state`` bookkeeping is disposable; this
  is the thing Phase 5 exists to make authoritative.
* Its schema version can move without touching a store whose strict version
  check guards the resume logic.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, NoReturn, Optional, Sequence

from .canonical_data import valid_isin, valid_security_id
from .state_store import StateCorruptionError, quarantine_copy

#: Segments whose published frames this store understands.  ``FO`` carries
#: open interest instead of delivery, and ``INDEX`` carries neither; both are
#: accommodated by leaving the columns they do not publish NULL.
SUPPORTED_SEGMENTS = frozenset({"EQ", "SME", "INDEX", "FO"})


def security_key(symbol: Any, isin: Any, security_id: Any) -> str:
    """Return the row identity used as part of the primary key.

    The preference order is measured, not assumed.  Across the owner's tree
    (196,137 equity rows over 29 trading days, both exchanges):

    * ``SECURITY_ID`` is unique within every date on both exchanges, and it
      survives a rename -- BSE code 543766 carries both ``ASHIKA`` and
      ``ASHIKAG``.  That is the property this store wants.
    * ``ISIN`` does **not** survive a face-value change: 11 NSE and 10 BSE
      security ids map to two ISINs each in five weeks.
    * ``SECURITY_ID`` is per *(security, series)* on NSE rather than per
      security: when ``AARTECH`` moved from ``EQ`` to ``BE`` on 2026-07-10 its
      id changed from 17145 to 17164 while its ISIN did not.  117 of 2,769 NSE
      ISINs are split that way.

    So neither identifier alone identifies a *company*, and this function does
    not pretend otherwise.  It returns a stable identity for a **published
    row**, which is what a primary key needs; deciding that two keys are the
    same security stays where it already lives and already works, in the symbol
    registry's stable-key merge.  ``isin`` and ``security_id`` are stored as
    columns precisely so that question stays answerable from this table.

    NSE SME publishes neither identifier -- all 12,418 rows sampled carry an
    empty ``ISIN`` and an empty ``SECURITY_ID`` -- so the symbol is the only
    identity that exists there, and it is unique within every date.
    """

    text = "" if security_id is None else str(security_id).strip().upper()
    if text and text not in {"NAN", "<NA>"} and valid_security_id(text):
        return f"ID:{text}"
    text = "" if isin is None else str(isin).strip().upper()
    if text and text not in {"NAN", "<NA>"} and valid_isin(text):
        return f"ISIN:{text}"
    text = "" if symbol is None else str(symbol).strip()
    if not text:
        raise ValueError("row has no symbol, ISIN or security id to key on")
    return f"SYM:{text.upper()}"


def _text(value: Any) -> str:
    """Normalize an identifier-ish cell, mapping pandas' nulls to ``''``."""

    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"", "nan", "NaN", "NAN", "<NA>", "None"} else text


def _real(value: Any) -> Optional[float]:
    """Parse a price-like cell.

    Empty stays ``None``, never ``0.0``: the exchanges leave fields blank where
    they publish nothing, and a zero would claim a BSE index traded no value
    rather than that none was published.  Measured safe -- all 208,555 sampled
    values in every price column round-trip through ``float`` unchanged.
    """

    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _count(value: Any) -> Optional[int]:
    """Parse a count-like cell (shares, trades, contracts) as an integer.

    Stored as ``INTEGER`` rather than ``REAL`` on measured grounds: every one of
    the 208,555 sampled ``VOLUME`` and ``TOTAL_TRADES`` values and 199,537
    ``DELIVERY_QTY`` values is a whole number well inside 2**53, while storing
    them as floats is the one thing that breaks an exact round-trip -- the
    sources write ``7``, and ``repr(7.0)`` is ``'7.0'``.  Keeping them integral
    makes the stored value exact and leaves how each exchange spells it to the
    export step, where it belongs.
    """

    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    rounded = int(round(number))
    return rounded if number == rounded else None


class EodStore:
    """Transactional store of one row per published security-day."""

    SCHEMA_VERSION = 1

    #: Ordered to match the INSERT below; kept as one list so a schema change
    #: cannot silently misalign the parameters.
    _COLUMNS: Sequence[str] = (
        "exchange", "segment", "security_key", "trade_date",
        "source_order", "symbol", "series", "isin", "security_id",
        "open", "high", "low", "close", "prev_close",
        "volume", "turnover", "total_trades", "qty_per_trade",
        "delivery_qty", "delivery_pct",
        "open_interest", "change_in_oi",
    )

    def __init__(self, path: Path, quarantine_root: Path):
        self.path = Path(path)
        self.quarantine_root = Path(quarantine_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    # ---- lifecycle -----------------------------------------------------

    def _raise_corruption(self, error: Exception) -> NoReturn:
        backup = quarantine_copy(self.path, self.quarantine_root, "eod_sqlite")
        for suffix in ("-wal", "-shm"):
            quarantine_copy(
                Path(str(self.path) + suffix), self.quarantine_root, "eod_sqlite"
            )
        raise StateCorruptionError(self.path, backup, error) from error

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
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
            journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal_mode is None or journal_mode[0].lower() != "wal":
                raise sqlite3.DatabaseError("EOD SQLite WAL is unavailable")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            # ``trade_date`` is YYYYMMDD as an integer so that ordering and
            # range scans are arithmetic, and so the index below is a plain
            # B-tree over fixed-width keys.
            #
            # ``WITHOUT ROWID`` is measured, not stylistic.  Loading the
            # owner's 208,555 rows both ways and querying each:
            #
            #     one symbol's whole series   52.04 ms -> 5.61 ms
            #     one bhavcopy by date         6.70 ms -> 11.80 ms
            #     bulk load                     1.17 s -> 1.64 s
            #     file size                    44.1 MB -> 43.4 MB
            #
            # Clustering the table on the primary key puts one security's rows
            # physically together, which is the query this project exists to
            # serve; a rowid table pays a separate row lookup per hit.  Across
            # 8,000 symbols that is the difference between a seven-minute
            # export pass and a forty-five-second one.  The bhavcopy query
            # gets slower and stays trivial.  Rows average ~130 bytes of
            # payload, comfortably inside the size where SQLite recommends
            # this.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS eod (
                    exchange      TEXT    NOT NULL,
                    segment       TEXT    NOT NULL,
                    security_key  TEXT    NOT NULL,
                    trade_date    INTEGER NOT NULL,
                    source_order  INTEGER NOT NULL,
                    symbol        TEXT    NOT NULL,
                    series        TEXT    NOT NULL DEFAULT '',
                    isin          TEXT    NOT NULL DEFAULT '',
                    security_id   TEXT    NOT NULL DEFAULT '',
                    open          REAL,
                    high          REAL,
                    low           REAL,
                    close         REAL,
                    prev_close    REAL,
                    volume        INTEGER,
                    turnover      REAL,
                    total_trades  INTEGER,
                    qty_per_trade REAL,
                    delivery_qty  INTEGER,
                    delivery_pct  REAL,
                    open_interest INTEGER,
                    change_in_oi  INTEGER,
                    PRIMARY KEY (exchange, segment, security_key, trade_date)
                ) WITHOUT ROWID
                """
            )
            # What the publisher's own frame looked like, one row per
            # published component.  The text a file carries is whatever
            # ``to_csv`` made of that frame, and pandas decides ``7`` versus
            # ``7.0`` from the column's dtype -- which is not recoverable from
            # the values.  A whole-number column with no gaps is ``int64`` in
            # an equity frame and ``float64`` in an index frame, and both are
            # correct.  Recording the dtypes is the only way to regenerate the
            # bytes; inferring them was measured to write ``352148975`` where
            # the publisher wrote ``352148975.0``.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS published_frames (
                    exchange   TEXT    NOT NULL,
                    segment    TEXT    NOT NULL,
                    trade_date INTEGER NOT NULL,
                    columns    TEXT    NOT NULL,
                    dtypes     TEXT    NOT NULL,
                    rows       INTEGER NOT NULL,
                    PRIMARY KEY (exchange, segment, trade_date)
                ) WITHOUT ROWID
                """
            )
            # ``WHERE trade_date = ?`` is the daily bhavcopy; the primary key
            # already serves ``WHERE security_key = ? ORDER BY trade_date``.
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_eod_date
                ON eod (trade_date, exchange, segment)
                """
            )
            # A security that changed ISIN or moved series has more than one
            # ``security_key``; this is how the pieces are found again.
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_eod_isin
                ON eod (exchange, isin, trade_date)
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
                current = int(version["value"]) if version is not None else None
            except (TypeError, ValueError) as error:
                raise sqlite3.DatabaseError(
                    "invalid EOD SQLite schema version"
                ) from error
            if current != self.SCHEMA_VERSION:
                raise sqlite3.DatabaseError("unsupported EOD SQLite schema")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise sqlite3.DatabaseError("EOD SQLite integrity check failed")

    # ---- writing -------------------------------------------------------

    @classmethod
    def _row_values(
        cls, exchange: str, segment: str, order: int, row: dict[str, Any]
    ) -> tuple[Any, ...]:
        symbol = _text(row.get("SYMBOL"))
        if not symbol:
            raise ValueError("frame row has no SYMBOL")
        trade_date = _text(row.get("DATE"))
        if not trade_date.isdigit() or len(trade_date) != 8:
            raise ValueError(f"frame row has an unusable DATE: {trade_date!r}")
        isin = _text(row.get("ISIN")).upper()
        identifier = _text(row.get("SECURITY_ID"))
        return (
            exchange,
            segment,
            security_key(symbol, isin, identifier),
            int(trade_date),
            order,
            symbol,
            _text(row.get("SERIES")),
            isin if valid_isin(isin) else "",
            identifier if valid_security_id(identifier) else "",
            _real(row.get("OPEN")),
            _real(row.get("HIGH")),
            _real(row.get("LOW")),
            _real(row.get("CLOSE")),
            _real(row.get("PREV_CLOSE")),
            _count(row.get("VOLUME")),
            _real(row.get("TURNOVER")),
            _count(row.get("TOTAL_TRADES")),
            _real(row.get("QTY_PER_TRADE")),
            _count(row.get("DELIVERY_QTY")),
            _real(row.get("DELIVERY_PERCENT")),
            _count(row.get("OPEN_INTEREST")),
            _count(row.get("CHANGE_IN_OI")),
        )

    def upsert_frame(
        self,
        exchange: str,
        segment: str,
        frame: Any,
        published: Any = None,
    ) -> int:
        """Insert or replace every row of one published frame.

        The whole frame lands in a single transaction, so a crash mid-write
        leaves the date either wholly present or wholly absent -- never half a
        bhavcopy.  Re-running a date is an update rather than a duplicate,
        which is what makes a re-download safe to repeat.

        ``published`` is the frame that reached ``to_csv``, when that is not
        the same object the values come from: equity segments carry identity
        in an internal frame and publish a narrower public one.  Its column
        names and dtypes are recorded so the file can be regenerated exactly;
        without them the export has to guess, and guessing was measured wrong.
        """

        if segment not in SUPPORTED_SEGMENTS:
            raise ValueError(f"unsupported segment for the EOD store: {segment}")
        records = self._records(frame)
        if not records:
            return 0
        values = [
            self._row_values(exchange, segment, order, row)
            for order, row in enumerate(records)
        ]
        placeholders = ", ".join("?" * len(self._COLUMNS))
        assignments = ", ".join(
            f"{column}=excluded.{column}"
            for column in self._COLUMNS
            if column not in {
                "exchange", "segment", "security_key", "trade_date",
            }
        )
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.executemany(
                    f"""
                    INSERT INTO eod ({", ".join(self._COLUMNS)})
                    VALUES ({placeholders})
                    ON CONFLICT (exchange, segment, security_key, trade_date)
                    DO UPDATE SET {assignments}
                    """,
                    values,
                )
                # Counted inside the same transaction as the rows it counts, so
                # a rolled-back date cannot leave the tally claiming it landed.
                signature = self._frame_signature(
                    published if published is not None else frame
                )
                if signature is not None:
                    columns, dtypes = signature
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO published_frames(
                            exchange, segment, trade_date, columns, dtypes, rows
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            exchange, segment, values[0][3],
                            ",".join(columns), ",".join(dtypes), len(values),
                        ),
                    )
                written = self._counter(connection, "rows_written") + len(values)
                self._set_counter(connection, "rows_written", written)
                analyzed = self._counter(connection, "rows_at_analyze")
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)
        if written >= max(self.ANALYZE_FLOOR, analyzed * self.ANALYZE_GROWTH):
            self._refresh_statistics(written)
        return len(values)

    #: Rows written before the first ANALYZE, and the growth factor that earns
    #: another one.  Doubling means ANALYZE runs about a dozen times on the way
    #: to a full multi-year archive rather than on every write -- it costs
    #: ~120 ms per 200,000 rows, so a scheduled handful is free and a per-write
    #: one would not be.
    ANALYZE_FLOOR = 20_000
    ANALYZE_GROWTH = 2

    def _refresh_statistics(self, written: int) -> None:
        """Keep the query planner's statistics roughly current.

        Without them the planner reads the clustered primary key as the
        cheapest path for a by-date query and scans the whole
        exchange/segment partition rather than using ``idx_eod_date``.  On the
        28 dates measured that is a 28-fold overscan; on a multi-year archive
        it is a several-thousand-fold one.

        ``PRAGMA optimize`` is the usual answer and is **not** used here,
        because it was measured not to work for this access pattern: this
        store opens a connection per transaction, so each one sees only its
        own small delta, decides no re-analysis is warranted, and leaves
        statistics frozen at whatever the first few thousand rows looked like.
        Stale statistics are worse than none -- they were what kept the
        planner on the wrong index.
        """

        try:
            with self._connect() as connection:
                connection.execute("ANALYZE")
                self._set_counter(connection, "rows_at_analyze", written)
        except sqlite3.DatabaseError:
            # A planner hint is never worth failing a committed write for.
            return

    @staticmethod
    def _counter(connection: sqlite3.Connection, key: str) -> int:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        try:
            return int(row["value"]) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _set_counter(
        connection: sqlite3.Connection, key: str, value: int
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (key, str(value)),
        )

    @staticmethod
    def _frame_signature(
        frame: Any,
    ) -> Optional[tuple[list[str], list[str]]]:
        """Column names and dtypes of a DataFrame, or ``None`` for anything else."""

        dtypes = getattr(frame, "dtypes", None)
        if dtypes is None:
            return None
        try:
            return (
                [str(name) for name in frame.columns],
                [str(dtype) for dtype in dtypes],
            )
        except (AttributeError, TypeError):
            return None

    def published_frame(
        self, exchange: str, segment: str, trade_date: int
    ) -> Optional[dict[str, Any]]:
        """What the publisher's frame looked like for one component."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT columns, dtypes, rows FROM published_frames "
                    "WHERE exchange = ? AND segment = ? AND trade_date = ?",
                    (exchange, segment, int(trade_date)),
                ).fetchone()
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)
        if row is None:
            return None
        return {
            "columns": row["columns"].split(","),
            "dtypes": row["dtypes"].split(","),
            "rows": int(row["rows"]),
        }

    @staticmethod
    def _records(frame: Any) -> list[dict[str, Any]]:
        """Accept a DataFrame or a plain sequence of mappings."""

        if frame is None:
            return []
        to_dict = getattr(frame, "to_dict", None)
        if callable(to_dict):
            return list(to_dict(orient="records"))
        return [dict(row) for row in frame]

    # ---- reading (for the parity work in step 2) -----------------------

    def row_count(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM eod").fetchone()
            return int(row[0]) if row is not None else 0
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    def dates(self, exchange: Optional[str] = None,
              segment: Optional[str] = None) -> list[int]:
        clauses, params = [], []
        if exchange is not None:
            clauses.append("exchange = ?")
            params.append(exchange)
        if segment is not None:
            clauses.append("segment = ?")
            params.append(segment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT DISTINCT trade_date FROM eod {where} "
                    "ORDER BY trade_date",
                    params,
                ).fetchall()
            return [int(row[0]) for row in rows]
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    def daily_rows(
        self, exchange: str, segment: str, trade_date: int
    ) -> list[dict[str, Any]]:
        """Every row of one bhavcopy, in the order the exchange published it.

        Not alphabetically.  Equity frames happen to be sorted by symbol, but
        index frames are not -- the NSE index report publishes ``Nifty 50``,
        ``Nifty Next 50``, ``Nifty 100`` in that order, which no sort of the
        stored columns reproduces.  ``source_order`` is the row's position in
        the frame that was published, and it is the only thing that makes the
        text regenerable byte for byte.
        """

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM eod WHERE exchange = ? AND segment = ? "
                    "AND trade_date = ? ORDER BY source_order",
                    (exchange, segment, int(trade_date)),
                ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)

    def security_rows(
        self, exchange: str, keys: Iterable[str]
    ) -> list[dict[str, Any]]:
        """One security's whole time series, ordered by date.

        Takes several keys because a security that changed ISIN or moved series
        has more than one, and the caller -- not this store -- is what knows
        they belong together.
        """

        keys = list(keys)
        if not keys:
            return []
        placeholders = ", ".join("?" * len(keys))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM eod WHERE exchange = ? "
                    f"AND security_key IN ({placeholders}) "
                    "ORDER BY trade_date, source_order",
                    [exchange, *keys],
                ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.DatabaseError as error:
            self._raise_corruption(error)
