"""
Base Downloader Class for NSE/BSE Data Downloader

Abstract base class providing common functionality for all exchange downloaders.
Includes date management, folder operations, and data processing interfaces.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any
import pandas as pd

from .config import Config, ExchangeConfig
from .data_manager import DataManager
from .exceptions import (
    DownloaderError, 
    DataProcessingError, 
    NetworkError,
    FileOperationError
)


class ProgressCallback:
    """Progress callback interface for download progress tracking"""
    
    def __init__(self, 
                 on_progress: Optional[Callable[[str, int, str], None]] = None,
                 on_status: Optional[Callable[[str, str], None]] = None,
                 on_error: Optional[Callable[[str, str], None]] = None):
        """
        Initialize progress callback
        
        Args:
            on_progress: Callback for progress updates (exchange_segment, percentage, message)
            on_status: Callback for status updates (exchange_segment, status_message)
            on_error: Callback for error notifications (exchange_segment, error_message)
        """
        self.on_progress = on_progress or self._default_progress
        self.on_status = on_status or self._default_status
        self.on_error = on_error or self._default_error
    
    def _default_progress(self, exchange_segment: str, percentage: int, message: str):
        print(f"[{exchange_segment}] {percentage}% - {message}")
    
    def _default_status(self, exchange_segment: str, message: str):
        print(f"[{exchange_segment}] {message}")
    
    def _default_error(self, exchange_segment: str, error: str):
        print(f"[{exchange_segment}] ERROR: {error}")


class BaseDownloader(ABC):
    """
    Abstract base class for all exchange downloaders
    
    Provides common functionality including:
    - Configuration management
    - Date range calculation
    - Progress tracking
    - Error handling
    - File operations
    """
    
    def __init__(self, exchange: str, segment: str, config: Config):
        """
        Initialize base downloader
        
        Args:
            exchange: Exchange name (e.g., 'NSE', 'BSE')
            segment: Segment name (e.g., 'EQ', 'FO', 'SME')
            config: Configuration object
        """
        self.exchange = exchange
        self.segment = segment
        self.config = config
        self.exchange_segment = f"{exchange}_{segment}"
        
        # Initialize components
        self.data_manager = DataManager(config)
        self.logger = logging.getLogger(f"{__name__}.{self.exchange_segment}")
        
        # Get exchange-specific configuration
        self.exchange_config = config.get_exchange_config(exchange, segment)
        
        # Setup paths
        self.data_path = config.get_data_path(exchange, segment)
        
        # Progress tracking
        self.progress_callback: Optional[ProgressCallback] = None
        self.total_files = 0
        self.completed_files = 0
    
    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Set progress callback for tracking download progress"""
        self.progress_callback = callback
    
    def _update_progress(self, message: str = "") -> None:
        """Update progress percentage"""
        if self.progress_callback and self.total_files > 0:
            percentage = int((self.completed_files / self.total_files) * 100)
            self.progress_callback.on_progress(self.exchange_segment, percentage, message)
    
    def _update_status(self, message: str) -> None:
        """Update status message"""
        if self.progress_callback:
            self.progress_callback.on_status(self.exchange_segment, message)
        self.logger.info(message)
    
    def _report_error(self, error: str) -> None:
        """Report error message"""
        if self.progress_callback:
            self.progress_callback.on_error(self.exchange_segment, error)
        self.logger.error(error)
    
    @abstractmethod
    def build_url(self, target_date: date) -> str:
        """
        Build download URL for specific date
        
        Args:
            target_date: Date for which to build URL
            
        Returns:
            Complete download URL
        """
        pass
    
    @abstractmethod
    def process_downloaded_data(self, file_data: bytes, file_date: date) -> Optional[pd.DataFrame]:
        """
        Process downloaded file data in memory

        Args:
            file_data: Downloaded file data as bytes
            file_date: Date of the data

        Returns:
            Processed DataFrame, or None if processing failed
        """
        pass
    
    @abstractmethod
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform DataFrame according to exchange-specific requirements
        
        Args:
            df: Input DataFrame
            file_date: Date of the data
            
        Returns:
            Transformed DataFrame
        """
        pass
    
    def get_date_range(self, 
                      custom_start: Optional[date] = None,
                      custom_end: Optional[date] = None) -> tuple[date, date]:
        """
        Get date range for downloading
        
        Args:
            custom_start: Custom start date (optional)
            custom_end: Custom end date (optional)
            
        Returns:
            Tuple of (start_date, end_date)
        """
        return self.data_manager.calculate_date_range(
            self.exchange, 
            self.segment, 
            custom_start, 
            custom_end
        )
    
    def get_working_days(self, start_date: date, end_date: date, include_weekends: bool = False) -> List[date]:
        """Get list of working days in date range"""
        return self.data_manager.get_working_days(start_date, end_date, include_weekends)
    
    def build_filename(self, target_date: date, extension: str = "txt") -> str:
        """
        Build standardized filename for processed data
        
        Args:
            target_date: Date for the file
            extension: File extension (default: txt)
            
        Returns:
            Standardized filename
        """
        date_str = target_date.strftime('%Y-%m-%d')
        suffix = self.exchange_config.file_suffix
        return f"{date_str}{suffix}.{extension}"
    
    def save_processed_data(self, df: pd.DataFrame, target_date: date) -> Path:
        """
        Save processed DataFrame to final location
        
        Args:
            df: Processed DataFrame
            target_date: Date of the data
            
        Returns:
            Path to saved file
            
        Raises:
            FileOperationError: If save operation fails
        """
        try:
            filename = self.build_filename(target_date)
            output_path = self.data_path / filename
            
            # Save without header and index (as per original code)
            df.to_csv(output_path, index=False, header=False)
            
            self.logger.info(f"Saved processed data: {filename}")
            return output_path
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to save processed data for {target_date}",
                file_path=str(output_path),
                operation="save_csv"
            ) from e
    
    def cleanup_temp_files(self) -> None:
        """Clean up temporary files for this downloader (no longer needed)"""
        pass  # No temp files to clean up with memory-based processing
    
    def validate_data_file(self, file_path: Path) -> bool:
        """
        Validate downloaded data file
        
        Args:
            file_path: Path to file to validate
            
        Returns:
            True if file is valid, False otherwise
        """
        try:
            if not file_path.exists():
                return False
            
            # Check file size
            if file_path.stat().st_size == 0:
                self.logger.warning(f"Empty file: {file_path}")
                return False
            
            # Try to read as CSV to validate format
            try:
                df = pd.read_csv(file_path, nrows=1)
                return len(df.columns) > 0
            except Exception:
                # If CSV read fails, check if it's a valid zip file
                if file_path.suffix.lower() == '.zip':
                    import zipfile
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                            return len(zip_ref.namelist()) > 0
                    except zipfile.BadZipFile:
                        return False
                return False
                
        except Exception as e:
            self.logger.error(f"Error validating file {file_path}: {e}")
            return False
    
    def get_download_summary(self) -> Dict[str, Any]:
        """
        Get summary of available data and download status
        
        Returns:
            Dictionary with download summary information
        """
        try:
            last_date = self.data_manager.get_last_file_date(self.exchange, self.segment)
            file_count = self.data_manager.get_file_count(self.exchange, self.segment)
            is_first_run = self.data_manager.is_first_run(self.exchange, self.segment)
            
            start_date, end_date = self.get_date_range()
            working_days = self.get_working_days(start_date, end_date)
            
            return {
                'exchange_segment': self.exchange_segment,
                'last_date': last_date.strftime('%Y-%m-%d') if last_date else None,
                'file_count': file_count,
                'is_first_run': is_first_run,
                'next_start_date': start_date.strftime('%Y-%m-%d'),
                'next_end_date': end_date.strftime('%Y-%m-%d'),
                'pending_days': len(working_days),
                'data_path': str(self.data_path)
            }
            
        except Exception as e:
            return {
                'exchange_segment': self.exchange_segment,
                'error': str(e),
                'last_date': None,
                'file_count': 0,
                'is_first_run': True
            }
    
    async def download_data_range(self, 
                                start_date: Optional[date] = None,
                                end_date: Optional[date] = None) -> bool:
        """
        Download data for specified date range
        
        Args:
            start_date: Start date (optional, uses calculated range if None)
            end_date: End date (optional, uses calculated range if None)
            
        Returns:
            True if download completed successfully, False otherwise
        """
        try:
            # Calculate date range
            if start_date is None or end_date is None:
                calc_start, calc_end = self.get_date_range(start_date, end_date)
                start_date = start_date or calc_start
                end_date = end_date or calc_end
            
            # Get working days
            working_days = self.get_working_days(start_date, end_date)
            
            if not working_days:
                self._update_status("No working days in date range")
                return True
            
            self.total_files = len(working_days)
            self.completed_files = 0
            
            self._update_status(f"Starting download for {len(working_days)} days")
            
            # This method should be implemented by concrete classes
            # to handle the actual download logic
            success = await self._download_implementation(working_days)
            
            if success:
                self._update_status("Download completed successfully")
            else:
                self._update_status("Download completed with errors")
            
            return success
            
        except Exception as e:
            error_msg = f"Download failed: {e}"
            self._report_error(error_msg)
            return False
        finally:
            # Always cleanup temp files
            self.cleanup_temp_files()
    
    async def update_async_session_timeout(self, async_manager, new_timeout_seconds: int):
        """
        Update timeout for async download manager session

        Args:
            async_manager: AsyncDownloadManager instance
            new_timeout_seconds: New timeout value in seconds
        """
        try:
            if hasattr(async_manager, 'update_session_timeout'):
                await async_manager.update_session_timeout(new_timeout_seconds)
                self.logger.info(f"Updated async session timeout to {new_timeout_seconds}s for {self.exchange_segment}")
        except Exception as e:
            self.logger.warning(f"Failed to update async session timeout: {e}")

    @abstractmethod
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement actual download logic (to be implemented by concrete classes)

        Args:
            working_days: List of dates to download

        Returns:
            True if successful, False otherwise
        """
        pass
