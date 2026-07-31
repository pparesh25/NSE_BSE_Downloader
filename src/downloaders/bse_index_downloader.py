"""BSE index downloader using the shared validated price pipeline."""

from datetime import date
from typing import List, Optional

import pandas as pd

from ..core.base_downloader import BaseDownloader
from ..core.config import Config
from ..core.exceptions import DataProcessingError
from ..services.canonical_data import normalize_bse_index, read_report
from ..services.source_resolver import price_source
from ..utils.memory_optimizer import MemoryOptimizer


class BSEIndexDownloader(BaseDownloader):
    """Download BSE index reports and publish the seven-column contract."""

    FIRST_AVAILABLE_DATE = date(2025, 4, 17)

    def __init__(self, config: Config):
        super().__init__("BSE", "INDEX", config)
        self.memory_optimizer = MemoryOptimizer()

    def get_date_range(
        self,
        custom_start: Optional[date] = None,
        custom_end: Optional[date] = None,
    ) -> tuple[date, date]:
        start_date, end_date = super().get_date_range(custom_start, custom_end)
        return max(start_date, self.FIRST_AVAILABLE_DATE), end_date

    def build_url(self, target_date: date) -> str:
        return price_source("BSE", "INDEX", target_date).url

    def process_downloaded_data(
        self,
        file_data: bytes,
        file_date: date,
        delivery_data: Optional[bytes] = None,
    ) -> Optional[pd.DataFrame]:
        try:
            result = self.transform_data(read_report(file_data), file_date)
            self.logger.info(
                f"Processed BSE Index data for {file_date}: {len(result)} rows"
            )
            return result
        except Exception as error:
            raise DataProcessingError(
                f"Error processing BSE Index data for {file_date}: {error}"
            ) from error

    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        try:
            with self.memory_optimizer.memory_monitor("bse_index_transform"):
                result = normalize_bse_index(df, file_date)
                return self.memory_optimizer.optimize_dataframe(result)
        except Exception as error:
            raise DataProcessingError(
                f"Error transforming BSE Index data: {error}"
            ) from error

    async def _download_implementation(self, working_days: List[date]) -> bool:
        return await self._download_price_implementation(working_days)
