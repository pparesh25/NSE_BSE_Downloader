from datetime import date
import hashlib
import json

import pandas as pd
import pytest

from main import setup_argument_parser
from src.services.corporate_actions import CorporateAction, CorporateActionEngine
from src.services.delivery_state import PendingDeliveryStore
from src.services.rebuild_service import SymbolHistoryRebuilder
from src.services.state_store import StateCorruptionError, StateStoreError
from src.services.symbol_history import (
    HistoryCorruptionError,
    SymbolHistoryStore,
)


def _rows():
    return pd.DataFrame([
        {
            "SYMBOL": "ABC",
            "DATE": "20250101",
            "OPEN": 100,
            "HIGH": 100,
            "LOW": 100,
            "CLOSE": 100,
            "VOLUME": 10,
            "DELIVERY_QTY": 5,
            "DELIVERY_PERCENT": 50,
            "SERIES": "EQ",
            "TOTAL_TRADES": 1,
            "QTY_PER_TRADE": 10,
            "ISIN": "INE1",
            "SECURITY_ID": "500001",
        },
        {
            "SYMBOL": "ABC",
            "DATE": "20250102",
            "OPEN": 50,
            "HIGH": 50,
            "LOW": 50,
            "CLOSE": 50,
            "VOLUME": 20,
            "DELIVERY_QTY": 10,
            "DELIVERY_PERCENT": 50,
            "SERIES": "EQ",
            "TOTAL_TRADES": 2,
            "QTY_PER_TRADE": 10,
            "ISIN": "INE1",
            "SECURITY_ID": "500001",
        },
    ])


def _action():
    return CorporateAction(
        "NSE",
        "ABC",
        "INE1",
        date(2025, 1, 2),
        "bonus",
        2.0,
        "Bonus 1:1",
        "EQ",
    )


def _history_path(tmp_path):
    return tmp_path / "NSE" / "SYMBOLS" / "abc.txt"


def test_corrupt_history_fails_closed_and_preserves_original(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows())
    path = _history_path(tmp_path)
    damaged = b"not,a,valid,history\n1,2\n"
    path.write_bytes(damaged)

    with pytest.raises(HistoryCorruptionError):
        store.upsert(
            "NSE", "EQ", date(2025, 1, 3), _rows().iloc[[1]].copy()
        )

    assert path.read_bytes() == damaged
    quarantined = list(
        (tmp_path / ".state" / "quarantine" / "history").glob(
            "abc.*.corrupt"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == damaged


def test_corrupt_registry_ledger_and_delivery_state_fail_closed(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows())

    registry = tmp_path / ".state" / "symbol_registry.json"
    registry_bytes = b"{broken registry"
    registry.write_bytes(registry_bytes)
    with pytest.raises(StateCorruptionError):
        store.resolve_symbol("NSE", "INE1")
    assert registry.read_bytes() == registry_bytes

    ledger = tmp_path / ".state" / "corporate_actions.json"
    ledger_bytes = b"{broken ledger"
    ledger.write_bytes(ledger_bytes)
    before = _history_path(tmp_path).read_bytes()
    with pytest.raises(StateCorruptionError):
        CorporateActionEngine(tmp_path).apply([_action()])
    assert ledger.read_bytes() == ledger_bytes
    assert _history_path(tmp_path).read_bytes() == before

    delivery = PendingDeliveryStore(tmp_path)
    delivery.path.parent.mkdir(parents=True, exist_ok=True)
    delivery_bytes = b"{broken delivery"
    delivery.path.write_bytes(delivery_bytes)
    with pytest.raises(StateCorruptionError):
        delivery.dates("NSE", "EQ")
    assert delivery.path.read_bytes() == delivery_bytes


def test_action_recovers_when_final_ledger_commit_fails(tmp_path, monkeypatch):
    SymbolHistoryStore(tmp_path).upsert(
        "NSE", "EQ", date(2025, 1, 2), _rows()
    )
    engine = CorporateActionEngine(tmp_path)
    original_write = engine._write_ledger
    writes = 0

    def fail_final_commit(ledger):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated final ledger failure")
        return original_write(ledger)

    monkeypatch.setattr(engine, "_write_ledger", fail_final_commit)
    with pytest.raises(OSError, match="final ledger failure"):
        engine.apply([_action()])

    assert float(pd.read_csv(_history_path(tmp_path)).loc[0, "CLOSE"]) == 50

    summary = CorporateActionEngine(tmp_path).apply([_action()])
    assert summary["recovered"] == 1
    assert summary["applied"] == 0
    assert float(pd.read_csv(_history_path(tmp_path)).loc[0, "CLOSE"]) == 50


def test_action_recovers_when_history_publish_fails(tmp_path, monkeypatch):
    SymbolHistoryStore(tmp_path).upsert(
        "NSE", "EQ", date(2025, 1, 2), _rows()
    )
    engine = CorporateActionEngine(tmp_path)

    def fail_publish(*args, **kwargs):
        raise OSError("simulated history publish failure")

    monkeypatch.setattr(engine.histories, "rewrite_symbol", fail_publish)
    with pytest.raises(OSError, match="history publish failure"):
        engine.apply([_action()])

    assert float(pd.read_csv(_history_path(tmp_path)).loc[0, "CLOSE"]) == 100

    summary = CorporateActionEngine(tmp_path).apply([_action()])
    assert summary["recovered"] == 1
    assert float(pd.read_csv(_history_path(tmp_path)).loc[0, "CLOSE"]) == 50


def test_action_recovery_rejects_an_unexpected_history_revision(
    tmp_path, monkeypatch
):
    SymbolHistoryStore(tmp_path).upsert(
        "NSE", "EQ", date(2025, 1, 2), _rows()
    )
    engine = CorporateActionEngine(tmp_path)
    original_write = engine._write_ledger
    writes = 0

    def fail_final_commit(ledger):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated final ledger failure")
        return original_write(ledger)

    monkeypatch.setattr(engine, "_write_ledger", fail_final_commit)
    with pytest.raises(OSError):
        engine.apply([_action()])

    history = pd.read_csv(_history_path(tmp_path))
    history.loc[0, ["OPEN", "HIGH", "LOW", "CLOSE"]] = 77
    history.to_csv(_history_path(tmp_path), index=False)

    with pytest.raises(StateStoreError, match="unexpected history revision"):
        CorporateActionEngine(tmp_path).apply([_action()])
    assert float(pd.read_csv(_history_path(tmp_path)).loc[0, "CLOSE"]) == 77


def test_backfill_reconciles_previously_missing_action(tmp_path):
    engine = CorporateActionEngine(tmp_path)
    assert engine.apply([_action()])["skipped"] == 1

    SymbolHistoryStore(tmp_path).upsert(
        "NSE", "EQ", date(2025, 1, 2), _rows()
    )

    history = pd.read_csv(_history_path(tmp_path))
    assert float(history.loc[0, "CLOSE"]) == 50
    ledger = json.loads(
        (tmp_path / ".state" / "corporate_actions.json").read_text()
    )
    assert ledger["actions"][_action().key]["status"] == "applied"


def test_rebuild_symbol_from_raw_preserves_applied_actions(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows())
    CorporateActionEngine(tmp_path).apply([_action()])
    path = _history_path(tmp_path)
    path.write_bytes(b"damaged history")

    rebuilt = SymbolHistoryRebuilder(tmp_path).rebuild_symbol("NSE", "ABC")

    assert rebuilt == path
    history = pd.read_csv(path)
    assert history["DATE"].astype(str).tolist() == ["20250101", "20250102"]
    assert float(history.loc[0, "CLOSE"]) == 50


def test_rebuild_registry_from_raw_snapshots(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows())
    store.registry_path.write_bytes(b"damaged registry")

    count = SymbolHistoryRebuilder(tmp_path).rebuild_registry()

    assert count >= 2
    assert store.resolve_symbol("NSE", "INE1") == "ABC"


def test_single_exchange_rebuild_preserves_other_exchange_registry(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _rows())
    bse_rows = _rows().assign(
        SYMBOL="BSEABC", ISIN="INE-BSE", SECURITY_ID="600001"
    )
    store.upsert("BSE", "EQ", date(2025, 1, 2), bse_rows)

    SymbolHistoryRebuilder(tmp_path).rebuild_symbol("NSE", "ABC")

    assert store.resolve_symbol("NSE", "INE1") == "ABC"
    assert store.resolve_symbol("BSE", "600001") == "BSEABC"


def test_colliding_symbol_slugs_get_stable_distinct_files(tmp_path):
    rows = pd.concat([
        _rows().iloc[[0]].assign(
            SYMBOL="A/B", ISIN="INE-A", SECURITY_ID="500001"
        ),
        _rows().iloc[[0]].assign(
            SYMBOL="A B", ISIN="INE-B", SECURITY_ID="500002"
        ),
    ], ignore_index=True)
    store = SymbolHistoryStore(tmp_path)

    store.upsert("NSE", "EQ", date(2025, 1, 1), rows)
    first = store.symbol_path("NSE", "A/B")
    second = store.symbol_path("NSE", "A B")

    assert first != second
    assert first.exists() and second.exists()
    assert first.name == "a_b.txt"
    assert second.name.startswith("a_b--")


def test_raw_snapshot_revisions_and_checksum_metadata_are_retained(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    rows = _rows()
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    snapshot = (
        tmp_path / ".state" / "raw" / "NSE" / "EQ" / "2025-01-02.csv"
    )
    original_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    revised = rows.copy()
    revised.loc[0, "DELIVERY_QTY"] = 9
    store.upsert("NSE", "EQ", date(2025, 1, 2), revised)

    revision = (
        tmp_path / ".state" / "raw_revisions" / "NSE" / "EQ" /
        "2025-01-02" / f"{original_digest}.csv"
    )
    metadata = json.loads(
        snapshot.with_suffix(".csv.meta.json").read_text()
    )
    assert revision.exists()
    assert hashlib.sha256(revision.read_bytes()).hexdigest() == original_digest
    assert metadata["sha256"] == hashlib.sha256(
        snapshot.read_bytes()
    ).hexdigest()
    assert metadata["row_count"] == 2


def test_tampered_raw_snapshot_blocks_upsert_and_rebuild(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    rows = _rows()
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    snapshot = (
        tmp_path / ".state" / "raw" / "NSE" / "EQ" / "2025-01-02.csv"
    )
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
    history_before = _history_path(tmp_path).read_bytes()

    with pytest.raises(StateCorruptionError, match="checksum"):
        store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    assert _history_path(tmp_path).read_bytes() == history_before

    with pytest.raises(StateCorruptionError):
        SymbolHistoryRebuilder(tmp_path).rebuild_symbol("NSE", "ABC")


def test_rebuild_cli_options_are_mutually_exclusive():
    parser = setup_argument_parser()
    args = parser.parse_args(["--rebuild-symbol", "NSE", "RELIANCE"])
    assert args.rebuild_symbol == ["NSE", "RELIANCE"]

    with pytest.raises(SystemExit):
        parser.parse_args([
            "--rebuild-symbol", "NSE", "RELIANCE",
            "--rebuild-exchange", "NSE",
        ])
