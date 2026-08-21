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

    def render(self) -> str:
        lines = [f"Checked {self.checked} published daily file(s)."]
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
                if self.checked
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
    mismatches: list[str] = []
    unmirrored: list[str] = []

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

    return ParityReport(checked, tuple(mismatches), tuple(unmirrored))
