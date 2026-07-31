from datetime import date

import pandas as pd

from src.services.corporate_actions import (
    CorporateAction,
    CorporateActionEngine,
    normalize_bse_actions,
    normalize_nse_actions,
    parse_bonus_factor,
    parse_split_factor,
)
from src.services.symbol_history import SymbolHistoryStore


def _history_rows():
    return pd.DataFrame([
        {
            "SYMBOL": "ABC", "DATE": "20250101", "OPEN": 100,
            "HIGH": 100, "LOW": 100, "CLOSE": 100, "VOLUME": 10,
            "DELIVERY_QTY": 5, "DELIVERY_PERCENT": 50, "SERIES": "EQ",
            "TOTAL_TRADES": 1, "QTY_PER_TRADE": 10, "ISIN": "INE1",
            "SECURITY_ID": "500001",
        },
        {
            "SYMBOL": "ABC", "DATE": "20250102", "OPEN": 50,
            "HIGH": 50, "LOW": 50, "CLOSE": 50, "VOLUME": 20,
            "DELIVERY_QTY": 10, "DELIVERY_PERCENT": 50, "SERIES": "EQ",
            "TOTAL_TRADES": 2, "QTY_PER_TRADE": 10, "ISIN": "INE1",
            "SECURITY_ID": "500001",
        },
    ])


def test_ratio_parsers_and_exchange_normalizers():
    assert parse_split_factor("From Rs 10/- Per Share To Rs 2/- Per Share") == 5
    assert parse_bonus_factor("Bonus 1:1") == 2
    nse = normalize_nse_actions([{
        "symbol": "ABC", "series": "EQ", "isin": "INE1",
        "exDate": "02-Jan-2025", "subject": "Bonus 1:1",
    }])
    assert len(nse) == 1 and nse[0].factor == 2

    bse = normalize_bse_actions(pd.DataFrame([{
        "Security Code": "500001", "Security Name": "ABC",
        "Ex Date": "02 Jan 2025",
        "Purpose": "Face Value Split From Rs 10 To Rs 2",
    }]))
    assert len(bse) == 1 and bse[0].factor == 5


def test_corporate_action_applies_once_and_keeps_raw_volume(tmp_path):
    rows = _history_rows()
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    action = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2),
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )
    engine = CorporateActionEngine(tmp_path)
    # A duplicated source record must not compound the same action twice.
    assert engine.apply([action, action])["applied"] == 1
    path = tmp_path / "NSE" / "SYMBOLS" / "abc.txt"
    adjusted = pd.read_csv(path)
    assert float(adjusted.loc[0, "CLOSE"]) == 50.0
    assert int(adjusted.loc[0, "VOLUME"]) == 10
    assert int(adjusted.loc[0, "DELIVERY_QTY"]) == 5

    assert engine.apply([action])["applied"] == 0
    unchanged = pd.read_csv(path)
    assert float(unchanged.loc[0, "CLOSE"]) == 50.0

    # A late delivery retry replays raw OHLC for the old date.  The audited
    # action must still be reflected while non-price values can be refreshed.
    retry = rows.iloc[[0]].copy()
    retry.loc[:, "DELIVERY_QTY"] = 9
    store.upsert("NSE", "EQ", date(2025, 1, 1), retry)
    retried = pd.read_csv(path)
    old_day = retried.loc[retried["DATE"] == 20250101].iloc[0]
    assert float(old_day["CLOSE"]) == 50.0
    assert int(old_day["DELIVERY_QTY"]) == 9


def test_continuity_failure_does_not_replace_symbol_file(tmp_path):
    rows = _history_rows()
    rows.loc[1, ["OPEN", "HIGH", "LOW", "CLOSE"]] = 200
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    action = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2),
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )
    summary = CorporateActionEngine(tmp_path).apply([action])
    assert summary["manual_review"] == 1
    result = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    assert float(result.loc[0, "CLOSE"]) == 100.0
