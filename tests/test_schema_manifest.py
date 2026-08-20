"""The marker that says what a headerless folder's columns mean.

`validate_daily_output` accepts seven, nine and eleven columns, so "valid"
alone never said which generation a file was -- whether column nine held a
delivery quantity or a turnover.  A header row would have answered that and
broken every positional reader; this answers it beside the data instead.
"""

from __future__ import annotations

import json


from src.core.config import Config
from src.core.data_manager import DataManager
from src.services.canonical_data import (
    EQUITY_DAILY_COLUMNS,
    FO_DAILY_COLUMNS,
    INDEX_DAILY_COLUMNS,
)
from src.services.schema_manifest import (
    SCHEMA_FILENAME,
    daily_columns_for,
    generations_for,
    read_manifest,
    write_manifest,
)


def test_every_published_width_is_named(tmp_path):
    """A reader counts a row's columns and looks the width up here."""

    for segment, columns in (
        ("EQ", EQUITY_DAILY_COLUMNS),
        ("SME", EQUITY_DAILY_COLUMNS),
        ("FO", FO_DAILY_COLUMNS),
        ("INDEX", INDEX_DAILY_COLUMNS),
    ):
        generations = generations_for(segment)
        widths = sorted(len(names) for names in generations.values())
        assert widths == ([7, 9, 11] if len(columns) == 11 else [7, 9])
        assert generations[max(generations)] == columns
        assert daily_columns_for(segment) == columns


def test_the_marker_is_written_and_reads_back(tmp_path):
    folder = tmp_path / "NSE" / "EQ"
    folder.mkdir(parents=True)

    written = write_manifest(folder, "NSE", "EQ")

    assert written == folder / SCHEMA_FILENAME
    manifest = read_manifest(folder)
    assert manifest["exchange"] == "NSE"
    assert manifest["segment"] == "EQ"
    assert manifest["columns"] == EQUITY_DAILY_COLUMNS
    assert manifest["generations"]["2"][-1] == "DELIVERY_PERCENT"
    assert manifest["generations"]["3"][-1] == "PREV_CLOSE"


def test_an_unchanged_marker_is_not_rewritten(tmp_path):
    """Otherwise every launch would restamp a file in the data folder."""

    folder = tmp_path / "NSE" / "EQ"
    folder.mkdir(parents=True)
    write_manifest(folder, "NSE", "EQ")
    path = folder / SCHEMA_FILENAME
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    assert write_manifest(folder, "NSE", "EQ") is None
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_a_damaged_marker_is_replaced_rather_than_trusted(tmp_path):
    folder = tmp_path / "NSE" / "EQ"
    folder.mkdir(parents=True)
    (folder / SCHEMA_FILENAME).write_text("{ not json", encoding="utf-8")

    assert read_manifest(folder) is None
    assert write_manifest(folder, "NSE", "EQ") is not None
    assert read_manifest(folder)["columns"] == EQUITY_DAILY_COLUMNS


def test_a_missing_folder_is_not_created_to_hold_a_marker(tmp_path):
    assert write_manifest(tmp_path / "absent", "NSE", "EQ") is None
    assert not (tmp_path / "absent").exists()


def test_no_temporary_file_is_left_behind(tmp_path):
    folder = tmp_path / "NSE" / "EQ"
    folder.mkdir(parents=True)

    write_manifest(folder, "NSE", "EQ")

    assert [path.name for path in folder.iterdir()] == [SCHEMA_FILENAME]


def test_preparing_the_folders_marks_every_segment(tmp_path):
    config = Config("config.yaml")

    DataManager(config)

    for exchange_segment in config.get_available_exchanges():
        exchange, segment = exchange_segment.split("_", 1)
        folder = config.resolve_data_path(exchange, segment)
        manifest = read_manifest(folder)
        assert manifest is not None, exchange_segment
        assert manifest["segment"] == segment
        assert manifest["columns"] == daily_columns_for(segment)


def test_the_marker_records_which_build_wrote_it(tmp_path):
    from version import get_version

    folder = tmp_path / "BSE" / "INDEX"
    folder.mkdir(parents=True)
    write_manifest(folder, "BSE", "INDEX")

    manifest = read_manifest(folder)
    assert manifest["written_by"] == get_version()
    assert manifest["current_generation"] == 2
    assert json.loads((folder / SCHEMA_FILENAME).read_text())["note"]
