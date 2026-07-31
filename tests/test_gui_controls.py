import asyncio
from datetime import date
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.gui.collapsible_section import CollapsibleSection
from src.gui.main_window import DownloadWorker
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
    section.set_expanded(False)
    assert not section.is_expanded()
    assert content.isHidden()
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


def test_date_and_section_preferences_survive_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
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
