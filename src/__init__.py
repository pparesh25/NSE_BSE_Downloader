"""
NSE/BSE Data Downloader Package

A comprehensive data downloader for NSE and BSE market data with:
- Concurrent downloads
- Memory optimization
- PyQt6 GUI interface
- Smart date management
"""

__version__ = "1.0.0"
__author__ = "NSE/BSE Data Downloader Team"
__email__ = "support@example.com"

from .core.config import Config
from .core.data_manager import DataManager
from .core.base_downloader import BaseDownloader

__all__ = [
    "Config",
    "DataManager", 
    "BaseDownloader",
]
