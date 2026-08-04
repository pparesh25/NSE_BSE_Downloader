from datetime import date

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
