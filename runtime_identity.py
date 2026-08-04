"""Best-effort process identity for source and packaged GUI launches."""

from __future__ import annotations

import logging
import sys

from app_metadata import PRODUCT_NAME


def configure_process_identity(name: str = PRODUCT_NAME) -> bool:
    """Expose the product name to process viewers during source launches.

    Nuitka supplies the native executable and bundle identity for packaged
    builds.  During development Python remains the executable, so setproctitle
    supplies a readable process name instead.  AppKit is optional and only
    updates the macOS process display name when PyObjC is already available.
    """

    configured = False
    try:
        import setproctitle

        setproctitle.setproctitle(name)
        configured = True
    except (ImportError, OSError, RuntimeError):
        logging.getLogger(__name__).debug(
            "setproctitle is unavailable; keeping the executable process name",
            exc_info=True,
        )

    if sys.platform == "darwin":
        try:
            from AppKit import NSProcessInfo  # type: ignore[import-untyped]

            NSProcessInfo.processInfo().setProcessName_(name)
            configured = True
        except (ImportError, AttributeError, OSError, RuntimeError):
            logging.getLogger(__name__).debug(
                "AppKit process naming is unavailable",
                exc_info=True,
            )
    return configured
