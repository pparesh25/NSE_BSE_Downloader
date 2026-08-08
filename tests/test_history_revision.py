import json
from datetime import date

import pandas as pd
import pytest

from src.services.history_revision import (
    SYMBOL_ADJUSTMENT_REVISION,
    HistoryRevisionStore,
)
from src.services.rebuild_service import SymbolHistoryRebuilder
from src.services.state_store import StateCorruptionError
from src.services.symbol_history import SymbolHistoryStore


def _rows(day="20250101"):
    return pd.DataFrame([{
        "SYMBOL": "ABC", "DATE": day, "OPEN": 100, "HIGH": 100, "LOW": 100,
        "CLOSE": 100, "VOLUME": 10, "DELIVERY_QTY": 5, "DELIVERY_PERCENT": 50,
        "SERIES": "EQ", "TOTAL_TRADES": 1, "QTY_PER_TRADE": 10,
        "ISIN": "INE111111111", "SECURITY_ID": "500001",
    }])


def _upgraded_tree(tmp_path, *exchanges):
    """A data root as an older build left it: symbol files, and no marker."""

    histories = SymbolHistoryStore(tmp_path)
    for exchange in exchanges:
        histories.upsert(exchange, "EQ", date(2025, 1, 1), _rows())
    (tmp_path / ".state" / "history_revision.json").unlink(missing_ok=True)
    (tmp_path / ".state" / "history_revision.json.bak").unlink(missing_ok=True)
    return histories


def test_a_fresh_installation_is_never_asked_to_rebuild(tmp_path):
    store = HistoryRevisionStore(tmp_path)
    assert store.stale_exchanges() == []
    assert store.notice() == ""


def test_a_history_this_build_started_is_not_stale(tmp_path):
    """The prompt is about files an older build wrote, not new downloads."""

    SymbolHistoryStore(tmp_path).upsert("NSE", "EQ", date(2025, 1, 1), _rows())
    store = HistoryRevisionStore(tmp_path)
    assert store.revision_for("NSE") == SYMBOL_ADJUSTMENT_REVISION
    assert store.stale_exchanges() == []
    assert store.notice() == ""

    # A later day must not disturb the stamp either.
    SymbolHistoryStore(tmp_path).upsert(
        "NSE", "EQ", date(2025, 1, 2), _rows("20250102")
    )
    assert store.stale_exchanges() == []


def test_symbol_files_written_before_the_marker_are_stale(tmp_path):
    _upgraded_tree(tmp_path, "NSE")
    store = HistoryRevisionStore(tmp_path)
    assert store.stale_exchanges() == ["NSE"]
    notice = store.notice()
    assert "NSE" in notice and "rebuild" in notice.lower()
    # The prompt must not promise more than .state/raw can deliver.
    assert ".state/raw" in notice


def test_only_exchanges_with_symbol_files_are_reported(tmp_path):
    _upgraded_tree(tmp_path, "NSE")
    (tmp_path / "BSE" / "EQ").mkdir(parents=True)
    (tmp_path / "BSE" / "EQ" / "2025-01-01.txt").write_text("x\n")
    assert HistoryRevisionStore(tmp_path).stale_exchanges() == ["NSE"]


def test_a_rebuild_clears_the_prompt_for_that_exchange(tmp_path):
    _upgraded_tree(tmp_path, "NSE", "BSE")
    store = HistoryRevisionStore(tmp_path)
    assert store.stale_exchanges() == ["BSE", "NSE"]

    SymbolHistoryRebuilder(tmp_path).rebuild_exchange("NSE")
    assert store.stale_exchanges() == ["BSE"]
    assert store.revision_for("NSE") == SYMBOL_ADJUSTMENT_REVISION
    assert store.revision_for("BSE") == 1


def test_an_exchange_a_rebuild_cannot_reach_is_named_separately(tmp_path):
    """A prompt that asks for a repair which fails would repeat forever."""

    _upgraded_tree(tmp_path, "NSE", "BSE")
    import shutil

    shutil.rmtree(tmp_path / ".state" / "raw" / "BSE")
    notice = HistoryRevisionStore(tmp_path).notice()
    assert "--rebuild-exchange" in notice
    lines = notice.splitlines()
    assert len(lines) == 2
    assert "NSE" in lines[0] and "BSE" not in lines[0]
    assert "BSE" in lines[1] and "cannot repair" in lines[1]


def test_the_marker_is_a_validated_state_document(tmp_path):
    HistoryRevisionStore(tmp_path).mark_current("NSE")
    path = tmp_path / ".state" / "history_revision.json"
    document = json.loads(path.read_text())
    assert document == {
        "version": 1, "adjustment": {"NSE": SYMBOL_ADJUSTMENT_REVISION}
    }

    path.write_text('{"version": 1, "adjustment": {"NSE": "two"}}')
    with pytest.raises(StateCorruptionError):
        HistoryRevisionStore(tmp_path).read()
    # Fail closed: the damaged bytes are preserved, not overwritten.
    assert list(
        (tmp_path / ".state" / "quarantine" / "history_revision").glob("*")
    )
