"""Normalize every supported exchange schema to stable output contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
import logging
import re
import statistics
import zipfile
from typing import Any, Iterable, Optional, Sequence

import pandas as pd

from ..core.exceptions import DataProcessingError


logger = logging.getLogger(__name__)


#: Turnover and previous close close out the published contract.  They are
#: appended rather than inserted so a consumer reading the first nine columns
#: positionally is unaffected, and they are the only two fields the exchanges
#: publish that cannot be recovered from `.state/raw` later -- everything else
#: is already stored at full fidelity and can be re-published offline.
EXTENDED_MARKET_COLUMNS = ["TURNOVER", "PREV_CLOSE"]
EQUITY_DAILY_COLUMNS = [
    "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "DELIVERY_QTY", "DELIVERY_PERCENT", *EXTENDED_MARKET_COLUMNS,
]
INDEX_DAILY_COLUMNS = EQUITY_DAILY_COLUMNS[:7] + EXTENDED_MARKET_COLUMNS
FO_DAILY_COLUMNS = [
    "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "OPEN_INTEREST", "CHANGE_IN_OI", *EXTENDED_MARKET_COLUMNS,
]
# The trailing ISIN column makes each symbol file self-identifying: a merge
# that folded two securities together is detectable and reversible from the
# file alone.  It is appended last so positional readers of the first eleven
# columns keep working, and it is blank where the source report carries no
# identifier (NSE SME).
LEGACY_SYMBOL_HISTORY_COLUMNS = [
    "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "SERIES",
    "TOTAL_TRADES", "QTY_PER_TRADE", "DELIVERY_QTY", "DELIVERY_PERCENT",
]
#: ``ISIN`` keeps its position: turnover and previous close go after it so
#: that no column already written to a history file moves.
SYMBOL_HISTORY_COLUMNS = (
    LEGACY_SYMBOL_HISTORY_COLUMNS + ["ISIN"] + EXTENDED_MARKET_COLUMNS
)
#: What a history file looked like before this generation, kept so an existing
#: one can still be read and upgraded rather than refused.
PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS = LEGACY_SYMBOL_HISTORY_COLUMNS + ["ISIN"]
INTERNAL_EQUITY_COLUMNS = EQUITY_DAILY_COLUMNS + [
    "SERIES", "TOTAL_TRADES", "QTY_PER_TRADE", "ISIN", "SECURITY_ID",
]
#: The snapshot column list before turnover and previous close existed.
#: ``read_internal_snapshot`` matches the list exactly and quarantines what it
#: cannot match, so without this every snapshot already on disk would be
#: quarantined by the first rebuild after the upgrade.
PRE_EXTENDED_INTERNAL_EQUITY_COLUMNS = [
    column for column in INTERNAL_EQUITY_COLUMNS
    if column not in set(EXTENDED_MARKET_COLUMNS)
]

# Identity values act as merge keys: two symbols that share one stable key are
# treated as the same security, so a malformed value that repeats across
# unrelated rows ("-", "NA", a stray number in the ISIN column) must never
# become a key.  Measured against the owner's full tree (1,057,349 rows):
# every non-blank ISIN matches the ISO 6166 shape and every SECURITY_ID is
# purely numeric, so these shapes reject nothing real.
ISIN_PATTERN = r"[A-Z]{2}[A-Z0-9]{9}[0-9]"
_ISIN_SHAPE = re.compile(ISIN_PATTERN)
_SECURITY_ID_SHAPE = re.compile(r"[0-9]{1,12}")


def valid_isin(value: Any) -> bool:
    """True when ``value`` has the ISO 6166 ISIN shape (prefix+body+digit)."""

    return bool(_ISIN_SHAPE.fullmatch(str(value).strip().upper()))


def valid_security_id(value: Any) -> bool:
    """True when ``value`` looks like an exchange security code (numeric)."""

    return bool(_SECURITY_ID_SHAPE.fullmatch(str(value).strip().upper()))


NSE_EQUITY_SERIES = {"EQ", "BE", "BZ"}
NSE_SME_SERIES = {"SM", "ST"}
BSE_EQUITY_SERIES = {
    "A", "B", "M", "MS", "MT", "P", "R", "T", "W", "X", "XT", "Z", "ZP"
}


@dataclass(frozen=True)
class SourceSchema:
    """Required shape and date authority for one official report era."""

    required: frozenset[str]
    key_columns: tuple[str, ...]
    date_column: Optional[str] = None


# Turnover and previous close are listed as *required* wherever the era
# publishes them.  A missing column then stops that date with a message naming
# it, instead of publishing a decade of silently empty turnover that no
# `.state/raw` snapshot could ever fill in afterwards.
SOURCE_SCHEMAS = {
    "nse-equity-legacy": SourceSchema(
        frozenset({
            "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
            "TOTTRDQTY", "TIMESTAMP", "TOTTRDVAL", "PREVCLOSE",
        }),
        ("SYMBOL", "SERIES"),
        "TIMESTAMP",
    ),
    "nse-equity-udiff": SourceSchema(
        frozenset({
            "TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
            "LwPric", "ClsPric", "TtlTradgVol", "TtlTrfVal", "PrvsClsgPric",
        }),
        ("TckrSymb", "SctySrs"),
        "TradDt",
    ),
    "nse-fo-legacy": SourceSchema(
        frozenset({
            "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "OPEN", "HIGH", "LOW",
            "CLOSE", "CONTRACTS", "OPEN_INT", "CHG_IN_OI", "TIMESTAMP",
            "VAL_INLAKH",
        }),
        ("INSTRUMENT", "SYMBOL", "EXPIRY_DT"),
        "TIMESTAMP",
    ),
    "nse-fo-udiff": SourceSchema(
        frozenset({
            "FinInstrmTp", "TckrSymb", "XpryDt", "TradDt", "OpnPric",
            "HghPric", "LwPric", "ClsPric", "TtlTradgVol", "OpnIntrst",
            "ChngInOpnIntrst", "TtlTrfVal", "PrvsClsgPric",
        }),
        ("FinInstrmTp", "TckrSymb", "XpryDt"),
        "TradDt",
    ),
    "nse-sme-two-digit-year": SourceSchema(
        frozenset({
            "SERIES", "SYMBOL", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
            "CLOSE_PRICE", "NET_TRDQTY", "NET_TRDVAL", "PREV_CL_PR",
        }),
        ("SERIES", "SYMBOL"),
    ),
    "nse-sme-four-digit-year": SourceSchema(
        frozenset({
            "SERIES", "SYMBOL", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
            "CLOSE_PRICE", "NET_TRDQTY", "NET_TRDVAL", "PREV_CL_PR",
        }),
        ("SERIES", "SYMBOL"),
    ),
    "bse-equity-isin-legacy": SourceSchema(
        frozenset({
            "SC_CODE", "SC_NAME", "SC_GROUP", "OPEN", "HIGH", "LOW",
            "CLOSE", "NO_OF_SHRS", "TRADING_DATE", "NET_TURNOV", "PREVCLOSE",
        }),
        ("SC_CODE",),
        "TRADING_DATE",
    ),
    "bse-equity-bhavcopy-legacy": SourceSchema(
        frozenset({
            "SCRIP ID", "SCRIP_CODE", "SC_GROUP", "OPEN PRICE",
            "HIGH PRICE", "LOW PRICE", "CLOSING PRICE", "NO_OF_SHRS",
            "TRADING_DATE", "NET_TURNOV", "PREVIOUS CLOSE PRICE",
        }),
        ("SCRIP_CODE",),
        "TRADING_DATE",
    ),
    "bse-equity-udiff": SourceSchema(
        frozenset({
            "TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
            "LwPric", "ClsPric", "TtlTradgVol", "FinInstrmId", "TtlTrfVal",
            "PrvsClsgPric",
        }),
        ("FinInstrmId",),
        "TradDt",
    ),
    # Identical columns to the era above, still delivered inside the older
    # BSE_EQ_BHAVCOPY ZIP.  Kept as its own era so the name stays honest about
    # where the file came from, and so the truncated-ticker handling in
    # normalize_bse_equity can key off it.
    "bse-equity-udiff-zip": SourceSchema(
        frozenset({
            "TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
            "LwPric", "ClsPric", "TtlTradgVol", "FinInstrmId", "TtlTrfVal",
            "PrvsClsgPric",
        }),
        ("FinInstrmId",),
        "TradDt",
    ),
    "nse-index": SourceSchema(
        frozenset({
            "Index Name", "Index Date", "Open Index Value",
            "High Index Value", "Low Index Value", "Closing Index Value",
            "Turnover (Rs. Cr.)",
        }),
        ("Index Name",),
        "Index Date",
    ),
    "bse-index": SourceSchema(
        frozenset({
            "IndexName", "OpenPrice", "HighPrice", "LowPrice", "ClosePrice",
            "PreviousClose",
        }),
        ("IndexName",),
    ),
    "nse-delivery": SourceSchema(
        frozenset({
            "SYMBOL", "SERIES", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
        }),
        ("SYMBOL", "SERIES"),
    ),
    "bse-delivery": SourceSchema(
        frozenset({"SCRIP CODE", "DELIVERY QTY"}),
        ("SCRIP CODE",),
    ),
}


#: Where each era publishes turnover, and the factor that turns its value into
#: rupees.  Measured against one real report from every era on 2026-08-20
#: rather than inferred from the column's name: `turnover / (volume * close)`
#: has a median of 1.000 in every equity era, `VAL_INLAKH` read as lakhs puts
#: 2024-07-05 within an order of magnitude of the next schema's `TtlTrfVal`
#: (and read as rupees, five orders away), and "Nifty Total Market" read as
#: crores is 83.8% of that day's whole NSE cash turnover.  See the sampled
#: evidence under section 4.2 of the remediation plan.
TURNOVER_SOURCES: dict[str, tuple[str, int]] = {
    "nse-equity-legacy": ("TOTTRDVAL", 1),
    "nse-equity-udiff": ("TtlTrfVal", 1),
    "nse-fo-legacy": ("VAL_INLAKH", 100_000),
    "nse-fo-udiff": ("TtlTrfVal", 1),
    "nse-sme-two-digit-year": ("NET_TRDVAL", 1),
    "nse-sme-four-digit-year": ("NET_TRDVAL", 1),
    "nse-index": ("Turnover (Rs. Cr.)", 10_000_000),
    "bse-equity-isin-legacy": ("NET_TURNOV", 1),
    "bse-equity-bhavcopy-legacy": ("NET_TURNOV", 1),
    "bse-equity-udiff-zip": ("TtlTrfVal", 1),
    "bse-equity-udiff": ("TtlTrfVal", 1),
}

#: Where each era publishes the previous session's close.  Three eras publish
#: none: NSE F&O before the UDiFF schema, and both index reports on the NSE
#: side.  NSE's index report offers `Points Change` instead, which reproduces
#: the previous close to the paisa for 162 of 163 indices and is wrong by 4.82
#: for *Nifty50 Dividend Points*, where NSE publishes a change of zero -- so it
#: is not used, and the field stays empty rather than quietly wrong.
PREV_CLOSE_SOURCES: dict[str, str] = {
    "nse-equity-legacy": "PREVCLOSE",
    "nse-equity-udiff": "PrvsClsgPric",
    "nse-fo-udiff": "PrvsClsgPric",
    "nse-sme-two-digit-year": "PREV_CL_PR",
    "nse-sme-four-digit-year": "PREV_CL_PR",
    "bse-equity-isin-legacy": "PREVCLOSE",
    "bse-equity-bhavcopy-legacy": "PREVIOUS CLOSE PRICE",
    "bse-equity-udiff-zip": "PrvsClsgPric",
    "bse-equity-udiff": "PrvsClsgPric",
    "bse-index": "PreviousClose",
}


def turnover_in_rupees(frame: pd.DataFrame, era: str) -> pd.Series:
    """Turnover as rupees, or missing where the era publishes none."""

    entry = TURNOVER_SOURCES.get(era)
    if entry is None:
        return pd.Series(pd.NA, index=frame.index, dtype="object")
    column, scale = entry
    values = _number(_column(frame, column))
    return values if scale == 1 else values * scale


def previous_close(frame: pd.DataFrame, era: str) -> pd.Series:
    """The previous session's close as the exchange published it, or missing."""

    column = PREV_CLOSE_SOURCES.get(era)
    if column is None:
        return pd.Series(pd.NA, index=frame.index, dtype="object")
    return _number(_column(frame, column))


def read_report(payload: bytes, separator: str = ",") -> pd.DataFrame:
    """Read a report while rejecting common server-error payloads."""

    if not payload:
        raise DataProcessingError("Downloaded report is empty")

    data = payload
    if payload[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                members = [
                    name for name in archive.namelist()
                    if not name.endswith("/") and name.lower().endswith((".csv", ".txt"))
                ]
                if not members:
                    raise DataProcessingError("Archive has no CSV/TXT report")
                data = archive.read(members[0])
        except zipfile.BadZipFile as error:
            raise DataProcessingError("Downloaded archive is invalid") from error

    preview = data.lstrip()[:100].lower()
    if preview.startswith((b"<!doctype html", b"<html")):
        raise DataProcessingError("Server returned an HTML page instead of market data")
    if preview.startswith((b"{", b"[")):
        raise DataProcessingError("Server returned JSON instead of market data")

    try:
        frame = pd.read_csv(BytesIO(data), sep=separator, dtype=str)
    except Exception as error:
        raise DataProcessingError(f"Unable to parse market report: {error}") from error

    frame.columns = [str(column).strip() for column in frame.columns]
    if not len(frame.columns):
        raise DataProcessingError("Market report has no columns")
    if frame.empty:
        raise DataProcessingError("Market report has no data rows")
    return frame


def _parse_dates(values: pd.Series) -> pd.Series:
    text = _clean_text(values)
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing], errors="coerce", dayfirst=True
        )
    return parsed


def validate_source_schema(
    frame: pd.DataFrame, era: str, target_date: Optional[date]
) -> None:
    """Fail closed when an official report does not match its expected era."""

    schema = SOURCE_SCHEMAS.get(era)
    if schema is None:
        raise DataProcessingError(f"No source schema registered for era: {era}")
    if frame.empty:
        raise DataProcessingError(f"{era} report has no data rows")
    missing = sorted(schema.required.difference(frame.columns))
    if missing:
        raise DataProcessingError(
            f"{era} report is missing required columns: {missing}"
        )
    if schema.date_column:
        if target_date is None:
            raise DataProcessingError(
                f"{era} validation requires a requested date"
            )
        parsed = _parse_dates(frame[schema.date_column])
        if parsed.isna().any():
            raise DataProcessingError(
                f"{era} report contains an invalid {schema.date_column}"
            )
        source_dates = set(parsed.dt.date)
        if source_dates != {target_date}:
            found = ", ".join(sorted(value.isoformat() for value in source_dates))
            raise DataProcessingError(
                f"{era} report date mismatch: requested {target_date}, found {found}"
            )


def _column(frame: pd.DataFrame, *names: str, default=None) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series([default] * len(frame), index=frame.index)


def _clean_text(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip()


def _clean_identifier(values: pd.Series) -> pd.Series:
    cleaned = _clean_text(values)
    return cleaned.str.replace(r"\.0$", "", regex=True)


def _number(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _date_values(values: pd.Series, target_date: date) -> pd.Series:
    parsed = _parse_dates(values)
    if parsed.isna().any() or set(parsed.dt.date) != {target_date}:
        raise DataProcessingError(
            f"Source report date does not match requested date {target_date}"
        )
    return parsed.dt.strftime("%Y%m%d")


#: A row whose whole OHLC is zero carries no price at all and reads downstream
#: as a genuine -100% bar.  Measured across the owner's real tree -- 1,057,349
#: equity rows, 91,870 futures rows and 31,874 index rows -- it never occurs,
#: so dropping it removes no real market data.
#:
#: Zero *within* a row is a different thing and must survive: 1,515 real NSE
#: futures rows carry ``0,0,0`` OHL with a real settlement CLOSE, because a
#: far-month contract that did not trade still has a settlement price.  That is
#: also why the coherence rule below only applies where a traded range exists.
def drop_unpriced_rows(frame: pd.DataFrame, era: str) -> pd.DataFrame:
    """Remove rows with no price at all, or fail if that is every row."""

    if frame.empty:
        return frame
    values = [_number(frame[column]) for column in ("OPEN", "HIGH", "LOW", "CLOSE")]
    unpriced = values[0].eq(0)
    for series in values[1:]:
        unpriced &= series.eq(0)
    unpriced = unpriced.fillna(False)
    dropped = int(unpriced.sum())
    if not dropped:
        return frame
    if dropped == len(frame):
        raise DataProcessingError(
            f"{era} report contains no priced rows: every row is 0,0,0,0"
        )
    logger.warning(
        "%s: dropped %d row(s) with an all-zero OHLC", era, dropped
    )
    return frame.loc[~unpriced].copy()


#: Share of rows allowed to fall outside their own traded range before the
#: report is treated as malformed rather than merely odd.  Both exchanges
#: publish a few genuinely inconsistent rows: a BSE settlement close above the
#: day's high on a 1-share trade, and NSE futures whose theoretical settlement
#: price sits outside a thin contract's range.  The worst real day measured is
#: 0.47% (3 of 640 NSE FO rows), while a misaligned or truncated source file
#: violates in nearly every row, so 5% separates the two cases with an order of
#: magnitude to spare.
MAX_INCOHERENT_ROW_SHARE = 0.05
MIN_INCOHERENT_ROWS = 5


def incoherent_ohlc(frame: pd.DataFrame) -> "pd.Series[bool]":
    """Flag rows whose own OPEN/CLOSE fall outside their traded range.

    Only rows with a real range (HIGH and LOW both above zero) are judged, and
    a zero OPEN or CLOSE inside such a row is read as "not traded", not as a
    price of zero -- that is how both exchanges spell an absent value.
    """

    if frame.empty:
        return pd.Series(dtype=bool)
    opens, highs, lows, closes = (
        _number(frame[column]) for column in ("OPEN", "HIGH", "LOW", "CLOSE")
    )
    ranged = highs.gt(0) & lows.gt(0)

    def outside(values: pd.Series) -> pd.Series:
        return values.gt(0) & (values.lt(lows) | values.gt(highs))

    return (
        ranged & (lows.gt(highs) | outside(opens) | outside(closes))
    ).fillna(False)


#: A published day must be roughly the size of the days around it.  The band is
#: deliberately wide -- across 864 real day-files (six exchange/segment pairs,
#: 144 sessions each) not one falls outside it -- because it exists to catch a
#: placeholder or truncated report, not to police ordinary listing churn.
MIN_BAND_SESSIONS = 5
BAND_SESSIONS = 20
BAND_LOW = 0.5
BAND_HIGH = 1.5


def row_count_band_error(rows: int, neighbours: Sequence[int]) -> Optional[str]:
    """Return why ``rows`` is implausible beside its neighbouring sessions.

    ``None`` means "publish it": too few neighbours to judge, or a count
    within the band.  The comparison is against the median rather than the
    mean so one bad session already on disk cannot drag the band with it.
    """

    sample = [value for value in neighbours if value > 0]
    if len(sample) < MIN_BAND_SESSIONS:
        return None
    median = statistics.median(sample)
    if median <= 0:
        return None
    ratio = rows / median
    if BAND_LOW <= ratio <= BAND_HIGH:
        return None
    return (
        f"{rows} rows is {ratio:.0%} of the {median:.0f}-row median of the "
        f"{len(sample)} nearest sessions, outside the "
        f"{BAND_LOW:.0%}-{BAND_HIGH:.0%} band"
    )


def validate_canonical_data(
    frame: pd.DataFrame,
    target_date: date,
    columns: Sequence[str],
    *,
    key_columns: Sequence[str] = ("SYMBOL",),
    index_profile: bool = False,
) -> None:
    """Validate keys, date and numeric market fields before any file is saved."""

    if frame.empty:
        raise DataProcessingError("Normalized market report has no usable rows")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DataProcessingError(
            f"Normalized market report is missing columns: {missing}"
        )
    expected_date = target_date.strftime("%Y%m%d")
    actual_dates = _clean_text(frame["DATE"])
    if actual_dates.ne(expected_date).any():
        raise DataProcessingError(
            f"Normalized report date does not match requested date {target_date}"
        )
    for key in key_columns:
        if _clean_text(frame[key]).eq("").any():
            raise DataProcessingError(f"Normalized report contains blank {key}")
    if frame.duplicated(list(key_columns)).any():
        raise DataProcessingError(
            f"Normalized report contains duplicate keys: {list(key_columns)}"
        )

    required_numeric = ["CLOSE"] if index_profile else [
        "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"
    ]
    if "OPEN_INTEREST" in columns:
        required_numeric.extend(["OPEN_INTEREST", "CHANGE_IN_OI"])
    for column in required_numeric:
        values = _number(frame[column])
        if values.isna().any():
            raise DataProcessingError(
                f"Normalized report contains invalid numeric {column}"
            )
        if values.lt(0).any() and column != "CHANGE_IN_OI":
            raise DataProcessingError(
                f"Normalized report contains negative {column}"
            )

    # Optional by design -- three eras publish neither -- but a value that is
    # there has to be a real one.  A negative turnover is not "missing".
    for column in ("TURNOVER", "PREV_CLOSE"):
        if column not in columns:
            continue
        present = _number(frame[column])
        if present.lt(0).any():
            raise DataProcessingError(
                f"Normalized report contains negative {column}"
            )

    incoherent = incoherent_ohlc(frame)
    offenders = int(incoherent.sum())
    if offenders > max(
        MIN_INCOHERENT_ROWS, MAX_INCOHERENT_ROW_SHARE * len(frame)
    ):
        named = ", ".join(
            str(value) for value in frame.loc[incoherent, key_columns[0]].head(3)
        )
        raise DataProcessingError(
            f"Normalized report has {offenders} of {len(frame)} rows whose "
            f"OPEN/CLOSE fall outside their own HIGH/LOW range ({named})"
        )


def _finalize_equity(frame: pd.DataFrame, era: str = "equity") -> pd.DataFrame:
    for column in INTERNAL_EQUITY_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["SYMBOL"] = _clean_text(frame["SYMBOL"]).str.upper()
    frame["SERIES"] = _clean_text(frame["SERIES"]).str.upper()
    frame["ISIN"] = _clean_text(frame["ISIN"]).str.upper()
    frame["SECURITY_ID"] = _clean_identifier(frame["SECURITY_ID"])
    for column in (
        "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DELIVERY_QTY",
        "DELIVERY_PERCENT", "TOTAL_TRADES", "QTY_PER_TRADE",
        *EXTENDED_MARKET_COLUMNS,
    ):
        frame[column] = _number(frame[column])
    frame = frame[frame["SYMBOL"].ne("")].copy()
    frame = drop_unpriced_rows(frame, era)
    return frame.loc[:, INTERNAL_EQUITY_COLUMNS].sort_values("SYMBOL").reset_index(drop=True)


def normalize_nse_equity(
    frame: pd.DataFrame,
    target_date: date,
    series: Iterable[str] = NSE_EQUITY_SERIES,
    add_sme_suffix: bool = False,
    era: Optional[str] = None,
) -> pd.DataFrame:
    """Normalize either legacy or UDiFF NSE cash data."""

    era = era or (
        "nse-equity-udiff" if "TckrSymb" in frame.columns
        else "nse-equity-legacy"
    )
    validate_source_schema(frame, era, target_date)
    if era == "nse-equity-udiff":
        normalized = pd.DataFrame({
            "SYMBOL": _column(frame, "TckrSymb"),
            "DATE": _date_values(_column(frame, "TradDt"), target_date),
            "OPEN": _column(frame, "OpnPric"),
            "HIGH": _column(frame, "HghPric"),
            "LOW": _column(frame, "LwPric"),
            "CLOSE": _column(frame, "ClsPric"),
            "VOLUME": _column(frame, "TtlTradgVol"),
            "SERIES": _column(frame, "SctySrs"),
            "TOTAL_TRADES": _column(frame, "TtlNbOfTxsExctd"),
            "ISIN": _column(frame, "ISIN"),
            "SECURITY_ID": _column(frame, "FinInstrmId"),
            "TURNOVER": turnover_in_rupees(frame, era),
            "PREV_CLOSE": previous_close(frame, era),
        })
    elif era == "nse-equity-legacy":
        normalized = pd.DataFrame({
            "SYMBOL": _column(frame, "SYMBOL"),
            "DATE": _date_values(_column(frame, "TIMESTAMP"), target_date),
            "OPEN": _column(frame, "OPEN"),
            "HIGH": _column(frame, "HIGH"),
            "LOW": _column(frame, "LOW"),
            "CLOSE": _column(frame, "CLOSE"),
            "VOLUME": _column(frame, "TOTTRDQTY"),
            "SERIES": _column(frame, "SERIES"),
            "TOTAL_TRADES": _column(frame, "TOTALTRADES"),
            "ISIN": _column(frame, "ISIN"),
            "SECURITY_ID": pd.NA,
            "TURNOVER": turnover_in_rupees(frame, era),
            "PREV_CLOSE": previous_close(frame, era),
        })

    wanted = {value.upper() for value in series}
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(wanted)].copy()
    if add_sme_suffix:
        normalized["SYMBOL"] = _clean_text(normalized["SYMBOL"]) + "_SME"
    result = _finalize_equity(normalized, era)
    validate_canonical_data(
        result, target_date, INTERNAL_EQUITY_COLUMNS,
        key_columns=("SYMBOL", "SERIES"),
    )
    return result


def normalize_nse_sme(
    frame: pd.DataFrame,
    target_date: date,
    add_suffix: bool = True,
    era: Optional[str] = None,
) -> pd.DataFrame:
    """Normalize both NSE SME filename eras (their internal schema is shared)."""

    era = era or "nse-sme-four-digit-year"
    validate_source_schema(frame, era, target_date)
    normalized = pd.DataFrame({
        "SYMBOL": _column(frame, "SYMBOL"),
        "DATE": pd.Series([target_date.strftime("%Y%m%d")] * len(frame), index=frame.index),
        "OPEN": _column(frame, "OPEN_PRICE"),
        "HIGH": _column(frame, "HIGH_PRICE"),
        "LOW": _column(frame, "LOW_PRICE"),
        "CLOSE": _column(frame, "CLOSE_PRICE"),
        "VOLUME": _column(frame, "NET_TRDQTY"),
        "SERIES": _column(frame, "SERIES"),
        "ISIN": pd.NA,
        "SECURITY_ID": pd.NA,
        "TURNOVER": turnover_in_rupees(frame, era),
        "PREV_CLOSE": previous_close(frame, era),
    })
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(NSE_SME_SERIES)].copy()
    if add_suffix:
        normalized["SYMBOL"] = _clean_text(normalized["SYMBOL"]) + "_SME"
    result = _finalize_equity(normalized, era)
    validate_canonical_data(
        result, target_date, INTERNAL_EQUITY_COLUMNS,
        key_columns=("SYMBOL", "SERIES"),
    )
    return result


def _resolve_truncated_bse_symbols(
    normalized: pd.DataFrame, full_names: Optional[pd.Series]
) -> pd.DataFrame:
    """Disambiguate BSE symbols truncated to nine characters.

    Reports published before 2024-07-08 truncate the ticker (``SCRIP ID``, and
    ``TckrSymb`` in the ZIP-era files) to nine characters while carrying the
    untruncated name in the same row.  Two instruments can therefore arrive with
    the same ``(SYMBOL, SERIES)``.  Observed on every sampled date from
    2022-08-18 to 2023-01-02: ``ICICIBANKN`` and ``ICICIBANKP``, two ICICI
    Prudential ETFs, both truncate to ``ICICIBANK`` in group ``B``.

    Only the colliding rows are renamed, to the exchange's own untruncated name.
    Dropping them would lose instruments the current era still publishes, and
    de-duplicating on the truncated symbol would let an ETF's price be written
    into the history of the equity that shares its prefix.
    """

    if full_names is None or normalized.empty:
        return normalized

    names = _clean_text(full_names).str.upper()
    names = names.reindex(normalized.index)
    collides = normalized.duplicated(subset=["SYMBOL", "SERIES"], keep=False)
    usable = collides & names.notna() & (names != "")
    if not usable.any():
        return normalized

    resolved = normalized.copy()
    resolved.loc[usable, "SYMBOL"] = names.loc[usable]
    return resolved


def normalize_bse_equity(
    frame: pd.DataFrame, target_date: date, era: Optional[str] = None
) -> pd.DataFrame:
    """Normalize every supported BSE cash-market schema."""

    if era is None:
        if "TckrSymb" in frame.columns:
            era = "bse-equity-udiff"
        elif "SCRIP ID" in frame.columns:
            era = "bse-equity-bhavcopy-legacy"
        else:
            era = "bse-equity-isin-legacy"
    validate_source_schema(frame, era, target_date)
    if era in ("bse-equity-udiff", "bse-equity-udiff-zip"):
        mapping = {
            "SYMBOL": _column(frame, "TckrSymb"),
            "DATE": _date_values(_column(frame, "TradDt"), target_date),
            "OPEN": _column(frame, "OpnPric"),
            "HIGH": _column(frame, "HghPric"),
            "LOW": _column(frame, "LwPric"),
            "CLOSE": _column(frame, "ClsPric"),
            "VOLUME": _column(frame, "TtlTradgVol"),
            "SERIES": _column(frame, "SctySrs"),
            "TOTAL_TRADES": _column(frame, "TtlNbOfTxsExctd"),
            "ISIN": _column(frame, "ISIN"),
            "SECURITY_ID": _column(frame, "FinInstrmId"),
        }
        full_names = _column(frame, "FinInstrmNm")
    elif era == "bse-equity-bhavcopy-legacy":
        mapping = {
            "SYMBOL": _column(frame, "SCRIP ID"),
            "DATE": _date_values(_column(frame, "TRADING_DATE"), target_date),
            "OPEN": _column(frame, "OPEN PRICE"),
            "HIGH": _column(frame, "HIGH PRICE"),
            "LOW": _column(frame, "LOW PRICE"),
            "CLOSE": _column(frame, "CLOSING PRICE"),
            "VOLUME": _column(frame, "NO_OF_SHRS"),
            "SERIES": _column(frame, "SC_GROUP"),
            "TOTAL_TRADES": _column(frame, "NO_TRADES"),
            "ISIN": _column(frame, "ISIN"),
            "SECURITY_ID": _column(frame, "SCRIP_CODE"),
        }
        full_names = _column(frame, "SC_NAME")
    elif era == "bse-equity-isin-legacy":
        mapping = {
            "SYMBOL": _column(frame, "SC_NAME"),
            "DATE": _date_values(_column(frame, "TRADING_DATE"), target_date),
            "OPEN": _column(frame, "OPEN"),
            "HIGH": _column(frame, "HIGH"),
            "LOW": _column(frame, "LOW"),
            "CLOSE": _column(frame, "CLOSE"),
            "VOLUME": _column(frame, "NO_OF_SHRS"),
            "SERIES": _column(frame, "SC_GROUP"),
            "TOTAL_TRADES": _column(frame, "NO_TRADES"),
            "ISIN": _column(frame, "ISIN_CODE"),
            "SECURITY_ID": _column(frame, "SC_CODE"),
        }
        full_names = None
    else:
        raise DataProcessingError(f"Unsupported BSE equity era: {era}")
    # One place for all three BSE eras: the column names differ but the table
    # above already knows which belongs to which.
    mapping["TURNOVER"] = turnover_in_rupees(frame, era)
    mapping["PREV_CLOSE"] = previous_close(frame, era)
    normalized = pd.DataFrame(mapping)
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(BSE_EQUITY_SERIES)].copy()
    normalized = _resolve_truncated_bse_symbols(normalized, full_names)
    result = _finalize_equity(normalized, era)
    validate_canonical_data(
        result, target_date, INTERNAL_EQUITY_COLUMNS,
        key_columns=("SYMBOL", "SERIES"),
    )
    return result


def normalize_nse_delivery(frame: pd.DataFrame) -> pd.DataFrame:
    validate_source_schema(frame, "nse-delivery", None)
    result = pd.DataFrame({
        "JOIN_SYMBOL": _clean_text(_column(frame, "SYMBOL")).str.upper(),
        "JOIN_SERIES": _clean_text(_column(frame, "SERIES")).str.upper(),
        "DLV_TOTAL_TRADES": _number(_column(frame, "NO_OF_TRADES")),
        "DLV_QTY": _number(_column(frame, "DELIV_QTY")),
        "DLV_PERCENT": _number(_column(frame, "DELIV_PER")),
    })
    if result[["JOIN_SYMBOL", "JOIN_SERIES"]].eq("").any().any():
        raise DataProcessingError("NSE delivery report contains a blank key")
    return result.drop_duplicates(["JOIN_SYMBOL", "JOIN_SERIES"], keep="last")


def normalize_bse_delivery(frame: pd.DataFrame) -> pd.DataFrame:
    validate_source_schema(frame, "bse-delivery", None)
    result = pd.DataFrame({
        "JOIN_SECURITY_ID": _clean_identifier(_column(frame, "SCRIP CODE")),
        "DLV_QTY": _number(_column(frame, "DELIVERY QTY")),
        "DLV_PERCENT": _number(_column(frame, "DELV. PER.", "DELV.PER.")),
    })
    if result["JOIN_SECURITY_ID"].eq("").any():
        raise DataProcessingError("BSE delivery report contains a blank key")
    return result.drop_duplicates("JOIN_SECURITY_ID", keep="last")


def merge_delivery(
    equity: pd.DataFrame,
    delivery: Optional[pd.DataFrame],
    exchange: str,
    sme_suffix: str = "_SME",
) -> pd.DataFrame:
    """Left-join delivery values without losing a valid price report."""

    result = equity.copy()
    if delivery is None or delivery.empty:
        return _finalize_equity(result)

    if exchange.upper() == "NSE":
        lookup = normalize_nse_delivery(delivery)
        result["JOIN_SYMBOL"] = (
            _clean_text(result["SYMBOL"]).str.upper().str.removesuffix(sme_suffix)
        )
        result["JOIN_SERIES"] = _clean_text(result["SERIES"]).str.upper()
        result = result.merge(lookup, on=["JOIN_SYMBOL", "JOIN_SERIES"], how="left")
    elif exchange.upper() == "BSE":
        lookup = normalize_bse_delivery(delivery)
        result["JOIN_SECURITY_ID"] = _clean_identifier(result["SECURITY_ID"])
        result = result.merge(lookup, on="JOIN_SECURITY_ID", how="left")
    else:
        raise ValueError(f"Unsupported delivery exchange: {exchange}")

    result["DELIVERY_QTY"] = result["DLV_QTY"]
    result["DELIVERY_PERCENT"] = result["DLV_PERCENT"]
    if "DLV_TOTAL_TRADES" in result.columns:
        delivery_trades = _number(result["DLV_TOTAL_TRADES"])
        base_trades = _number(result["TOTAL_TRADES"])
        result["TOTAL_TRADES"] = delivery_trades.where(
            delivery_trades.notna(), base_trades
        )
    volume = _number(result["VOLUME"])
    trades = _number(result["TOTAL_TRADES"])
    result["QTY_PER_TRADE"] = (volume / trades.where(trades.gt(0))).round(2)
    return _finalize_equity(result)


def delivery_match(frame: pd.DataFrame) -> tuple[int, int]:
    """How many published rows the delivery join actually reached.

    The delivery stage is marked complete the moment the report downloads,
    which says nothing about whether a single row of it joined.  An exchange
    that renames a series or changes a scrip code produces a report that
    downloads perfectly and matches nothing, and until this was measured that
    date published with every delivery field empty and no complaint anywhere.
    """

    if "DELIVERY_QTY" not in frame.columns:
        return 0, len(frame)
    matched = int(
        pd.to_numeric(frame["DELIVERY_QTY"], errors="coerce").notna().sum()
    )
    return matched, len(frame)


def _roman(value: int) -> str:
    values = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    output = []
    for number, symbol in values:
        count, value = divmod(value, number)
        output.append(symbol * count)
    return "".join(output)


def normalize_nse_fo(
    frame: pd.DataFrame, target_date: date, era: Optional[str] = None
) -> pd.DataFrame:
    """Normalize legacy and UDiFF NSE futures while retaining OI fields."""

    era = era or (
        "nse-fo-udiff" if "TckrSymb" in frame.columns else "nse-fo-legacy"
    )
    validate_source_schema(frame, era, target_date)
    if era == "nse-fo-udiff":
        instrument = _clean_text(_column(frame, "FinInstrmTp")).str.upper()
        source = frame[instrument.isin({"STF", "IDF"})].copy()
        expiry = pd.to_datetime(_column(source, "XpryDt"), errors="coerce")
        if expiry.isna().any():
            raise DataProcessingError(
                "nse-fo-udiff report contains an invalid XpryDt"
            )
        normalized = pd.DataFrame({
            "BASE_SYMBOL": _column(source, "TckrSymb"),
            "EXPIRY": expiry,
            "DATE": _date_values(_column(source, "TradDt"), target_date),
            "OPEN": _column(source, "OpnPric"),
            "HIGH": _column(source, "HghPric"),
            "LOW": _column(source, "LwPric"),
            "CLOSE": _column(source, "ClsPric"),
            "VOLUME": _column(source, "TtlTradgVol"),
            "OPEN_INTEREST": _column(source, "OpnIntrst"),
            "CHANGE_IN_OI": _column(source, "ChngInOpnIntrst"),
            "TURNOVER": turnover_in_rupees(source, era),
            "PREV_CLOSE": previous_close(source, era),
        })
    elif era == "nse-fo-legacy":
        instrument = _clean_text(_column(frame, "INSTRUMENT")).str.upper()
        source = frame[instrument.isin({"FUTSTK", "FUTIDX"})].copy()
        expiry = pd.to_datetime(
            _column(source, "EXPIRY_DT"), errors="coerce", dayfirst=True
        )
        if expiry.isna().any():
            raise DataProcessingError(
                "nse-fo-legacy report contains an invalid EXPIRY_DT"
            )
        normalized = pd.DataFrame({
            "BASE_SYMBOL": _column(source, "SYMBOL"),
            "EXPIRY": expiry,
            "DATE": _date_values(_column(source, "TIMESTAMP"), target_date),
            "OPEN": _column(source, "OPEN"),
            "HIGH": _column(source, "HIGH"),
            "LOW": _column(source, "LOW"),
            "CLOSE": _column(source, "CLOSE"),
            "VOLUME": _column(source, "CONTRACTS"),
            "OPEN_INTEREST": _column(source, "OPEN_INT"),
            "CHANGE_IN_OI": _column(source, "CHG_IN_OI"),
            # Lakhs in this era, rupees in the next: the boundary at
            # 2024-07-08 is a five-order-of-magnitude step if it is missed.
            "TURNOVER": turnover_in_rupees(source, era),
            "PREV_CLOSE": previous_close(source, era),
        })

    else:
        raise DataProcessingError(f"Unsupported NSE FO era: {era}")
    normalized["BASE_SYMBOL"] = _clean_text(normalized["BASE_SYMBOL"]).str.upper()
    normalized = normalized.sort_values(["BASE_SYMBOL", "EXPIRY"], kind="stable")
    normalized["EXPIRY_RANK"] = (
        normalized.groupby("BASE_SYMBOL")["EXPIRY"].rank(method="dense").astype("Int64")
    )
    normalized = normalized[normalized["EXPIRY_RANK"].notna()].copy()
    normalized["SYMBOL"] = normalized.apply(
        lambda row: f"{row['BASE_SYMBOL']}-{_roman(int(row['EXPIRY_RANK']))}", axis=1
    )
    for column in FO_DAILY_COLUMNS[2:]:
        normalized[column] = _number(normalized[column])
    result = normalized.loc[:, FO_DAILY_COLUMNS].reset_index(drop=True)
    validate_canonical_data(result, target_date, FO_DAILY_COLUMNS)
    return result


def normalize_nse_index(frame: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """Normalize NSE index data without replacing an invalid source date."""

    validate_source_schema(frame, "nse-index", target_date)
    for column in (
        "Open Index Value", "High Index Value", "Low Index Value", "Volume"
    ):
        if column not in frame.columns:
            continue
        text = _clean_text(frame[column])
        # NSE represents legitimate close-only index rows with a dash in the
        # optional OHLC fields.  Treat that official sentinel as missing while
        # continuing to reject arbitrary text.
        missing = text.isin({"", "-"})
        if (_number(frame[column]).isna() & ~missing).any():
            raise DataProcessingError(
                f"nse-index report contains invalid numeric {column}"
            )
    if _number(frame["Closing Index Value"]).isna().any():
        raise DataProcessingError(
            "nse-index report contains invalid numeric Closing Index Value"
        )
    result = pd.DataFrame({
        "SYMBOL": _clean_text(_column(frame, "Index Name")),
        "DATE": _date_values(_column(frame, "Index Date"), target_date),
        "OPEN": _number(_column(frame, "Open Index Value")),
        "HIGH": _number(_column(frame, "High Index Value")),
        "LOW": _number(_column(frame, "Low Index Value")),
        "CLOSE": _number(_column(frame, "Closing Index Value")),
        "VOLUME": _number(_column(frame, "Volume", default=0)).fillna(0),
        "TURNOVER": turnover_in_rupees(frame, "nse-index"),
        "PREV_CLOSE": previous_close(frame, "nse-index"),
    })
    result = result.loc[:, INDEX_DAILY_COLUMNS]
    validate_canonical_data(
        result, target_date, INDEX_DAILY_COLUMNS, index_profile=True
    )
    return result.reset_index(drop=True)


def normalize_bse_index(frame: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """Normalize filename-dated BSE index data to the common contract."""

    validate_source_schema(frame, "bse-index", target_date)
    for column in ("OpenPrice", "HighPrice", "LowPrice", "ClosePrice"):
        if _number(frame[column]).isna().any():
            raise DataProcessingError(
                f"bse-index report contains invalid numeric {column}"
            )
    result = pd.DataFrame({
        "SYMBOL": _clean_text(_column(frame, "IndexName")),
        "DATE": target_date.strftime("%Y%m%d"),
        "OPEN": _number(_column(frame, "OpenPrice")),
        "HIGH": _number(_column(frame, "HighPrice")),
        "LOW": _number(_column(frame, "LowPrice")),
        "CLOSE": _number(_column(frame, "ClosePrice")),
        "VOLUME": 0,
        "TURNOVER": turnover_in_rupees(frame, "bse-index"),
        "PREV_CLOSE": previous_close(frame, "bse-index"),
    })
    result = result.loc[:, INDEX_DAILY_COLUMNS]
    validate_canonical_data(
        result, target_date, INDEX_DAILY_COLUMNS, index_profile=True
    )
    return result.reset_index(drop=True)


def public_equity(equity: pd.DataFrame) -> pd.DataFrame:
    """Return the stable extended equity/SME output contract."""

    return equity.loc[:, EQUITY_DAILY_COLUMNS].copy()


def public_fo(
    fo: pd.DataFrame, *, include_open_interest: bool = True
) -> pd.DataFrame:
    """Return stable FO columns, blanking optional OI values when disabled."""

    result = fo.loc[:, FO_DAILY_COLUMNS].copy()
    if not include_open_interest:
        for column in ("OPEN_INTEREST", "CHANGE_IN_OI"):
            result[column] = pd.Series("", index=result.index, dtype="string")
    return result
