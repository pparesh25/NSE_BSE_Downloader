#!/usr/bin/env python3
"""
NSE/BSE Data Downloader - Main Entry Point

A comprehensive data downloader for NSE and BSE market data with:
- Concurrent downloads for faster processing
- Memory optimization for large datasets
- PySide6 GUI interface for easy use
- Smart date management and automatic updates

Usage:
    python main.py              # Launch GUI interface
    python main.py --help       # Show help
"""

import sys
import argparse
import logging
from pathlib import Path

MINIMUM_PYTHON = (3, 10)


def _require_supported_python() -> None:
    """Stop with a readable message instead of an error from a later import.

    Users upgrading from v1.0.1 may still be on the Python 3.8 that release
    supported.  Every ``src`` module uses syntax that only parses on 3.10 or
    newer, so this has to run before the first project import.
    """

    if sys.version_info >= MINIMUM_PYTHON:
        return
    running = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MINIMUM_PYTHON)
    print(
        "Error: this application needs Python {0} or newer, but it is running "
        "on Python {1}.".format(required, running)
    )
    print("Install a newer Python from https://www.python.org/downloads/ and")
    print("run the application with it, then reinstall the dependencies:")
    print("    pip install -r requirements.txt")
    print("See UPGRADE.md for the full upgrade instructions.")
    raise SystemExit(1)


_require_supported_python()

try:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("Warning: PySide6 not available. GUI mode disabled.")
    print("Install the application dependencies with:")
    print("    pip install -r requirements.txt")
    print(
        "Version 1.1.0 replaced PyQt6 with PySide6, so an installation carried "
        "over from v1.0.1 needs this step.  See UPGRADE.md."
    )

# Deliberately below the interpreter check: these modules use syntax that only
# parses on Python 3.10 or newer, so importing them first would replace the
# guard's readable message with a SyntaxError traceback.
from src.core.config import Config  # noqa: E402
from runtime_paths import default_config_path, resource_path  # noqa: E402
from runtime_identity import configure_process_identity  # noqa: E402
from version import get_version  # noqa: E402
from app_metadata import (  # noqa: E402
    APP_NAME,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    PRODUCT_NAME,
)


def setup_argument_parser():
    """Setup command line argument parser"""
    parser = argparse.ArgumentParser(
        description="NSE/BSE Data Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                    # Launch GUI
    python main.py --config custom.yaml  # Use custom config
    python main.py --rebuild-symbol NSE RELIANCE
    python main.py --rebuild-exchange BSE
    python main.py --rebuild-combined NSE 2026-07-31
    python main.py --audit                # verify the database, change nothing
    python main.py --audit NSE_EQ BSE_EQ  # verify only these segments
    python main.py --verify-eod-parity    # regenerate every daily file from
                                          # the database and diff it
        """
    )

    parser.add_argument(
        "--config",
        type=str,
        default=str(default_config_path()),
        help="Path to configuration file (default: config.yaml)"
    )

    repair = parser.add_mutually_exclusive_group()
    repair.add_argument(
        "--rebuild-symbol",
        nargs=2,
        metavar=("EXCHANGE", "SYMBOL"),
        help="Rebuild one symbol history from validated raw snapshots",
    )
    parser.add_argument(
        "--smoke-gui",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    repair.add_argument(
        "--rebuild-exchange",
        choices=("NSE", "BSE"),
        help="Rebuild every symbol history for one exchange",
    )
    repair.add_argument(
        "--rebuild-registry",
        action="store_true",
        help="Rebuild the stable-id/symbol registry from raw snapshots",
    )
    repair.add_argument(
        "--rebuild-all",
        action="store_true",
        help="Rebuild all NSE/BSE symbol histories from raw snapshots",
    )
    repair.add_argument(
        "--rebuild-combined",
        nargs=2,
        metavar=("EXCHANGE", "YYYY-MM-DD"),
        help="Rebuild one deterministic EQ+SME/Index output from components",
    )
    # Read-only like ``--audit``, and in the same group for the same reason.
    repair.add_argument(
        "--verify-eod-parity",
        nargs="*",
        default=None,
        metavar="EXCHANGE_SEGMENT",
        help=(
            "Regenerate published daily files from the EOD database and "
            "report any that do not match byte for byte"
        ),
    )
    # In the same mutually exclusive group as the repairs even though it
    # repairs nothing: asking a question about the database while rewriting it
    # would not answer the question.  ``default=None`` distinguishes "not
    # asked" from ``--audit`` with no segments, which is an empty list.
    repair.add_argument(
        "--audit",
        nargs="*",
        default=None,
        metavar="EXCHANGE_SEGMENT",
        help=(
            "Verify the data root against its own records without writing "
            "anything; optionally limit it to named segments, e.g. NSE_EQ"
        ),
    )

    return parser


def run_audit_mode(config_path: str, segments) -> int:
    """Report on the data root without changing it.

    Exit codes are three-valued on purpose, because "the database has a
    problem" and "the audit could not look" are different answers and a
    script that treats them alike would report a broken audit as clean data.
    """

    from src.services.audit_service import DatabaseAudit

    try:
        config = Config(config_path)
        report = DatabaseAudit(config, segments).run()
    except Exception as error:
        print(f"Audit could not run: {error}")
        return 2

    print(report.render())
    return 1 if report.failed else 0


def run_eod_parity_mode(config_path: str, segments) -> int:
    """Diff every published daily file against the database it was mirrored to.

    Phase 5 step 2.  This changes nothing and is the evidence that has to hold
    before step 3 lets the database publish.  Exit codes match ``--audit``:
    0 clean, 1 a mismatch was found, 2 the check could not run -- because "no
    differences" and "could not look" must not be the same answer.
    """

    from src.services.eod_export import verify_parity

    try:
        config = Config(config_path)
        report = verify_parity(config, segments)
    except Exception as error:
        print(f"Parity check could not run: {error}")
        return 2

    print(report.render())
    return 1 if report.mismatches else 0


def run_rebuild_mode(config_path: str, args) -> int:
    """Run an explicit fail-closed symbol-history repair command."""

    from src.services.instance_lock import InstanceLockError, SingleInstanceLock

    try:
        config = Config(config_path)
    except Exception as error:
        print(f"Repair failed; existing data was left in place: {error}")
        return 1

    # A rebuild rewrites the same histories a running download appends to, so
    # it takes the same lock the application holds.
    try:
        lock = SingleInstanceLock(config.base_data_path).acquire()
    except InstanceLockError as error:
        print(f"Repair not started: {error}")
        return 1

    try:
        if args.rebuild_combined:
            from datetime import date
            from src.services.combined_file_builder import CombinedFileBuilder
            from src.utils.user_preferences import UserPreferences

            exchange, raw_date = args.rebuild_combined
            exchange = exchange.upper()
            if exchange not in {"NSE", "BSE"}:
                raise ValueError("EXCHANGE must be NSE or BSE")
            target_date = date.fromisoformat(raw_date)
            builder = CombinedFileBuilder(config)
            dependencies = builder.dependencies_from_options(
                exchange,
                UserPreferences(config).get_append_options(),
            )
            build_result = builder.reconcile(
                exchange, target_date, dependencies
            )
            if not build_result.ok:
                raise RuntimeError(build_result.error)
            print(
                f"Rebuilt {build_result.output_path} with "
                f"{build_result.rows} rows from "
                f"{', '.join(build_result.components)}"
            )
            return 0

        from src.services.rebuild_service import SymbolHistoryRebuilder

        rebuilder = SymbolHistoryRebuilder(config.base_data_path)
        if args.rebuild_symbol:
            exchange, symbol = args.rebuild_symbol
            path = rebuilder.rebuild_symbol(exchange, symbol)
            print(f"Rebuilt symbol history: {path}")
        elif args.rebuild_exchange:
            paths = rebuilder.rebuild_exchange(args.rebuild_exchange)
            print(
                f"Rebuilt {len(paths)} {args.rebuild_exchange} symbol histories"
            )
        elif args.rebuild_registry:
            count = rebuilder.rebuild_registry()
            print(f"Rebuilt symbol registry with {count} stable identifiers")
        elif args.rebuild_all:
            result = rebuilder.rebuild_all()
            count = sum(len(paths) for paths in result.values())
            print(f"Rebuilt {count} symbol histories across all exchanges")

        from src.services.history_revision import HistoryRevisionStore

        notice = HistoryRevisionStore(config.base_data_path).notice()
        if notice:
            print(notice)
        return 0
    except Exception as error:
        print(f"Repair failed; existing data was left in place: {error}")
        return 1
    finally:
        lock.release()


def run_gui_mode(config_path: str, *, smoke_test: bool = False):
    """Run the application in GUI mode"""
    if not GUI_AVAILABLE:
        print("Error: PySide6 is not installed. Cannot run GUI mode.")
        print("Install the application dependencies with:")
        print("    pip install -r requirements.txt")
        return 1

    # Imported here rather than at module scope so that the repair commands and
    # the messages above still work on an installation whose dependencies have
    # not been updated yet.
    from src.gui.main_window import MainWindow

    # Enable High DPI support
    import os
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '1'
    os.environ['QT_SCALE_FACTOR'] = '1'

    configure_process_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(PRODUCT_NAME)
    app.setApplicationVersion(get_version())
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setOrganizationDomain(ORGANIZATION_DOMAIN)
    app_icon = QIcon(str(resource_path("src", "gui", "resources", "icon.png")))
    if app_icon.isNull():
        print("Error: bundled application icon could not be loaded.")
        return 1
    app.setWindowIcon(app_icon)

    # Set application style
    app.setStyle("Fusion")

    from src.services.instance_lock import InstanceLockError, SingleInstanceLock

    lock = None
    try:
        # Initialize configuration
        config = Config(config_path)

        # Two copies writing one data root destroy each other's histories
        # silently, so the second copy stops here rather than at the first
        # damaged file.
        try:
            lock = SingleInstanceLock(config.base_data_path).acquire()
        except InstanceLockError as error:
            print(f"Error starting GUI: {error}")
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(None, "Already running", str(error))
            return 1

        # Create and show main window
        main_window = MainWindow(config)
        main_window.setWindowIcon(app_icon)
        main_window.show()

        if smoke_test:
            # Exercise real widget construction, one event-processing pass,
            # and the normal close path without relying on a compiled Python
            # timer callback to terminate the release probe.
            app.processEvents()
            main_window.close()
            app.processEvents()
            return 0

        # Run the application
        return app.exec()

    except Exception as e:
        print(f"Error starting GUI: {e}")
        return 1
    finally:
        if lock is not None:
            lock.release()





def run_tls_check() -> int:
    """Prove this build can complete one verified HTTPS request.

    Every other packaging check -- checksums, layout, provenance, Gatekeeper,
    --smoke-gui -- is satisfied by a binary that cannot open a TLS connection,
    which is exactly how v1.1.0 shipped without a certificate store. This is the
    one check that touches the network, so it runs against the compiled artifact
    during packaging rather than only against the source tree under pytest.
    """

    import ssl

    import aiohttp

    from src.utils.http_client import HTTPStatusError, fetch_text_sync
    from src.utils.tls import certificate_bundle_path, default_ssl_context

    bundle = certificate_bundle_path()
    authorities = len(default_ssl_context().get_ca_certs())
    print(f"Certificate bundle: {bundle if bundle is not None else 'NONE (system default)'}")
    print(f"Trusted certificate authorities loaded: {authorities}")

    if bundle is None:
        # A build that relies on the host's trust store is the defect itself: it
        # works on the machine that built it and nowhere else.
        print("TLS verification FAILED: no certificate bundle travelled with this build")
        return 1
    if authorities == 0:
        print("TLS verification FAILED: the bundle loaded no certificate authorities")
        return 1

    url = "https://raw.githubusercontent.com/pparesh25/NSE_BSE_Downloader/main/version.py"
    try:
        # The updater's own endpoint, reached through the application's own HTTP
        # client, so this exercises the shipped path rather than a private one.
        fetch_text_sync(url, timeout=30)
    # Order matters, as it does in the downloader's own classifier: aiohttp's
    # certificate errors subclass its connection errors.
    except (ssl.SSLError, aiohttp.ClientSSLError) as error:
        print(f"TLS verification FAILED for {url}: {error!r}")
        return 1
    except HTTPStatusError as answered:
        # A status line can only arrive after the handshake completed and the
        # certificate verified.  What the server chose to answer -- a rate limit,
        # a 404, an outage -- is not this check's business, and failing a release
        # build over it would be a false alarm about certificates.
        print(f"TLS verified: the endpoint answered HTTP {answered.status}.")
        return 0
    except Exception as error:
        # Not a certificate problem, but nothing was proved either.  Say which
        # it is, so a reader retries instead of debugging the trust store.
        print(f"TLS verification INCONCLUSIVE: {url} was unreachable: {error!r}")
        return 1

    print("TLS verification passed.")
    return 0


def main():
    """Main entry point"""
    parser = setup_argument_parser()
    args = parser.parse_args()

    # Before anything that can fail, so whatever happens next is recorded.
    from src.utils.logging_setup import configure_logging, log_environment

    log_path = configure_logging()
    log_environment()
    if log_path is not None:
        logging.getLogger(__name__).info(f"Logging to {log_path}")

    if args.verify_tls:
        return run_tls_check()

    # Validate config file exists
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file '{config_path}' not found.")
        return 1

    try:
        if args.audit is not None:
            return run_audit_mode(str(config_path), args.audit)

        if args.verify_eod_parity is not None:
            return run_eod_parity_mode(
                str(config_path), args.verify_eod_parity
            )

        if (
            args.rebuild_symbol
            or args.rebuild_exchange
            or args.rebuild_registry
            or args.rebuild_all
            or args.rebuild_combined
        ):
            return run_rebuild_mode(str(config_path), args)

        # Run in GUI mode
        if args.smoke_gui:
            return run_gui_mode(str(config_path), smoke_test=True)
        return run_gui_mode(str(config_path))

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
