from datetime import date
from io import StringIO

import pandas as pd
import pytest

from src.core.exceptions import DataProcessingError
from src.services.canonical_data import (
    EQUITY_DAILY_COLUMNS,
    FO_DAILY_COLUMNS,
    merge_delivery,
    normalize_bse_equity,
    normalize_nse_equity,
    normalize_nse_fo,
    normalize_nse_sme,
    public_equity,
    read_report,
)


def _csv(text):
    return pd.read_csv(StringIO(text), dtype=str)


def test_nse_equity_legacy_and_udiff_share_one_output_contract():
    legacy = _csv(
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP,TOTALTRADES,ISIN\n"
        "ABC,EQ,10,12,9,11,1000,05-JUL-2024,20,INEABC\n"
    )
    udiff = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId\n"
        "2024-07-08,ABC,EQ,10,12,9,11,1000,20,INEABC,123\n"
    )
    old_result = public_equity(normalize_nse_equity(legacy, date(2024, 7, 5)))
    new_result = public_equity(normalize_nse_equity(udiff, date(2024, 7, 8)))
    assert list(old_result.columns) == EQUITY_DAILY_COLUMNS
    assert list(new_result.columns) == EQUITY_DAILY_COLUMNS
    assert old_result.loc[0, "SYMBOL"] == new_result.loc[0, "SYMBOL"] == "ABC"


def test_nse_delivery_uses_symbol_and_series_not_symbol_alone():
    prices = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,ISIN\n"
        "2025-01-01,ABC,EQ,1,2,1,2,100,INE1\n"
        "2025-01-01,ABC,BE,1,2,1,2,200,INE2\n"
    )
    delivery = _csv(
        "SYMBOL,SERIES,NO_OF_TRADES,DELIV_QTY,DELIV_PER\n"
        "ABC,EQ,10,60,60\n"
        "ABC,BE,20,200,100\n"
    )
    merged = merge_delivery(
        normalize_nse_equity(prices, date(2025, 1, 1)), delivery, "NSE"
    ).set_index("SERIES")
    assert int(merged.loc["EQ", "DELIVERY_QTY"]) == 60
    assert int(merged.loc["BE", "DELIVERY_QTY"]) == 200
    assert float(merged.loc["EQ", "QTY_PER_TRADE"]) == 10.0


def test_nse_sme_and_fo_retain_new_columns():
    sme = _csv(
        "MARKET,SERIES,SYMBOL,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,NET_TRDQTY\n"
        "N,SM,SMALL,10,12,9,11,1000\n"
    )
    sme_result = normalize_nse_sme(sme, date(2025, 10, 13), add_suffix=True)
    assert sme_result.loc[0, "SYMBOL"] == "SMALL_SME"

    legacy_fo = _csv(
        "INSTRUMENT,SYMBOL,EXPIRY_DT,OPEN,HIGH,LOW,CLOSE,CONTRACTS,"
        "OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTSTK,ABC,25-Jul-2024,10,12,9,11,100,500,25,05-JUL-2024\n"
    )
    udiff_fo = _csv(
        "FinInstrmTp,TckrSymb,XpryDt,TradDt,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,OpnIntrst,ChngInOpnIntrst\n"
        "STF,ABC,2024-07-25,2024-07-08,10,12,9,11,100,500,25\n"
    )
    for raw, day in ((legacy_fo, date(2024, 7, 5)), (udiff_fo, date(2024, 7, 8))):
        result = normalize_nse_fo(raw, day)
        assert list(result.columns) == FO_DAILY_COLUMNS
        assert result.loc[0, "SYMBOL"] == "ABC-I"
        assert int(result.loc[0, "OPEN_INTEREST"]) == 500
        assert int(result.loc[0, "CHANGE_IN_OI"]) == 25


@pytest.mark.parametrize(
    "raw",
    [
        "SC_CODE,SC_NAME,SC_GROUP,OPEN,HIGH,LOW,CLOSE,NO_OF_SHRS,NO_TRADES,ISIN_CODE,TRADING_DATE\n"
        "500002,ABB LTD.,A,10,12,9,11,100,5,INE1,16-Aug-22\n",
        "ISIN,SCRIP ID,SCRIP_CODE,SC_GROUP,OPEN PRICE,HIGH PRICE,LOW PRICE,CLOSING PRICE,NO_OF_SHRS,NO_TRADES,TRADING_DATE\n"
        "INE1,ABB,500002,A,10,12,9,11,100,5,17-Aug-22\n",
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId\n"
        "2024-07-08,ABB,A,10,12,9,11,100,5,INE1,500002\n",
    ],
)
def test_bse_three_eras_normalize_and_join_delivery(raw):
    result = normalize_bse_equity(_csv(raw), date(2024, 7, 8))
    delivery = _csv(
        "SCRIP CODE|DELIVERY QTY|DELV. PER.\n500002|80|80\n"
    )
    # The helper normally receives an already pipe-parsed delivery frame.
    delivery = pd.read_csv(StringIO(
        "SCRIP CODE|DELIVERY QTY|DELV. PER.\n500002|80|80\n"
    ), sep="|", dtype=str)
    merged = merge_delivery(result, delivery, "BSE")
    assert int(merged.loc[0, "DELIVERY_QTY"]) == 80
    assert float(merged.loc[0, "DELIVERY_PERCENT"]) == 80.0


def test_html_error_page_is_not_accepted_as_csv():
    with pytest.raises(DataProcessingError):
        read_report(b"<!DOCTYPE html><html><body>missing</body></html>")
