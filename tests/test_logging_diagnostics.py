"""Diagnostics a user can actually send.

A packaged build has no console, so anything it fails to write down is lost.
These tests pin the two properties that matter: something is written, and it is
never written into the data root.
"""

import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import runtime_paths
from src.utils import logging_setup
from src.utils.logging_setup import (
    BACKUP_COUNT,
    LOG_FILENAME,
    MAX_LOG_BYTES,
    configure_logging,
    log_environment,
)


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for handler in [h for h in root.handlers if h not in saved]:
        root.removeHandler(handler)
        handler.close()
    for handler in saved:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(level)


def _our_handlers():
    return [
        h for h in logging.getLogger().handlers
        if getattr(h, logging_setup._MARKER, False)
    ]


def test_logging_writes_a_file_under_the_user_state_directory(tmp_path):
    path = configure_logging(directory=tmp_path)
    assert path == tmp_path / LOG_FILENAME

    logging.getLogger("test.diagnostics").info("a recorded message")
    assert "a recorded message" in path.read_text(encoding="utf-8")


def test_the_log_file_is_bounded(tmp_path):
    configure_logging(directory=tmp_path)
    rotating = [h for h in _our_handlers() if hasattr(h, "maxBytes")]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == MAX_LOG_BYTES
    assert rotating[0].backupCount == BACKUP_COUNT


def test_configuring_twice_does_not_duplicate_every_record(tmp_path):
    configure_logging(directory=tmp_path)
    configure_logging(directory=tmp_path)

    logging.getLogger("test.diagnostics").warning("said once")
    written = (tmp_path / LOG_FILENAME).read_text(encoding="utf-8")
    assert written.count("said once") == 1
    assert len(_our_handlers()) == 2  # one console, one file


def test_the_console_stays_quiet_below_warning(tmp_path, capsys):
    configure_logging(directory=tmp_path)
    logger = logging.getLogger("test.diagnostics")
    logger.info("routine detail")
    logger.warning("worth interrupting for")

    captured = capsys.readouterr().err
    assert "routine detail" not in captured
    assert "worth interrupting for" in captured
    # The detail is not lost, it is just not shouted about.
    assert "routine detail" in (tmp_path / LOG_FILENAME).read_text(encoding="utf-8")


def test_an_unwritable_location_does_not_stop_the_application(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    path = configure_logging(directory=blocked / "logs")

    assert path is None
    assert _our_handlers(), "console logging must survive a failed log file"


def test_logs_never_go_near_the_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    data_root = tmp_path / "NSE_BSE_Data"
    data_root.mkdir()

    path = configure_logging()

    assert path is not None
    assert data_root not in path.parents
    assert path.parent == runtime_paths.log_directory()
    assert list(data_root.iterdir()) == []


def test_the_log_identifies_the_build_that_wrote_it(tmp_path):
    path = configure_logging(directory=tmp_path)
    log_environment()

    written = path.read_text(encoding="utf-8")
    from version import get_version

    assert get_version() in written
    assert "Packaged build:" in written
    # The absence of this line is what withdrew v1.1.0.
    assert "Certificate bundle:" in written


def test_help_menu_can_open_the_log_folder(tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    from src.core.config import Config
    from src.gui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    opened = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: bool(opened.append(url.toLocalFile())) or True,
    )

    config = Config("config.yaml")
    config.base_data_path = tmp_path / "data"
    window = MainWindow(config)
    try:
        # Kept in real variables rather than a generator: PySide6 drops the
        # Python wrapper for a QMenu obtained inside a discarded generator, and
        # the next call on it raises "already deleted".
        help_menu = None
        for item in window.menuBar().actions():
            if item.text() == "Help":
                help_menu = item.menu()
        assert help_menu is not None

        open_logs = None
        for action in help_menu.actions():
            if action.text() == "Open Log Folder":
                open_logs = action
        assert open_logs is not None

        open_logs.trigger()

        assert opened == [str(runtime_paths.log_directory())]
        assert runtime_paths.log_directory().is_dir()
        # The window creates its data root, as it always does; what must never
        # appear there is a log file.
        assert list((tmp_path / "data").rglob("*.log")) == []
    finally:
        window.close()
