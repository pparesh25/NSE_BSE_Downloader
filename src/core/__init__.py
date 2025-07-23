"""
Core module for NSE/BSE Data Downloader

Contains base classes, configuration management, and core functionality.
"""

from .config import Config
from .data_manager import DataManager
from .base_downloader import BaseDownloader
from .exceptions import (
    DownloaderError,
    ConfigError,
    DataProcessingError,
    NetworkError
)

__all__ = [
    "Config",
    "DataManager",
    "BaseDownloader", 
    "DownloaderError",
    "ConfigError",
    "DataProcessingError",
    "NetworkError",
]
