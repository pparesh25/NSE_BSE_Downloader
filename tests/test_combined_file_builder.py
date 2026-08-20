from datetime import date
from itertools import permutations
from pathlib import Path

import pandas as pd

from src.services.canonical_data import (
    EQUITY_DAILY_COLUMNS,
    INDEX_DAILY_COLUMNS,
)
from src.services.combined_file_builder import CombinedFileBuilder


class _Config:
    def __init__(self, base_data_path: Path):
        self.base_data_path = base_data_path

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


DAY = date(2026, 7, 31)


def _equity(symbol, volume=100):
    return pd.DataFrame([[
        symbol, "20260731", 10, 12, 9, 11, volume, 50, 50, 1100, 10,
    ]], columns=EQUITY_DAILY_COLUMNS)


def _index(symbol):
    return pd.DataFrame([[
        symbol, "20260731", 100, 120, 90, 110, 0, 5000, 99,
    ]], columns=INDEX_DAILY_COLUMNS)


def _save_components(builder, exchange, order):
    frames = {
        "EQ": _equity(f"{exchange}_EQ"),
        "SME": _equity("NSE_SME", 25),
        "INDEX": _index(f"{exchange}_INDEX"),
    }
    for segment in order:
        builder.save_component(exchange, segment, DAY, frames[segment])


def test_nse_all_arrival_permutations_are_byte_identical(tmp_path):
    outputs = []
    for position, order in enumerate(permutations(("EQ", "SME", "INDEX"))):
        builder = CombinedFileBuilder(_Config(tmp_path / str(position)))
        _save_components(builder, "NSE", order)
        result = builder.reconcile("NSE", DAY, ("SME", "INDEX"))
        assert result.ok
        assert result.components == ("EQ", "SME", "INDEX")
        assert result.rows == 3
        outputs.append(result.output_path.read_bytes())
    assert len(set(outputs)) == 1


def test_bse_both_arrival_permutations_are_byte_identical(tmp_path):
    outputs = []
    for position, order in enumerate(permutations(("EQ", "INDEX"))):
        builder = CombinedFileBuilder(_Config(tmp_path / str(position)))
        _save_components(builder, "BSE", order)
        result = builder.reconcile("BSE", DAY, ("INDEX",))
        assert result.ok
        assert result.components == ("EQ", "INDEX")
        outputs.append(result.output_path.read_bytes())
    assert len(set(outputs)) == 1


def test_restart_rebuild_uses_persisted_components(tmp_path):
    config = _Config(tmp_path)
    first = CombinedFileBuilder(config)
    _save_components(first, "NSE", ("INDEX", "EQ", "SME"))
    original = first.reconcile("NSE", DAY, ("SME", "INDEX"))
    original_bytes = original.output_path.read_bytes()

    restarted = CombinedFileBuilder(config)
    rebuilt = restarted.reconcile("NSE", DAY, ("INDEX", "SME"))
    assert rebuilt.output_path.read_bytes() == original_bytes
    assert rebuilt.sha256 == original.sha256


def test_restart_preserves_zero_prefixed_symbol(tmp_path):
    builder = CombinedFileBuilder(_Config(tmp_path))
    builder.save_component("NSE", "EQ", DAY, _equity("00123"))
    result = CombinedFileBuilder(_Config(tmp_path)).reconcile("NSE", DAY, ())
    assert result.output_path.read_text(encoding="utf-8").startswith("00123,")


def test_missing_dependency_does_not_touch_existing_eq_output(tmp_path):
    config = _Config(tmp_path)
    builder = CombinedFileBuilder(config)
    builder.save_component("NSE", "EQ", DAY, _equity("BASE"))
    builder.save_component("NSE", "SME", DAY, _equity("SME"))
    output = config.get_data_path("NSE", "EQ") / f"{DAY}-NSE-EQ.txt"
    output.write_bytes(b"validated-base-output\n")

    result = builder.reconcile("NSE", DAY, ("SME", "INDEX"))
    assert not result.ok
    assert "NSE_INDEX" in result.error
    assert output.read_bytes() == b"validated-base-output\n"
    date_result = builder.pipeline.date_result("NSE", "EQ", DAY)
    assert date_result.failed_stages == ("combined",)


def test_disabled_combining_restores_only_the_persisted_eq_component(tmp_path):
    config = _Config(tmp_path)
    builder = CombinedFileBuilder(config)
    builder.save_component("NSE", "EQ", DAY, _equity("BASE"))
    builder.save_component("NSE", "INDEX", DAY, _index("NIFTY"))
    combined = builder.reconcile("NSE", DAY, ("INDEX",))
    assert combined.rows == 2

    disabled = CombinedFileBuilder(config).reconcile("NSE", DAY, ())
    assert disabled.ok
    assert disabled.status == "disabled"
    output = pd.read_csv(disabled.output_path, header=None)
    assert output.iloc[:, 0].tolist() == ["BASE"]


def test_index_alignment_uses_names_and_leaves_delivery_blank(tmp_path):
    builder = CombinedFileBuilder(_Config(tmp_path))
    builder.save_component("NSE", "EQ", DAY, _equity("ABC"))
    builder.save_component("NSE", "INDEX", DAY, _index("NIFTY 50"))
    result = builder.reconcile("NSE", DAY, ("INDEX",))
    output = pd.read_csv(result.output_path, header=None, keep_default_na=False)
    assert len(output.columns) == len(EQUITY_DAILY_COLUMNS)
    assert output.iloc[1, 7] == ""
    assert output.iloc[1, 8] == ""


def test_cross_component_duplicate_is_preserved_and_reported(tmp_path):
    builder = CombinedFileBuilder(_Config(tmp_path))
    builder.save_component("NSE", "EQ", DAY, _equity("DUPLICATE"))
    builder.save_component("NSE", "SME", DAY, _equity("DUPLICATE"))
    result = builder.reconcile("NSE", DAY, ("SME",))
    output = pd.read_csv(result.output_path, header=None)
    assert output.iloc[:, 0].tolist() == ["DUPLICATE", "DUPLICATE"]
    assert result.duplicate_keys == 1


def test_old_seven_column_component_is_upgraded_read_only(tmp_path):
    builder = CombinedFileBuilder(_Config(tmp_path))
    old_component = builder.component_path("NSE", "EQ", DAY)
    old_component.parent.mkdir(parents=True)
    old_bytes = _equity("ABC").iloc[:, :7].to_csv(index=False).encode()
    old_component.write_bytes(old_bytes)
    builder.save_component("NSE", "INDEX", DAY, _index("NIFTY 50"))
    result = builder.reconcile("NSE", DAY, ("INDEX",))
    output = pd.read_csv(result.output_path, header=None, keep_default_na=False)
    assert len(output.columns) == len(EQUITY_DAILY_COLUMNS)
    assert output.iloc[:, 0].tolist() == ["ABC", "NIFTY 50"]
    # Delivery stays blank for both rows, and the upgraded seven-column
    # component invents no turnover -- while the index row keeps its own.
    assert output.iloc[:, 7:9].eq("").all().all()
    assert output.iloc[0, 9:].eq("").all()
    assert output.iloc[1, 9:].tolist() == ["5000", "99"]
    assert old_component.read_bytes() == old_bytes
