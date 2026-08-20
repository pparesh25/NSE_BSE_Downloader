import asyncio
from datetime import date
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.exceptions import DataProcessingError
from src.downloaders.bse_eq_downloader import BSEEQDownloader
from src.downloaders.bse_index_downloader import BSEIndexDownloader
from src.downloaders.nse_eq_downloader import NSEEQDownloader
from src.downloaders.nse_fo_downloader import NSEFODownloader
from src.downloaders.nse_index_downloader import NSEIndexDownloader
from src.downloaders.nse_sme_downloader import NSESMEDownloader
from src.services.canonical_data import (
    EQUITY_DAILY_COLUMNS,
    FO_DAILY_COLUMNS,
    INDEX_DAILY_COLUMNS,
)
from src.utils.memory_optimizer import MemoryOptimizer


class _Options:
    def __init__(self, **values):
        self.values = values

    def get_download_option(self, name, default=None):
        return self.values.get(name, default)


def _bare(downloader_class, **options):
    downloader = object.__new__(downloader_class)
    downloader.memory_optimizer = MemoryOptimizer()
    downloader.logger = logging.getLogger(f"test.{downloader_class.__name__}")
    downloader.settings = _Options(**options)
    downloader.config = None
    return downloader


def test_cash_downloaders_keep_one_output_contract_across_eras(
    tmp_path, monkeypatch
):
    nse_day = date(2024, 7, 8)
    nse_price = (
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId,TtlTrfVal,"
        "PrvsClsgPric\n"
        "2024-07-08,ABC,EQ,10,12,9,11,1000,20,INEABC000009,123,11000,10\n"
    ).encode()
    nse_delivery = (
        "SYMBOL,SERIES,NO_OF_TRADES,DELIV_QTY,DELIV_PER\n"
        "ABC,EQ,20,600,60\n"
    ).encode()
    nse = _bare(NSEEQDownloader, include_delivery_data=True)
    nse_result = nse.process_downloaded_data(
        nse_price, nse_day, nse_delivery
    )
    assert list(nse_result.columns) == EQUITY_DAILY_COLUMNS
    assert nse_result.loc[0, "DELIVERY_QTY"] == 600
    assert "BhavCopy_NSE_CM_0_0_0_20240708" in nse.build_url(nse_day)
    assert nse._extract_date_from_filename(
        "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv"
    ) == "20240708"
    assert nse._extract_date_from_filename("invalid.csv") is None

    bse_day = date(2024, 7, 8)
    bse_price = (
        "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
        "TtlTradgVol,TtlNbOfTxsExctd,ISIN,FinInstrmId,TtlTrfVal,"
        "PrvsClsgPric\n"
        "2024-07-08,ABB,A,10,12,9,11,100,5,INE111111111,500002,1100,10\n"
    ).encode()
    bse_delivery = (
        "SCRIP CODE|DELIVERY QTY|DELV. PER.\n500002|80|80\n"
    ).encode()
    bse = _bare(BSEEQDownloader, include_delivery_data=True)
    bse.mutual_fund_symbols = []
    bse_result = bse.process_downloaded_data(
        bse_price, bse_day, bse_delivery
    )
    assert list(bse_result.columns) == EQUITY_DAILY_COLUMNS
    assert bse_result.loc[0, "DELIVERY_QTY"] == 80
    assert "BhavCopy_BSE_CM_0_0_0_20240708" in bse.build_url(bse_day)
    assert bse._extract_date_from_filename(
        "BhavCopy_BSE_CM_0_0_0_20240708_F_0000.CSV"
    ) == "20240708"
    assert bse._extract_date_from_filename("invalid.csv") is None


def test_fo_adapter_retains_stable_columns_when_open_interest_is_disabled():
    day = date(2024, 7, 8)
    raw = (
        "FinInstrmTp,TckrSymb,XpryDt,TradDt,OpnPric,HghPric,LwPric,"
        "ClsPric,TtlTradgVol,OpnIntrst,ChngInOpnIntrst,TtlTrfVal,"
        "PrvsClsgPric\n"
        "STF,ABC,2024-07-25,2024-07-08,10,12,9,11,100,500,25,1100000,10\n"
    ).encode()
    downloader = _bare(
        NSEFODownloader,
        include_fo_open_interest=True,
    )
    result = downloader.process_downloaded_data(raw, day)
    assert list(result.columns) == FO_DAILY_COLUMNS
    assert result.loc[0, "OPEN_INTEREST"] == 500
    assert result.loc[0, "CHANGE_IN_OI"] == 25
    assert NSEFODownloader.int_to_roman(49) == "XLIX"
    assert downloader._extract_date_from_filename(
        "BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv"
    ) == "20240708"
    assert downloader._extract_date_from_filename("fo08.csv") is None
    assert "BhavCopy_NSE_FO_0_0_0_20240708" in downloader.build_url(day)

    downloader.settings = _Options(include_fo_open_interest=False)
    without_oi = downloader.process_downloaded_data(raw, day)
    assert list(without_oi.columns) == FO_DAILY_COLUMNS
    assert without_oi["OPEN_INTEREST"].eq("").all()
    assert without_oi["CHANGE_IN_OI"].eq("").all()


def test_sme_adapter_switches_filename_era_and_applies_suffix(
    tmp_path, monkeypatch
):
    day = date(2025, 10, 13)
    raw = (
        "MARKET,SERIES,SYMBOL,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,"
        "CLOSE_PRICE,NET_TRDQTY,NET_TRDVAL,PREV_CL_PR\n"
        "N,SM,SMALL,10,12,9,11,1000,11000,10\n"
    ).encode()
    downloader = _bare(NSESMEDownloader)
    result = downloader.process_downloaded_data(raw, day)
    assert list(result.columns) == EQUITY_DAILY_COLUMNS
    assert result.loc[0, "SYMBOL"] == "SMALL"
    assert downloader.build_url(date(2025, 10, 10)).endswith("sme101025.csv")
    assert downloader.build_url(day).endswith("sme13102025.csv")
    assert downloader._extract_date_from_filename("sme101025.csv") == "101025"
    assert downloader._extract_date_from_filename("sme13102025.csv") == "13102025"
    assert downloader._extract_date_from_filename("sme123.csv") is None

    frame = pd.DataFrame({"SYMBOL": ["SMALL"], "OPEN_PRICE": [10]})
    dated = downloader.add_date_column(frame, day)
    assert list(dated.columns) == ["SYMBOL", "DATE", "OPEN_PRICE"]
    assert dated.loc[0, "DATE"] == "20251013"


@pytest.mark.parametrize(
    ("downloader_class", "raw", "symbol"),
    [
        (
            NSEIndexDownloader,
            "Index Name,Index Date,Open Index Value,High Index Value,"
            "Low Index Value,Closing Index Value,Volume,Turnover (Rs. Cr.)\n"
            "NIFTY 50,30-07-2026,25000,25100,24900,25050,123,987.6\n",
            "NIFTY 50",
        ),
        (
            BSEIndexDownloader,
            "IndexName,OpenPrice,HighPrice,LowPrice,ClosePrice,PreviousClose\n"
            "SENSEX,80000,80100,79900,80050,79800\n",
            "SENSEX",
        ),
    ],
)
def test_index_adapters_process_bytes_and_use_shared_download_path(
    downloader_class, raw, symbol
):
    day = date(2026, 7, 30)
    downloader = _bare(downloader_class)
    result = downloader.process_downloaded_data(raw.encode(), day)
    assert list(result.columns) == INDEX_DAILY_COLUMNS
    assert result.loc[0, "SYMBOL"] == symbol
    assert downloader.build_url(day).endswith("30072026.csv")

    calls = []

    async def shared(days):
        calls.extend(days)
        return True

    downloader._download_price_implementation = shared
    assert asyncio.run(downloader._download_implementation([day]))
    assert calls == [day]


def test_bse_index_never_requests_dates_before_first_public_report():
    downloader = _bare(BSEIndexDownloader)
    downloader.exchange = "BSE"
    downloader.segment = "INDEX"
    downloader.data_manager = SimpleNamespace(
        calculate_date_range=lambda *_args: (
            date(2020, 1, 1), date(2026, 7, 30)
        )
    )
    assert downloader.get_date_range() == (
        date(2025, 4, 17), date(2026, 7, 30)
    )


@pytest.mark.parametrize(
    ("downloader_class", "message"),
    [
        (NSEEQDownloader, "NSE EQ"),
        (BSEEQDownloader, "BSE EQ"),
        (NSEFODownloader, "NSE FO"),
        (NSESMEDownloader, "NSE SME"),
    ],
)
def test_adapter_processing_errors_include_segment_context(
    tmp_path, monkeypatch, downloader_class, message
):
    downloader = _bare(downloader_class)
    if downloader_class is BSEEQDownloader:
        downloader.mutual_fund_symbols = []
    with pytest.raises(DataProcessingError, match=message):
        downloader.process_downloaded_data(b"<html>not a report</html>", date(2026, 7, 30))


def test_cash_adapters_delegate_to_shared_delivery_pipeline():
    day = date(2026, 7, 30)
    for downloader_class in (NSEEQDownloader, BSEEQDownloader, NSESMEDownloader):
        downloader = _bare(downloader_class)
        calls = []

        async def shared(days):
            calls.extend(days)
            return True

        downloader._download_equity_implementation = shared
        assert asyncio.run(downloader._download_implementation([day]))
        assert calls == [day]
