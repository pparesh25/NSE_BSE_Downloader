"""
NSE SME Downloader

Downloads and processes NSE SME (Small and Medium Enterprises) data.
Based on the original Getbhavcopy_NSE_SME.py implementation.
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
    normalize_nse_sme,
    public_equity,
    read_report,
)
from ..services.source_resolver import price_source


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
        return price_source("NSE", "SME", target_date).url

    def process_downloaded_data(
        self,
        file_data: bytes,
        file_date: date,
        delivery_data: Optional[bytes] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Process downloaded CSV file data in memory

        Args:
            file_data: Downloaded CSV file data as bytes
            file_date: Date of the data

        Returns:
            Processed DataFrame
        """
        try:
            df = read_report(file_data)
            delivery_df = read_report(delivery_data) if delivery_data else None
            transformed_df = self.transform_data(df, file_date, delivery_df)

            self.logger.info(f"Processed NSE SME data for {file_date}: {len(transformed_df)} rows")
            return transformed_df

        except Exception as e:
            raise DataProcessingError(f"Error processing NSE SME data for {file_date}: {e}")

    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from NSE SME filename format"""
        try:
            # NSE SME used ddmmyy through 2025-10-10 and ddmmyyyy from
            # 2025-10-13 onward.
            if filename.startswith("sme") and filename.endswith(".csv"):
                date_part = filename[3:-4]
                return date_part if len(date_part) in (6, 8) else None
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

    def transform_data(
        self,
        df: pd.DataFrame,
        file_date: date,
        delivery_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
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
                from ..utils.user_preferences import UserPreferences
                user_prefs = UserPreferences(self.config)
                sme_add_suffix = user_prefs.get_sme_add_suffix()
                source = price_source("NSE", "SME", file_date)
                internal = normalize_nse_sme(
                    df,
                    file_date,
                    add_suffix=sme_add_suffix,
                    era=source.era,
                )
                internal = merge_delivery(internal, delivery_df, "NSE")
                self._internal_equity_data = internal
                legacy = self.get_download_option("legacy_seven_column_output", False)
                output = public_equity(internal, legacy_seven_columns=legacy)
                output = self.memory_optimizer.optimize_dataframe(output)
                self.logger.info(
                    f"Transformed NSE SME data: {len(output)} rows, "
                    f"{len(output.columns)} columns"
                )
                return output

        except Exception as e:
            raise DataProcessingError(f"Error transforming NSE SME data: {e}")

    async def _download_implementation(self, working_days: List[date]) -> bool:
        """Download price and delivery reports through the shared cash workflow."""
        return await self._download_equity_implementation(working_days)
