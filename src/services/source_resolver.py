"""Date-aware official NSE/BSE report locations.

The exchanges have changed both file names and schemas over time.  Keeping
those rules here prevents individual downloaders from growing separate legacy
branches and gives tests one stable place to verify every supported era.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional


NSE_UDIFF_START = date(2024, 7, 8)
BSE_SECOND_GENERATION_START = date(2022, 8, 17)
#: BSE switched the BSE_EQ_BHAVCOPY ZIP to UDiFF column names without renaming
#: the file.  Sampled 2026-08-06: 2022-12-30 is still legacy
#: (``SCRIP ID``/``SC_GROUP``/``TRADING_DATE``) and 2023-01-02 is already UDiFF
#: (``TckrSymb``/``SctySrs``/``TradDt``).  Those are consecutive trading days --
#: 2022-12-31 fell on a Saturday -- so the boundary is exact.
BSE_UDIFF_SCHEMA_IN_ZIP_START = date(2023, 1, 1)
BSE_UDIFF_START = date(2024, 7, 8)
NSE_SME_FOUR_DIGIT_YEAR_START = date(2025, 10, 13)


#: Earliest date each segment has an official report for.  A segment is not a
#: missing dependency before this date -- it never existed -- so a combined file
#: must still publish rather than waiting forever for a component the exchange
#: never produced.  It is also the floor every download clamps to, and the
#: earliest date the date picker offers.  Segments absent from this mapping
#: have no known floor, which is not the same as an early one: a caller must
#: keep its own default rather than assume.
#:
#: Pinned against the archives on 2026-08-20 the same way the delivery floors
#: were -- the date itself downloads and the previous weekday does not:
#:
#: * NSE EQ 1994-11-03, which is the exchange's own first trading day;
#: * NSE FO 2000-06-12, the day index futures launched;
#: * NSE INDEX 2012-02-21;
#: * NSE SME 2012-09-18, shortly after the Emerge platform opened.
#:
#: BSE EQ is deliberately absent, and not for want of measuring. Its archive
#: is not contiguous, so no single date describes it. Five attempts per date
#: on 2026-08-20:
#:
#: * 2016-12-08, 12-09 and 12-12 each served a complete bhavcopy of roughly
#:   2,900 rows, five times out of five;
#: * 2016-12-13 served nothing, five times out of five -- and it is a trading
#:   day, not a holiday in the bundled 2016 calendar, whose only December
#:   entry is the 25th;
#: * 2017-03-15, 2019-07-10 and 2021-04-08 served complete bhavcopies again;
#: * 2006-01-02, 2010-06-15, 2013-02-11, 2015-06-10, 2016-06-10 and every
#:   sampled day of November and early December 2016 served nothing.
#:
#: So the boundary a binary search converges on is the edge of a hole rather
#: than the start of the archive. A floor stops the application from even
#: trying earlier dates, which puts real data permanently out of reach if it
#: is wrong; leaving the segment unbounded only costs some doomed requests at
#: the start of a backfill, and the absent-report ledger settles each of them
#: once. Worth revisiting only with evidence of a contiguous start.
SEGMENT_FIRST_AVAILABLE = {
    ("NSE", "EQ"): date(1994, 11, 3),
    ("NSE", "FO"): date(2000, 6, 12),
    ("NSE", "INDEX"): date(2012, 2, 21),
    ("NSE", "SME"): date(2012, 9, 18),
    ("BSE", "INDEX"): date(2025, 4, 17),
}


def first_available(exchange: str, segment: str) -> Optional[date]:
    """Return the first date this segment can be downloaded, if bounded."""

    return SEGMENT_FIRST_AVAILABLE.get((exchange.upper(), segment.upper()))


def earliest_available(
    segments: Iterable[tuple[str, str]]
) -> Optional[date]:
    """The earliest date any of these segments can be downloaded at all.

    ``None`` when even one of them has no established floor.  An unknown
    floor is not the same as an early one, so a caller bounding a date picker
    keeps its own default rather than guessing a security's history away.
    """

    floors = []
    for exchange, segment in segments:
        floor = first_available(exchange, segment)
        if floor is None:
            return None
        floors.append(floor)
    return min(floors) if floors else None


def is_available(exchange: str, segment: str, target_date: date) -> bool:
    """Whether the segment publishes a report for ``target_date`` at all."""

    floor = first_available(exchange, segment)
    return floor is None or target_date >= floor


#: Earliest date each exchange published its *separate* delivery report.  The
#: price bhavcopy goes back much further, so a historical backfill would
#: otherwise queue a delivery download that can never succeed, hold the date
#: below complete forever, and retry it on every run.
#:
#: Pinned by request against the archives on 2026-08-06, in both cases between
#: consecutive trading days:
#:
#: * NSE ``sec_bhavdata_full``: 2019-09-27 (Fri) answers 404 and 2019-09-30
#:   (Mon) answers 200.  Every sampled month from 2005 to 2019-09 is absent.
#: * BSE ``SCBSEALL``: every trading day of December 2005 answers 404 and
#:   2006-01-02 answers 200, which matches the ``/gross/{year}/`` path layout.
DELIVERY_FIRST_AVAILABLE = {
    "NSE": date(2019, 9, 30),
    "BSE": date(2006, 1, 2),
}


def delivery_first_available(exchange: str) -> Optional[date]:
    """Return the first date a separate delivery report exists, if bounded."""

    return DELIVERY_FIRST_AVAILABLE.get(exchange.upper())


def delivery_available(exchange: str, target_date: date) -> bool:
    """Whether a separate delivery report exists for ``target_date`` at all."""

    floor = delivery_first_available(exchange)
    return floor is None or target_date >= floor


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
        if target_date < BSE_UDIFF_SCHEMA_IN_ZIP_START:
            return SourceSpec(
                f"{base}/BSE_EQ_BHAVCOPY_{target_date:%d%m%Y}.ZIP",
                "bse-equity-bhavcopy-legacy",
                "zip-csv",
            )
        if target_date < BSE_UDIFF_START:
            # Same URL as the era above, UDiFF columns inside.
            return SourceSpec(
                f"{base}/BSE_EQ_BHAVCOPY_{target_date:%d%m%Y}.ZIP",
                "bse-equity-udiff-zip",
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
