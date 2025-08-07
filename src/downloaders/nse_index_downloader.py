"""
NSE Index Downloader

Downloads and processes NSE Index data separately from equity data.
"""

import asyncio
from datetime import date
from pathlib import Path
from typing import List, Optional
import pandas as pd
import logging

from ..core.base_downloader import BaseDownloader
from ..core.config import Config
from ..utils.async_downloader import AsyncDownloadManager, DownloadTask
from ..utils.memory_optimizer import MemoryOptimizer
from ..core.exceptions import DataProcessingError


class NSEIndexDownloader(BaseDownloader):
    """
    NSE Index data downloader
    
    Downloads NSE index data separately from equity data.
    """
    
    def __init__(self, config: Config):
        """Initialize NSE Index downloader"""
        super().__init__("NSE", "INDEX", config)
        self.memory_optimizer = MemoryOptimizer()
    
    def build_url(self, target_date: date) -> str:
        """Build NSE index download URL"""
        date_str = self.exchange_config.date_format
        formatted_date = target_date.strftime(date_str)
        filename = self.exchange_config.filename_pattern.format(date=formatted_date)
        return f"{self.exchange_config.base_url}/{filename}"
    
    def process_downloaded_data(self, file_data: bytes, file_date: date) -> Optional[pd.DataFrame]:
        """
        Process downloaded CSV file data in memory
        
        Args:
            file_data: Downloaded CSV file data as bytes
            file_date: Date of the data
            
        Returns:
            Processed DataFrame
        """
        try:
            import io
            
            # Read CSV data from memory
            df = pd.read_csv(io.BytesIO(file_data))
            
            # Transform data
            transformed_df = self.transform_data(df, file_date)
            
            self.logger.info(f"Processed NSE Index data for {file_date}: {len(transformed_df)} rows")
            return transformed_df
            
        except Exception as e:
            raise DataProcessingError(f"Error processing NSE Index data for {file_date}: {e}")
    
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform NSE Index data according to original logic
        
        Args:
            df: Input DataFrame
            file_date: Date of the data
            
        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("nse_index_transform"):
                # Remove unwanted columns (from original code)
                columns_to_remove = ['Points Change', 'Change(%)', 'Turnover (Rs. Cr.)', 'P/E', 'P/B', 'Div Yield']
                existing_columns = [col for col in columns_to_remove if col in df.columns]
                df = df.drop(columns=existing_columns)
                
                # Convert Index Date to desired format
                if 'Index Date' in df.columns:
                    df['Index Date'] = pd.to_datetime(df['Index Date'], format='%d-%m-%Y', errors='coerce').dt.strftime('%Y%m%d')
                else:
                    # Add date column if not present
                    df['Index Date'] = file_date.strftime('%Y%m%d')
                
                # Ensure date consistency
                expected_date = file_date.strftime('%Y%m%d')
                if 'Index Date' in df.columns:
                    df['Index Date'] = expected_date
                
                # Optimize memory usage
                df = self.memory_optimizer.optimize_dataframe(df)
                
                self.logger.info(f"Transformed NSE Index data: {len(df)} rows, {len(df.columns)} columns")
                return df
                
        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE Index data: {e}")
    
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement NSE Index download logic with immediate processing
        
        Args:
            working_days: List of dates to download
            
        Returns:
            True if successful, False otherwise
        """
        try:
            success_count = 0
            
            # Process files one by one for immediate progress updates
            for i, target_date in enumerate(working_days):
                try:
                    # Skip current date if it's before 6:00 PM on a trading day
                    from ..utils.date_utils import DateUtils
                    today = date.today()
                    if (target_date == today and
                        DateUtils.is_trading_day(today) and
                        not DateUtils.is_data_available_time()):
                        self.logger.info(f"Skipping {target_date} (current trading day, data available after 6:00 PM)")
                        continue

                    # Update progress
                    progress = int((i / len(working_days)) * 100)
                    self._update_progress(f"Processing {target_date} ({i+1}/{len(working_days)})")

                    # Create download task
                    url = self.build_url(target_date)
                    task = DownloadTask(
                        url=url,
                        date_str=target_date.strftime('%Y-%m-%d'),
                        target_date=target_date
                    )
                    
                    # Download file
                    async with AsyncDownloadManager(self.config) as download_manager:
                        # Update session timeout to current config value
                        await self.update_async_session_timeout(download_manager, self.config.download_settings.timeout_seconds)

                        results = await download_manager.download_multiple([task])
                        
                        if results and results[0].success:
                            result = results[0]
                            
                            # Process downloaded data immediately
                            processed_df = self.process_downloaded_data(result.file_data, target_date)
                            
                            if processed_df is not None:
                                # Save processed data
                                self.save_processed_data(processed_df, target_date)
                                success_count += 1
                                
                                # Update progress
                                self.completed_files += 1
                                progress = int(((i + 1) / len(working_days)) * 100)
                                self._update_progress(f"Completed {target_date}")
                                
                                self.logger.info(f"Successfully processed {target_date}")
                            else:
                                self._report_error(f"Failed to process data for {target_date}")
                        else:
                            error_msg = results[0].error_message if results else "Unknown download error"
                            # Check if file is simply not available (weekend/holiday/timeout)
                            if "not available" in error_msg.lower() or "404" in error_msg:
                                # Check if it's a weekend or holiday
                                is_weekend = target_date.weekday() >= 5
                                is_holiday = self.config.holiday_manager.is_holiday(target_date)
                                is_current_date = target_date == date.today()

                                if not is_weekend and not is_holiday and not is_current_date:
                                    # File skipped for non-weekend, non-holiday, non-current date - notify user
                                    self._report_notice(f"⚠️ NSE INDEX NOTICE: File skipped for {target_date} (not weekend/holiday) - Server timeout or file unavailable")
                                else:
                                    if is_current_date:
                                        self.logger.info(f"File not available for {target_date} (current date - files available after market close)")
                                    else:
                                        self.logger.info(f"File not available for {target_date} (weekend/holiday)")
                            else:
                                self._report_error(f"NSE INDEX download failed for {target_date}: {error_msg}")
                            
                except Exception as e:
                    self._report_error(f"Error processing {target_date}: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {success_count}/{len(working_days)} files")
            return success_count > 0
            
        except Exception as e:
            self._report_error(f"Download implementation failed: {e}")
            return False
