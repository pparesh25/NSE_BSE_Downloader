import asyncio
from datetime import date
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.core.base_downloader import BaseDownloader, ProgressCallback
from src.gui.collapsible_section import CollapsibleSection
from src.gui.main_window import DownloadWorker
from src.gui import update_dialog as update_dialog_module
from src.gui.update_dialog import UpdateDialog
from src.services.canonical_data import EQUITY_DAILY_COLUMNS, INDEX_DAILY_COLUMNS
from src.services.date_join_coordinator import DateJoinCoordinator
from src.services.pipeline_state import PipelineManifest
from src.utils.update_checker import UpdateChecker
from src.utils.user_preferences import UserPreferences


def _application():
    return QApplication.instance() or QApplication([])


def test_collapsible_section_hides_content_and_emits_state():
    _application()
    content = QLabel("contents")
    section = CollapsibleSection("sample", "Sample", content, expanded=True)
    changes = []
    section.toggled.connect(lambda key, expanded: changes.append((key, expanded)))

    assert section.is_expanded()
    assert not content.isHidden()
    assert section.toggle_button.property("sectionState") == "expanded"
    section.set_expanded(False)
    assert not section.is_expanded()
    assert content.isHidden()
    assert section.toggle_button.property("sectionState") == "collapsed"
    assert changes[-1] == ("sample", False)


class _RangeDownloader:
    def __init__(self):
        self.config = SimpleNamespace(
            download_settings=SimpleNamespace(timeout_seconds=0)
        )
        self.received_range = None
        self.received_days = None
        self.total_files = 0
        self.completed_files = 0

    def get_date_range(self, start, end):
        self.received_range = (start, end)
        return start, end

    def get_working_days(self, start, end, include_weekends):
        return [start, end]

    def _with_pending_delivery_days(self, days):
        return days

    async def _download_implementation(self, days):
        self.received_days = days
        return True


def test_download_worker_passes_custom_calendar_range():
    _application()
    start = date(2024, 7, 5)
    end = date(2024, 7, 8)
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(
        config, [], custom_start_date=start, custom_end_date=end
    )
    downloader = _RangeDownloader()
    assert asyncio.run(worker._download_exchange_data("NSE_EQ", downloader))
    assert downloader.received_range == (start, end)
    assert downloader.received_days == [start, end]


def test_download_worker_uses_only_exact_retry_dates():
    _application()
    days = [date(2026, 7, 30), date(2026, 8, 3)]
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(config, [], retry_dates={"NSE_EQ": days})
    downloader = _RangeDownloader()

    assert asyncio.run(worker._download_exchange_data("NSE_EQ", downloader))
    assert downloader.received_range is None
    assert downloader.received_days == days


def test_progress_message_includes_remaining_count_and_eta(monkeypatch):
    downloader = _RangeDownloader()
    downloader.exchange_segment = "NSE_EQ"
    downloader.total_files = 4
    downloader.completed_files = 2
    downloader._progress_started_at = 0.0
    updates = []
    downloader.progress_callback = ProgressCallback(
        lambda segment, percent, message: updates.append(
            (segment, percent, message)
        ),
        lambda *_args: None,
        lambda *_args: None,
    )
    monkeypatch.setattr("src.core.base_downloader.time.monotonic", lambda: 10.0)

    BaseDownloader._update_progress(downloader, "Completed 2026-08-04")

    assert updates == [(
        "NSE_EQ",
        50,
        "Completed 2026-08-04 · 2 remaining · ETA 10s",
    )]


def test_retry_dates_close_over_combined_dependencies_without_range_growth():
    first = date(2026, 7, 30)
    second = date(2026, 8, 3)
    expanded = DownloadWorker.expand_retry_dates(
        {"NSE_INDEX": [second], "NSE_EQ": [first]},
        {"sme_append_to_eq": True, "index_append_to_eq": True},
    )

    assert expanded == {
        "NSE_EQ": [first, second],
        "NSE_INDEX": [first, second],
        "NSE_SME": [first, second],
    }


def test_combined_options_expand_required_segments_in_fixed_order():
    selected = DownloadWorker.expand_selected_exchanges(
        ["BSE_EQ", "NSE_EQ"],
        {
            "sme_append_to_eq": True,
            "index_append_to_eq": True,
            "bse_index_append_to_eq": True,
        },
    )
    assert selected == [
        "NSE_EQ", "NSE_SME", "NSE_INDEX", "BSE_EQ", "BSE_INDEX"
    ]


class _CombinedConfig:
    def __init__(self, base_path):
        self.base_data_path = Path(base_path)
        self.download_settings = SimpleNamespace(timeout_seconds=5)

    def get_data_path(self, exchange, segment):
        path = self.base_data_path / exchange / segment
        path.mkdir(parents=True, exist_ok=True)
        return path


def _completed_result(manifest, exchange, segment, day):
    manifest.begin(
        exchange,
        segment,
        day,
        ("downloaded", "validated", "daily"),
        ("symbols", "delivery", "actions", "combined"),
    )
    for stage in ("downloaded", "validated", "daily"):
        manifest.mark(exchange, segment, day, stage, "complete")
    return manifest.segment_result(exchange, segment, [day])


def test_worker_finalizes_staged_components_after_tasks_settle(tmp_path):
    _application()
    day = date(2026, 7, 31)
    config = _CombinedConfig(tmp_path)
    coordinator = DateJoinCoordinator(
        config, {"NSE": ("SME", "INDEX")}
    )
    equity = pd.DataFrame([[
        "ABC", "20260731", 1, 2, 1, 2, 100, 50, 50,
    ]], columns=EQUITY_DAILY_COLUMNS)
    sme = pd.DataFrame([[
        "SMALL_SME", "20260731", 1, 2, 1, 2, 10, 5, 50,
    ]], columns=EQUITY_DAILY_COLUMNS)
    index = pd.DataFrame([[
        "NIFTY 50", "20260731", 1, 2, 1, 2, 0,
    ]], columns=INDEX_DAILY_COLUMNS)
    coordinator.offer("NSE", "INDEX", day, index)
    coordinator.offer("NSE", "EQ", day, equity)
    coordinator.offer("NSE", "SME", day, sme)

    manifest = PipelineManifest(tmp_path)
    downloaders = {
        "NSE_EQ": SimpleNamespace(
            last_segment_result=_completed_result(
                manifest, "NSE", "EQ", day
            )
        ),
        "NSE_SME": SimpleNamespace(
            last_segment_result=_completed_result(
                manifest, "NSE", "SME", day
            )
        ),
        "NSE_INDEX": SimpleNamespace(
            last_segment_result=_completed_result(
                manifest, "NSE", "INDEX", day
            )
        ),
    }
    worker = DownloadWorker(
        config,
        [],
        append_options={
            "sme_append_to_eq": True,
            "index_append_to_eq": True,
        },
    )
    worker.downloaders = downloaders
    config.date_join_coordinator = coordinator
    worker._finalize_staged_outputs()

    result = downloaders["NSE_EQ"].last_segment_result
    assert result.ok
    output = pd.read_csv(
        tmp_path / "NSE" / "EQ" / f"{day}-NSE-EQ.txt", header=None
    )
    assert output.iloc[:, 0].tolist() == ["ABC", "SMALL_SME", "NIFTY 50"]


def test_worker_staged_failure_preserves_previous_combined_file(tmp_path):
    _application()
    day = date(2026, 7, 31)
    config = _CombinedConfig(tmp_path)
    coordinator = DateJoinCoordinator(config, {"NSE": ("INDEX",)})
    equity = pd.DataFrame([[
        "ABC", "20260731", 1, 2, 1, 2, 100, 50, 50,
    ]], columns=EQUITY_DAILY_COLUMNS)
    coordinator.offer("NSE", "EQ", day, equity)
    output = config.get_data_path("NSE", "EQ") / f"{day}-NSE-EQ.txt"
    output.write_bytes(b"previous-validated-combined\n")

    manifest = PipelineManifest(tmp_path)
    downloaders = {
        "NSE_EQ": SimpleNamespace(
            last_segment_result=_completed_result(
                manifest, "NSE", "EQ", day
            )
        ),
        "NSE_INDEX": SimpleNamespace(last_segment_result=None),
    }
    worker = DownloadWorker(
        config,
        [],
        append_options={"index_append_to_eq": True},
    )
    worker.downloaders = downloaders
    config.date_join_coordinator = coordinator
    worker._finalize_staged_outputs()

    assert output.read_bytes() == b"previous-validated-combined\n"
    result = downloaders["NSE_EQ"].last_segment_result
    assert not result.ok
    assert result.dates[0].failed_stages == ("combined",)


def test_date_and_section_preferences_survive_reload(tmp_path, monkeypatch):
    start = date(2024, 7, 5)
    end = date(2024, 7, 8)

    preferences = UserPreferences()
    preferences.set_date_selection(True, start, end)
    preferences.set_section_state("options", False)

    reloaded = UserPreferences()
    assert reloaded.get_date_selection() == {
        "use_custom_range": True,
        "start_date": "2024-07-05",
        "end_date": "2024-07-08",
    }
    assert not reloaded.get_section_states()["options"]


def test_update_dialog_offers_release_page_without_verified_package(monkeypatch):
    _application()
    checker = UpdateChecker(current_version="1.1.0")
    dialog = UpdateDialog(
        {
            "latest_version": "1.2.0",
            "artifact_verified": False,
            "artifact_error": "Verified metadata missing",
            "release_page_url": checker.release_page_url,
        },
        update_checker=checker,
    )

    # A notification-only release must still lead somewhere, so the button stays
    # usable and opens the official release page instead of downloading.
    assert dialog.download_btn.isEnabled()
    assert dialog.download_btn.text() == "🌐 Open Release Page"

    opened = []
    monkeypatch.setattr(
        update_dialog_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    dialog.download_btn.click()

    assert opened == [
        "https://github.com/pparesh25/NSE_BSE_Downloader/releases/latest"
    ]
    dialog.close()


def test_update_dialog_downloads_when_package_is_verified():
    _application()
    checker = UpdateChecker(current_version="1.1.0")
    dialog = UpdateDialog(
        {
            "latest_version": "1.2.0",
            "artifact_verified": True,
            "release_page_url": checker.release_page_url,
        },
        update_checker=checker,
    )

    assert dialog.download_btn.isEnabled()
    assert dialog.download_btn.text() == "📥 Download Update"
    dialog.close()


def test_update_dialog_persists_skipped_version(tmp_path, monkeypatch):
    _application()
    dialog = UpdateDialog(
        {"latest_version": "1.2.0", "artifact_verified": False},
        update_checker=UpdateChecker(current_version="1.1.0"),
    )
    dialog.skip_version()
    assert UserPreferences().get_skipped_update_version() == "1.2.0"
