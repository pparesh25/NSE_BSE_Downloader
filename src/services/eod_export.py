"""Regenerate published text from the EOD database.

Step 2 of Phase 5 in
[CODE_DEFECT_REMEDIATION_PLAN.md](../../docs/engineering/CODE_DEFECT_REMEDIATION_PLAN.md):
prove that the database holds everything the text files hold, by writing the
files back out of it and diffing them byte for byte against what the run
published.  Nothing here is wired into publication -- step 3 does that, and
only once this has held for a release.

The exporter deliberately does **not** format numbers itself.  It rebuilds the
frame and hands it to ``DataFrame.to_csv`` with the same arguments the
publisher uses, because the published text is whatever pandas made of that
frame and the surest way to reproduce it is to reproduce the frame.  That
turns byte parity into a question about *dtypes*, which is a question with a
finite answer, instead of a question about float repr, which is not.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from .canonical_data import (
    EQUITY_DAILY_COLUMNS,
    FO_DAILY_COLUMNS,
    INDEX_DAILY_COLUMNS,
    SYMBOL_HISTORY_COLUMNS,
)
from .eod_store import EodReader, ReadOnlyEodStore

#: What each segment publishes.
PUBLIC_COLUMNS: Mapping[str, Sequence[str]] = {
    "EQ": EQUITY_DAILY_COLUMNS,
    "SME": EQUITY_DAILY_COLUMNS,
    "INDEX": INDEX_DAILY_COLUMNS,
    "FO": FO_DAILY_COLUMNS,
}

#: Published column -> stored column.
_STORED = {
    "SYMBOL": "symbol",
    "DATE": "trade_date",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "VOLUME": "volume",
    "DELIVERY_QTY": "delivery_qty",
    "DELIVERY_PERCENT": "delivery_pct",
    "TURNOVER": "turnover",
    "PREV_CLOSE": "prev_close",
    "OPEN_INTEREST": "open_interest",
    "CHANGE_IN_OI": "change_in_oi",
    "SERIES": "series",
    "TOTAL_TRADES": "total_trades",
    "QTY_PER_TRADE": "qty_per_trade",
    "ISIN": "isin",
}

#: Columns the sources publish as whole numbers.  They reach the frame through
#: ``canonical_data._number``, which is ``pd.to_numeric`` over the source text:
#: a column with no missing value lands as ``int64`` and prints ``7``, and one
#: with any missing value lands as ``float64`` and prints ``7.0``.  That is a
#: property of the whole column on that date, not of the value, which is why
#: the rule below looks at every row before choosing a dtype.
_COUNT_COLUMNS = frozenset({
    "VOLUME", "TOTAL_TRADES", "DELIVERY_QTY", "OPEN_INTEREST", "CHANGE_IN_OI",
})

_TEXT_COLUMNS = frozenset({"SYMBOL", "DATE", "SERIES", "ISIN"})


def _column(
    name: str, values: Sequence[Any], dtype: Optional[str] = None
) -> pd.Series:
    """Rebuild one published column with the dtype the publisher gave it.

    ``dtype`` is the one the publisher's frame actually carried, recorded at
    write time.  It is used when it is available because the alternative --
    choosing from the values -- was measured to be wrong: a gapless
    whole-number column is ``int64`` in an equity frame and ``float64`` in an
    index frame, so inference wrote ``352148975`` where NSE published
    ``352148975.0``.  The fallback below is only for a database written before
    the signature was recorded.
    """

    if name == "DATE":
        return pd.Series(
            [f"{int(value):08d}" for value in values], dtype="object"
        )
    if name in _TEXT_COLUMNS:
        return pd.Series(["" if v is None else str(v) for v in values],
                         dtype="object")
    if dtype is not None and dtype.startswith("int"):
        return pd.Series(values, dtype=dtype)
    if dtype is None and name in _COUNT_COLUMNS and all(
        value is not None for value in values
    ):
        return pd.Series(values, dtype="int64")
    return pd.Series(
        [float("nan") if value is None else float(value) for value in values],
        dtype=dtype if dtype is not None and dtype.startswith("float")
        else "float64",
    )


def segment_frame(
    store: EodReader, exchange: str, segment: str, target_date: date
) -> pd.DataFrame:
    """Rebuild one segment's published component frame from the database."""

    exchange, segment = exchange.upper(), segment.upper()
    stamp = int(target_date.strftime("%Y%m%d"))
    recorded = store.published_frame(exchange, segment, stamp)
    columns = (
        list(recorded["columns"]) if recorded is not None
        else PUBLIC_COLUMNS.get(segment)
    )
    if columns is None:
        raise ValueError(f"No published contract for segment {segment}")
    dtypes = dict(zip(columns, recorded["dtypes"])) if recorded else {}
    rows = store.daily_rows(exchange, segment, stamp)
    return pd.DataFrame({
        name: _column(
            name, [row[_STORED[name]] for row in rows], dtypes.get(name)
        )
        for name in columns
    }, columns=list(columns))


def daily_text(
    store: EodReader,
    exchange: str,
    target_date: date,
    segment: str = "EQ",
    appended: Sequence[str] = (),
) -> str:
    """Return the exact text of one published daily file.

    ``appended`` names the segments a combined file carries after its own
    rows -- NSE EQ files can carry SME and index rows -- in the order
    ``CombinedFileBuilder`` concatenates them.  They are concatenated here the
    same way and for the same reason: ``pd.concat`` of an ``int64`` column with
    a column that is missing from another component produces ``float64``, so
    reproducing the file means reproducing the concatenation, not just the
    rows.
    """

    base = segment_frame(store, exchange, segment, target_date)
    if not appended:
        buffer = StringIO()
        base.to_csv(buffer, index=False, header=False, lineterminator="\n")
        return buffer.getvalue()

    # A combined file is a concatenation of the components' *text*, not of
    # their values.  Every frame reaches the builder through
    # ``DateJoinCoordinator``, which puts it through ``lexical_frame`` first,
    # and a component reloaded from disk is read back with ``dtype=str``
    # besides -- so by the time ``to_csv`` runs, every column is a string and
    # prints exactly as its component file printed it.
    #
    # This is why a real download was needed.  Handing numeric frames straight
    # to ``reconcile_frames`` in a test reproduces a path the application never
    # takes, and it silently agreed with a numeric export: the first real
    # combined file showed ``244646.0`` where the exchange published
    # ``244646``.
    from .combined_file_builder import CombinedFileBuilder

    frames = [CombinedFileBuilder.lexical_frame(base)]
    base_columns = list(frames[0].columns)
    for extra in appended:
        frame = segment_frame(store, exchange, extra, target_date)
        frames.append(
            CombinedFileBuilder.lexical_frame(frame).reindex(
                columns=base_columns
            )
        )
    combined = pd.concat(frames, ignore_index=True, sort=False)
    buffer = StringIO()
    combined.to_csv(buffer, index=False, header=False, lineterminator="\n")
    return buffer.getvalue()


def compare_daily(
    store: EodReader,
    published: Any,
    exchange: str,
    target_date: date,
    segment: str = "EQ",
    appended: Sequence[str] = (),
) -> Optional[str]:
    """Return ``None`` when the export matches the published file byte for byte.

    Otherwise return the first differing line rendered for a human, because
    "they differ" is not an actionable answer and the whole point of this step
    is to find out *how* they differ while it is still cheap to change.
    """

    expected = published.read_text(encoding="utf-8")
    actual = daily_text(store, exchange, target_date, segment, appended)
    if actual == expected:
        return None
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    if len(expected_lines) != len(actual_lines):
        return (
            f"{published.name}: published {len(expected_lines)} rows, "
            f"exported {len(actual_lines)}"
        )
    for index, (want, got) in enumerate(zip(expected_lines, actual_lines), 1):
        if want != got:
            return (
                f"{published.name}:{index}\n  published: {want}\n"
                f"  exported : {got}"
            )
    return f"{published.name}: trailing bytes differ"


# ---- the verification pass ---------------------------------------------


@dataclass(frozen=True)
class ParityReport:
    """What the parity pass found, kept separate from how it is printed."""

    checked: int
    mismatches: tuple[str, ...]
    unmirrored: tuple[str, ...]
    histories: int = 0

    def render(self) -> str:
        lines = [
            f"Checked {self.checked} published daily file(s) and "
            f"{self.histories} symbol history/histories."
        ]
        if self.unmirrored:
            lines.append(
                f"\n{len(self.unmirrored)} file(s) predate the database and "
                "were not checked:"
            )
            lines.extend(f"  {name}" for name in self.unmirrored[:20])
            if len(self.unmirrored) > 20:
                lines.append(f"  ... and {len(self.unmirrored) - 20} more")
        if not self.mismatches:
            # "Nothing was wrong" and "nothing was looked at" must not read
            # the same, or an empty data root reports as a clean one.
            lines.append(
                "\nEvery mirrored file regenerated byte for byte."
                if self.checked or self.histories
                else "\nNothing was compared: no published file has a "
                     "mirrored counterpart yet."
            )
            return "\n".join(lines)
        lines.append(f"\n{len(self.mismatches)} file(s) did not match:\n")
        lines.extend(self.mismatches)
        return "\n".join(lines)


def _appended_for(
    store: EodReader, exchange: str, target_date: date, published_lines: int
) -> Optional[Sequence[str]]:
    """Which components an EQ file carries, decided by row counts.

    A combined file is its own rows followed by another segment's, so the only
    candidate that can be right is the one whose component row counts sum to
    the number of lines in the file.  Guessing from the user's current append
    preferences would be wrong for any file published under different ones.
    """

    from .combined_file_builder import CombinedFileBuilder

    order = CombinedFileBuilder.COMPONENT_ORDER.get(exchange.upper())
    if order is None:
        return ()
    stamp = int(target_date.strftime("%Y%m%d"))

    def rows(segment: str) -> Optional[int]:
        recorded = store.published_frame(exchange.upper(), segment, stamp)
        return None if recorded is None else recorded["rows"]

    base = rows("EQ")
    if base is None:
        return None
    optional = [segment for segment in order[1:] if rows(segment) is not None]
    for size in range(len(optional), -1, -1):
        for index in range(len(optional) - size + 1):
            candidate = tuple(optional[index:index + size])
            total = base + sum(rows(segment) or 0 for segment in candidate)
            if total == published_lines:
                return candidate
    return None


def verify_parity(config: Any, segments: Sequence[str] = ()) -> ParityReport:
    """Regenerate every published daily file and diff it against the original.

    Reads only.  A file the database has no record of is reported as
    unmirrored rather than as a mismatch: dual-write fills the database as
    dates are downloaded, so files written before it was switched on have
    nothing to be compared with, and calling that a failure would bury the
    real ones.
    """

    base = Path(config.base_data_path)
    # Never ``EodStore`` here.  Constructing one creates the database, so a
    # command that only reports would bring into existence the very thing it
    # was asked to report on -- and on an empty data root that is the whole
    # answer, silently replaced with an empty success.
    store = ReadOnlyEodStore(base / ".state" / "eod.sqlite3")
    wanted = {value.upper() for value in segments}
    checked = 0
    histories = 0
    mismatches: list[str] = []
    unmirrored: list[str] = []
    state = base / ".state"
    registry_path = state / "symbol_registry.json"
    registry: Mapping[str, Any] = (
        json.loads(registry_path.read_text(encoding="utf-8"))
        if registry_path.is_file() else {}
    )
    actions = applied_actions(state)

    for name in config.get_available_exchanges():
        exchange, _, segment = name.partition("_")
        if wanted and name.upper() not in wanted:
            continue
        folder = config.resolve_data_path(exchange, segment)
        if not folder.is_dir():
            continue
        for published in sorted(folder.glob("*.txt")):
            try:
                target_date = date.fromisoformat(published.stem[:10])
            except ValueError:
                continue
            stamp = int(target_date.strftime("%Y%m%d"))
            if store.published_frame(exchange, segment, stamp) is None:
                unmirrored.append(published.name)
                continue
            appended: Sequence[str] = ()
            if segment == "EQ":
                lines = sum(1 for _ in published.open("rb"))
                resolved = _appended_for(store, exchange, target_date, lines)
                if resolved is None:
                    mismatches.append(
                        f"{published.name}: no combination of mirrored "
                        f"components accounts for its {lines} rows"
                    )
                    checked += 1
                    continue
                appended = resolved
            report = compare_daily(
                store, published, exchange, target_date, segment, appended
            )
            checked += 1
            if report is not None:
                mismatches.append(report)

    # Symbol histories, once per exchange rather than once per segment: a
    # history is not owned by a segment, it accumulates rows from every one
    # that mentions the security.
    with _session(store):
        for exchange in sorted({
            name.partition("_")[0]
            for name in config.get_available_exchanges()
            if not wanted or name.upper() in wanted
        }):
            folder = base / exchange / "SYMBOLS"
            if not folder.is_dir():
                continue
            for published in sorted(folder.glob("*.txt")):
                report = compare_symbol(
                    store, registry, published, exchange, actions
                )
                histories += 1
                if report is not None:
                    mismatches.append(report)

    return ParityReport(
        checked, tuple(mismatches), tuple(unmirrored), histories
    )


@contextmanager
def _session(store: Any) -> Any:
    """Use the store's one-copy session when it has one.

    Without it the read-only store copies the whole database per query, which
    turned a pass over 8,078 histories into minutes of copying; with it the
    same pass takes ten seconds.
    """

    opener = getattr(store, "session", None)
    if opener is None:
        yield store
        return
    with opener():
        yield store


# ---- symbol histories ---------------------------------------------------
#
# A daily file is one frame written once.  A symbol history is an accumulation:
# rows from many dates, possibly from more than one segment, under a name the
# registry chose and may since have changed.  So three things have to be
# resolved that the daily export never faced -- which rows belong to the file,
# which row wins when a date appears twice, and what dtype a column reaches
# after rows from differently-typed dates are combined.

#: Always float in a published history.  Measured over 8,078 real symbol files:
#: not one value in any of these columns is spelled as an integer.  Prices are
#: parsed from decimal text and ``QTY_PER_TRADE`` is a rounded quotient, so
#: none of them can arrive as ``int64``.
_ALWAYS_FLOAT = frozenset({
    "OPEN", "HIGH", "LOW", "CLOSE", "QTY_PER_TRADE", "DELIVERY_PERCENT",
    "TURNOVER", "PREV_CLOSE",
})

#: Why the recorded dtypes are needed rather than inferred ones.  ``VOLUME``
#: and ``TOTAL_TRADES`` come straight from the source report, so "spelled as a
#: float" and "has a missing value" agree for all 8,078 measured files.
#: ``DELIVERY_QTY`` arrives through a left join, so one unmatched row anywhere
#: in the day's frame makes the whole column ``float64`` -- including for
#: symbols whose own history has no gap at all.  Inference disagrees with the
#: published file for 3,430 of those 8,078 symbols; the recorded dtype agrees.


def symbol_keys(
    registry: Mapping[str, Any], exchange: str, filename: str
) -> list[str]:
    """Every stable key whose rows belong in one symbol file.

    The registry's own keys are used unchanged -- they are already
    ``ID:``/``ISIN:`` strings, which is the format ``security_key`` follows so
    that this lookup needs no translation.  NSE SME files have no entry at all,
    because the exchange publishes neither identifier for them; their rows are
    keyed by symbol, so the name the registry filed them under is the key.
    """

    exchange = exchange.upper()
    identities = registry.get("identities", {}).get(exchange, {})
    keys = list(identities.get(filename, ()))
    names = [
        symbol for symbol, name
        in registry.get("files", {}).get(exchange, {}).items()
        if name == filename
    ]
    keys.extend(f"SYM:{symbol.upper()}" for symbol in names)
    return list(dict.fromkeys(keys))


def _history_column(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    spellings: Sequence[Mapping[str, str]],
) -> pd.Series:
    """Rebuild one history column, spelled the way each row was written.

    A history is not one frame.  Rows written together share their frame's
    dtype, but a row appended by a later run is merged against the stored file
    read back as **text**, so it keeps whatever spelling it already had.  A
    real two-run history shows both in one column::

        20250101,...,80,80.0,...     <- written when the day had no gap
        20250102,...,,80.0,...       <- the day whose delivery join missed

    So the spelling is a property of the row, taken from the frame its own
    date published, and not of the column.  Where every row agrees the result
    is indistinguishable from a typed column; where they do not, each cell
    prints as itself, which is what the file does.
    """

    stored = _STORED[name]
    values = [row[stored] for row in rows]
    if name == "DATE":
        return pd.Series([f"{int(v):08d}" for v in values], dtype="object")
    if name in {"SERIES", "ISIN"}:
        return pd.Series(["" if v is None else str(v) for v in values],
                         dtype="object")
    if name in _ALWAYS_FLOAT:
        return pd.Series(
            [float("nan") if v is None else float(v) for v in values],
            dtype="float64",
        )

    spelled: list[Any] = []
    integral = True
    for value, spelling in zip(values, spellings):
        if value is None:
            spelled.append(None)
            integral = False
            continue
        recorded = spelling.get(name)
        if recorded is not None and recorded.startswith("float"):
            spelled.append(float(value))
            integral = False
        else:
            spelled.append(int(value))
    if integral:
        return pd.Series(spelled, dtype="int64")
    return pd.Series(
        [float("nan") if v is None else v for v in spelled], dtype="object"
    )


def applied_actions(state_path: Any) -> list[dict]:
    """Every corporate action the engine recorded as successfully applied.

    Read as plain JSON rather than through ``VersionedJSONStore``, which
    quarantines what it cannot parse -- a write, and this path must not make
    one.  A malformed ledger raises, and the caller reports that the check
    could not run rather than reporting the histories as sound.
    """

    ledger = Path(state_path) / "corporate_actions.json"
    if not ledger.is_file():
        return []
    data = json.loads(ledger.read_text(encoding="utf-8"))
    return [
        record for record in data.get("actions", {}).values()
        if record.get("status") == "applied"
    ]


def _adjust(
    frame: pd.DataFrame,
    exchange: str,
    keys: Sequence[str],
    symbols: Sequence[str],
    actions: Sequence[dict],
) -> pd.DataFrame:
    """Reapply the recorded adjustments a published history already carries.

    The database stores rows as the exchange published them.  A symbol history
    does not: when a split or bonus is recorded, the engine rescales every
    earlier row in the file.  So a history with an applied action cannot be
    regenerated from raw rows alone, and this is the step that closes the gap.

    ``corporate_actions.adjust_rows`` does the arithmetic -- the engine's own
    function, not a restatement of it, for the reason its caller in
    ``symbol_history`` already gives: a replayed row that disagreed with the
    row the engine wrote would be resolved silently by deduplication. It also
    settles a dtype question on the way, because scaled share counts come back
    as ``int64``.
    """

    if frame.empty or not actions:
        return frame
    from .corporate_actions import adjust_rows

    identifiers = {key.split(":", 1)[1] for key in keys}
    known = {str(value).strip().upper() for value in symbols}
    by_ex_date: dict[str, float] = {}
    for record in actions:
        if str(record.get("exchange", "")).upper() != exchange.upper():
            continue
        stable_id = str(record.get("stable_id", "")).strip().upper()
        symbol = str(record.get("symbol", "")).strip().upper()
        if stable_id not in identifiers and symbol not in known:
            continue
        try:
            ex_date = date.fromisoformat(str(record["ex_date"]))
            factor = float(record["factor"])
        except (KeyError, TypeError, ValueError):
            continue
        if factor <= 0:
            continue
        stamp = ex_date.strftime("%Y%m%d")
        by_ex_date[stamp] = by_ex_date.get(stamp, 1.0) * factor

    if not by_ex_date:
        return frame
    # A partial adjustment leaves the column holding both kinds of value, and
    # the published file shows it: NSE KIRLPNU prints ``110878`` on the
    # adjusted row and ``98368.0`` on the two after it, in the same column.
    # That is an object column printing each cell by its own type -- scaled
    # share counts come back from the engine as ``int64`` while the untouched
    # rows stay floats -- so the column is widened here rather than left to
    # coerce the engine's integers back into floats.
    from .corporate_actions import ADJUSTED_SHARE_COUNT_COLUMNS

    for column in ADJUSTED_SHARE_COUNT_COLUMNS:
        if column in frame.columns and frame[column].dtype != object:
            frame[column] = frame[column].astype(object)
    for stamp in sorted(by_ex_date):
        mask = frame["DATE"] < stamp
        if mask.any():
            adjust_rows(frame, mask, by_ex_date[stamp])
    return frame


def symbol_frame(
    store: EodReader,
    registry: Mapping[str, Any],
    exchange: str,
    filename: str,
    actions: Sequence[dict] = (),
    since: Optional[int] = None,
) -> pd.DataFrame:
    """Rebuild one published symbol history from the database.

    ``since`` returns only the rows after a date, which is what an append
    needs: reading the whole history to write one line back onto the end of it
    is the cost this exists to avoid.  It is never combined with a corporate
    action, because an action rescales rows the bound would have excluded --
    the publisher rewrites in full in that case.
    """

    exchange = exchange.upper()
    keys = symbol_keys(registry, exchange, filename)
    rows = store.security_rows(exchange, keys, since) if keys else []

    # One row per date, the last offered winning, matching
    # ``SymbolHistoryStore._deduplicate``.  ``security_rows`` already orders by
    # date and then by the row's position in its published frame.
    by_date: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        by_date[int(row["trade_date"])] = row
    ordered = [by_date[stamp] for stamp in sorted(by_date)]

    # One lookup per row, of the frame its own date published.  Cached because
    # thousands of symbols share the same handful of dates.
    cache: dict[tuple[str, int], Mapping[str, str]] = {}
    spellings = []
    for entry in ordered:
        key = (str(entry["segment"]), int(entry["trade_date"]))
        if key not in cache:
            recorded = store.published_frame(exchange, key[0], key[1])
            cache[key] = (
                dict(zip(recorded["columns"], recorded["dtypes"]))
                if recorded is not None else {}
            )
        spellings.append(cache[key])

    frame = pd.DataFrame({
        name: _history_column(name, ordered, spellings)
        for name in SYMBOL_HISTORY_COLUMNS
    }, columns=list(SYMBOL_HISTORY_COLUMNS))
    names = [
        symbol for symbol, stored
        in registry.get("files", {}).get(exchange, {}).items()
        if stored == filename
    ]
    return _adjust(frame, exchange, keys, names, actions)


def symbol_text(
    store: EodReader,
    registry: Mapping[str, Any],
    exchange: str,
    filename: str,
    actions: Sequence[dict] = (),
) -> str:
    """Return the exact text of one published symbol history, header included."""

    buffer = StringIO()
    symbol_frame(store, registry, exchange, filename, actions).to_csv(
        buffer, index=False, lineterminator="\n"
    )
    return buffer.getvalue()


def compare_symbol(
    store: EodReader,
    registry: Mapping[str, Any],
    published: Any,
    exchange: str,
    actions: Sequence[dict] = (),
) -> Optional[str]:
    """``None`` when the history regenerates byte for byte, else the difference."""

    expected = published.read_text(encoding="utf-8")
    actual = symbol_text(store, registry, exchange, published.name, actions)
    if actual == expected:
        return None
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    if len(expected_lines) != len(actual_lines):
        return (
            f"{published.name}: published {len(expected_lines)} lines, "
            f"exported {len(actual_lines)}"
        )
    for index, (want, got) in enumerate(zip(expected_lines, actual_lines), 1):
        if want != got:
            return (
                f"{published.name}:{index}\n  published: {want}\n"
                f"  exported : {got}"
            )
    return f"{published.name}: trailing bytes differ"
