"""Phase 2.2 gates: no per-write symbol backup, bounded ``.state`` diagnostics.

The retention sweep deletes files inside the user's data root, so most of what
is pinned here is what it must *not* touch: the checksummed rebuild source,
the live state documents, anything reached through a symlink, and any tree
whose policy is disabled.
"""

from __future__ import annotations

import asyncio
from datetime import date
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from runtime_paths import default_config_path
from src.core.config import Config, RetentionSettings
from src.gui.main_window import DownloadWorker
from src.services.pipeline_telemetry import PipelineTelemetry
from src.services import state_retention
from src.services.state_retention import (
    DEFAULT_POLICIES,
    RetentionPolicy,
    policies_from_settings,
    prune_state_directories,
    prune_state_tree,
)
from src.services.symbol_history import SymbolHistoryStore


DAY = 86400


def _row(day: str, close: int = 100) -> pd.DataFrame:
    return pd.DataFrame([{
        "SYMBOL": "AAA",
        "DATE": day,
        "OPEN": close,
        "HIGH": close,
        "LOW": close,
        "CLOSE": close,
        "VOLUME": 100,
        "DELIVERY_QTY": 80,
        "DELIVERY_PERCENT": 80,
        "SERIES": "EQ",
        "TOTAL_TRADES": 10,
        "QTY_PER_TRADE": 10,
        "ISIN": "INE000000000",
        "SECURITY_ID": "123",
    }])


def _config_for(tmp_path: Path, **retention: int) -> Path:
    """Write a copy of the shipped config.yaml rooted inside ``tmp_path``."""

    values = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    values["data_paths"]["base_folder"] = str(tmp_path / "market_data")
    values["state_retention"].update(retention)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def _aged(path: Path, days: float, *, contents: bytes = b"x") -> Path:
    """Create ``path`` with an mtime ``days`` in the past."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------
# The write path itself
# --------------------------------------------------------------------------


def test_symbol_writes_no_longer_copy_a_backup(tmp_path):
    """Fails on the previous code, which copied the file before every write."""

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _row("20250101"))
    # The second write is the one that used to produce a backup: the first
    # creates the file, so there is nothing to copy yet.
    store.upsert("NSE", "EQ", date(2025, 1, 2), _row("20250102", close=110))

    history = tmp_path / "NSE" / "SYMBOLS" / "aaa.txt"
    assert history.exists()
    assert pd.read_csv(history)["DATE"].astype(str).tolist() == [
        "20250101", "20250102"
    ]
    assert not (tmp_path / ".state" / "backups").exists()
    assert list((tmp_path / ".state").rglob("*.txt")) == []


def test_raw_snapshot_still_backs_a_rebuild_after_the_backup_is_gone(tmp_path):
    """The recovery story the removed copy never provided is still intact."""

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 1), _row("20250101"))
    snapshot = tmp_path / ".state" / "raw" / "NSE" / "EQ" / "2025-01-01.csv"
    assert snapshot.exists()
    assert snapshot.with_suffix(".csv.meta.json").exists()

    prune_state_directories(tmp_path / ".state")
    assert snapshot.exists()
    assert store.read_internal_snapshot(snapshot).shape[0] == 1


# --------------------------------------------------------------------------
# Age and count rules
# --------------------------------------------------------------------------


def test_quarantine_files_past_the_window_are_removed(tmp_path):
    state = tmp_path / ".state"
    fresh = _aged(state / "quarantine" / "history" / "fresh.corrupt", 1)
    stale = _aged(state / "quarantine" / "history" / "stale.corrupt", 45)

    outcome = prune_state_tree(
        state, RetentionPolicy("quarantine", 1, 30, None)
    )

    assert fresh.exists() and not stale.exists()
    assert (outcome.removed_files, outcome.kept_files) == (1, 1)
    assert outcome.removed_bytes == 1


def test_quarantine_count_budget_is_per_category_not_global(tmp_path):
    state = tmp_path / ".state"
    for category in ("history", "source_reports"):
        for index in range(4):
            _aged(
                state / "quarantine" / category / f"{index}.bin",
                days=index,  # 0 is newest
            )

    prune_state_tree(state, RetentionPolicy("quarantine", 1, -1, 2))

    for category in ("history", "source_reports"):
        kept = sorted(
            path.name
            for path in (state / "quarantine" / category).glob("*.bin")
        )
        assert kept == ["0.bin", "1.bin"], category


def test_raw_revisions_are_capped_per_date(tmp_path):
    state = tmp_path / ".state"
    for day in ("2025-01-02", "2025-01-03"):
        for index in range(4):
            _aged(
                state / "raw_revisions" / "NSE" / "EQ" / day / f"{index}.csv",
                days=index,
            )

    prune_state_tree(state, RetentionPolicy("raw_revisions", 3, -1, 2))

    for day in ("2025-01-02", "2025-01-03"):
        kept = sorted(
            path.name
            for path in (state / "raw_revisions" / "NSE" / "EQ" / day).iterdir()
        )
        assert kept == ["0.csv", "1.csv"], day


def test_legacy_backup_tree_is_drained_including_its_directories(tmp_path):
    """Nothing writes this tree any more, so the default policy clears it."""

    state = tmp_path / ".state"
    for symbol in ("aaa", "bbb"):
        _aged(
            state / "backups" / "history" / "NSE" / "SYMBOLS" /
            f"{symbol}.txt",
            days=0,
            contents=b"DATE,OPEN\n",
        )

    outcomes = {
        outcome.directory: outcome
        for outcome in prune_state_directories(state, DEFAULT_POLICIES)
    }

    assert not (state / "backups").exists()
    assert outcomes["backups"].removed_files == 2
    assert outcomes["backups"].removed_bytes == 20
    assert outcomes["backups"].errors == 0


def test_a_disabled_policy_leaves_its_tree_untouched(tmp_path):
    """`legacy_backup_days: -1` is the documented way to keep the old tree."""

    state = tmp_path / ".state"
    kept = _aged(state / "backups" / "history" / "aaa.txt", days=900)

    settings = SimpleNamespace(legacy_backup_days=-1)
    outcomes = {
        outcome.directory: outcome
        for outcome in prune_state_directories(
            state, policies_from_settings(settings)
        )
    }

    assert kept.exists()
    assert outcomes["backups"] == state_retention.RetentionOutcome(
        "backups", 0, 0, 0, 0
    )


# --------------------------------------------------------------------------
# What the sweep must never do
# --------------------------------------------------------------------------


def test_live_state_outside_the_named_trees_is_never_touched(tmp_path):
    state = tmp_path / ".state"
    survivors = [
        _aged(state / "symbol_registry.json", days=900),
        _aged(state / "corporate_actions.json", days=900),
        _aged(state / "pipeline_manifest.sqlite3", days=900),
        _aged(state / "raw" / "NSE" / "EQ" / "2015-01-02.csv", days=4000),
        _aged(state / "components" / "NSE" / "EQ" / "2015-01-02.csv", days=4000),
        _aged(state / "transport_events.jsonl", days=900),
    ]

    prune_state_directories(state, DEFAULT_POLICIES)

    assert all(path.exists() for path in survivors)


def test_a_symlink_is_neither_followed_nor_removed(tmp_path):
    state = tmp_path / ".state"
    outside = _aged(tmp_path / "outside" / "precious.txt", days=900)
    quarantine = state / "quarantine" / "history"
    quarantine.mkdir(parents=True)
    link = quarantine / "link.corrupt"
    link.symlink_to(outside)
    linked_directory = quarantine / "elsewhere"
    linked_directory.symlink_to(outside.parent, target_is_directory=True)

    outcome = prune_state_tree(
        state, RetentionPolicy("quarantine", 1, 0, None)
    )

    assert outside.exists()
    assert link.is_symlink()
    assert outcome.removed_files == 0


def test_an_undeletable_file_is_reported_rather_than_raised(tmp_path):
    state = tmp_path / ".state"
    locked_directory = state / "quarantine" / "history"
    target = _aged(locked_directory / "stale.corrupt", days=900)
    os.chmod(locked_directory, 0o500)
    try:
        outcome = prune_state_tree(
            state, RetentionPolicy("quarantine", 1, 30, None)
        )
    finally:
        os.chmod(locked_directory, 0o700)

    assert target.exists()
    assert (outcome.removed_files, outcome.errors) == (0, 1)


def test_missing_and_unreadable_trees_report_instead_of_failing(
    tmp_path, monkeypatch
):
    state = tmp_path / ".state"

    # Nothing on disk at all: three trees, nothing removed, no exception.
    outcomes = prune_state_directories(state, DEFAULT_POLICIES)
    assert [outcome.removed_files for outcome in outcomes] == [0, 0, 0]

    _aged(state / "quarantine" / "history" / "stale.corrupt", days=900)

    def explode(*_args, **_kwargs):
        raise OSError("state tree is unreadable")

    monkeypatch.setattr(state_retention, "_candidates", explode)
    (outcome,) = prune_state_directories(
        state, [RetentionPolicy("quarantine", 1, 0, None)]
    )
    assert outcome.errors == 1
    assert (state / "quarantine" / "history" / "stale.corrupt").exists()


# --------------------------------------------------------------------------
# Configuration and the run that triggers it
# --------------------------------------------------------------------------


def test_shipped_config_yaml_reaches_the_retention_policies(tmp_path):
    """2.1 shipped an inert key once; this follows the value to the policy."""

    shipped = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    assert "state_retention" in shipped, "config.yaml documents the defaults"

    config = Config(str(_config_for(
        tmp_path, quarantine_days=17, raw_revision_max_per_date=3
    )))
    policies = {
        policy.directory: policy
        for policy in policies_from_settings(config.retention_settings)
    }

    assert policies["quarantine"].max_age_days == 17
    assert policies["raw_revisions"].max_entries == 3
    assert policies["backups"].max_age_days == 0


def test_an_unusable_retention_value_falls_back_instead_of_failing(tmp_path):
    """A hand-edited config.yaml must not stop a download run."""

    config = Config(str(_config_for(tmp_path, quarantine_days="thirty")))
    policies = {
        policy.directory: policy
        for policy in policies_from_settings(config.retention_settings)
    }

    assert policies["quarantine"].max_age_days == 30
    assert policies_from_settings(object()) == DEFAULT_POLICIES
    assert policies_from_settings(None) == DEFAULT_POLICIES


def test_a_config_without_the_section_keeps_the_shipped_defaults(tmp_path):
    values = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    values.pop("state_retention")
    values["data_paths"]["base_folder"] = str(tmp_path / "market_data")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(values), encoding="utf-8")

    config = Config(str(config_path))

    assert policies_from_settings(config.retention_settings) == DEFAULT_POLICIES


def test_a_run_prunes_state_once_and_records_what_it_removed(tmp_path):
    state = tmp_path / ".state"
    _aged(state / "quarantine" / "history" / "stale.corrupt", days=90)
    _aged(state / "backups" / "history" / "aaa.txt", days=1)
    telemetry = PipelineTelemetry()
    config = SimpleNamespace(
        base_data_path=tmp_path,
        retention_settings=RetentionSettings(),
        pipeline_telemetry=telemetry,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )

    DownloadWorker(config, [])._prune_state_directories()

    events = {
        event.fields["directory"]: event.fields
        for event in telemetry.events
        if event.kind == "state_retention"
    }
    assert set(events) == {"quarantine", "raw_revisions", "backups"}
    assert events["quarantine"]["removed_files"] == 1
    assert events["backups"]["removed_files"] == 1
    assert not (state / "quarantine" / "history").exists()
    assert not (state / "backups").exists()


def test_a_retention_failure_cannot_fail_the_run(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(
        "src.gui.main_window.prune_state_directories", explode
    )
    config = SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )

    # No raise, and no telemetry to record it against either.
    DownloadWorker(config, [])._prune_state_directories()


def test_pruning_runs_at_the_end_of_a_download_run(tmp_path, monkeypatch):
    """The sweep is wired into the run, not merely callable in isolation."""

    calls: list[Path] = []
    monkeypatch.setattr(
        DownloadWorker,
        "_prune_state_directories",
        lambda self: calls.append(Path(self.config.base_data_path)),
    )
    config = Config(str(_config_for(tmp_path)))
    worker = DownloadWorker(config, ["NSE_EQ"])
    worker.downloaders = {
        "NSE_EQ": SimpleNamespace(exchange="NSE", segment="EQ")
    }

    async def nothing_to_download(_exchange, _downloader):
        return True

    monkeypatch.setattr(
        worker, "_download_exchange_data", nothing_to_download
    )
    asyncio.run(worker._run_downloads())

    assert calls == [Path(config.base_data_path)]


@pytest.mark.parametrize(
    "policy",
    DEFAULT_POLICIES,
    ids=[policy.directory for policy in DEFAULT_POLICIES],
)
def test_default_policies_only_name_diagnostic_trees(policy):
    assert policy.directory in {"quarantine", "raw_revisions", "backups"}
