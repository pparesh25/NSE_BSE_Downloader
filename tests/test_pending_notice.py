"""A delivery report that is not published yet is a notice, not an error.

NSE publishes the day's delivery file after its bhavcopy, so a run in the early
evening downloads the day and leaves two delivery columns empty until Retry
Failed/Pending fills them in.  The owner's v1.2.0 test showed what reporting that
through the error path looked like: NSE EQ and NSE SME read
"100% - Completed 2026-09-15" in red, which says two contradictory things at once.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from src.core.base_downloader import BaseDownloader, ProgressCallback
from src.gui.main_window import (
    ERROR_COLOR,
    NOTICE_COLOR,
    RUNNING_COLOR,
    DownloadWorker,
    running_label_color,
)


class _Downloader:
    """Only what ``_report_notice`` itself touches."""

    _report_notice = BaseDownloader._report_notice

    def __init__(self, callback: object) -> None:
        self.progress_callback = callback
        self.exchange_segment = "NSE_EQ"
        self.logger = logging.getLogger("test.notice")


def test_a_notice_is_not_reported_as_an_error():
    errors: list[tuple[str, str]] = []
    notices: list[tuple[str, str]] = []
    callback = ProgressCallback(
        on_error=lambda segment, message: errors.append((segment, message)),
        on_notice=lambda segment, message: notices.append((segment, message)),
    )

    _Downloader(callback)._report_notice("NSE_EQ delivery pending for 2026-09-15")

    assert notices == [("NSE_EQ", "NSE_EQ delivery pending for 2026-09-15")]
    assert errors == []


def test_a_callback_that_predates_notices_still_shows_them():
    """A notice is still better seen than lost."""

    seen: list[str] = []
    callback = SimpleNamespace(
        on_error=lambda _segment, message: seen.append(message)
    )

    _Downloader(callback)._report_notice("delivery pending")

    assert seen == ["delivery pending"]


def test_the_colour_says_pending_until_something_actually_fails():
    assert running_label_color(False, False) == RUNNING_COLOR
    assert running_label_color(False, True) == NOTICE_COLOR
    assert running_label_color(True, True) == ERROR_COLOR


def test_the_worker_forwards_a_notice_under_its_gui_segment(tmp_path):
    config = SimpleNamespace(
        base_data_path=tmp_path,
        download_settings=SimpleNamespace(timeout_seconds=5),
        stage_executors={},
    )
    worker = DownloadWorker(config, [])
    seen: list[tuple[str, str]] = []
    worker.notice_occurred.connect(lambda *args: seen.append(args))

    worker._progress_callback_for("NSE_EQ").on_notice(
        "the downloader's own name", "delivery pending for 2026-09-15"
    )

    assert seen == [("NSE_EQ", "delivery pending for 2026-09-15")]
