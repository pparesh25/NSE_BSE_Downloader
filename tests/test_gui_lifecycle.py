import asyncio
from datetime import date
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea
from PySide6.QtCore import QEventLoop, QTimer

from src.core.config import Config
from src.gui.main_window import DownloadWorker, GUIOutcome, MainWindow
from src.services.pipeline_state import DateResult, SegmentResult


def _application():
    return QApplication.instance() or QApplication([])


class _SlowDownloader:
    def __init__(self, config):
        self.config = config
        self.started = asyncio.Event()
        self.finalized = False
        self.last_segment_result = None

    def get_date_range(self, start, end):
        day = date(2026, 7, 31)
        return day, day

    def get_working_days(self, start, end, include_weekends):
        return [start]

    def _with_pending_delivery_days(self, days):
        return days

    def _with_incomplete_pipeline_days(self, days):
        return days

    async def _download_implementation(self, days):
        self.started.set()
        try:
            await asyncio.sleep(30)
        finally:
            self.finalized = True


def test_download_worker_cancels_async_tasks_cooperatively():
    _application()
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(config, [])
    slow = _SlowDownloader(config)
    worker.downloaders = {"NSE_FO": slow}

    async def scenario():
        running = asyncio.create_task(worker._run_downloads())
        await slow.started.wait()
        worker.request_stop()
        return await asyncio.wait_for(running, timeout=2)

    assert not asyncio.run(scenario())
    assert slow.finalized
    assert worker.final_outcome == GUIOutcome.CANCELLED


def test_running_qthread_stops_cooperatively_in_gui_event_loop():
    _application()
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(config, [])
    slow = _SlowDownloader(config)
    worker.downloaders = {"NSE_FO": slow}
    outcomes = []
    event_loop = QEventLoop()
    worker.overall_outcome.connect(outcomes.append)
    worker.finished.connect(event_loop.quit)

    worker.start()
    QTimer.singleShot(100, worker.request_stop)
    QTimer.singleShot(3000, event_loop.quit)
    event_loop.exec()
    if worker.isRunning():
        worker.request_stop()
        worker.wait(2000)

    assert not worker.isRunning()
    assert slow.finalized
    assert outcomes == [GUIOutcome.CANCELLED.value]


def test_worker_classifies_pending_warning_and_repair_required():
    _application()
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(config, [])
    day = date(2026, 7, 31)

    pending = SegmentResult("NSE", "EQ", (DateResult(
        day,
        "partial",
        completed_stages=("daily",),
        failed_stages=("delivery",),
        error="delivery report pending",
    ),))
    worker.downloaders = {
        "NSE_EQ": SimpleNamespace(last_segment_result=pending)
    }
    assert worker._classify_segment_outcome(
        "NSE_EQ", False
    ) == GUIOutcome.PENDING

    repair = SegmentResult("NSE", "EQ", (DateResult(
        day, "failed", error="Repair required before state can be updated"
    ),))
    worker.downloaders["NSE_EQ"].last_segment_result = repair
    assert worker._classify_segment_outcome(
        "NSE_EQ", False
    ) == GUIOutcome.REPAIR_REQUIRED

    skipped = SegmentResult("NSE", "EQ", (DateResult(
        day, "skipped", error="not published yet"
    ),))
    worker.downloaders["NSE_EQ"].last_segment_result = skipped
    assert worker._classify_segment_outcome(
        "NSE_EQ", True
    ) == GUIOutcome.WARNING


def test_retry_candidates_include_failed_and_partial_but_not_skipped():
    _application()
    config = SimpleNamespace(
        download_settings=SimpleNamespace(timeout_seconds=5)
    )
    worker = DownloadWorker(config, [])
    days = [date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 3)]
    worker.downloaders = {
        "NSE_EQ": SimpleNamespace(last_segment_result=SegmentResult(
            "NSE",
            "EQ",
            (
                DateResult(days[0], "partial"),
                DateResult(days[1], "failed"),
                DateResult(days[2], "skipped"),
            ),
        ))
    }

    assert worker._collect_retry_candidates() == {
        "NSE_EQ": ["2026-07-30", "2026-07-31"]
    }

    worker.downloaders["NSE_EQ"] = SimpleNamespace(
        last_segment_result=None,
        no_work=True,
    )
    assert worker._classify_segment_outcome(
        "NSE_EQ", True
    ) == GUIOutcome.WARNING


class _RunningWorker:
    def __init__(self):
        self.running = True
        self.stop_requested = False

    def isRunning(self):
        return self.running

    def request_stop(self):
        self.stop_requested = True


class _CloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def test_window_close_requests_safe_stop_without_terminating(
    tmp_path, monkeypatch
):
    _application()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    config = Config("config.yaml")
    config.base_data_path = tmp_path / "data"
    window = MainWindow(config)
    worker = _RunningWorker()
    window.download_worker = worker
    event = _CloseEvent()

    window.closeEvent(event)
    assert event.ignored and not event.accepted
    assert worker.stop_requested
    assert window._close_after_workers

    worker.running = False
    window.download_worker = None
    window._close_after_workers = False
    window.close()


def test_default_window_keeps_dates_and_donate_action_visible(
    tmp_path, monkeypatch
):
    app = _application()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config = Config("config.yaml")
    config.base_data_path = tmp_path / "data"
    window = MainWindow(config)
    window.show()
    app.processEvents()

    scroll_area = window.centralWidget()
    assert isinstance(scroll_area, QScrollArea)
    assert window.width() >= 720
    assert window.windowTitle() == "NSE BSE Data Downloader"
    assert window.start_date_edit.width() >= 145
    assert window.end_date_edit.width() >= 145
    assert window.donate_button.isVisibleTo(window)
    assert window.update_check_timer.isActive()
    assert window.legacy_output_checkbox.text() == (
        "7-column compatibility output"
    )
    donate_right = window.donate_button.mapTo(
        scroll_area.viewport(), window.donate_button.rect().bottomRight()
    ).x()
    end_date_right = window.end_date_edit.mapTo(
        scroll_area.viewport(), window.end_date_edit.rect().bottomRight()
    ).x()
    assert donate_right <= scroll_area.viewport().width()
    assert end_date_right <= scroll_area.viewport().width()

    window.expand_all_sections()
    app.processEvents()
    window._fit_window_to_sections()
    expanded_height = window.height()
    window.collapse_all_sections()
    app.processEvents()
    window._fit_window_to_sections()
    collapsed_height = window.height()
    assert collapsed_height < expanded_height
    assert collapsed_height >= window.minimumHeight()

    window.close()
    assert not window.update_check_timer.isActive()
