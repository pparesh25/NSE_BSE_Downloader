"""
NSE F&O Downloader

Downloads and processes NSE Futures & Options data with Roman numeral suffixes.
Based on the original New_Nse_fo_roman_suffixes.py implementation.
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
from ..core.exceptions import DataProcessingError


class NSEFODownloader(BaseDownloader):
    """
    NSE F&O data downloader with Roman numeral suffix processing
    
    Downloads NSE F&O data and processes with Roman numeral suffixes
    according to the original implementation logic.
    """
    
    def __init__(self, config: Config):
        """Initialize NSE FO downloader"""
        super().__init__("NSE", "FO", config)
        self.memory_optimizer = MemoryOptimizer()
    
    def build_url(self, target_date: date) -> str:
        """Build NSE F&O download URL"""
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

                self.logger.info(f"Processed NSE FO data for {file_date}: {len(transformed_df)} rows")
                return transformed_df

        except Exception as e:
            raise DataProcessingError(f"Error processing NSE FO data for {file_date}: {e}")
    
    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from NSE F&O filename format"""
        try:
            # NSE F&O format: BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv
            if "BhavCopy_NSE_FO_0_0_0_" in filename:
                start_idx = filename.find("BhavCopy_NSE_FO_0_0_0_") + len("BhavCopy_NSE_FO_0_0_0_")
                date_str = filename[start_idx:start_idx+8]
                return date_str
            return None
        except Exception:
            return None
    
    @staticmethod
    def int_to_roman(num: int) -> str:
        """
        Convert integer to Roman numeral (from original code)
        
        Args:
            num: Integer to convert
            
        Returns:
            Roman numeral string
        """
        val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
        syb = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
        
        roman_num = ''
        i = 0
        while num > 0:
            for _ in range(num // val[i]):
                roman_num += syb[i]
                num -= val[i]
            i += 1
        return roman_num
    
    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        """
        Transform NSE F&O data according to original logic with Roman suffixes
        
        Args:
            df: Input DataFrame
            file_date: Date of the data
            
        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("nse_fo_transform"):
                # Filter rows based on FinInstrmTp column (STF, IDF)
                if 'FinInstrmTp' in df.columns:
                    df = df[df['FinInstrmTp'].isin(['STF', 'IDF'])]
                
                # Remove specified columns (from original code)
                columns_to_remove = [
                    'BizDt', 'Sgmt', 'Src', 'FinInstrmTp', 'FinInstrmId', 'ISIN', 'SctySrs',
                    'FininstrmActlXpryDt', 'StrkPric', 'OptnTp', 'FinInstrmNm', 'LastPric',
                    'PrvsClsgPric', 'UndrlygPric', 'SttlmPric', 'OpnIntrst', 'ChngInOpnIntrst',
                    'TtlTrfVal', 'TtlNbOfTxsExctd', 'SsnId', 'NewBrdLotQty', 'Rmks',
                    'Rsvd1', 'Rsvd2', 'Rsvd3', 'Rsvd4'
                ]
                
                # Remove columns that exist in the DataFrame
                existing_columns = [col for col in columns_to_remove if col in df.columns]
                df = df.drop(columns=existing_columns)
                
                # Sort by TckrSymb
                if 'TckrSymb' in df.columns:
                    df = df.sort_values(by='TckrSymb')
                
                # Convert XpryDt to datetime and sort
                if 'XpryDt' in df.columns:
                    df['XpryDt'] = pd.to_datetime(df['XpryDt'])
                    df = df.sort_values(by=['TckrSymb', 'XpryDt']).reset_index(drop=True)
                    
                    # Create incremental numbering in Roman numerals
                    df['SYMBOL_NEW'] = df.groupby('TckrSymb').cumcount() + 1
                    df['SYMBOL_NEW'] = df.apply(
                        lambda row: f"{row['TckrSymb']}-{self.int_to_roman(row['SYMBOL_NEW'])}", 
                        axis=1
                    )
                
                # Convert and format TradDt column
                if 'TradDt' in df.columns:
                    df['TradDt'] = pd.to_datetime(df['TradDt'], errors='coerce')
                    
                    # Check for conversion issues
                    if df['TradDt'].isna().any():
                        self.logger.warning("Some dates couldn't be converted in TradDt column")
                    
                    # Convert to desired format
                    df['TradDt'] = df['TradDt'].dt.strftime('%Y%m%d')
                    
                    # Reorder columns
                    if 'OpnPric' in df.columns and 'SYMBOL_NEW' in df.columns:
                        cols = df.columns.tolist()
                        cols.remove('TradDt')
                        cols.remove('SYMBOL_NEW')
                        
                        opn_idx = cols.index('OpnPric')
                        cols.insert(opn_idx, 'TradDt')
                        cols.insert(opn_idx, 'SYMBOL_NEW')
                        df = df[cols]
                
                # Remove original columns that are no longer needed
                final_columns_to_remove = ['TckrSymb', 'XpryDt']
                existing_final_columns = [col for col in final_columns_to_remove if col in df.columns]
                df = df.drop(columns=existing_final_columns)
                
                # Optimize memory usage
                df = self.memory_optimizer.optimize_dataframe(df)
                
                self.logger.info(f"Transformed NSE FO data: {len(df)} rows, {len(df.columns)} columns")
                return df
                
        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE FO data: {e}")
    
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement NSE FO download logic with immediate processing

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
                            # Enhanced error handling for NSE FO
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
                                        # File skipped for non-weekend, non-holiday, non-current date - notify user with more details
                                        self._report_error(f"⚠️ NSE FO NOTICE: File skipped for {target_date} (not weekend/holiday)")
                                        self.logger.warning(f"  URL attempted: {url}")
                                        self.logger.warning(f"  Error details: {error_msg}")
                                else:
                                    if is_current_date:
                                        self.logger.info(f"NSE FO file not available for {target_date} (current date - files available after market close)")
                                    else:
                                        self.logger.info(f"NSE FO file not available for {target_date} (weekend/holiday)")
                            else:
                                # Other errors - provide detailed information
                                self._report_error(f"NSE FO download failed for {target_date}: {error_msg}")
                                self.logger.error(f"  URL attempted: {url}")
                                if not error_msg or error_msg == "Download attempt failed - no specific error details":
                                    self.logger.error(f"  This may indicate a connection issue or server problem")
                                self.logger.error(f"  URL attempted: {url}")

                except Exception as e:
                    self._report_error(f"Error processing {target_date}: {e}")
                    continue

            self.logger.info(f"Successfully processed {success_count}/{len(working_days)} files")
            return success_count > 0

        except Exception as e:
            self._report_error(f"Download implementation failed: {e}")
            return False
