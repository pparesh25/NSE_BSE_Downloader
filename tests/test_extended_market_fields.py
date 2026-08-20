"""Turnover and previous close: the eras, the units, and the two migrations.

These are the only fields the exchanges publish that cannot be recovered from
`.state/raw` afterwards, so getting a unit wrong here would not show up until
a decade of history had been built on it.  Every case below was taken from a
real report; the sampled evidence is recorded under section 4.2 of the
remediation plan.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from src.core.exceptions import DataProcessingError
from src.services.canonical_data import (
    EXTENDED_MARKET_COLUMNS,
    INTERNAL_EQUITY_COLUMNS,
    PRE_EXTENDED_INTERNAL_EQUITY_COLUMNS,
    PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS,
    SYMBOL_HISTORY_COLUMNS,
    normalize_bse_equity,
    normalize_bse_index,
    normalize_nse_equity,
    normalize_nse_fo,
    normalize_nse_index,
    normalize_nse_sme,
    read_report,
)
from src.services.symbol_history import SymbolHistoryStore

FO_DAY = date(2024, 7, 5)
UDIFF_DAY = date(2024, 7, 8)


def _csv(text: str) -> pd.DataFrame:
    return read_report(text.encode())


# --- units ------------------------------------------------------------------


def test_legacy_fo_turnover_in_lakhs_becomes_rupees():
    """The 2024-07-08 boundary is a five-order-of-magnitude step if missed."""

    legacy = _csv(
        "INSTRUMENT,SYMBOL,EXPIRY_DT,OPEN,HIGH,LOW,CLOSE,CONTRACTS,"
        "OPEN_INT,CHG_IN_OI,TIMESTAMP,VAL_INLAKH\n"
        "FUTSTK,ABC,25-Jul-2024,10,12,9,11,100,500,25,05-JUL-2024,14.03\n"
    )
    result = normalize_nse_fo(legacy, FO_DAY, era="nse-fo-legacy")

    assert float(result.loc[0, "TURNOVER"]) == pytest.approx(1_403_000.0)


def test_udiff_fo_turnover_is_already_rupees():
    udiff = _csv(
        "FinInstrmTp,TckrSymb,XpryDt,TradDt,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,OpnIntrst,ChngInOpnIntrst,TtlTrfVal,PrvsClsgPric\n"
        "STF,ABC,2024-07-25,2024-07-08,10,12,9,11,100,500,25,1403000,10.5\n"
    )
    result = normalize_nse_fo(udiff, UDIFF_DAY, era="nse-fo-udiff")

    assert float(result.loc[0, "TURNOVER"]) == pytest.approx(1_403_000.0)
    assert float(result.loc[0, "PREV_CLOSE"]) == pytest.approx(10.5)


def test_index_turnover_in_crores_becomes_rupees():
    frame = _csv(
        "Index Name,Index Date,Open Index Value,High Index Value,"
        "Low Index Value,Closing Index Value,Volume,Turnover (Rs. Cr.)\n"
        "NIFTY 50,08-07-2024,100,120,90,110,5,26131.19\n"
    )
    result = normalize_nse_index(frame, UDIFF_DAY)

    assert float(result.loc[0, "TURNOVER"]) == pytest.approx(261_311_900_000.0)


# --- what each era publishes ------------------------------------------------


def test_every_equity_era_carries_both_fields():
    cases = [
        (
            normalize_nse_equity,
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP,"
            "TOTALTRADES,ISIN,TOTTRDVAL,PREVCLOSE\n"
            "ABC,EQ,10,12,9,11,100,08-JUL-2024,5,INEABC000009,1100,10.5\n",
            "nse-equity-legacy",
        ),
        (
            normalize_nse_equity,
            "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
            "TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId,TtlTrfVal,"
            "PrvsClsgPric\n"
            "2024-07-08,ABC,EQ,10,12,9,11,100,5,INEABC000009,1,1100,10.5\n",
            "nse-equity-udiff",
        ),
        (
            normalize_bse_equity,
            "SC_CODE,SC_NAME,SC_GROUP,OPEN,HIGH,LOW,CLOSE,NO_OF_SHRS,"
            "NO_TRADES,ISIN_CODE,TRADING_DATE,NET_TURNOV,PREVCLOSE\n"
            "500002,ABB,A,10,12,9,11,100,5,INE111111111,08-Jul-24,1100,10.5\n",
            "bse-equity-isin-legacy",
        ),
        (
            normalize_bse_equity,
            "ISIN,SCRIP ID,SCRIP_CODE,SC_GROUP,OPEN PRICE,HIGH PRICE,"
            "LOW PRICE,CLOSING PRICE,NO_OF_SHRS,NO_TRADES,TRADING_DATE,"
            "NET_TURNOV,PREVIOUS CLOSE PRICE\n"
            "INE111111111,ABB,500002,A,10,12,9,11,100,5,08-Jul-24,1100,10.5\n",
            "bse-equity-bhavcopy-legacy",
        ),
    ]
    for normalize, raw, era in cases:
        result = normalize(_csv(raw), UDIFF_DAY, era=era)
        assert float(result.loc[0, "TURNOVER"]) == 1100, era
        assert float(result.loc[0, "PREV_CLOSE"]) == pytest.approx(10.5), era


def test_sme_uses_its_own_column_names():
    frame = _csv(
        "MARKET,SERIES,SYMBOL,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,"
        "NET_TRDQTY,NET_TRDVAL,PREV_CL_PR\n"
        "N,SM,SMALL,10,12,9,11,100,1100,10.5\n"
    )
    result = normalize_nse_sme(frame, UDIFF_DAY, add_suffix=False)

    assert float(result.loc[0, "TURNOVER"]) == 1100
    assert float(result.loc[0, "PREV_CLOSE"]) == pytest.approx(10.5)


# --- what no era publishes --------------------------------------------------


def test_an_unpublished_field_is_empty_rather_than_zero():
    """Zero would claim a BSE index has no turnover, not that none is given."""

    frame = _csv(
        "IndexCode,IndexID,IndexName,PreviousClose,OpenPrice,HighPrice,"
        "LowPrice,ClosePrice\n"
        "1,SENSEX,BSE SENSEX,79800,10,12,9,11\n"
    )
    result = normalize_bse_index(frame, UDIFF_DAY)

    assert pd.isna(result.loc[0, "TURNOVER"])
    assert float(result.loc[0, "PREV_CLOSE"]) == 79800


def test_the_nse_index_previous_close_is_left_empty_not_derived():
    # `Closing - Points Change` is right to the paisa for 162 of 163 indices
    # and wrong by 4.82 for Nifty50 Dividend Points, where NSE publishes a
    # change of zero.  A rule that wrong for one index in a hundred and sixty
    # is not worth having.
    frame = _csv(
        "Index Name,Index Date,Open Index Value,High Index Value,"
        "Low Index Value,Closing Index Value,Volume,Turnover (Rs. Cr.),"
        "Points Change\n"
        "Nifty50 Dividend Points,08-07-2024,100,120,90,187.24,0,1,0.0\n"
    )
    result = normalize_nse_index(frame, UDIFF_DAY)

    assert pd.isna(result.loc[0, "PREV_CLOSE"])


def test_legacy_fo_has_no_previous_close_to_publish():
    legacy = _csv(
        "INSTRUMENT,SYMBOL,EXPIRY_DT,OPEN,HIGH,LOW,CLOSE,CONTRACTS,"
        "OPEN_INT,CHG_IN_OI,TIMESTAMP,VAL_INLAKH\n"
        "FUTSTK,ABC,25-Jul-2024,10,12,9,11,100,500,25,05-JUL-2024,14.03\n"
    )
    result = normalize_nse_fo(legacy, FO_DAY, era="nse-fo-legacy")

    assert pd.isna(result.loc[0, "PREV_CLOSE"])


# --- fail closed ------------------------------------------------------------


def test_a_source_that_stops_publishing_turnover_stops_the_date():
    """Loud beats a decade of silently empty turnover no snapshot can fill."""

    without = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,PrvsClsgPric\n"
        "2024-07-08,ABC,EQ,10,12,9,11,100,10.5\n"
    )
    with pytest.raises(DataProcessingError, match="TtlTrfVal"):
        normalize_nse_equity(without, UDIFF_DAY, era="nse-equity-udiff")


def test_a_negative_turnover_is_refused():
    frame = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId,TtlTrfVal,PrvsClsgPric\n"
        "2024-07-08,ABC,EQ,10,12,9,11,100,5,INEABC000009,1,-1100,10.5\n"
    )
    with pytest.raises(DataProcessingError, match="negative TURNOVER"):
        normalize_nse_equity(frame, UDIFF_DAY, era="nse-equity-udiff")


# --- the two migrations -----------------------------------------------------


def test_a_snapshot_written_before_these_fields_is_read_not_quarantined(
    tmp_path,
):
    """The hazard that would have emptied every rebuild into quarantine.

    `read_internal_snapshot` matches the column list exactly, and the raw
    snapshots are the only thing a rebuild has to work from.
    """

    store = SymbolHistoryStore(tmp_path)
    folder = tmp_path / ".state" / "raw" / "NSE" / "EQ"
    folder.mkdir(parents=True)
    body = pd.DataFrame(
        [["ABC", "20240708", 10, 12, 9, 11, 100, 50, 50, "EQ", 5, 20,
          "INEABC000009", "1"]],
        columns=PRE_EXTENDED_INTERNAL_EQUITY_COLUMNS,
    ).to_csv(index=False, lineterminator="\n")
    path = folder / "2024-07-08.csv"
    path.write_text(body, encoding="utf-8")
    path.with_suffix(".csv.meta.json").write_text(
        json.dumps({
            "version": 1,
            "exchange": "NSE",
            "segment": "EQ",
            "target_date": "2024-07-08",
            "row_count": 1,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }),
        encoding="utf-8",
    )

    frame = store.read_internal_snapshot(path)

    assert list(frame.columns) == INTERNAL_EQUITY_COLUMNS
    assert frame.loc[0, "SYMBOL"] == "ABC"
    for column in EXTENDED_MARKET_COLUMNS:
        assert pd.isna(frame.loc[0, column])
    assert not (tmp_path / ".state" / "quarantine").exists()
    assert path.read_text(encoding="utf-8") == body


def test_a_history_written_before_these_fields_upgrades_to_empty_columns(
    tmp_path,
):
    store = SymbolHistoryStore(tmp_path)
    older = pd.DataFrame(
        [["20240708", 10, 12, 9, 11, 100, "EQ", 5, 20, 50, 50,
          "INEABC000009"]],
        columns=PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS,
    )

    upgraded = store._upgrade_legacy_history(older)

    assert list(upgraded.columns) == SYMBOL_HISTORY_COLUMNS
    # Empty, not zero: this file was written before the value was collected.
    for column in EXTENDED_MARKET_COLUMNS:
        assert upgraded.loc[0, column] == ""


def test_the_pre_isin_history_still_upgrades_through_both_generations(
    tmp_path,
):
    store = SymbolHistoryStore(tmp_path)
    oldest = pd.DataFrame(
        [["20240708", 10, 12, 9, 11, 100, "EQ", 5, 20, 50, 50]],
        columns=PRE_EXTENDED_SYMBOL_HISTORY_COLUMNS[:-1],
    )

    upgraded = store._upgrade_legacy_history(oldest)

    assert list(upgraded.columns) == SYMBOL_HISTORY_COLUMNS
    assert upgraded.loc[0, "ISIN"] == ""
