"""NSE index downloader using the shared validated price pipeline."""

from datetime import date
from typing import List, Optional

import pandas as pd

from ..core.base_downloader import BaseDownloader
from ..core.config import Config
from ..core.exceptions import DataProcessingError
from ..services.canonical_data import normalize_nse_index, read_report
from ..services.source_resolver import price_source
from ..utils.memory_optimizer import MemoryOptimizer


class NSEIndexDownloader(BaseDownloader):
    """Download NSE index reports and publish the seven-column contract."""

    def __init__(self, config: Config):
        super().__init__("NSE", "INDEX", config)
        self.memory_optimizer = MemoryOptimizer()

    def build_url(self, target_date: date) -> str:
        return price_source("NSE", "INDEX", target_date).url

    def process_downloaded_data(
        self, file_data: bytes, file_date: date
    ) -> Optional[pd.DataFrame]:
        try:
            result = self.transform_data(read_report(file_data), file_date)
            self.logger.info(
                f"Processed NSE Index data for {file_date}: {len(result)} rows"
            )
            return result
        except Exception as error:
            raise DataProcessingError(
                f"Error processing NSE Index data for {file_date}: {error}"
            ) from error

    def transform_data(self, df: pd.DataFrame, file_date: date) -> pd.DataFrame:
        try:
            with self.memory_optimizer.memory_monitor("nse_index_transform"):
                result = normalize_nse_index(df, file_date)
                return self.memory_optimizer.optimize_dataframe(result)
        except Exception as error:
            raise DataProcessingError(
                f"Error transforming NSE Index data: {error}"
            ) from error

    async def _download_implementation(self, working_days: List[date]) -> bool:
        return await self._download_price_implementation(working_days)
