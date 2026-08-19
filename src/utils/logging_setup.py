"""Where the application's diagnostics go, and what they say.

Until this existed there was no logging configuration anywhere, so a packaged
build produced nothing a user could send: every ``logger.info`` went to a
handler that was never installed, and only Python's last-resort handler put
warnings on a console a windowed application does not have.  Diagnosing the
v1.1.1 certificate defect meant running the packaged binary from a terminal to
capture stderr, which is not something a user can be asked to do.

Logs are written under the per-user state directory, never under the data root:
the data root holds years of downloaded market history, and a log file has no
business growing inside it.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
from pathlib import Path
from typing import Optional

from runtime_paths import log_directory

#: Roughly a dozen long runs before the oldest is dropped.  Large enough to
#: cover a multi-day backfill, bounded so it can never quietly fill a disk.
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5
LOG_FILENAME = "nse_bse_downloader.log"

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s: %(message)s"

#: Marks the handlers this module installs, so configuring twice replaces them
#: instead of writing every record two or three times.
_MARKER = "_nse_bse_downloader_handler"


def configure_logging(
    *,
    console_level: int = logging.WARNING,
    file_level: int = logging.INFO,
    directory: Optional[Path] = None,
) -> Optional[Path]:
    """Install the file and console handlers and return the log file path.

    Returns ``None`` when no file could be opened -- a read-only home, a full
    disk, a permission the user does not have.  Console logging is still set up
    in that case: losing diagnostics is bad, but refusing to start the
    application because a log file could not be created would be worse.
    """

    root = logging.getLogger()
    root.setLevel(min(console_level, file_level))
    for handler in [h for h in root.handlers if getattr(h, _MARKER, False)]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    setattr(console, _MARKER, True)
    root.addHandler(console)

    target = directory if directory is not None else log_directory()
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / LOG_FILENAME
        file_handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        root.warning(f"Could not open a log file under {target}: {error}")
        return None

    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    setattr(file_handler, _MARKER, True)
    root.addHandler(file_handler)
    return path


def log_environment(logger: Optional[logging.Logger] = None) -> None:
    """Record what this build is, at the top of every run.

    A log file arriving on an issue should identify itself without anyone
    having to ask which build produced it -- including whether it is a packaged
    artifact and whether it carries the certificate bundle whose absence
    withdrew v1.1.0.
    """

    from app_metadata import PRODUCT_NAME
    from src.utils.tls import certificate_bundle_path
    from version import __build_date__, get_version

    log = logger or logging.getLogger(__name__)
    packaged = getattr(sys, "frozen", False) or "__compiled__" in globals()
    bundle = certificate_bundle_path()

    log.info(f"{PRODUCT_NAME} {get_version()} (build {__build_date__})")
    log.info(
        f"Python {platform.python_version()} on {platform.system()} "
        f"{platform.release()} {platform.machine()}"
    )
    log.info(f"Packaged build: {packaged}")
    log.info(f"Certificate bundle: {bundle if bundle is not None else 'system default'}")
