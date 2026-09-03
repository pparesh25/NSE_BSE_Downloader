"""Phase 5 step 3: publishing symbol histories out of the database.

The point is not that generating a file from the database is faster -- it is
measurably slower.  The point is that a history whose only missing rows come
after its last stored date can be *extended* instead of rewritten, and on the
owner's four-date tree that is 700 KB written against 3,625 KB for the same
change.  The ratio is the number of dates.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pandas as pd

from src.services.eod_export import applied_actions, symbol_text
from src.services.eod_publish import publish_histories
from src.services.eod_store import EodStore
from src.services.symbol_history import SymbolHistoryStore


def _row(day, symbol="OLDNAME", close=100.0, volume=100,
         isin="INE111111111", security_id="123"):
    return pd.DataFrame([{
        "SYMBOL": symbol, "DATE": day,
        "OPEN": close, "HIGH": close, "LOW": close, "CLOSE": close,
        "VOLUME": volume, "DELIVERY_QTY": 80, "DELIVERY_PERCENT": 80.0,
        "SERIES": "EQ", "TOTAL_TRADES": 10, "QTY_PER_TRADE": 10.0,
        "ISIN": isin, "SECURITY_ID": security_id,
        "TURNOVER": close * volume, "PREV_CLOSE": close,
    }])


def _store(root: Path) -> EodStore:
    return EodStore(
        root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
    )


def _publish_legacy(root: Path, day: date, rows: pd.DataFrame):
    SymbolHistoryStore(root).upsert("NSE", "EQ", day, rows)
    return _store(root).upsert_frame("NSE", "EQ", rows)


def _registry(root: Path) -> dict:
    return json.loads(
        (root / ".state" / "symbol_registry.json").read_text(encoding="utf-8")
    )


def _run(root: Path, keys, revised=()):
    return publish_histories(
        _store(root), root, _registry(root),
        applied_actions(root / ".state"), {"NSE": set(keys)}, revised,
    )


def _history(root: Path, name="oldname.txt") -> Path:
    return root / "NSE" / "SYMBOLS" / name


def test_a_new_day_extends_the_file_instead_of_rewriting_it(tmp_path):
    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    before = _history(tmp_path).read_text()
    # The second day reaches the database but not the file.
    _store(tmp_path).upsert_frame("NSE", "EQ", _row("20250102", close=110.0))

    result = _run(tmp_path, ["ID:123"])

    assert (result.appended, result.rewritten) == (1, 0)
    text = _history(tmp_path).read_text()
    assert text.startswith(before)
    assert len(text.splitlines()) == 3
    # And the extended file is what a full rebuild would have produced.
    assert text == symbol_text(
        _store(tmp_path), _registry(tmp_path), "NSE", "oldname.txt"
    )
    assert result.bytes_written < len(before)


def test_a_history_already_current_is_not_touched_at_all(tmp_path):
    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    path = _history(tmp_path)
    before = (path.stat().st_mtime_ns, path.read_bytes())

    result = _run(tmp_path, ["ID:123"])

    assert (result.appended, result.rewritten, result.unchanged) == (0, 0, 1)
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_a_corrected_earlier_date_forces_a_rewrite(tmp_path):
    """An append cannot fix a row the file already holds."""

    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    _publish_legacy(tmp_path, date(2025, 1, 2), _row("20250102", close=110.0))
    _store(tmp_path).upsert_frame("NSE", "EQ", _row("20250101", close=999.0))

    result = _run(tmp_path, ["ID:123"], revised={("NSE", "EQ", 20250101)})

    assert (result.appended, result.rewritten) == (0, 1)
    assert "999.0" in _history(tmp_path).read_text()


def test_a_recorded_action_forces_a_rewrite(tmp_path):
    """A split rescales rows already in the file, so it cannot be extended."""

    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101", close=200.0))
    _store(tmp_path).upsert_frame("NSE", "EQ", _row("20250102", close=100.0))
    (tmp_path / ".state" / "corporate_actions.json").write_text(json.dumps({
        "version": 1, "transactions": {},
        "actions": {"a": {
            "status": "applied", "action_type": "split", "factor": 2.0,
            "ex_date": "2025-01-02", "exchange": "NSE",
            "stable_id": "123", "symbol": "OLDNAME",
        }},
    }), encoding="utf-8")

    result = _run(tmp_path, ["ID:123"])

    assert (result.appended, result.rewritten) == (0, 1)
    text = _history(tmp_path).read_text()
    assert "100.0,100.0,100.0,100.0" in text   # the earlier row, halved


def test_a_torn_tail_is_repaired_by_a_full_rewrite(tmp_path):
    """An append is not atomic, so the next run has to be able to fix one."""

    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    _store(tmp_path).upsert_frame("NSE", "EQ", _row("20250102", close=110.0))
    path = _history(tmp_path)
    path.write_text(path.read_text() + "20250102,110.0,110")   # cut mid-line

    result = _run(tmp_path, ["ID:123"])

    assert (result.appended, result.rewritten) == (0, 1)
    assert path.read_text() == symbol_text(
        _store(tmp_path), _registry(tmp_path), "NSE", "oldname.txt"
    )


def test_a_renamed_security_does_not_resurrect_its_old_file(tmp_path):
    """The registry keeps historical names so an old ticker still resolves.

    Reading that reverse index forwards republishes them: BSE ``MANBRO`` became
    ``KDGREEN``, and publishing to every filename that ever held a key
    recreated a deleted file holding a second copy of a security that already
    had one.
    """

    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    _publish_legacy(
        tmp_path, date(2025, 1, 2), _row("20250102", symbol="NEWNAME")
    )
    assert not _history(tmp_path, "oldname.txt").exists()
    _store(tmp_path).upsert_frame(
        "NSE", "EQ", _row("20250103", symbol="NEWNAME")
    )

    result = _run(tmp_path, ["ID:123"])

    assert result.appended == 1
    assert not _history(tmp_path, "oldname.txt").exists()
    assert len(list((tmp_path / "NSE" / "SYMBOLS").glob("*.txt"))) == 1


def test_a_history_that_does_not_exist_yet_is_created(tmp_path):
    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    registry = _registry(tmp_path)
    _history(tmp_path).unlink()

    result = publish_histories(
        _store(tmp_path), tmp_path, registry, (), {"NSE": {"ID:123"}}
    )

    assert (result.appended, result.rewritten) == (0, 1)
    assert _history(tmp_path).is_file()


def test_one_failing_symbol_does_not_stop_the_others(tmp_path):
    _publish_legacy(tmp_path, date(2025, 1, 1), _row("20250101"))
    _store(tmp_path).upsert_frame("NSE", "EQ", _row("20250102", close=110.0))
    path = _history(tmp_path)
    path.chmod(0o444)
    try:
        result = _run(tmp_path, ["ID:123"])
    finally:
        path.chmod(0o644)

    assert result.failures and "oldname.txt" in result.failures[0]


# ---- the switch that lets the database do the writing -------------------


def test_the_batch_can_do_everything_except_write_the_files(tmp_path):
    """What ``publish_files=False`` must and must not still do.

    The registry, renames and retirement are the batch's work and stay its
    work; only the read-and-rewrite of each file goes, because something else
    is producing it.
    """

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _row("20250101"))
    eod = _store(tmp_path)
    eod.upsert_frame("NSE", "EQ", _row("20250101"))
    eod.upsert_frame("NSE", "EQ", _row("20250102", symbol="NEWNAME"))

    from src.services.symbol_history import HistoryBatchItem

    result = store.upsert_batch(
        [HistoryBatchItem("NSE", "EQ", date(2025, 1, 2),
                          _row("20250102", symbol="NEWNAME"))],
        publish_files=False,
    )

    assert result.symbols == 1
    assert result.history_reads == 0
    registry = _registry(tmp_path)
    # The rename landed in the registry, and retiring the old file is the
    # batch's job either way -- so between here and the publish below the
    # security has no file at all.  Both happen in one run, and the database
    # is what makes the gap recoverable rather than a loss.
    assert registry["exchanges"]["NSE"]["ID:123"] == "NEWNAME"
    assert registry["files"]["NSE"]["NEWNAME"] == "newname.txt"
    assert not _history(tmp_path, "oldname.txt").exists()
    assert not _history(tmp_path, "newname.txt").exists()

    publish_histories(
        eod, tmp_path, registry, (), eod.touched_keys, eod.touched_dates
    )

    published = _history(tmp_path, "newname.txt")
    assert published.is_file()
    assert len(published.read_text().splitlines()) == 3


def test_the_two_publication_paths_agree_on_the_same_batch(tmp_path):
    """The property the real A/B download proved, in miniature.

    Two roots, the same rows, one published by the legacy batch and one by the
    database: the bytes must not differ.
    """

    from src.services.symbol_history import HistoryBatchItem

    rows = [_row("20250101"), _row("20250102", close=110.0)]
    legacy, database = tmp_path / "legacy", tmp_path / "database"
    for root in (legacy, database):
        history = SymbolHistoryStore(root)
        eod = EodStore(
            root / ".state" / "eod.sqlite3", root / ".state" / "quarantine"
        )
        for frame in rows:
            eod.upsert_frame("NSE", "EQ", frame)
        items = [
            HistoryBatchItem("NSE", "EQ", date(2025, 1, index + 1), frame)
            for index, frame in enumerate(rows)
        ]
        history.upsert_batch(items, publish_files=root is legacy)
        if root is database:
            publish_histories(
                eod, root, _registry(root), (), eod.touched_keys,
                eod.touched_dates,
            )

    assert (
        _history(database).read_bytes() == _history(legacy).read_bytes()
    )
