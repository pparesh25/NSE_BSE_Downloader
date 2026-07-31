"""Date-aware official NSE/BSE report locations.

The exchanges have changed both file names and schemas over time.  Keeping
those rules here prevents individual downloaders from growing separate legacy
branches and gives tests one stable place to verify every supported era.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


NSE_UDIFF_START = date(2024, 7, 8)
BSE_SECOND_GENERATION_START = date(2022, 8, 17)
BSE_UDIFF_START = date(2024, 7, 8)
NSE_SME_FOUR_DIGIT_YEAR_START = date(2025, 10, 13)


@dataclass(frozen=True)
class SourceSpec:
    """One downloadable report and the schema era it belongs to."""

    url: str
    era: str
    kind: str


def price_source(exchange: str, segment: str, target_date: date) -> SourceSpec:
    """Return the official price-report source for ``target_date``."""

    exchange = exchange.upper()
    segment = segment.upper()

    if exchange == "NSE" and segment == "EQ":
        if target_date < NSE_UDIFF_START:
            month = target_date.strftime("%b").upper()
            filename = f"cm{target_date:%d}{month}{target_date:%Y}bhav.csv.zip"
            return SourceSpec(
                "https://nsearchives.nseindia.com/content/historical/"
                f"EQUITIES/{target_date:%Y}/{month}/{filename}",
                "nse-equity-legacy",
                "zip-csv",
            )
        return SourceSpec(
            "https://nsearchives.nseindia.com/content/cm/"
            f"BhavCopy_NSE_CM_0_0_0_{target_date:%Y%m%d}_F_0000.csv.zip",
            "nse-equity-udiff",
            "zip-csv",
        )

    if exchange == "NSE" and segment == "FO":
        if target_date < NSE_UDIFF_START:
            month = target_date.strftime("%b").upper()
            filename = f"fo{target_date:%d}{month}{target_date:%Y}bhav.csv.zip"
            return SourceSpec(
                "https://nsearchives.nseindia.com/content/historical/"
                f"DERIVATIVES/{target_date:%Y}/{month}/{filename}",
                "nse-fo-legacy",
                "zip-csv",
            )
        return SourceSpec(
            "https://nsearchives.nseindia.com/content/fo/"
            f"BhavCopy_NSE_FO_0_0_0_{target_date:%Y%m%d}_F_0000.csv.zip",
            "nse-fo-udiff",
            "zip-csv",
        )

    if exchange == "NSE" and segment == "SME":
        if target_date < NSE_SME_FOUR_DIGIT_YEAR_START:
            filename = f"sme{target_date:%d%m%y}.csv"
            era = "nse-sme-two-digit-year"
        else:
            filename = f"sme{target_date:%d%m%Y}.csv"
            era = "nse-sme-four-digit-year"
        return SourceSpec(
            f"https://nsearchives.nseindia.com/archives/sme/bhavcopy/{filename}",
            era,
            "csv",
        )

    if exchange == "NSE" and segment == "INDEX":
        return SourceSpec(
            "https://nsearchives.nseindia.com/content/indices/"
            f"ind_close_all_{target_date:%d%m%Y}.csv",
            "nse-index",
            "csv",
        )

    if exchange == "BSE" and segment == "EQ":
        base = "https://www.bseindia.com/download/BhavCopy/Equity"
        if target_date < BSE_SECOND_GENERATION_START:
            return SourceSpec(
                f"{base}/EQ_ISINCODE_{target_date:%d%m%y}.zip",
                "bse-equity-isin-legacy",
                "zip-csv",
            )
        if target_date < BSE_UDIFF_START:
            return SourceSpec(
                f"{base}/BSE_EQ_BHAVCOPY_{target_date:%d%m%Y}.ZIP",
                "bse-equity-bhavcopy-legacy",
                "zip-csv",
            )
        return SourceSpec(
            f"{base}/BhavCopy_BSE_CM_0_0_0_{target_date:%Y%m%d}_F_0000.CSV",
            "bse-equity-udiff",
            "csv",
        )

    if exchange == "BSE" and segment == "INDEX":
        return SourceSpec(
            "https://www.bseindia.com/bsedata/Index_Bhavcopy/"
            f"INDEXSummary_{target_date:%d%m%Y}.csv",
            "bse-index",
            "csv",
        )

    raise ValueError(f"Unsupported market source: {exchange}_{segment}")


def delivery_source(exchange: str, target_date: date) -> SourceSpec:
    """Return the separate equity delivery report for an exchange/date."""

    exchange = exchange.upper()
    if exchange == "NSE":
        return SourceSpec(
            "https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{target_date:%d%m%Y}.csv",
            "nse-delivery",
            "csv",
        )
    if exchange == "BSE":
        return SourceSpec(
            "https://www.bseindia.com/BSEDATA/gross/"
            f"{target_date:%Y}/SCBSEALL{target_date:%d%m}.zip",
            "bse-delivery",
            "zip-pipe",
        )
    raise ValueError(f"Delivery report is not supported for {exchange}")
