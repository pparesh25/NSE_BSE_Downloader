"""
BSE Index Downloader

Downloads and processes BSE Index data.
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


class BSEIndexDownloader(BaseDownloader):
    """
    BSE Index data downloader
    
    Downloads BSE index data from BSE website.
    URL pattern: https://www.bseindia.com/bsedata/Index_Bhavcopy/INDEXSummary_DDMMYYYY.csv
    """
    
    def __init__(self, config: Config):
        """Initialize BSE Index downloader"""
        super().__init__("BSE", "INDEX", config)
        self.memory_optimizer = MemoryOptimizer()

    def get_date_range(self) -> tuple[date, date]:
        """
        Get date range for BSE Index downloads

        BSE Index files are available from April 17, 2025 onwards
        Override parent method to set correct start date

        Returns:
            Tuple of (start_date, end_date)
        """
        # Get the standard date range from parent
        start_date, end_date = super().get_date_range()

        # BSE INDEX files available from April 17, 2025
        bse_index_start = date(2025, 4, 17)

        # Use the later of calculated start_date or BSE INDEX availability
        if start_date < bse_index_start:
            original_start = start_date
            start_date = bse_index_start
            self.logger.info(f"🔍 BSE INDEX start date adjusted:")
            self.logger.info(f"  Original start: {original_start}")
            self.logger.info(f"  Adjusted start: {start_date} (BSE INDEX availability)")

        self.logger.info(f"BSE INDEX date range: {start_date} to {end_date}")
        return start_date, end_date
    
    def build_url(self, target_date: date) -> str:
        """
        Build BSE index download URL

        URL pattern: https://www.bseindia.com/bsedata/Index_Bhavcopy/INDEXSummary_DDMMYYYY.csv
        Example: https://www.bseindia.com/bsedata/Index_Bhavcopy/INDEXSummary_23072025.csv
        """
        date_str = self.exchange_config.date_format
        formatted_date = target_date.strftime(date_str)
        filename = self.exchange_config.filename_pattern.format(date=formatted_date)
        url = f"{self.exchange_config.base_url}/{filename}"

        # Debug logging
        self.logger.info(f"🔍 BSE INDEX URL Debug:")
        self.logger.info(f"  Target Date: {target_date}")
        self.logger.info(f"  Date Format: {date_str}")
        self.logger.info(f"  Formatted Date: {formatted_date}")
        self.logger.info(f"  Filename: {filename}")
        self.logger.info(f"  Final URL: {url}")

        return url
    
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

            # Debug logging
            self.logger.info(f"🔍 BSE INDEX Processing Debug:")
            self.logger.info(f"  File Date: {file_date}")
            self.logger.info(f"  File Data Size: {len(file_data)} bytes")

            # Check if file data is valid
            if len(file_data) == 0:
                self.logger.error(f"❌ Empty file data for {file_date}")
                return None

            # Preview first 200 characters of file data
            try:
                preview = file_data[:200].decode('utf-8', errors='ignore')
                self.logger.info(f"  File Preview: {preview}")
            except Exception as e:
                self.logger.warning(f"  Could not preview file data: {e}")

            # Read CSV data from memory
            df = pd.read_csv(io.BytesIO(file_data))

            self.logger.info(f"  Original DataFrame Shape: {df.shape}")
            self.logger.info(f"  Original Columns: {list(df.columns)}")

            # Transform data
            transformed_df = self.transform_data(df, file_date)

            self.logger.info(f"✅ Processed BSE Index data for {file_date}: {len(transformed_df)} rows")
            return transformed_df

        except Exception as e:
            self.logger.error(f"❌ Error processing BSE Index data for {file_date}: {e}")
            self.logger.error(f"   File data length: {len(file_data) if file_data else 'None'}")
            raise DataProcessingError(f"Error processing BSE Index data for {file_date}: {e}")
    
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform BSE Index data according to standard format

        Original columns: IndexCode, IndexID, IndexName, PreviousClose, OpenPrice,
                         HighPrice, LowPrice, ClosePrice, 52weeksHigh, 52weeksLow,
                         Filler1, Filler2, Filler3, Filler4

        Keep only: IndexName, OpenPrice, HighPrice, LowPrice, ClosePrice + Date

        Args:
            df: Input DataFrame
            file_date: Date of the data

        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("bse_index_transform"):
                self.logger.info(f"🔍 BSE INDEX Transform Debug:")
                self.logger.info(f"  Input DataFrame Shape: {df.shape}")
                self.logger.info(f"  Input Columns: {list(df.columns)}")

                # Define columns to keep
                columns_to_keep = ['IndexName', 'OpenPrice', 'HighPrice', 'LowPrice', 'ClosePrice']
                self.logger.info(f"  Required Columns: {columns_to_keep}")

                # Check if required columns exist
                missing_columns = [col for col in columns_to_keep if col not in df.columns]
                if missing_columns:
                    self.logger.warning(f"⚠️ Missing columns in BSE Index data: {missing_columns}")
                    self.logger.info(f"  Available columns: {list(df.columns)}")
                    # Use available columns only
                    columns_to_keep = [col for col in columns_to_keep if col in df.columns]
                    self.logger.info(f"  Using available columns: {columns_to_keep}")

                # Keep only required columns
                if columns_to_keep:
                    df = df[columns_to_keep].copy()
                    self.logger.info(f"  After column filtering: {df.shape}")
                else:
                    self.logger.error("❌ No valid columns found in BSE Index data")
                    return pd.DataFrame()

                # Add date column for each row
                date_str = file_date.strftime('%Y%m%d')
                df['Date'] = date_str
                self.logger.info(f"  Added Date column: {date_str}")

                # Reorder columns: IndexName first, then Date, then price columns
                # Required format: IndexName, Date, OpenPrice, HighPrice, LowPrice, ClosePrice
                column_order = ['IndexName', 'Date', 'OpenPrice', 'HighPrice', 'LowPrice', 'ClosePrice']

                # Ensure all required columns exist
                available_columns = [col for col in column_order if col in df.columns]
                if len(available_columns) != len(column_order):
                    missing = [col for col in column_order if col not in df.columns]
                    self.logger.warning(f"  Missing columns for final order: {missing}")
                    column_order = available_columns

                df = df[column_order]
                self.logger.info(f"  Final column order: {column_order}")

                # Show sample data
                if len(df) > 0:
                    self.logger.info(f"  Sample row: {df.iloc[0].to_dict()}")

                # Optimize memory usage
                df = self.memory_optimizer.optimize_dataframe(df)

                self.logger.info(f"✅ Transformed BSE Index data: {len(df)} rows, {len(df.columns)} columns")
                self.logger.info(f"  Final Columns: {list(df.columns)}")
                return df

        except Exception as e:
            raise DataProcessingError(f"Error transforming BSE Index data: {e}")

    def save_processed_data(self, df: pd.DataFrame, target_date: date) -> Path:
        """
        Save BSE Index processed data without header

        Args:
            df: Processed DataFrame
            target_date: Date of the data

        Returns:
            Path to saved file
        """
        try:
            filename = self.build_filename(target_date)
            output_path = self.data_path / filename

            # Save without header and index for BSE Index data
            # Format: IndexName, Date, OpenPrice, HighPrice, LowPrice, ClosePrice
            df.to_csv(output_path, index=False, header=False)

            self.logger.info(f"Saved BSE Index data: {filename} ({len(df)} rows)")
            return output_path

        except Exception as e:
            from ..core.exceptions import FileOperationError
            raise FileOperationError(
                f"Failed to save BSE Index data for {target_date}",
                file_path=str(output_path),
                operation="save_csv"
            ) from e

    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement BSE Index download logic with immediate processing
        
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

                    self.logger.info(f"🔍 BSE INDEX Download Debug for {target_date}:")

                    # Create download task
                    url = self.build_url(target_date)
                    self.logger.info(f"  Download URL: {url}")

                    task = DownloadTask(
                        url=url,
                        date_str=target_date.strftime('%Y-%m-%d'),
                        target_date=target_date
                    )
                    self.logger.info(f"  Download Task Created: {task.date_str}")
                    
                    # Download file
                    self.logger.info(f"  Starting download...")
                    async with AsyncDownloadManager(self.config) as download_manager:
                        results = await download_manager.download_multiple([task])

                        self.logger.info(f"  Download completed. Results: {len(results) if results else 0}")

                        if results and results[0].success:
                            result = results[0]
                            self.logger.info(f"  ✅ Download successful!")
                            self.logger.info(f"  File size: {result.file_size if hasattr(result, 'file_size') else 'Unknown'}")
                            self.logger.info(f"  Download time: {result.download_time if hasattr(result, 'download_time') else 'Unknown'}")

                            # Process downloaded data immediately
                            self.logger.info(f"  Processing downloaded data...")
                            processed_df = self.process_downloaded_data(result.file_data, target_date)

                            if processed_df is not None and len(processed_df) > 0:
                                # Save processed data
                                self.logger.info(f"  Saving processed data...")
                                saved_path = self.save_processed_data(processed_df, target_date)
                                self.logger.info(f"  ✅ Data saved to: {saved_path}")
                                success_count += 1

                                # Update progress
                                self.completed_files += 1
                                progress = int(((i + 1) / len(working_days)) * 100)
                                self._update_progress(f"Completed {target_date}")

                                self.logger.info(f"✅ Successfully processed {target_date}")
                            else:
                                self.logger.error(f"❌ Failed to process data for {target_date} - Empty or invalid DataFrame")
                                self._report_error(f"Failed to process data for {target_date}")
                        else:
                            error_msg = results[0].error_message if results else "Unknown download error"
                            self.logger.error(f"❌ Download failed for {target_date}: {error_msg}")
                            # In fast mode, don't report as error if file is simply not available
                            if hasattr(self.config.download_settings, 'fast_mode') and self.config.download_settings.fast_mode:
                                if "not available" in error_msg.lower() or "404" in error_msg:
                                    # Check if it's a weekend or holiday
                                    is_weekend = target_date.weekday() >= 5
                                    is_holiday = self.config.holiday_manager.is_holiday(target_date)
                                    is_current_date = target_date == date.today()

                                    if not is_weekend and not is_holiday and not is_current_date:
                                        # File skipped for non-weekend, non-holiday, non-current date - notify user
                                        self._report_error(f"⚠️ NOTICE: File skipped for {target_date} (not weekend/holiday) - Server timeout or file unavailable")
                                    else:
                                        if is_current_date:
                                            self.logger.info(f"File not available for {target_date} (current date - files available after market close)")
                                        else:
                                            self.logger.info(f"File not available for {target_date} (weekend/holiday)")
                                else:
                                    self._report_error(f"Download failed for {target_date}: {error_msg}")
                            else:
                                self._report_error(f"Download failed for {target_date}: {error_msg}")
                            
                except Exception as e:
                    self._report_error(f"Error processing {target_date}: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {success_count}/{len(working_days)} files")
            return success_count > 0
            
        except Exception as e:
            self._report_error(f"Download implementation failed: {e}")
            return False
