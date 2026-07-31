"""
NSE F&O Downloader

Downloads and processes NSE Futures & Options data with Roman numeral suffixes.
Based on the original New_Nse_fo_roman_suffixes.py implementation.
"""

from datetime import date
from typing import List, Optional
import pandas as pd

from ..core.base_downloader import BaseDownloader
from ..core.config import Config
from ..utils.async_downloader import AsyncDownloadManager, DownloadTask
from ..utils.memory_optimizer import MemoryOptimizer
from ..core.exceptions import DataProcessingError
from ..services.canonical_data import normalize_nse_fo, public_fo, read_report
from ..services.source_resolver import price_source


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
        return price_source("NSE", "FO", target_date).url

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
            df = read_report(file_data)
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
                normalized = normalize_nse_fo(df, file_date)
                legacy = (
                    self.get_download_option("legacy_seven_column_output", False)
                    or not self.get_download_option("include_fo_open_interest", True)
                )
                output = public_fo(normalized, legacy_seven_columns=legacy)
                output = self.memory_optimizer.optimize_dataframe(output)
                self.logger.info(
                    f"Transformed NSE FO data: {len(output)} rows, "
                    f"{len(output.columns)} columns"
                )
                return output

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
