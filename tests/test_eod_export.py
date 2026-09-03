"""Phase 5 step 2: text regenerated from the database, byte for byte.

Every frame here is built by running the **real** normalizer over a synthetic
source report rather than by hand.  A hand-built frame would carry whatever
dtypes the test chose, so the export would be compared against the test's own
assumptions instead of against what the publisher actually writes -- which is
the one thing this step exists to establish.
"""

from __future__ import annotations

from datetime import date
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.base_downloader import BaseDownloader
from src.services.canonical_data import (
    normalize_nse_equity,
    public_equity,
)
from src.services.eod_export import compare_daily, daily_text, segment_frame
from src.services.eod_store import EodStore
from src.services.pipeline_state import PipelineManifest

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

    def resolve_data_path(self, exchange, segment):
        return self.base_data_path / exchange / segment


class _Publisher(BaseDownloader):
    """The real publication path, with the network and the GUI removed."""

    def __init__(self, base_path, exchange="NSE", segment="EQ"):
        self.exchange = exchange
        self.segment = segment
        self.exchange_segment = f"{exchange}_{segment}"
        self.config = _Config(Path(base_path))
        self.logger = logging.getLogger("test.export")
        self.data_path = self.config.get_data_path(exchange, segment)
        self.exchange_config = SimpleNamespace(
            file_suffix=f"-{exchange}-{segment}"
        )
        self.combined_required = False
        self.pipeline_manifest = PipelineManifest(base_path)

    def build_url(self, target_date):
        return "https://example.test/report.csv"

    def process_downloaded_data(self, file_data, file_date):
        return None

    def transform_data(self, df, file_date):
        return df

    async def _download_implementation(self, working_days):
        return True

    def get_download_option(self, name, default=None):
        return {
            "dual_write_eod_database": True,
            "generate_symbol_files": False,
        }.get(name, default)

    def published(self, target_date=DAY) -> Path:
        return self.data_path / self.build_filename(target_date)

    def store(self) -> EodStore:
        state = self.config.base_data_path / ".state"
        return EodStore(state / "eod.sqlite3", state / "quarantine")


def _source(rows, *, volumes=None, target_date=DAY):
    """A legacy NSE bhavcopy, the shape the real normalizer expects."""

    volumes = volumes or [100] * len(rows)
    stamp = target_date.strftime("%d-%b-%Y").upper()
    return pd.DataFrame([
        {
            "SYMBOL": symbol, "SERIES": "EQ",
            "OPEN": open_, "HIGH": high, "LOW": low, "CLOSE": close,
            "TOTTRDQTY": volume, "TOTALTRADES": 10,
            "ISIN": f"INE00000{index:04d}",
            "TIMESTAMP": stamp,
            "TOTTRDVAL": close * volume, "PREVCLOSE": close,
        }
        for index, ((symbol, open_, high, low, close), volume)
        in enumerate(zip(rows, volumes))
    ])


def _publish(publisher, frame, target_date=DAY):
    publisher._begin_pipeline_date(target_date)
    publisher.save_processed_data(frame, target_date)
    return publisher.published(target_date)


_ROWS = [
    ("ABC", 10.0, 12.0, 9.0, 11.0),
    ("DEF", 20.5, 21.0, 19.5, 20.0),
    ("GHI", 5.25, 5.5, 5.0, 5.4),
]


def test_a_published_equity_day_regenerates_byte_for_byte(tmp_path):
    publisher = _Publisher(tmp_path)
    frame = public_equity(normalize_nse_equity(_source(_ROWS), DAY))

    published = _publish(publisher, frame)

    assert compare_daily(publisher.store(), published, "NSE", DAY) is None
    assert daily_text(publisher.store(), "NSE", DAY) == published.read_text()


def test_a_whole_number_column_does_not_grow_a_decimal_point(tmp_path):
    """The failure this guards is one character wide and breaks every line."""

    publisher = _Publisher(tmp_path)
    frame = public_equity(normalize_nse_equity(
        _source(_ROWS, volumes=[100, 250, 375]), DAY
    ))

    published = _publish(publisher, frame)

    assert ",100," in published.read_text()
    assert ",100.0," not in published.read_text()
    assert compare_daily(publisher.store(), published, "NSE", DAY) is None


def test_a_column_with_one_missing_value_keeps_its_decimal_point(tmp_path):
    """One unmatched delivery row turns the whole column into floats.

    That is pandas' rule, not a choice this project makes, and an exporter
    that decides dtype per value rather than per column writes ``50`` where
    the publisher wrote ``50.0`` on every other row of the file.
    """

    publisher = _Publisher(tmp_path)
    frame = public_equity(normalize_nse_equity(_source(_ROWS), DAY))
    frame.loc[1, "DELIVERY_QTY"] = 50.0
    frame.loc[0, "DELIVERY_QTY"] = float("nan")

    published = _publish(publisher, frame)

    assert ",50.0," in published.read_text()
    assert compare_daily(publisher.store(), published, "NSE", DAY) is None


def test_index_rows_keep_the_order_the_exchange_published_them_in(tmp_path):
    """NSE publishes Nifty 50, then Nifty Next 50, then Nifty 100.

    No sort of the stored columns reproduces that, which is why the row's
    position in the published frame is stored.
    """

    publisher = _Publisher(tmp_path, segment="INDEX")
    frame = pd.DataFrame({
        "SYMBOL": ["Nifty 50", "Nifty Next 50", "Nifty 100"],
        "DATE": ["20260731"] * 3,
        "OPEN": [23897.65, 71792.9, 24953.2],
        "HIGH": [24049.9, 72166.85, 25105.95],
        "LOW": [23895.1, 71590.6, 24940.3],
        "CLOSE": [24005.85, 72100.65, 25065.1],
        "VOLUME": [352148975.0, 262723262.0, 614872237.0],
        "TURNOVER": [2.776e11, 1.141e11, 3.917e11],
        "PREV_CLOSE": [float("nan")] * 3,
    })

    published = _publish(publisher, frame)

    exported = segment_frame(publisher.store(), "NSE", "INDEX", DAY)
    assert list(exported["SYMBOL"]) == ["Nifty 50", "Nifty Next 50", "Nifty 100"]
    assert compare_daily(
        publisher.store(), published, "NSE", DAY, segment="INDEX"
    ) is None


def test_a_futures_day_regenerates_byte_for_byte(tmp_path):
    publisher = _Publisher(tmp_path, segment="FO")
    frame = pd.DataFrame({
        "SYMBOL": ["360ONE-I", "360ONE-II", "360ONE-III"],
        "DATE": ["20260731"] * 3,
        "OPEN": [1087.4, 1089.7, 0.0],
        "HIGH": [1089.9, 1091.2, 0.0],
        "LOW": [1067.1, 1075.1, 0.0],
        "CLOSE": [1076.0, 1080.7, 1091.3],
        "VOLUME": [3751, 68, 0],
        "OPEN_INTEREST": [7353500, 26000, 0],
        "CHANGE_IN_OI": [1071500, 10500, 0],
        "TURNOVER": [2020258700.0, 36817000.0, 0.0],
        "PREV_CLOSE": [1082.8, 1087.4, 1091.3],
    })

    published = _publish(publisher, frame)

    assert compare_daily(
        publisher.store(), published, "NSE", DAY, segment="FO"
    ) is None


def test_a_mismatch_is_reported_with_the_line_that_differs(tmp_path):
    publisher = _Publisher(tmp_path)
    frame = public_equity(normalize_nse_equity(_source(_ROWS), DAY))
    published = _publish(publisher, frame)
    published.write_text(
        published.read_text().replace("ABC,20260731,10.0", "ABC,20260731,10.5")
    )

    report = compare_daily(publisher.store(), published, "NSE", DAY)

    assert report is not None
    assert "published: ABC,20260731,10.5" in report
    assert "exported : ABC,20260731,10.0" in report


def test_a_row_count_difference_is_reported_as_such(tmp_path):
    publisher = _Publisher(tmp_path)
    frame = public_equity(normalize_nse_equity(_source(_ROWS), DAY))
    published = _publish(publisher, frame)
    published.write_text(
        "\n".join(published.read_text().splitlines()[:2]) + "\n"
    )

    report = compare_daily(publisher.store(), published, "NSE", DAY)

    assert report is not None and "published 2 rows, exported 3" in report


def test_the_publishers_dtypes_are_recorded_not_guessed(tmp_path):
    """A gapless whole-number column is int64 in equity and float64 in index.

    Both are correct and the values are identical, so nothing about the stored
    numbers distinguishes them.  Only the recorded signature does.
    """

    equity = _Publisher(tmp_path / "eq")
    _publish(equity, public_equity(normalize_nse_equity(_source(_ROWS), DAY)))
    index = _Publisher(tmp_path / "ix", segment="INDEX")
    _publish(index, pd.DataFrame({
        "SYMBOL": ["Nifty 50"], "DATE": ["20260731"],
        "OPEN": [1.0], "HIGH": [2.0], "LOW": [0.5], "CLOSE": [1.5],
        "VOLUME": [100.0], "TURNOVER": [150.0], "PREV_CLOSE": [1.0],
    }))

    equity_signature = equity.store().published_frame("NSE", "EQ", 20260731)
    index_signature = index.store().published_frame("NSE", "INDEX", 20260731)

    assert dict(zip(
        equity_signature["columns"], equity_signature["dtypes"]
    ))["VOLUME"] == "int64"
    assert dict(zip(
        index_signature["columns"], index_signature["dtypes"]
    ))["VOLUME"] == "float64"


def test_a_combined_file_regenerates_from_its_components(tmp_path):
    """The hardest case: three segments concatenated into one NSE EQ file.

    ``pd.concat`` decides the result's dtypes, and an index component that has
    no delivery columns widens the equity ones to float.  Reproducing the file
    therefore means reproducing the concatenation, which is why the exporter
    rebuilds each component and concatenates rather than reading one table.
    """

    from src.services.combined_file_builder import CombinedFileBuilder

    publisher = _Publisher(tmp_path)
    equity = public_equity(normalize_nse_equity(_source(_ROWS), DAY))
    sme = public_equity(normalize_nse_equity(
        _source([("JKL", 3.0, 3.5, 2.9, 3.2)]), DAY
    ))
    index = pd.DataFrame({
        "SYMBOL": ["Nifty 50"], "DATE": ["20260731"],
        "OPEN": [23897.65], "HIGH": [24049.9], "LOW": [23895.1],
        "CLOSE": [24005.85], "VOLUME": [352148975.0],
        "TURNOVER": [2.776e11], "PREV_CLOSE": [float("nan")],
    })

    for segment, frame in (("EQ", equity), ("SME", sme), ("INDEX", index)):
        component = _Publisher(tmp_path, segment=segment)
        component._begin_pipeline_date(DAY)
        component.save_processed_data(frame, DAY)

    builder = CombinedFileBuilder(publisher.config)
    # Through ``lexical_frame``, because that is what ``DateJoinCoordinator``
    # does to every frame before the builder sees it.  Handing numeric frames
    # straight to ``reconcile_frames`` exercises a path the application never
    # takes, and it agreed with a numeric export while the first real combined
    # file did not.
    result = builder.reconcile_frames(
        "NSE", DAY, ("SME", "INDEX"),
        {
            segment: builder.lexical_frame(frame)
            for segment, frame in (
                ("EQ", equity), ("SME", sme), ("INDEX", index)
            )
        },
    )
    assert result.ok, result.error

    published = publisher.data_path / f"{DAY}-NSE-EQ.txt"
    assert compare_daily(
        publisher.store(), published, "NSE", DAY,
        appended=("SME", "INDEX"),
    ) is None
    assert len(published.read_text().splitlines()) == 5
    # The property the real download exposed: a whole-number column keeps its
    # integer spelling through the concatenation, because the components are
    # concatenated as text.
    assert ",100," in published.read_text()
    assert ",100.0," not in published.read_text()


# ---- the verification pass ---------------------------------------------


def test_a_clean_data_root_reports_every_file_regenerated(tmp_path):
    from src.services.eod_export import verify_parity

    publisher = _Publisher(tmp_path)
    for day in (date(2026, 7, 30), DAY):
        _publish(publisher, public_equity(
            normalize_nse_equity(_source(_ROWS, target_date=day), day)
        ), day)

    report = verify_parity(publisher.config)

    assert report.checked == 2
    assert report.mismatches == ()
    assert "regenerated byte for byte" in report.render()


def test_a_corrupted_file_is_named_with_the_line_that_differs(tmp_path):
    from src.services.eod_export import verify_parity

    publisher = _Publisher(tmp_path)
    published = _publish(publisher, public_equity(
        normalize_nse_equity(_source(_ROWS), DAY)
    ))
    published.write_text(published.read_text().replace("11.0", "11.5"))

    report = verify_parity(publisher.config)

    assert len(report.mismatches) == 1
    assert "2026-07-31-NSE-EQ.txt" in report.render()


def test_files_written_before_the_mirror_existed_are_not_called_failures(
    tmp_path
):
    """Dual-write fills forward.  Older files have nothing to compare with."""

    from src.services.eod_export import verify_parity

    publisher = _Publisher(tmp_path)
    folder = publisher.config.get_data_path("NSE", "EQ")
    (folder / "2026-07-01-NSE-EQ.txt").write_text("OLD,20260701,1,1,1,1,1,1,1,1,1\n")

    report = verify_parity(publisher.config)

    assert report.checked == 0
    assert report.mismatches == ()
    assert report.unmirrored == ("2026-07-01-NSE-EQ.txt",)
    assert "predate the database" in report.render()
    # Having compared nothing must not print like having compared everything.
    assert "Nothing was compared" in report.render()


def test_the_parity_pass_changes_nothing_it_looks_at(tmp_path):
    """A report is only evidence about the tree if producing it left it alone."""

    from src.services.eod_export import verify_parity

    publisher = _Publisher(tmp_path)
    _publish(publisher, public_equity(normalize_nse_equity(_source(_ROWS), DAY)))
    before = {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(tmp_path.rglob("*")) if path.is_file()
    }

    assert verify_parity(publisher.config).mismatches == ()

    after = {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(tmp_path.rglob("*")) if path.is_file()
    }
    assert after == before


def test_the_cli_flag_is_read_only_and_excludes_the_repairs():
    """It sits in the repair group so it cannot run alongside a rewrite."""

    import main as entrypoint

    parser = entrypoint.setup_argument_parser()
    assert parser.parse_args(["--verify-eod-parity"]).verify_eod_parity == []
    assert parser.parse_args(
        ["--verify-eod-parity", "NSE_EQ"]
    ).verify_eod_parity == ["NSE_EQ"]
    assert parser.parse_args([]).verify_eod_parity is None

    with pytest.raises(SystemExit):
        parser.parse_args(["--verify-eod-parity", "--rebuild-all"])


def test_the_parity_pass_does_not_create_the_database_it_reports_on(tmp_path):
    """The defect this pins shipped once already, in a different command.

    Constructing an ``EodStore`` creates the file.  A pass that only reports
    would therefore bring into existence the very thing it was asked to report
    on, and on an empty data root that is the entire answer -- silently
    replaced by an empty success.  The earlier fingerprint test could not catch
    it, because in that test the database already existed.
    """

    from src.services.eod_export import verify_parity

    root = tmp_path / "root"
    (root / "NSE" / "EQ").mkdir(parents=True)
    (root / ".state").mkdir()
    config = _Config(root)
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    report = verify_parity(config)

    assert report.checked == 0 and report.mismatches == ()
    assert not (root / ".state" / "eod.sqlite3").exists()
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_reading_the_database_leaves_no_shm_or_wal_behind(tmp_path):
    """``mode=ro`` is not read-only; a read-only connection still stamps -shm."""

    from src.services.eod_export import verify_parity

    publisher = _Publisher(tmp_path)
    _publish(publisher, public_equity(normalize_nse_equity(_source(_ROWS), DAY)))
    state = tmp_path / ".state"
    for stale in state.glob("eod.sqlite3-*"):
        stale.unlink()
    before = {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(state.rglob("*")) if path.is_file()
    }

    assert verify_parity(publisher.config).mismatches == ()

    assert not (state / "eod.sqlite3-shm").exists()
    after = {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(state.rglob("*")) if path.is_file()
    }
    assert after == before
