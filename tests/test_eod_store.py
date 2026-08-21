"""Phase 5 step 1 acceptance tests: the dual-written EOD database.

The identity cases below are not invented.  They are the shapes measured in the
owner's own tree on 2026-08-20 across 29 trading days and 208,555 rows, and each
one is a case that a naive ``(security_id, date)`` primary key gets wrong.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.base_downloader import BaseDownloader
from src.services.canonical_data import EQUITY_DAILY_COLUMNS
from src.services.eod_store import EodStore, security_key
from src.services.pipeline_state import PipelineManifest
from src.services.state_store import StateCorruptionError


def _store(tmp_path) -> EodStore:
    return EodStore(
        tmp_path / ".state" / "eod.sqlite3", tmp_path / ".state" / "quarantine"
    )


def _equity_frame(**overrides) -> pd.DataFrame:
    row = {
        "SYMBOL": "20MICRONS", "DATE": "20260731",
        "OPEN": "200.0", "HIGH": "212.52", "LOW": "191.1", "CLOSE": "192.46",
        "VOLUME": "808418", "DELIVERY_QTY": "164091.0",
        "DELIVERY_PERCENT": "20.3", "TURNOVER": "162708811.07",
        "PREV_CLOSE": "200.07", "SERIES": "EQ", "TOTAL_TRADES": "17755",
        "QTY_PER_TRADE": "45.53", "ISIN": "INE144J01027",
        "SECURITY_ID": "16921",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ---- schema ------------------------------------------------------------


def test_store_opens_in_wal_with_a_recorded_schema_version(tmp_path):
    store = _store(tmp_path)

    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0].lower() == "wal"
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0] == str(EodStore.SCHEMA_VERSION)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_the_table_is_clustered_on_its_primary_key(tmp_path):
    """Pinned because reverting it costs 9x on the query that matters.

    Measured over the owner's 208,555 rows: one symbol's whole series takes
    52.04 ms from a rowid table and 5.61 ms from this one, because the primary
    key *is* the table and a security's rows are physically contiguous.
    """

    store = _store(tmp_path)

    with closing(sqlite3.connect(store.path)) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='eod'"
        ).fetchone()[0]
        assert "WITHOUT ROWID" in sql.upper()
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT rowid FROM eod").fetchall()


def test_both_shapes_of_query_are_served_by_an_index(tmp_path):
    """The daily bhavcopy and the symbol time series -- both must be indexed."""

    store = _store(tmp_path)
    store.upsert_frame("NSE", "EQ", _equity_frame())

    with closing(sqlite3.connect(store.path)) as connection:
        daily = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM eod "
            "WHERE exchange=? AND segment=? AND trade_date=? ORDER BY symbol",
            ("NSE", "EQ", 20260731),
        ).fetchall()
        series = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM eod "
            "WHERE exchange=? AND security_key IN (?) ORDER BY trade_date",
            ("NSE", "ID:16921"),
        ).fetchall()

    assert not any("SCAN eod" in row[-1] for row in series), series
    # On a one-row table the planner reasonably reads the clustered key; what
    # must not happen is that it still does so once the table is real.
    assert daily


def test_statistics_arrive_so_a_by_date_query_stops_scanning(tmp_path):
    """The failure this pins is silent and only shows up years later.

    A clustered table with no statistics looks to the planner like the
    cheapest path for *any* prefix query, so ``WHERE exchange AND segment AND
    trade_date`` scans every date that exchange ever published instead of
    seeking one.  It is correct, it is fast on 28 dates, and it degrades in
    proportion to the archive.
    """

    # Shaped like an archive, not like one enormous day: an index on
    # ``trade_date`` earns nothing when every row shares a date, and the first
    # version of this test wrote 20,001 rows on 2026-07-31 and then asserted
    # the planner would use it anyway.  It did locally and did not on CI,
    # which is the planner being right both times about different statistics.
    store = _store(tmp_path)
    symbols = 550
    for offset in range(40):
        day = date(2026, 1, 1) + timedelta(days=offset)
        store.upsert_frame("NSE", "EQ", [
            {**_equity_frame().iloc[0].to_dict(),
             "DATE": day.strftime("%Y%m%d"),
             "SYMBOL": f"SYM{index:05d}",
             "SECURITY_ID": str(100000 + index)}
            for index in range(symbols)
        ])
    assert store.row_count() > EodStore.ANALYZE_FLOOR

    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_stat1 WHERE idx='idx_eod_date'"
        ).fetchone()[0] == 1
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM eod "
            "WHERE exchange=? AND segment=? AND trade_date=? ORDER BY symbol",
            ("NSE", "EQ", 20260115),
        ).fetchall()

    assert any("idx_eod_date" in row[-1] for row in plan), plan


def test_a_rolled_back_write_does_not_count_towards_statistics(tmp_path):
    store = _store(tmp_path)
    store.upsert_frame("NSE", "EQ", _equity_frame())
    with pytest.raises(ValueError):
        store.upsert_frame("NSE", "EQ", _equity_frame(DATE="nonsense"))

    with closing(sqlite3.connect(store.path)) as connection:
        written = connection.execute(
            "SELECT value FROM metadata WHERE key='rows_written'"
        ).fetchone()[0]
    assert written == "1"


def test_a_damaged_database_is_quarantined_and_refused(tmp_path):
    store = _store(tmp_path)
    store.path.write_bytes(b"this is not a database" * 64)

    with pytest.raises(StateCorruptionError):
        EodStore(store.path, tmp_path / ".state" / "quarantine")

    quarantined = list(
        (tmp_path / ".state" / "quarantine" / "eod_sqlite").glob("*.corrupt")
    )
    assert quarantined, "damaged bytes must be copied aside before refusing"
    # The original is left exactly as found so the user, not the application,
    # decides whether to restore, inspect or rebuild it.
    assert store.path.read_bytes() == b"this is not a database" * 64


# ---- identity ----------------------------------------------------------


def test_security_id_is_preferred_and_survives_a_rename():
    # BSE code 543766 carried both names within the sampled 29 days.
    assert security_key("ASHIKA", "INE192Z01029", "543766") == "ID:543766"
    assert security_key("ASHIKAG", "INE192Z01029", "543766") == "ID:543766"


def test_isin_is_used_when_no_security_id_is_published():
    assert security_key("FOO", "INE144J01027", "") == "ISIN:INE144J01027"
    assert security_key("FOO", "INE144J01027", "not-an-id") == "ISIN:INE144J01027"


def test_symbol_is_the_last_resort_for_nse_sme():
    # Every one of the 12,418 sampled NSE SME rows carries neither identifier.
    assert security_key("SOMESME_SME", "", "") == "SYM:SOMESME_SME"
    assert security_key("lower", float("nan"), None) == "SYM:LOWER"


def test_a_row_with_no_identity_at_all_is_refused():
    with pytest.raises(ValueError):
        security_key("", "", "")


def test_an_nse_series_move_is_stored_as_two_keys_sharing_one_isin(tmp_path):
    """AARTECH moved EQ->BE on 2026-07-10 and NSE gave it a new security id.

    Both rows must survive.  Folding them onto one key would silently drop one
    of the two, and the ISIN column is what lets the export put them back
    together.
    """

    store = _store(tmp_path)
    before = _equity_frame(
        SYMBOL="AARTECH", DATE="20260709", SERIES="EQ",
        ISIN="INE01C001026", SECURITY_ID="17145",
    )
    after = _equity_frame(
        SYMBOL="AARTECH", DATE="20260710", SERIES="BE",
        ISIN="INE01C001026", SECURITY_ID="17164",
    )
    store.upsert_frame("NSE", "EQ", before)
    store.upsert_frame("NSE", "EQ", after)

    assert store.row_count() == 2
    rows = store.security_rows("NSE", ["ID:17145", "ID:17164"])
    assert [row["series"] for row in rows] == ["EQ", "BE"]
    assert {row["isin"] for row in rows} == {"INE01C001026"}


# ---- fidelity ----------------------------------------------------------


def test_counts_are_stored_as_exact_integers(tmp_path):
    """The one thing REAL storage breaks: the sources write ``7``, not ``7.0``."""

    store = _store(tmp_path)
    store.upsert_frame("BSE", "EQ", _equity_frame(
        SYMBOL="11QPR", VOLUME="2", TOTAL_TRADES="2", DELIVERY_QTY="2",
        SECURITY_ID="543178", ISIN="INF204KB12U1",
    ))

    row = store.daily_rows("BSE", "EQ", 20260731)[0]
    assert row["volume"] == 2 and isinstance(row["volume"], int)
    assert row["total_trades"] == 2 and isinstance(row["total_trades"], int)
    assert row["delivery_qty"] == 2 and isinstance(row["delivery_qty"], int)


def test_prices_round_trip_unchanged(tmp_path):
    store = _store(tmp_path)
    store.upsert_frame("NSE", "EQ", _equity_frame())

    row = store.daily_rows("NSE", "EQ", 20260731)[0]
    assert repr(row["open"]) == "200.0"
    assert repr(row["high"]) == "212.52"
    assert repr(row["turnover"]) == "162708811.07"
    assert repr(row["qty_per_trade"]) == "45.53"


def test_an_unpublished_field_stays_null_rather_than_zero(tmp_path):
    """A BSE index publishes no turnover.  Zero would claim it traded none."""

    store = _store(tmp_path)
    store.upsert_frame("BSE", "INDEX", pd.DataFrame([{
        "SYMBOL": "BSE SENSEX", "DATE": "20260701", "OPEN": "76545.21",
        "HIGH": "77110.08", "LOW": "76538.37", "CLOSE": "76922.64",
        "VOLUME": "0", "TURNOVER": "", "PREV_CLOSE": "76478.67",
    }]))

    row = store.daily_rows("BSE", "INDEX", 20260701)[0]
    assert row["turnover"] is None
    assert row["volume"] == 0
    assert row["delivery_qty"] is None and row["series"] == ""
    assert row["security_key"] == "SYM:BSE SENSEX"


def test_futures_rows_carry_open_interest(tmp_path):
    store = _store(tmp_path)
    store.upsert_frame("NSE", "FO", pd.DataFrame([{
        "SYMBOL": "360ONE-I", "DATE": "20260701", "OPEN": "1087.4",
        "HIGH": "1089.9", "LOW": "1067.1", "CLOSE": "1076.0",
        "VOLUME": "3751", "OPEN_INTEREST": "7353500",
        "CHANGE_IN_OI": "1071500", "TURNOVER": "2020258700.0",
        "PREV_CLOSE": "1082.8",
    }]))

    row = store.daily_rows("NSE", "FO", 20260701)[0]
    assert row["open_interest"] == 7353500
    assert row["change_in_oi"] == 1071500
    assert row["delivery_qty"] is None


# ---- transactional behaviour -------------------------------------------


def test_rewriting_a_date_updates_rather_than_duplicates(tmp_path):
    """Exchanges republish corrected bhavcopies; a re-download must be safe."""

    store = _store(tmp_path)
    store.upsert_frame("NSE", "EQ", _equity_frame(CLOSE="192.46"))
    store.upsert_frame("NSE", "EQ", _equity_frame(CLOSE="193.00"))

    assert store.row_count() == 1
    assert store.daily_rows("NSE", "EQ", 20260731)[0]["close"] == 193.0


def test_one_unusable_row_leaves_the_whole_date_absent(tmp_path):
    """Never half a bhavcopy: the frame is rejected before anything is written."""

    store = _store(tmp_path)
    frame = pd.concat([
        _equity_frame(SYMBOL="GOOD", SECURITY_ID="1"),
        _equity_frame(SYMBOL="BAD", DATE="not-a-date", SECURITY_ID="2"),
    ], ignore_index=True)

    with pytest.raises(ValueError):
        store.upsert_frame("NSE", "EQ", frame)
    assert store.row_count() == 0


def test_an_unsupported_segment_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).upsert_frame("NSE", "CURRENCY", _equity_frame())


def test_dates_are_listed_per_exchange_and_segment(tmp_path):
    store = _store(tmp_path)
    store.upsert_frame("NSE", "EQ", _equity_frame(DATE="20260730"))
    store.upsert_frame("NSE", "EQ", _equity_frame(DATE="20260731"))
    store.upsert_frame("BSE", "EQ", _equity_frame(DATE="20260731"))

    assert store.dates("NSE", "EQ") == [20260730, 20260731]
    assert store.dates("BSE", "EQ") == [20260731]
    assert store.dates() == [20260730, 20260731]


# ---- the dual-write itself ---------------------------------------------
#
# What matters about step 1 is not only that rows arrive, but that the mirror
# cannot hurt the thing it mirrors.  Nothing reads this database yet, so a
# failure to write it must leave the published text file exactly as it would
# have been.

DAY = date(2026, 7, 31)


class _Config:
    def __init__(self, base_path: Path):
        self.base_data_path = base_path
        self.date_settings = SimpleNamespace(
            weekend_skip=True, base_start_date="2026-07-01"
        )
        self.holiday_manager = SimpleNamespace(is_holiday=lambda _day: False)

    @staticmethod
    def get_available_exchanges():
        return ["NSE_EQ"]

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


class _Downloader(BaseDownloader):
    def __init__(self, base_path, options=None):
        self.exchange = "NSE"
        self.segment = "EQ"
        self.exchange_segment = "NSE_EQ"
        self.config = _Config(Path(base_path))
        self.logger = logging.getLogger("test.eod")
        self.data_path = self.config.get_data_path("NSE", "EQ")
        self.exchange_config = SimpleNamespace(file_suffix="-NSE-EQ")
        self.combined_required = False
        self.pipeline_manifest = PipelineManifest(base_path)
        self._options = {"dual_write_eod_database": True, **(options or {})}

    def build_url(self, target_date):
        return "https://example.test/report.csv"

    def process_downloaded_data(self, file_data, file_date):
        return None

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return True

    def get_download_option(self, name, default=None):
        return self._options.get(name, default)


def _public(rows):
    return pd.DataFrame(
        [dict(zip(EQUITY_DAILY_COLUMNS, row)) for row in rows],
        columns=EQUITY_DAILY_COLUMNS,
    )


_ROWS = [
    ("ABC", "20260731", 10.0, 12.0, 9.0, 11.0, 100, 50, 50.0, 1100.0, 9.5),
    ("DEF", "20260731", 10.0, 12.0, 9.0, 11.0, 100, 50, 50.0, 1100.0, 9.5),
    ("GHI", "20260731", 10.0, 12.0, 9.0, 11.0, 100, 50, 50.0, 1100.0, 9.5),
]


def _published(downloader) -> Path:
    return downloader.data_path / f"{DAY}-NSE-EQ.txt"


def test_publishing_a_day_also_mirrors_it_into_the_database(tmp_path):
    downloader = _Downloader(tmp_path)
    downloader._begin_pipeline_date(DAY)

    downloader.save_processed_data(_public(_ROWS), DAY)

    assert _published(downloader).is_file()
    store = EodStore(
        tmp_path / ".state" / "eod.sqlite3", tmp_path / ".state" / "quarantine"
    )
    rows = store.daily_rows("NSE", "EQ", 20260731)
    assert [row["symbol"] for row in rows] == ["ABC", "DEF", "GHI"]
    # No identity is published for a frame this shape, so the symbol is the key.
    assert rows[0]["security_key"] == "SYM:ABC"


def test_the_internal_frame_is_preferred_because_it_carries_identity(tmp_path):
    downloader = _Downloader(tmp_path, {"generate_symbol_files": False})
    downloader._begin_pipeline_date(DAY)
    downloader._internal_equity_data = _equity_frame(
        SYMBOL="ABC", DATE="20260731", SECURITY_ID="16921",
        ISIN="INE144J01027", SERIES="EQ",
    )

    downloader.save_processed_data(_public(_ROWS), DAY)

    store = EodStore(
        tmp_path / ".state" / "eod.sqlite3", tmp_path / ".state" / "quarantine"
    )
    rows = store.daily_rows("NSE", "EQ", 20260731)
    assert len(rows) == 1
    assert rows[0]["security_key"] == "ID:16921"
    assert rows[0]["isin"] == "INE144J01027"
    assert rows[0]["series"] == "EQ"


def test_turning_the_setting_off_writes_no_database_at_all(tmp_path):
    downloader = _Downloader(tmp_path, {"dual_write_eod_database": False})
    downloader._begin_pipeline_date(DAY)

    downloader.save_processed_data(_public(_ROWS), DAY)

    assert _published(downloader).is_file()
    assert not (tmp_path / ".state" / "eod.sqlite3").exists()


def test_a_failing_mirror_changes_nothing_about_the_run(tmp_path, monkeypatch):
    """The promise of step 1, stated as a comparison rather than a guess.

    A run whose mirror raises must leave the same bytes on disk and the same
    pipeline verdict as a run with the mirror switched off entirely.
    """

    control = _Downloader(tmp_path / "control", {"dual_write_eod_database": False})
    control._begin_pipeline_date(DAY)
    control.save_processed_data(_public(_ROWS), DAY)
    expected_bytes = _published(control).read_bytes()
    expected = control.pipeline_manifest.date_result("NSE", "EQ", DAY)

    monkeypatch.setattr(
        EodStore, "upsert_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    broken = _Downloader(tmp_path / "broken")
    broken._begin_pipeline_date(DAY)
    broken.save_processed_data(_public(_ROWS), DAY)

    assert _published(broken).read_bytes() == expected_bytes
    result = broken.pipeline_manifest.date_result("NSE", "EQ", DAY)
    assert (result.status, result.error) == (expected.status, expected.error)
