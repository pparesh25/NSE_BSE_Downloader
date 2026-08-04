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
from pathlib import Path

from src.core.config import Config
from src.gui.main_window import MainWindow
from runtime_paths import default_config_path, resource_path
from runtime_identity import configure_process_identity
from version import get_version
from app_metadata import (
    APP_NAME,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    PRODUCT_NAME,
)

try:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("Warning: PySide6 not available. GUI mode disabled.")


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

    return parser


def run_rebuild_mode(config_path: str, args) -> int:
    """Run an explicit fail-closed symbol-history repair command."""

    try:
        config = Config(config_path)
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
        return 0
    except Exception as error:
        print(f"Repair failed; existing data was left in place: {error}")
        return 1


def run_gui_mode(config_path: str, *, smoke_test: bool = False):
    """Run the application in GUI mode"""
    if not GUI_AVAILABLE:
        print("Error: PySide6 is not installed. Cannot run GUI mode.")
        print("Install PySide6 with: pip install PySide6")
        return 1

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

    try:
        # Initialize configuration
        config = Config(config_path)

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





def main():
    """Main entry point"""
    parser = setup_argument_parser()
    args = parser.parse_args()

    # Validate config file exists
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file '{config_path}' not found.")
        return 1

    try:
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
