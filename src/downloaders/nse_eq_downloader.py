"""
NSE Equity Downloader

Downloads and processes NSE Equity data with index data integration.
Based on the original Final_Bhavcopy_index_2024.py implementation.
"""

import asyncio
import zipfile
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
from ..core.exceptions import DataProcessingError, NetworkError


class NSEEQDownloader(BaseDownloader):
    """
    NSE Equity data downloader with index data integration
    
    Downloads both equity bhavcopy and index data, processes and combines them
    according to the original implementation logic.
    """
    
    def __init__(self, config: Config):
        """Initialize NSE EQ downloader"""
        super().__init__("NSE", "EQ", config)
        self.memory_optimizer = MemoryOptimizer()
    
    def build_url(self, target_date: date) -> str:
        """Build NSE equity bhavcopy download URL"""
        date_str = self.exchange_config.date_format
        formatted_date = target_date.strftime(date_str)
        filename = self.exchange_config.filename_pattern.format(date=formatted_date)
        return f"{self.exchange_config.base_url}/{filename}"
    
    def process_downloaded_data(self, file_data: bytes, file_date: date) -> Optional[pd.DataFrame]:
        """
        Process downloaded ZIP file data in memory

        Args:
            file_data: Downloaded ZIP file data as bytes
            file_date: Date of the data

        Returns:
            Processed DataFrame
        """
        try:
            import zipfile
            import io

            # Extract ZIP file from memory
            with zipfile.ZipFile(io.BytesIO(file_data), 'r') as zip_ref:
                # Find CSV file in ZIP
                csv_files = [name for name in zip_ref.namelist() if name.endswith('.csv')]

                if not csv_files:
                    self.logger.warning(f"No CSV file found in ZIP for {file_date}")
                    return None

                # Read CSV data from ZIP
                csv_data = zip_ref.read(csv_files[0])

                # Read CSV into DataFrame
                df = pd.read_csv(io.BytesIO(csv_data))

                # Transform data
                transformed_df = self.transform_data(df, file_date)

                self.logger.info(f"Processed NSE EQ data for {file_date}: {len(transformed_df)} rows")
                return transformed_df

        except Exception as e:
            raise DataProcessingError(f"Error processing NSE EQ data for {file_date}: {e}")
    
    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from NSE filename format"""
        try:
            # NSE format: BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv
            if "BhavCopy_NSE_CM_0_0_0_" in filename:
                start_idx = filename.find("BhavCopy_NSE_CM_0_0_0_") + len("BhavCopy_NSE_CM_0_0_0_")
                date_str = filename[start_idx:start_idx+8]
                return date_str
            return None
        except Exception:
            return None
    
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform NSE equity data according to original logic
        
        Args:
            df: Input DataFrame
            file_date: Date of the data
            
        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("nse_eq_transform"):
                # Remove specified columns (from original code)
                columns_to_remove = [
                    'BizDt', 'Sgmt', 'Src', 'FinInstrmTp', 'FinInstrmId', 'ISIN', 
                    'XpryDt', 'FininstrmActlXpryDt', 'StrkPric', 'OptnTp', 'FinInstrmNm', 
                    'LastPric', 'PrvsClsgPric', 'UndrlygPric', 'SttlmPric', 'OpnIntrst', 
                    'ChngInOpnIntrst', 'TtlTrfVal', 'TtlNbOfTxsExctd', 'SsnId', 
                    'NewBrdLotQty', 'Rmks', 'Rsvd1', 'Rsvd2', 'Rsvd3', 'Rsvd4'
                ]
                
                # Remove columns that exist in the DataFrame
                existing_columns = [col for col in columns_to_remove if col in df.columns]
                df = df.drop(columns=existing_columns)
                
                # Filter rows based on SERIES column
                if 'SctySrs' in df.columns:
                    df = df[df['SctySrs'].isin(['EQ', 'BE', 'BZ'])]
                
                # Convert and format TradDt column
                if 'TradDt' in df.columns:
                    df['TradDt'] = pd.to_datetime(df['TradDt'], errors='coerce')
                    
                    # Check for conversion issues
                    if df['TradDt'].isna().any():
                        self.logger.warning("Some dates couldn't be converted in TradDt column")
                    
                    # Convert to desired format
                    df['TradDt'] = df['TradDt'].dt.strftime('%Y%m%d')
                    
                    # Reorder columns - move TradDt before OpnPric
                    if 'OpnPric' in df.columns:
                        cols = df.columns.tolist()
                        cols.remove('TradDt')
                        opn_idx = cols.index('OpnPric')
                        cols.insert(opn_idx, 'TradDt')
                        df = df[cols]
                
                # Remove SctySrs column if it exists
                if 'SctySrs' in df.columns:
                    df = df.drop(columns=['SctySrs'])
                
                # Sort by TckrSymb
                if 'TckrSymb' in df.columns:
                    df = df.sort_values(by='TckrSymb')
                
                # Optimize memory usage
                df = self.memory_optimizer.optimize_dataframe(df)
                
                self.logger.info(f"Transformed NSE EQ data: {len(df)} rows, {len(df.columns)} columns")
                return df
                
        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE EQ data: {e}")
    

    
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement NSE EQ download logic with immediate processing

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
