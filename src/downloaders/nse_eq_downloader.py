"""
NSE Equity Downloader

Downloads and processes NSE Equity data with index data integration.
Based on the original Final_Bhavcopy_index_2024.py implementation.
"""

from datetime import date
from typing import List, Optional
import pandas as pd

from ..core.base_downloader import BaseDownloader
from ..core.config import Config
from ..utils.memory_optimizer import MemoryOptimizer
from ..core.exceptions import DataProcessingError
from ..services.canonical_data import (
    merge_delivery,
    normalize_nse_equity,
    public_equity,
    read_report,
)
from ..services.source_resolver import price_source


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
        return price_source("NSE", "EQ", target_date).url

    def process_downloaded_data(
        self,
        file_data: bytes,
        file_date: date,
        delivery_data: Optional[bytes] = None,
    ) -> Optional[pd.DataFrame]:
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
            delivery_df = read_report(delivery_data) if delivery_data else None
            transformed_df = self.transform_data(df, file_date, delivery_df)
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

    def transform_data(
        self,
        df: pd.DataFrame,
        file_date: date,
        delivery_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
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
                source = price_source("NSE", "EQ", file_date)
                internal = normalize_nse_equity(
                    df, file_date, era=source.era
                )
                internal = merge_delivery(internal, delivery_df, "NSE")
                self._internal_equity_data = internal
                legacy = self.get_download_option("legacy_seven_column_output", False)
                output = public_equity(internal, legacy_seven_columns=legacy)
                output = self.memory_optimizer.optimize_dataframe(output)
                self.logger.info(
                    f"Transformed NSE EQ data: {len(output)} rows, "
                    f"{len(output.columns)} columns"
                )
                return output

        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE EQ data: {e}")

    async def _download_implementation(self, working_days: List[date]) -> bool:
        """Download price and delivery reports through the shared cash workflow."""
        return await self._download_equity_implementation(working_days)
