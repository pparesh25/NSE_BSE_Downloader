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
    "raw,target_date",
    [
        (
            "SC_CODE,SC_NAME,SC_GROUP,OPEN,HIGH,LOW,CLOSE,NO_OF_SHRS,NO_TRADES,ISIN_CODE,TRADING_DATE\n"
            "500002,ABB LTD.,A,10,12,9,11,100,5,INE1,16-Aug-22\n",
            date(2022, 8, 16),
        ),
        (
            "ISIN,SCRIP ID,SCRIP_CODE,SC_GROUP,OPEN PRICE,HIGH PRICE,LOW PRICE,CLOSING PRICE,NO_OF_SHRS,NO_TRADES,TRADING_DATE\n"
            "INE1,ABB,500002,A,10,12,9,11,100,5,17-Aug-22\n",
            date(2022, 8, 17),
        ),
        (
            "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId\n"
            "2024-07-08,ABB,A,10,12,9,11,100,5,INE1,500002\n",
            date(2024, 7, 8),
        ),
    ],
)
def test_bse_three_eras_normalize_and_join_delivery(raw, target_date):
    result = normalize_bse_equity(_csv(raw), target_date)
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


def test_header_only_and_json_reports_are_rejected():
    with pytest.raises(DataProcessingError, match="no data rows"):
        read_report(b"SYMBOL,SERIES,OPEN\n")
    with pytest.raises(DataProcessingError, match="JSON"):
        read_report(b'{"message":"not found"}')


def test_unknown_schema_and_wrong_source_date_fail_closed():
    unknown = _csv("foo,bar\n1,2\n")
    with pytest.raises(DataProcessingError, match="missing required columns"):
        normalize_nse_equity(
            unknown, date(2024, 7, 8), era="nse-equity-udiff"
        )

    wrong_date = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2024-07-09,ABC,EQ,10,12,9,11,1000\n"
    )
    with pytest.raises(DataProcessingError, match="date mismatch"):
        normalize_nse_equity(
            wrong_date, date(2024, 7, 8), era="nse-equity-udiff"
        )


def test_invalid_ohlcv_and_duplicate_keys_are_rejected():
    invalid = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2024-07-08,ABC,EQ,broken,12,9,11,1000\n"
    )
    with pytest.raises(DataProcessingError, match="numeric OPEN"):
        normalize_nse_equity(
            invalid, date(2024, 7, 8), era="nse-equity-udiff"
        )

    duplicate = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2024-07-08,ABC,EQ,10,12,9,11,1000\n"
        "2024-07-08,ABC,EQ,10,12,9,11,1000\n"
    )
    with pytest.raises(DataProcessingError, match="duplicate keys"):
        normalize_nse_equity(
            duplicate, date(2024, 7, 8), era="nse-equity-udiff"
        )


def test_mixed_udiff_report_allows_unselected_rows_without_a_series():
    mixed = _csv(
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2024-07-08,ABC,EQ,10,12,9,11,1000\n"
        "2024-07-08,865BOND29,,100,100,100,100,1\n"
    )
    result = normalize_nse_equity(
        mixed, date(2024, 7, 8), era="nse-equity-udiff"
    )
    assert result["SYMBOL"].tolist() == ["ABC"]


def _bse_legacy_row(isin, scrip_id, scrip_code, name, group, close):
    return {
        "ISIN": isin, "SCRIP ID": scrip_id, "SCRIP_CODE": scrip_code,
        "SC_NAME": name, "SC_GROUP": group, "OPEN PRICE": close,
        "HIGH PRICE": close, "LOW PRICE": close, "CLOSING PRICE": close,
        "NO_OF_SHRS": "100", "NO_TRADES": "10",
        "TRADING_DATE": "2022-12-30",
    }


def _bse_udiff_zip_row(isin, ticker, instrument_id, name, series, close):
    return {
        "ISIN": isin, "TckrSymb": ticker, "FinInstrmId": instrument_id,
        "FinInstrmNm": name, "SctySrs": series, "OpnPric": close,
        "HghPric": close, "LwPric": close, "ClsPric": close,
        "TtlTradgVol": "100", "TtlNbOfTxsExctd": "10",
        "TradDt": "2023-01-02",
    }


def test_truncated_bse_ticker_collision_is_resolved_not_dropped():
    # Real data, 2022-12-30: BSE truncates SCRIP ID to nine characters, so two
    # ICICI Prudential ETFs both arrive as "ICICIBANK" in group B while the bank
    # itself is "ICICIBANK" in group A.  Before this was handled the whole date
    # was rejected for duplicate keys.
    frame = pd.DataFrame([
        _bse_legacy_row("INE090A01021", "ICICIBANK", "532174", "ICICI BANK", "A", "890.95"),
        _bse_legacy_row("INF109KC15I8", "ICICIBANK", "542730", "ICICIBANKN", "B", "43.15"),
        _bse_legacy_row("INF109KC1E35", "ICICIBANK", "542758", "ICICIBANKP", "B", "217.40"),
    ])

    result = normalize_bse_equity(frame, date(2022, 12, 30), "bse-equity-bhavcopy-legacy")

    # Nothing is lost and nothing is merged.
    assert len(result) == 3
    by_isin = result.set_index("ISIN")
    # The equity keeps the plain ticker; it never collided, being in group A.
    assert by_isin.loc["INE090A01021", "SYMBOL"] == "ICICIBANK"
    assert float(by_isin.loc["INE090A01021", "CLOSE"]) == 890.95
    # The two funds are separated by the exchange's own untruncated names.
    assert by_isin.loc["INF109KC15I8", "SYMBOL"] == "ICICIBANKN"
    assert by_isin.loc["INF109KC1E35", "SYMBOL"] == "ICICIBANKP"
    # The fund prices must not have reached the bank's symbol.
    bank_rows = result[result["SYMBOL"] == "ICICIBANK"]
    assert len(bank_rows) == 1


def test_truncated_ticker_collision_is_resolved_in_the_zip_udiff_era_too():
    frame = pd.DataFrame([
        _bse_udiff_zip_row("INE090A01021", "ICICIBANK", "532174", "ICICI BANK", "A", "903.00"),
        _bse_udiff_zip_row("INF109KC15I8", "ICICIBANK", "542730", "ICICIBANKN", "B", "43.30"),
        _bse_udiff_zip_row("INF109KC1E35", "ICICIBANK", "542758", "ICICIBANKP", "B", "219.16"),
    ])

    result = normalize_bse_equity(frame, date(2023, 1, 2), "bse-equity-udiff-zip")

    assert len(result) == 3
    assert set(result["SYMBOL"]) == {"ICICIBANK", "ICICIBANKN", "ICICIBANKP"}


def test_uncontested_symbols_keep_their_ticker():
    # Only colliding rows are renamed; an ordinary row must not pick up its
    # long instrument name.
    frame = pd.DataFrame([
        _bse_udiff_zip_row("INE090A01021", "ICICIBANK", "532174", "ICICI BANK LTD.", "A", "903.00"),
        _bse_udiff_zip_row("INE002A01018", "RELIANCE", "500325", "RELIANCE INDUSTRIES", "A", "2500.00"),
    ])

    result = normalize_bse_equity(frame, date(2023, 1, 2), "bse-equity-udiff-zip")

    assert set(result["SYMBOL"]) == {"ICICIBANK", "RELIANCE"}
