"""Normalize every supported exchange schema to stable output contracts."""

from __future__ import annotations

from datetime import date
from io import BytesIO
import zipfile
from typing import Iterable, Optional

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


def read_report(payload: bytes, separator: str = ",") -> pd.DataFrame:
    """Read a plain or zipped exchange report and reject HTML error pages."""

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

    try:
        frame = pd.read_csv(BytesIO(data), sep=separator, dtype=str)
    except Exception as error:
        raise DataProcessingError(f"Unable to parse market report: {error}") from error

    frame.columns = [str(column).strip() for column in frame.columns]
    if frame.empty and not len(frame.columns):
        raise DataProcessingError("Market report has no columns")
    return frame


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
    text = _clean_text(values)
    # UDiFF reports use ISO dates while legacy reports use values such as
    # 05-JUL-2024.  Parse ISO explicitly first so pandas does not reinterpret
    # year-first dates when ``dayfirst`` is enabled.
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing], errors="coerce", dayfirst=True
        )
    fallback = pd.Timestamp(target_date)
    return parsed.fillna(fallback).dt.strftime("%Y%m%d")


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
) -> pd.DataFrame:
    """Normalize either legacy or UDiFF NSE cash data."""

    if "TckrSymb" in frame.columns:
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
    else:
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
    return _finalize_equity(normalized)


def normalize_nse_sme(
    frame: pd.DataFrame, target_date: date, add_suffix: bool = True
) -> pd.DataFrame:
    """Normalize both NSE SME filename eras (their internal schema is shared)."""

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
    return _finalize_equity(normalized)


def normalize_bse_equity(frame: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """Normalize all three supported BSE cash-market schemas."""

    if "TckrSymb" in frame.columns:
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
    elif "SCRIP ID" in frame.columns:
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
    else:
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

    normalized = pd.DataFrame(mapping)
    normalized["SERIES"] = _clean_text(normalized["SERIES"]).str.upper()
    normalized = normalized[normalized["SERIES"].isin(BSE_EQUITY_SERIES)].copy()
    return _finalize_equity(normalized)


def normalize_nse_delivery(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({
        "JOIN_SYMBOL": _clean_text(_column(frame, "SYMBOL")).str.upper(),
        "JOIN_SERIES": _clean_text(_column(frame, "SERIES")).str.upper(),
        "DLV_TOTAL_TRADES": _number(_column(frame, "NO_OF_TRADES")),
        "DLV_QTY": _number(_column(frame, "DELIV_QTY")),
        "DLV_PERCENT": _number(_column(frame, "DELIV_PER")),
    })
    return result.drop_duplicates(["JOIN_SYMBOL", "JOIN_SERIES"], keep="last")


def normalize_bse_delivery(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({
        "JOIN_SECURITY_ID": _clean_identifier(_column(frame, "SCRIP CODE")),
        "DLV_QTY": _number(_column(frame, "DELIVERY QTY")),
        "DLV_PERCENT": _number(_column(frame, "DELV. PER.", "DELV.PER.")),
    })
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


def normalize_nse_fo(frame: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """Normalize legacy and UDiFF NSE futures while retaining OI fields."""

    if "TckrSymb" in frame.columns:
        instrument = _clean_text(_column(frame, "FinInstrmTp")).str.upper()
        source = frame[instrument.isin({"STF", "IDF"})].copy()
        normalized = pd.DataFrame({
            "BASE_SYMBOL": _column(source, "TckrSymb"),
            "EXPIRY": pd.to_datetime(_column(source, "XpryDt"), errors="coerce"),
            "DATE": _date_values(_column(source, "TradDt"), target_date),
            "OPEN": _column(source, "OpnPric"),
            "HIGH": _column(source, "HghPric"),
            "LOW": _column(source, "LwPric"),
            "CLOSE": _column(source, "ClsPric"),
            "VOLUME": _column(source, "TtlTradgVol"),
            "OPEN_INTEREST": _column(source, "OpnIntrst"),
            "CHANGE_IN_OI": _column(source, "ChngInOpnIntrst"),
        })
    else:
        instrument = _clean_text(_column(frame, "INSTRUMENT")).str.upper()
        source = frame[instrument.isin({"FUTSTK", "FUTIDX"})].copy()
        normalized = pd.DataFrame({
            "BASE_SYMBOL": _column(source, "SYMBOL"),
            "EXPIRY": pd.to_datetime(_column(source, "EXPIRY_DT"), errors="coerce", dayfirst=True),
            "DATE": _date_values(_column(source, "TIMESTAMP"), target_date),
            "OPEN": _column(source, "OPEN"),
            "HIGH": _column(source, "HIGH"),
            "LOW": _column(source, "LOW"),
            "CLOSE": _column(source, "CLOSE"),
            "VOLUME": _column(source, "CONTRACTS"),
            "OPEN_INTEREST": _column(source, "OPEN_INT"),
            "CHANGE_IN_OI": _column(source, "CHG_IN_OI"),
        })

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
    return normalized.loc[:, FO_DAILY_COLUMNS].reset_index(drop=True)


def public_equity(equity: pd.DataFrame, legacy_seven_columns: bool = False) -> pd.DataFrame:
    columns = EQUITY_DAILY_COLUMNS[:7] if legacy_seven_columns else EQUITY_DAILY_COLUMNS
    return equity.loc[:, columns].copy()


def public_fo(fo: pd.DataFrame, legacy_seven_columns: bool = False) -> pd.DataFrame:
    columns = FO_DAILY_COLUMNS[:7] if legacy_seven_columns else FO_DAILY_COLUMNS
    return fo.loc[:, columns].copy()
