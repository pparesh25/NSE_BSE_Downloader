"""Normalize every supported exchange schema to stable output contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
import zipfile
from typing import Iterable, Optional, Sequence

import pandas as pd

from ..core.exceptions import DataProcessingError


EQUITY_DAILY_COLUMNS = [
    "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "DELIVERY_QTY", "DELIVERY_PERCENT",
]
INDEX_DAILY_COLUMNS = EQUITY_DAILY_COLUMNS[:7]
FO_DAILY_COLUMNS = [
    "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "OPEN_INTEREST", "CHANGE_IN_OI",
]
SYMBOL_HISTORY_COLUMNS = [
    "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "SERIES",
    "TOTAL_TRADES", "QTY_PER_TRADE", "DELIVERY_QTY", "DELIVERY_PERCENT",
]
INTERNAL_EQUITY_COLUMNS = EQUITY_DAILY_COLUMNS + [
    "SERIES", "TOTAL_TRADES", "QTY_PER_TRADE", "ISIN", "SECURITY_ID",
]

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


SOURCE_SCHEMAS = {
    "nse-equity-legacy": SourceSchema(
        frozenset({
            "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
            "TOTTRDQTY", "TIMESTAMP",
        }),
        ("SYMBOL", "SERIES"),
        "TIMESTAMP",
    ),
    "nse-equity-udiff": SourceSchema(
        frozenset({
            "TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
            "LwPric", "ClsPric", "TtlTradgVol",
        }),
        ("TckrSymb", "SctySrs"),
        "TradDt",
    ),
    "nse-fo-legacy": SourceSchema(
        frozenset({
            "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "OPEN", "HIGH", "LOW",
            "CLOSE", "CONTRACTS", "OPEN_INT", "CHG_IN_OI", "TIMESTAMP",
        }),
        ("INSTRUMENT", "SYMBOL", "EXPIRY_DT"),
        "TIMESTAMP",
    ),
    "nse-fo-udiff": SourceSchema(
        frozenset({
            "FinInstrmTp", "TckrSymb", "XpryDt", "TradDt", "OpnPric",
            "HghPric", "LwPric", "ClsPric", "TtlTradgVol", "OpnIntrst",
            "ChngInOpnIntrst",
        }),
        ("FinInstrmTp", "TckrSymb", "XpryDt"),
        "TradDt",
    ),
    "nse-sme-two-digit-year": SourceSchema(
        frozenset({
            "SERIES", "SYMBOL", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
            "CLOSE_PRICE", "NET_TRDQTY",
        }),
        ("SERIES", "SYMBOL"),
    ),
    "nse-sme-four-digit-year": SourceSchema(
        frozenset({
            "SERIES", "SYMBOL", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
            "CLOSE_PRICE", "NET_TRDQTY",
        }),
        ("SERIES", "SYMBOL"),
    ),
    "bse-equity-isin-legacy": SourceSchema(
        frozenset({
            "SC_CODE", "SC_NAME", "SC_GROUP", "OPEN", "HIGH", "LOW",
            "CLOSE", "NO_OF_SHRS", "TRADING_DATE",
        }),
        ("SC_CODE",),
        "TRADING_DATE",
    ),
    "bse-equity-bhavcopy-legacy": SourceSchema(
        frozenset({
            "SCRIP ID", "SCRIP_CODE", "SC_GROUP", "OPEN PRICE",
            "HIGH PRICE", "LOW PRICE", "CLOSING PRICE", "NO_OF_SHRS",
            "TRADING_DATE",
        }),
        ("SCRIP_CODE",),
        "TRADING_DATE",
    ),
    "bse-equity-udiff": SourceSchema(
        frozenset({
            "TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
            "LwPric", "ClsPric", "TtlTradgVol", "FinInstrmId",
        }),
        ("FinInstrmId",),
        "TradDt",
    ),
    "nse-index": SourceSchema(
        frozenset({
            "Index Name", "Index Date", "Open Index Value",
            "High Index Value", "Low Index Value", "Closing Index Value",
        }),
        ("Index Name",),
        "Index Date",
    ),
    "bse-index": SourceSchema(
        frozenset({
            "IndexName", "OpenPrice", "HighPrice", "LowPrice", "ClosePrice",
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


def _finalize_equity(frame: pd.DataFrame) -> pd.DataFrame:
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
    ):
        frame[column] = _number(frame[column])
    frame = frame[frame["SYMBOL"].ne("")].copy()
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
        })

    wanted = {value.upper() for value in series}
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(wanted)].copy()
    if add_sme_suffix:
        normalized["SYMBOL"] = _clean_text(normalized["SYMBOL"]) + "_SME"
    result = _finalize_equity(normalized)
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
    })
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(NSE_SME_SERIES)].copy()
    if add_suffix:
        normalized["SYMBOL"] = _clean_text(normalized["SYMBOL"]) + "_SME"
    result = _finalize_equity(normalized)
    validate_canonical_data(
        result, target_date, INTERNAL_EQUITY_COLUMNS,
        key_columns=("SYMBOL", "SERIES"),
    )
    return result


def normalize_bse_equity(
    frame: pd.DataFrame, target_date: date, era: Optional[str] = None
) -> pd.DataFrame:
    """Normalize all three supported BSE cash-market schemas."""

    if era is None:
        if "TckrSymb" in frame.columns:
            era = "bse-equity-udiff"
        elif "SCRIP ID" in frame.columns:
            era = "bse-equity-bhavcopy-legacy"
        else:
            era = "bse-equity-isin-legacy"
    validate_source_schema(frame, era, target_date)
    if era == "bse-equity-udiff":
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

    else:
        raise DataProcessingError(f"Unsupported BSE equity era: {era}")
    normalized = pd.DataFrame(mapping)
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(BSE_EQUITY_SERIES)].copy()
    result = _finalize_equity(normalized)
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
