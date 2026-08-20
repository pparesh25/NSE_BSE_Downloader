from datetime import date

import pytest

from src.services.source_resolver import delivery_source, price_source


def test_nse_udiff_cutover_and_sme_filename_cutover():
    assert "cm05JUL2024bhav.csv.zip" in price_source(
        "NSE", "EQ", date(2024, 7, 5)
    ).url
    assert "BhavCopy_NSE_CM" in price_source(
        "NSE", "EQ", date(2024, 7, 8)
    ).url
    assert price_source("NSE", "SME", date(2025, 10, 10)).url.endswith(
        "sme101025.csv"
    )
    assert price_source("NSE", "SME", date(2025, 10, 13)).url.endswith(
        "sme13102025.csv"
    )


def test_bse_three_price_eras_and_delivery_report():
    assert price_source("BSE", "EQ", date(2022, 8, 16)).url.endswith(
        "EQ_ISINCODE_160822.zip"
    )
    assert price_source("BSE", "EQ", date(2022, 8, 17)).url.endswith(
        "BSE_EQ_BHAVCOPY_17082022.ZIP"
    )
    assert price_source("BSE", "EQ", date(2024, 7, 8)).url.endswith(
        "BhavCopy_BSE_CM_0_0_0_20240708_F_0000.CSV"
    )
    assert delivery_source("BSE", date(2025, 10, 14)).url.endswith(
        "/2025/SCBSEALL1410.zip"
    )


@pytest.mark.parametrize(
    "target,expected_era",
    [
        # Sampled from the exchange on 2026-08-06.  2022-12-31 was a Saturday,
        # so these two are consecutive trading days and the boundary is exact.
        (date(2022, 8, 17), "bse-equity-bhavcopy-legacy"),
        (date(2022, 12, 30), "bse-equity-bhavcopy-legacy"),
        (date(2023, 1, 2), "bse-equity-udiff-zip"),
        (date(2023, 6, 15), "bse-equity-udiff-zip"),
        (date(2024, 7, 5), "bse-equity-udiff-zip"),
        (date(2024, 7, 8), "bse-equity-udiff"),
    ],
)
def test_bse_equity_zip_era_flips_at_2023(target, expected_era):
    assert price_source("BSE", "EQ", target).era == expected_era


def test_bse_equity_zip_eras_share_one_filename():
    # The schema changed inside the archive; the URL did not.  If these ever
    # diverge, the era split above is no longer the reason they differ.
    before = price_source("BSE", "EQ", date(2022, 12, 30))
    after = price_source("BSE", "EQ", date(2023, 1, 2))
    assert before.url.endswith("BSE_EQ_BHAVCOPY_30122022.ZIP")
    assert after.url.endswith("BSE_EQ_BHAVCOPY_02012023.ZIP")
    assert before.era != after.era


def test_every_recorded_floor_is_a_date_the_exchange_could_have_traded():
    """Floors are evidence, not guesses, so they must at least be plausible."""

    from src.services.source_resolver import SEGMENT_FIRST_AVAILABLE

    for (exchange, segment), floor in SEGMENT_FIRST_AVAILABLE.items():
        assert floor.weekday() < 5, f"{exchange}_{segment} floor is a weekend"
        assert date(1994, 1, 1) <= floor <= date(2026, 1, 1), (
            f"{exchange}_{segment} floor {floor} is outside the plausible range"
        )


def test_an_unknown_floor_keeps_a_caller_from_guessing():
    from src.services.source_resolver import earliest_available, is_available

    # BSE EQ has no established floor, so nothing may bound a range with it.
    assert earliest_available([("NSE", "EQ"), ("BSE", "EQ")]) is None
    assert is_available("BSE", "EQ", date(1995, 1, 2))

    assert earliest_available([("NSE", "EQ"), ("NSE", "SME")]) == date(
        1994, 11, 3
    )


def test_a_segment_refuses_dates_before_its_source_existed():
    from src.services.source_resolver import is_available

    assert not is_available("NSE", "SME", date(2012, 9, 17))
    assert is_available("NSE", "SME", date(2012, 9, 18))
    assert not is_available("NSE", "FO", date(2000, 6, 9))
    assert is_available("NSE", "FO", date(2000, 6, 12))
