"""
Configuration Management for NSE/BSE Data Downloader

Handles loading and validation of configuration from YAML files.
Provides cross-platform path resolution and default settings.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from .exceptions import ConfigError


@dataclass
class ExchangeConfig:
    """Configuration for a specific exchange and segment"""
    base_url: str
    filename_pattern: str
    date_format: str
    file_suffix: str


@dataclass
class DownloadSettings:
    """Download-related configuration"""
    max_concurrent_downloads: int = 5
    retry_attempts: int = 3
    timeout_seconds: int = 30
    chunk_size: int = 8192
    rate_limit_delay: float = 0.5


@dataclass
class DateSettings:
    """Date-related configuration"""
    base_start_date: str = "2025-01-01"
    weekend_skip: bool = True
    holiday_skip: bool = True


@dataclass
class GUISettings:
    """GUI-related configuration"""
    window_title: str = "NSE/BSE Data Downloader"
    window_width: int = 800
    window_height: int = 600
    default_exchanges: List[str] = field(default_factory=lambda: ["NSE_EQ", "BSE_EQ"])
    progress_update_interval: int = 100


class Config:
    """
    Main configuration class for NSE/BSE Data Downloader
    
    Handles loading configuration from YAML files, path resolution,
    and provides access to all configuration settings.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration
        
        Args:
            config_path: Path to configuration file. If None, uses default config.yaml
        """
        self.config_path = Path(config_path) if config_path else Path("config.yaml")
        self._config_data: Dict[str, Any] = {}
        self._exchange_configs: Dict[str, Dict[str, ExchangeConfig]] = {}
        
        self.load_config()
        self._validate_config()
        self._setup_paths()
        
    def load_config(self) -> None:
        """Load configuration from YAML file"""
        try:
            if not self.config_path.exists():
                raise ConfigError(f"Configuration file not found: {self.config_path}")
                
            with open(self.config_path, 'r', encoding='utf-8') as file:
                self._config_data = yaml.safe_load(file)
                
            if not self._config_data:
                raise ConfigError("Configuration file is empty or invalid")
                
        except yaml.YAMLError as e:
            raise ConfigError(f"Error parsing YAML configuration: {e}")
        except Exception as e:
            raise ConfigError(f"Error loading configuration: {e}")
    
    def _validate_config(self) -> None:
        """Validate configuration data"""
        required_sections = ['data_paths', 'download_settings', 'exchange_config']
        
        for section in required_sections:
            if section not in self._config_data:
                raise ConfigError(f"Missing required configuration section: {section}")
        
        # Validate exchange configurations
        exchange_config = self._config_data.get('exchange_config', {})
        for exchange_name, exchange_data in exchange_config.items():
            for segment_name, segment_data in exchange_data.items():
                try:
                    self._exchange_configs.setdefault(exchange_name, {})[segment_name] = ExchangeConfig(
                        base_url=segment_data['base_url'],
                        filename_pattern=segment_data['filename_pattern'],
                        date_format=segment_data['date_format'],
                        file_suffix=segment_data['file_suffix']
                    )
                except KeyError as e:
                    raise ConfigError(f"Missing required field in {exchange_name}.{segment_name}: {e}")
    
    def _setup_paths(self) -> None:
        """Setup and validate data paths"""
        data_paths = self._config_data.get('data_paths', {})
        
        # Expand user home directory
        base_folder = data_paths.get('base_folder', '~/NSE_BSE_Data')
        self.base_data_path = Path(base_folder).expanduser().resolve()
        
        # Create base directory if it doesn't exist
        self.base_data_path.mkdir(parents=True, exist_ok=True)

        # Setup holiday manager (use user home directory)
        from ..utils.holiday_manager import HolidayManager
        user_cache_dir = Path.home() / ".nse_bse_downloader"
        self.holiday_manager = HolidayManager(user_cache_dir)
    
    @property
    def download_settings(self) -> DownloadSettings:
        """Get download settings"""
        settings_data = self._config_data.get('download_settings', {})
        return DownloadSettings(
            max_concurrent_downloads=settings_data.get('max_concurrent_downloads', 5),
            retry_attempts=settings_data.get('retry_attempts', 3),
            timeout_seconds=settings_data.get('timeout_seconds', 30),
            chunk_size=settings_data.get('chunk_size', 8192),
            rate_limit_delay=settings_data.get('rate_limit_delay', 0.5)
        )
    
    @property
    def date_settings(self) -> DateSettings:
        """Get date settings"""
        settings_data = self._config_data.get('date_settings', {})
        return DateSettings(
            base_start_date=settings_data.get('base_start_date', '2025-01-01'),
            weekend_skip=settings_data.get('weekend_skip', True),
            holiday_skip=settings_data.get('holiday_skip', True)
        )
    
    @property
    def gui_settings(self) -> GUISettings:
        """Get GUI settings"""
        settings_data = self._config_data.get('gui_settings', {})
        return GUISettings(
            window_title=settings_data.get('window_title', 'NSE/BSE Data Downloader'),
            window_width=settings_data.get('window_width', 800),
            window_height=settings_data.get('window_height', 600),
            default_exchanges=settings_data.get('default_exchanges', ['NSE_EQ', 'BSE_EQ']),
            progress_update_interval=settings_data.get('progress_update_interval', 100)
        )
    
    def get_exchange_config(self, exchange: str, segment: str) -> ExchangeConfig:
        """
        Get configuration for specific exchange and segment
        
        Args:
            exchange: Exchange name (e.g., 'NSE', 'BSE')
            segment: Segment name (e.g., 'EQ', 'FO', 'SME')
            
        Returns:
            ExchangeConfig object
            
        Raises:
            ConfigError: If exchange/segment configuration not found
        """
        if exchange not in self._exchange_configs:
            raise ConfigError(f"Exchange '{exchange}' not configured")
            
        if segment not in self._exchange_configs[exchange]:
            raise ConfigError(f"Segment '{segment}' not configured for exchange '{exchange}'")
            
        return self._exchange_configs[exchange][segment]
    
    def get_data_path(self, exchange: str, segment: str) -> Path:
        """
        Get data storage path for specific exchange and segment
        
        Args:
            exchange: Exchange name
            segment: Segment name
            
        Returns:
            Path object for data storage
        """
        exchange_paths = self._config_data.get('data_paths', {}).get('exchanges', {})
        
        if exchange in exchange_paths and segment in exchange_paths[exchange]:
            relative_path = exchange_paths[exchange][segment]
        else:
            # Default path structure
            relative_path = f"{exchange}/{segment}"
            
        data_path = self.base_data_path / relative_path
        data_path.mkdir(parents=True, exist_ok=True)
        
        return data_path
    
    def get_temp_path(self, exchange: str, segment: str) -> Path:
        """
        Get temporary path for downloads (deprecated - no longer used)

        Args:
            exchange: Exchange name
            segment: Segment name

        Returns:
            Path object (not used with memory-based processing)
        """
        # Return data path as fallback (not actually used)
        return self.get_data_path(exchange, segment)
    
    def get_available_exchanges(self) -> List[str]:
        """
        Get list of available exchange/segment combinations
        
        Returns:
            List of exchange_segment strings (e.g., ['NSE_EQ', 'NSE_FO', 'BSE_EQ'])
        """
        exchanges = []
        for exchange_name, segments in self._exchange_configs.items():
            for segment_name in segments.keys():
                exchanges.append(f"{exchange_name}_{segment_name}")
        
        return sorted(exchanges)
    
    def get_download_options(self) -> Dict:
        """Get download options for data processing"""
        return self._config_data.get("download_options", {})

    def get_output_directory(self) -> Path:
        """Get base output directory for data files"""
        return self.base_data_path

    def reload_config(self) -> None:
        """Reload configuration from file"""
        self.load_config()
        self._validate_config()
        self._setup_paths()
    
    def __str__(self) -> str:
        """String representation of configuration"""
        return f"Config(path={self.config_path}, exchanges={len(self._exchange_configs)})"
    
    def __repr__(self) -> str:
        """Detailed string representation"""
        return (f"Config(config_path='{self.config_path}', "
                f"base_data_path='{self.base_data_path}', "
                f"exchanges={list(self._exchange_configs.keys())})")
