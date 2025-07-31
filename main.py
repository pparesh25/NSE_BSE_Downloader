#!/usr/bin/env python3
"""
NSE/BSE Data Downloader - Main Entry Point

A comprehensive data downloader for NSE and BSE market data with:
- Concurrent downloads for faster processing
- Memory optimization for large datasets
- PyQt6 GUI interface for easy use
- Smart date management and automatic updates

Usage:
    python main.py              # Launch GUI interface
    python main.py --help       # Show help
"""

import sys
import argparse
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.config import Config
from src.gui.main_window import MainWindow

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("Warning: PyQt6 not available. GUI mode disabled.")


def setup_argument_parser():
    """Setup command line argument parser"""
    parser = argparse.ArgumentParser(
        description="NSE/BSE Data Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                    # Launch GUI
    python main.py --config custom.yaml  # Use custom config
        """
    )

    parser.add_argument(
        "--config",
        type=str,
        default=str(Path(__file__).parent / "config.yaml"),
        help="Path to configuration file (default: config.yaml)"
    )

    return parser


def run_gui_mode(config_path: str):
    """Run the application in GUI mode"""
    if not GUI_AVAILABLE:
        print("Error: PyQt6 is not installed. Cannot run GUI mode.")
        print("Install PyQt6 with: pip install PyQt6")
        return 1

    # Enable High DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("NSE/BSE Data Downloader")
    app.setApplicationVersion("1.0.0")

    # Set application style
    app.setStyle("Fusion")

    try:
        # Initialize configuration
        config = Config(config_path)

        # Create and show main window
        main_window = MainWindow(config)
        main_window.show()

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
        # Run in GUI mode
        return run_gui_mode(str(config_path))

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

    
