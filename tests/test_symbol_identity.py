"""Phase 3.3: symbol identity is protected against merges that destroy data.

Every test here reproduces a way one security's rows could historically end
up inside another security's file -- malformed identifiers acting as merge
keys, overlapping-date merges, ticker reuse, and rebuild closures -- and
asserts the write path now refuses, forks or quarantines instead of
silently deduplicating two companies into one.
"""

from datetime import date
import json

import pandas as pd
import pytest

from src.services.canonical_data import SYMBOL_HISTORY_COLUMNS
from src.services.corporate_actions import (
    CorporateAction,
    CorporateActionEngine,
    normalize_bse_actions,
    normalize_nse_actions,
)
from src.services.rebuild_service import SymbolHistoryRebuilder
from src.services.state_store import StateCorruptionError
from src.services.symbol_history import (
    HistoryBatchItem,
    SymbolHistoryStore,
)


ISIN_A = "INE00A000011"
ISIN_B = "INE00B000012"


def _rows(day, symbol, isin="", security_id="", close=100.0, volume=100):
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
        "SECURITY_ID": security_id,
    }])


def _dates_in(path):
    return pd.read_csv(path, dtype=str)["DATE"].tolist()


def _quarantined(base):
    return sorted((base / ".state" / "quarantine" / "merged").glob("*"))


# --- 3.3 item 1: identifier validation ---------------------------------------


def test_malformed_shared_isin_never_becomes_a_merge_key(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin="-"))
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "BBB", isin="-"))

    # Before validation the shared "-" registered as ISIN:- and BBB's upsert
    # merged AAA's history away as a "rename".
    assert (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert (tmp_path / "NSE" / "SYMBOLS" / "bbb.txt").exists()
    assert store.resolve_symbol("NSE", "-") is None


def test_action_identifiers_fall_back_to_symbol_when_malformed():
    nse = normalize_nse_actions([{
        "symbol": "AAA", "series": "EQ", "isin": "-",
        "subject": "Bonus 1:1", "exDate": "02-Jan-2025",
    }])
    assert nse[0].stable_id == "AAA"

    bse = normalize_bse_actions(pd.DataFrame([{
        "Security Name": "BBB", "Security Code": "N.A.",
        "Purpose": "Bonus issue 1:1", "Detail/Remarks": "",
        "Ex Date": "02-01-2025",
    }]))
    assert bse[0].stable_id == "BBB"


# --- 3.3 item 2: overlapping-date merges are refused --------------------------


def test_upsert_refuses_merge_when_histories_overlap(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A, close=100))
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "BBB", close=200))
    # BBB suddenly carries AAA's ISIN while both files already claim day one.
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "BBB", isin=ISIN_A, close=210))

    aaa = tmp_path / "NSE" / "SYMBOLS" / "aaa.txt"
    bbb = tmp_path / "NSE" / "SYMBOLS" / "bbb.txt"
    assert aaa.exists() and bbb.exists()
    assert _dates_in(aaa) == ["20250101"]
    assert float(pd.read_csv(aaa)["CLOSE"].iloc[0]) == 100.0
    assert _dates_in(bbb) == ["20250101", "20250102"]


def test_batch_refuses_overlapping_merge_and_reports_it(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert_batch([
        HistoryBatchItem("NSE", "EQ", date(2025, 1, 1), pd.concat([
            _rows("20250101", "AAA", isin=ISIN_A, close=100),
            _rows("20250101", "BBB", close=200),
        ], ignore_index=True)),
    ])
    result = store.upsert_batch([
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 2),
            _rows("20250102", "BBB", isin=ISIN_A, close=210),
        ),
    ])

    assert len(result.refused_merges) == 1
    refusal = result.refused_merges[0]
    assert refusal.old_symbol == "AAA"
    assert refusal.new_symbol == "BBB"
    assert refusal.overlapping_dates == 1
    assert refusal.sample_dates == ("20250101",)
    aaa = tmp_path / "NSE" / "SYMBOLS" / "aaa.txt"
    bbb = tmp_path / "NSE" / "SYMBOLS" / "bbb.txt"
    assert _dates_in(aaa) == ["20250101"]
    assert _dates_in(bbb) == ["20250101", "20250102"]
    # The key follows today's carrier, so the refusal does not repeat forever.
    assert store.resolve_symbol("NSE", ISIN_A) == "BBB"


# --- 3.3 item 3: superseded files are quarantined, not deleted ----------------


def test_upsert_rename_quarantines_the_superseded_file(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A))
    original = (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").read_bytes()
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "BBB", isin=ISIN_A))

    assert not (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    quarantined = _quarantined(tmp_path)
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original
    assert quarantined[0].suffix == ".txt"


def test_batch_rename_quarantines_the_superseded_file(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert_batch([
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 1),
            _rows("20250101", "AAA", isin=ISIN_A),
        ),
    ])
    original = (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").read_bytes()
    store.upsert_batch([
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 2),
            _rows("20250102", "BBB", isin=ISIN_A),
        ),
    ])

    assert not (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    merged = tmp_path / "NSE" / "SYMBOLS" / "bbb.txt"
    assert _dates_in(merged) == ["20250101", "20250102"]
    quarantined = _quarantined(tmp_path)
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original


# --- 3.3 item 4: ticker reuse -------------------------------------------------


def test_reused_ticker_gets_a_fresh_file(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A, close=100))
    # AAA delists; a different company later lists under the freed ticker.
    store.upsert("NSE", "EQ", date(2025, 6, 2), _rows("20250602", "AAA", isin=ISIN_B, close=55))

    old_file = tmp_path / "NSE" / "SYMBOLS" / "aaa.txt"
    assert _dates_in(old_file) == ["20250101"]
    forked = [
        path for path in (tmp_path / "NSE" / "SYMBOLS").glob("aaa--*.txt")
    ]
    assert len(forked) == 1
    assert _dates_in(forked[0]) == ["20250602"]
    frame = pd.read_csv(forked[0], dtype=str)
    assert frame["ISIN"].iloc[0] == ISIN_B
    assert store.symbol_path("NSE", "AAA") == forked[0]


def test_same_batch_rename_and_reuse_drops_no_rows(tmp_path):
    """The exact scenario recorded in the remediation plan.

    AAA (ISIN A) renames to AAA-NEW, retiring aaa.txt; a different company
    (ISIN B) lists under the freed ticker AAA in the same batch.  The day-3
    row used to be assigned to the retired aaa.txt, excluded from the write
    set and deleted with it.
    """

    store = SymbolHistoryStore(tmp_path)
    result = store.upsert_batch([
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 1),
            _rows("20250101", "AAA", isin=ISIN_A, close=100),
        ),
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 2),
            _rows("20250102", "AAA-NEW", isin=ISIN_A, close=101),
        ),
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 3),
            _rows("20250103", "AAA", isin=ISIN_B, close=55),
        ),
    ])

    assert result.failures == ()
    renamed = tmp_path / "NSE" / "SYMBOLS" / "aaa-new.txt"
    assert _dates_in(renamed) == ["20250101", "20250102"]
    reused = store.symbol_path("NSE", "AAA")
    assert reused.name.startswith("aaa--")
    assert _dates_in(reused) == ["20250103"]
    # aaa.txt was planned away before it was ever written, so nothing is on
    # disk under that name and nothing needed quarantining.
    assert not (tmp_path / "NSE" / "SYMBOLS" / "aaa.txt").exists()
    assert _quarantined(tmp_path) == []


def test_v2_registry_migration_seeds_identities_and_prunes_bad_keys(tmp_path):
    registry_path = tmp_path / ".state" / "symbol_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({
        "version": 2,
        "exchanges": {"NSE": {
            f"ISIN:{ISIN_A}": "AAA",
            "ISIN:-": "AAA",
        }},
        "files": {"NSE": {"AAA": "aaa.txt"}},
    }))
    store = SymbolHistoryStore(tmp_path)
    registry = store._read_registry()

    assert registry["version"] == 3
    assert registry["identities"]["NSE"]["aaa.txt"] == [f"ISIN:{ISIN_A}"]
    assert "ISIN:-" not in registry["exchanges"]["NSE"]

    # The migrated identity immediately protects the file from ticker reuse.
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "AAA", isin=ISIN_B))
    assert store.symbol_path("NSE", "AAA").name.startswith("aaa--")


def test_registry_with_invalid_identities_fails_closed(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A))
    registry_path = tmp_path / ".state" / "symbol_registry.json"
    data = json.loads(registry_path.read_text())
    data["identities"] = {"NSE": {"aaa.txt": "not-a-list"}}
    registry_path.write_text(json.dumps(data))

    with pytest.raises(StateCorruptionError):
        SymbolHistoryStore(tmp_path).resolve_symbol("NSE", ISIN_A)


# --- 3.3 item 5: histories are self-identifying -------------------------------


def test_history_carries_its_isin_and_blank_when_absent(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A))
    store.upsert("NSE", "SME", date(2025, 1, 1), _rows("20250101", "SME_CO"))

    aaa = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "aaa.txt", dtype=str)
    assert list(aaa.columns) == SYMBOL_HISTORY_COLUMNS
    assert aaa["ISIN"].iloc[0] == ISIN_A
    sme = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "sme_co.txt", dtype=str)
    assert sme["ISIN"].isna().all()


def test_legacy_history_upgrades_on_next_write(tmp_path):
    symbols_dir = tmp_path / "NSE" / "SYMBOLS"
    symbols_dir.mkdir(parents=True)
    legacy_header = (
        "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,"
        "TOTAL_TRADES,QTY_PER_TRADE,DELIVERY_QTY,DELIVERY_PERCENT"
    )
    (symbols_dir / "aaa.txt").write_text(
        f"{legacy_header}\n20250101,10,10,10,10,5,EQ,1,5.0,4,80\n"
    )
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "AAA", isin=ISIN_A, close=11))

    upgraded = pd.read_csv(symbols_dir / "aaa.txt", dtype=str)
    assert list(upgraded.columns) == SYMBOL_HISTORY_COLUMNS
    assert upgraded["DATE"].tolist() == ["20250101", "20250102"]
    assert upgraded["ISIN"].isna().iloc[0]
    assert upgraded["ISIN"].iloc[1] == ISIN_A


# --- rebuild and replay identity ----------------------------------------------


def test_rebuild_does_not_swallow_a_reused_ticker(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A, close=100))
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "AAA-NEW", isin=ISIN_A, close=101))
    store.upsert("NSE", "EQ", date(2025, 1, 3), _rows("20250103", "AAA", isin=ISIN_B, close=55))

    SymbolHistoryRebuilder(tmp_path).rebuild_exchange("NSE")

    renamed = store.symbol_path("NSE", "AAA-NEW")
    assert _dates_in(renamed) == ["20250101", "20250102"]
    reused = store.symbol_path("NSE", "AAA")
    assert _dates_in(reused) == ["20250103"]
    reused_frame = pd.read_csv(reused, dtype=str)
    assert reused_frame["ISIN"].iloc[0] == ISIN_B


def test_rebuild_preserves_ticker_keyed_adjustment_across_rename(tmp_path):
    """The identity defect recorded during 3.2: a ticker-fallback stable_id
    stops matching after the security is renamed, so a rebuild silently
    reverted the audited adjustment back to raw prices."""

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A, close=100))
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "AAA", isin=ISIN_A, close=50))
    summary = CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "AAA", "AAA", date(2025, 1, 2), "split", 2.0,
        "Fv Split Rs.10 To Rs.5", "EQ",
    )])
    assert summary["applied"] == 1
    store.upsert("NSE", "EQ", date(2025, 1, 3), _rows("20250103", "BBB", isin=ISIN_A, close=50))
    merged = store.symbol_path("NSE", "BBB")
    before = pd.read_csv(merged)
    assert float(before.loc[0, "CLOSE"]) == 50.0

    SymbolHistoryRebuilder(tmp_path).rebuild_exchange("NSE")

    after = pd.read_csv(store.symbol_path("NSE", "BBB"))
    assert after["DATE"].astype(str).tolist() == [
        "20250101", "20250102", "20250103"
    ]
    assert float(after.loc[0, "CLOSE"]) == 50.0


def test_batch_replay_matches_action_recorded_under_current_name(tmp_path):
    """A re-downloaded pre-rename row must still replay an action whose
    stable_id fell back to the post-rename ticker."""

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _rows("20250101", "AAA", isin=ISIN_A, close=100))
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows("20250102", "BBB", isin=ISIN_A, close=50))
    summary = CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "BBB", "BBB", date(2025, 1, 2), "split", 2.0,
        "Fv Split Rs.10 To Rs.5", "EQ",
    )])
    assert summary["applied"] == 1
    assert float(
        pd.read_csv(store.symbol_path("NSE", "BBB")).loc[0, "CLOSE"]
    ) == 50.0

    # The repair path re-downloads day one under the pre-rename ticker.
    store.upsert_batch([
        HistoryBatchItem(
            "NSE", "EQ", date(2025, 1, 1),
            _rows("20250101", "AAA", isin=ISIN_A, close=100),
        ),
    ])

    history = pd.read_csv(store.symbol_path("NSE", "AAA"))
    day_one = history[history["DATE"].astype(str) == "20250101"]
    assert float(day_one["CLOSE"].iloc[0]) == 50.0
