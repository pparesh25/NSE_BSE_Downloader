"""
Data Manager for NSE/BSE Data Downloader

Centralized data management system for:
- Folder structure creation and management
- Last file date detection
- Smart date range calculation
- Data validation and cleanup
"""

import os
import re
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple, Dict
import logging

from .config import Config
from .exceptions import DataProcessingError, FileOperationError, DateRangeError


class DataManager:
    """
    Centralized data management system
    
    Handles all data-related operations including folder management,
    date calculations, and file operations.
    """
    
    def __init__(self, config: Config):
        """
        Initialize data manager
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Date patterns for different exchanges
        self.date_patterns = {
            'NSE_EQ': r'(\d{4}-\d{2}-\d{2})-NSE-EQ\.(txt|csv)',
            'NSE_FO': r'(\d{4}-\d{2}-\d{2})-NSE-FO\.(txt|csv)',
            'NSE_SME': r'(\d{4}-\d{2}-\d{2})-NSE-SME\.(txt|csv)',
            'NSE_INDEX': r'(\d{4}-\d{2}-\d{2})-NSE-INDEX\.(txt|csv)',
            'BSE_EQ': r'(\d{4}-\d{2}-\d{2})-BSE-EQ\.(txt|csv)',
            'BSE_INDEX': r'(\d{4}-\d{2}-\d{2})-BSE-INDEX\.(txt|csv)',
        }
        
        self._ensure_folder_structure()
    
    def _ensure_folder_structure(self) -> None:
        """Ensure all required folders exist"""
        try:
            # Create base data directory
            self.config.base_data_path.mkdir(parents=True, exist_ok=True)
            
            # Create exchange-specific directories
            for exchange_segment in self.config.get_available_exchanges():
                exchange, segment = exchange_segment.split('_', 1)
                data_path = self.config.get_data_path(exchange, segment)
                data_path.mkdir(parents=True, exist_ok=True)
                
            self.logger.info("Folder structure created successfully")
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to create folder structure: {e}",
                operation="create_folders"
            )
    
    def get_last_file_date(self, exchange: str, segment: str) -> Optional[date]:
        """
        Get the date of the last available file for an exchange/segment
        
        Args:
            exchange: Exchange name (e.g., 'NSE', 'BSE')
            segment: Segment name (e.g., 'EQ', 'FO', 'SME')
            
        Returns:
            Date of last file, or None if no files exist
            
        Raises:
            DataProcessingError: If there's an error reading files
        """
        try:
            data_path = self.config.get_data_path(exchange, segment)
            exchange_segment = f"{exchange}_{segment}"
            
            if exchange_segment not in self.date_patterns:
                raise DataProcessingError(f"No date pattern defined for {exchange_segment}")
            
            pattern = self.date_patterns[exchange_segment]
            dates = []
            
            # Scan directory for files matching the pattern
            for file_path in data_path.iterdir():
                if file_path.is_file():
                    match = re.match(pattern, file_path.name)
                    if match:
                        date_str = match.group(1)
                        try:
                            file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                            dates.append(file_date)
                        except ValueError:
                            self.logger.warning(f"Invalid date format in filename: {file_path.name}")
            
            if dates:
                last_date = max(dates)
                self.logger.info(f"Last file date for {exchange_segment}: {last_date}")
                return last_date
            
            self.logger.info(f"No files found for {exchange_segment}")
            return None
            
        except Exception as e:
            raise DataProcessingError(
                f"Error getting last file date for {exchange}_{segment}: {e}",
                file_path=str(data_path)
            )
    
    def is_first_run(self, exchange: str, segment: str) -> bool:
        """
        Check if this is the first run for an exchange/segment
        
        Args:
            exchange: Exchange name
            segment: Segment name
            
        Returns:
            True if no data files exist, False otherwise
        """
        return self.get_last_file_date(exchange, segment) is None
    
    def calculate_date_range(self, exchange: str, segment: str, 
                           custom_start: Optional[date] = None,
                           custom_end: Optional[date] = None) -> Tuple[date, date]:
        """
        Calculate the date range for downloading data
        
        Args:
            exchange: Exchange name
            segment: Segment name
            custom_start: Custom start date (optional)
            custom_end: Custom end date (optional)
            
        Returns:
            Tuple of (start_date, end_date)
            
        Raises:
            DateRangeError: If date calculation fails
        """
        try:
            # Use custom dates if provided
            if custom_start and custom_end:
                if custom_start > custom_end:
                    raise DateRangeError(
                        "Start date cannot be after end date",
                        start_date=str(custom_start),
                        end_date=str(custom_end)
                    )
                return custom_start, custom_end
            
            # Default end date is today
            end_date = custom_end or date.today()
            
            # Calculate start date
            if custom_start:
                start_date = custom_start
            elif self.is_first_run(exchange, segment):
                # First run: use base start date from config
                base_start_str = self.config.date_settings.base_start_date
                start_date = datetime.strptime(base_start_str, '%Y-%m-%d').date()
            else:
                # Subsequent run: continue from last file date + 1
                last_date = self.get_last_file_date(exchange, segment)
                if last_date:
                    start_date = last_date + timedelta(days=1)
                else:
                    # Fallback to base start date
                    base_start_str = self.config.date_settings.base_start_date
                    start_date = datetime.strptime(base_start_str, '%Y-%m-%d').date()
            
            # Validate date range
            if start_date > end_date:
                self.logger.info(f"No new data to download for {exchange}_{segment} (start: {start_date}, end: {end_date})")
                return start_date, start_date  # Return same date to indicate no download needed
            
            self.logger.info(f"Date range for {exchange}_{segment}: {start_date} to {end_date}")
            return start_date, end_date
            
        except Exception as e:
            raise DateRangeError(
                f"Error calculating date range for {exchange}_{segment}: {e}",
                start_date=str(custom_start) if custom_start else None,
                end_date=str(custom_end) if custom_end else None
            )
    
    def get_working_days(self, start_date: date, end_date: date, include_weekends: bool = False) -> List[date]:
        """
        Get list of working days in date range

        Args:
            start_date: Start date
            end_date: End date
            include_weekends: Override weekend skip setting

        Returns:
            List of working days
        """
        working_days = []
        current_date = start_date

        # Determine if weekends should be skipped
        skip_weekends = self.config.date_settings.weekend_skip and not include_weekends

        while current_date <= end_date:
            # Skip weekends if configured and not overridden
            if skip_weekends and current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # Skip market holidays
            if self.config.holiday_manager.is_holiday(current_date):
                self.logger.debug(f"Skipping holiday: {current_date}")
                current_date += timedelta(days=1)
                continue

            working_days.append(current_date)
            current_date += timedelta(days=1)

        return working_days
    
    def get_file_count(self, exchange: str, segment: str) -> int:
        """
        Get count of data files for an exchange/segment
        
        Args:
            exchange: Exchange name
            segment: Segment name
            
        Returns:
            Number of data files
        """
        try:
            data_path = self.config.get_data_path(exchange, segment)
            exchange_segment = f"{exchange}_{segment}"
            
            if exchange_segment not in self.date_patterns:
                return 0
            
            pattern = self.date_patterns[exchange_segment]
            count = 0
            
            for file_path in data_path.iterdir():
                if file_path.is_file() and re.match(pattern, file_path.name):
                    count += 1
            
            return count
            
        except Exception as e:
            self.logger.error(f"Error counting files for {exchange}_{segment}: {e}")
            return 0
    
    def get_data_summary(self) -> Dict[str, Dict[str, any]]:
        """
        Get summary of available data for all exchanges
        
        Returns:
            Dictionary with data summary for each exchange/segment
        """
        summary = {}
        
        for exchange_segment in self.config.get_available_exchanges():
            exchange, segment = exchange_segment.split('_', 1)
            
            try:
                last_date = self.get_last_file_date(exchange, segment)
                file_count = self.get_file_count(exchange, segment)
                is_first = self.is_first_run(exchange, segment)
                
                summary[exchange_segment] = {
                    'last_date': last_date.strftime('%Y-%m-%d') if last_date else None,
                    'file_count': file_count,
                    'is_first_run': is_first,
                    'data_path': str(self.config.get_data_path(exchange, segment))
                }
                
            except Exception as e:
                summary[exchange_segment] = {
                    'error': str(e),
                    'last_date': None,
                    'file_count': 0,
                    'is_first_run': True,
                    'data_path': str(self.config.get_data_path(exchange, segment))
                }
        
        return summary
    
    def cleanup_temp_files(self, exchange: Optional[str] = None, segment: Optional[str] = None) -> None:
        """
        Clean up temporary files (no longer needed with memory-based processing)

        Args:
            exchange: Specific exchange to clean (optional)
            segment: Specific segment to clean (optional)
        """
        # No temp files to clean up with memory-based processing
        self.logger.debug("Temp file cleanup not needed with memory-based processing")
    
    def _cleanup_directory(self, directory: Path) -> None:
        """Clean up files in a directory"""
        if directory.exists():
            for file_path in directory.iterdir():
                if file_path.is_file():
                    file_path.unlink()
                elif file_path.is_dir():
                    self._cleanup_directory(file_path)
                    file_path.rmdir()
    
    def validate_data_integrity(self, exchange: str, segment: str) -> bool:
        """
        Validate data integrity for an exchange/segment
        
        Args:
            exchange: Exchange name
            segment: Segment name
            
        Returns:
            True if data is valid, False otherwise
        """
        try:
            data_path = self.config.get_data_path(exchange, segment)
            
            # Check if directory exists
            if not data_path.exists():
                return False
            
            # Check if files follow naming convention
            exchange_segment = f"{exchange}_{segment}"
            if exchange_segment not in self.date_patterns:
                return False
            
            pattern = self.date_patterns[exchange_segment]
            valid_files = 0
            
            for file_path in data_path.iterdir():
                if file_path.is_file():
                    if re.match(pattern, file_path.name):
                        # Check if file is not empty
                        if file_path.stat().st_size > 0:
                            valid_files += 1
            
            self.logger.info(f"Data integrity check for {exchange_segment}: {valid_files} valid files")
            return valid_files > 0
            
        except Exception as e:
            self.logger.error(f"Error validating data integrity for {exchange}_{segment}: {e}")
            return False
