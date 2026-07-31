from datetime import date

import pandas as pd

from src.services.symbol_history import SymbolHistoryStore


def _row(day, symbol="OLDNAME", close=100, volume=100, isin="INE1"):
    return pd.DataFrame([{
        "SYMBOL": symbol,
        "DATE": day,
        "OPEN": close,
        "HIGH": close,
        "LOW": close,
        "CLOSE": close,
        "VOLUME": volume,
        "DELIVERY_QTY": 80,
        "DELIVERY_PERCENT": 80,
        "SERIES": "EQ",
        "TOTAL_TRADES": 10,
        "QTY_PER_TRADE": 10,
        "ISIN": isin,
        "SECURITY_ID": "123",
    }])


def test_symbol_history_upsert_is_idempotent_and_has_header(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    rows = _row("20250101")
    store.upsert("NSE", "EQ", date(2025, 1, 1), rows)
    store.upsert("NSE", "EQ", date(2025, 1, 1), rows)
    path = tmp_path / "NSE" / "SYMBOLS" / "oldname.txt"
    result = pd.read_csv(path)
    assert list(result.columns)[0] == "DATE"
    assert len(result) == 1
    assert (tmp_path / ".state" / "raw" / "NSE" / "EQ" / "2025-01-01.csv").exists()


def test_isin_symbol_rename_merges_history_and_prefers_liquid_collision(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _row("20250101"))
    store.upsert(
        "NSE", "EQ", date(2025, 1, 2),
        _row("20250102", symbol="NEWNAME", close=110),
    )
    assert not (tmp_path / "NSE" / "SYMBOLS" / "oldname.txt").exists()
    path = tmp_path / "NSE" / "SYMBOLS" / "newname.txt"
    result = pd.read_csv(path)
    assert result["DATE"].astype(str).tolist() == ["20250101", "20250102"]

    store.upsert(
        "NSE", "EQ", date(2025, 1, 2),
        _row("20250102", symbol="NEWNAME", close=120, volume=200),
    )
    result = pd.read_csv(path)
    assert len(result) == 2
    assert int(result.loc[result["DATE"] == 20250102, "CLOSE"].iloc[0]) == 120


def test_registry_keeps_isin_and_security_code(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("BSE", "EQ", date(2025, 1, 1), _row("20250101"))
    assert store.resolve_symbol("BSE", "INE1") == "OLDNAME"
    assert store.resolve_symbol("BSE", "123") == "OLDNAME"
