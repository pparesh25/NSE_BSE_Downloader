"""
NSE SME Downloader

Downloads and processes NSE SME (Small and Medium Enterprises) data.
Based on the original Getbhavcopy_NSE_SME.py implementation.
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
from ..utils.file_utils import FileUtils
from ..utils.memory_optimizer import MemoryOptimizer
from ..core.exceptions import DataProcessingError


class NSESMEDownloader(BaseDownloader):
    """
    NSE SME data downloader
    
    Downloads NSE SME data and processes according to
    the original implementation logic.
    """
    
    def __init__(self, config: Config):
        """Initialize NSE SME downloader"""
        super().__init__("NSE", "SME", config)
        self.memory_optimizer = MemoryOptimizer()
    
    def build_url(self, target_date: date) -> str:
        """Build NSE SME download URL"""
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

            self.logger.info(f"Processed NSE SME data for {file_date}: {len(transformed_df)} rows")
            return transformed_df

        except Exception as e:
            raise DataProcessingError(f"Error processing NSE SME data for {file_date}: {e}")
    
    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from NSE SME filename format"""
        try:
            # NSE SME format: smeXXXXXX.csv where XXXXXX is ddmmyy
            if filename.startswith("sme") and filename.endswith(".csv"):
                date_part = filename[3:9]  # Extract ddmmyy part
                return date_part
            return None
        except Exception:
            return None
    
    def add_date_column(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Add date column to DataFrame (from original logic)
        
        Args:
            df: Input DataFrame
            file_date: Date to add
            
        Returns:
            DataFrame with date column added
        """
        try:
            # Add DATE column with yyyymmdd format
            date_column_value = file_date.strftime('%Y%m%d')
            df['DATE'] = date_column_value
            
            # Reorder columns - move DATE before OPEN_PRICE
            if 'OPEN_PRICE' in df.columns:
                cols = df.columns.tolist()
                cols.remove('DATE')
                open_idx = cols.index('OPEN_PRICE')
                cols.insert(open_idx, 'DATE')
                df = df[cols]
            
            return df
            
        except Exception as e:
            raise DataProcessingError(f"Error adding date column: {e}")
    
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform NSE SME data according to original logic
        
        Args:
            df: Input DataFrame
            file_date: Date of the data
            
        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("nse_sme_transform"):
                # Add date column first
                df = self.add_date_column(df, file_date)
                
                # Remove specified columns (from original code)
                columns_to_remove = [
                    "MARKET",
                    "SERIES", 
                    "SECURITY",
                    "PREV_CL_PR",
                    "NET_TRDVAL",
                    "CORP_IND",
                    "HI_52_WK",
                    "LO_52_WK"
                ]
                
                # Remove columns that exist in the DataFrame
                existing_columns = [col for col in columns_to_remove if col in df.columns]
                df = df.drop(columns=existing_columns)

                # Add '_SME' suffix to symbol names if option is enabled
                # Check user preferences first, then fallback to config
                from src.utils.user_preferences import UserPreferences
                user_prefs = UserPreferences()
                sme_add_suffix = user_prefs.get_sme_add_suffix()

                if sme_add_suffix and 'SYMBOL' in df.columns:
                    df['SYMBOL'] = df['SYMBOL'].astype(str) + '_SME'
                    self.logger.info("Added '_SME' suffix to symbol names")
                else:
                    self.logger.info("SME suffix option disabled - keeping original symbol names")

                # Optimize memory usage
                df = self.memory_optimizer.optimize_dataframe(df)
                
                self.logger.info(f"Transformed NSE SME data: {len(df)} rows, {len(df.columns)} columns")
                return df
                
        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE SME data: {e}")
    
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement NSE SME download logic with immediate processing

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
                            # Enhanced error handling for NSE SME
                            error_msg = results[0].error_message if results and results[0].error_message else "Download attempt failed - no specific error details"

                            # Build detailed URL for debugging
                            url = self.build_url(target_date)

                            # Check if file is simply not available (weekend/holiday/timeout)
                            if "not available" in error_msg.lower() or "404" in error_msg or "timeout" in error_msg.lower():
                                # Check if it's a weekend or holiday
                                is_weekend = target_date.weekday() >= 5
                                is_holiday = self.config.holiday_manager.is_holiday(target_date)
                                is_current_date = target_date == date.today()

                                if not is_weekend and not is_holiday and not is_current_date:
                                        # NSE SME specific handling - some dates may genuinely not have files
                                        self._report_error(f"⚠️ NSE SME NOTICE: File skipped for {target_date} (not weekend/holiday)")
                                        self.logger.warning(f"  URL attempted: {url}")
                                        self.logger.warning(f"  Error details: {error_msg}")
                                        if target_date.strftime('%Y-%m-%d') == "2025-01-10":
                                            self.logger.warning(f"  Note: 2025-01-10 consistently fails - may be server-specific issue")
                                else:
                                    if is_current_date:
                                        self.logger.info(f"NSE SME file not available for {target_date} (current date - files available after market close)")
                                    else:
                                        self.logger.info(f"NSE SME file not available for {target_date} (weekend/holiday)")
                            else:
                                # Other errors - provide detailed information
                                self._report_error(f"NSE SME download failed for {target_date}: {error_msg}")
                                self.logger.error(f"  URL attempted: {url}")
                                if not error_msg or error_msg == "Download attempt failed - no specific error details":
                                    self.logger.error(f"  This may indicate a connection issue or server problem")

                except Exception as e:
                    self._report_error(f"Error processing {target_date}: {e}")
                    continue

            self.logger.info(f"Successfully processed {success_count}/{len(working_days)} files")
            return success_count > 0

        except Exception as e:
            self._report_error(f"Download implementation failed: {e}")
            return False
