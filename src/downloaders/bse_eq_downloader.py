"""
BSE Equity Downloader

Downloads and processes BSE Equity data.
Based on the original Getbhavcopy_BSE_Eq_New.py implementation.
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
    normalize_bse_equity,
    public_equity,
    read_report,
)
from ..services.source_resolver import price_source


class BSEEQDownloader(BaseDownloader):
    """
    BSE Equity data downloader

    Downloads BSE equity bhavcopy data and processes according to
    the original implementation logic.
    """

    def __init__(self, config: Config):
        """Initialize BSE EQ downloader"""
        super().__init__("BSE", "EQ", config)
        self.memory_optimizer = MemoryOptimizer()

        # Mutual fund symbols to remove (from original code)
        self.mutual_fund_symbols = [
            'SENSEX1', 'SBISENSEX', 'CPSEETF', 'SENSEXBEES', 'SETFBSE100', 'UTISENSETF',
            'HDFCSENSEX', 'BSLSENETFG', 'IDFSENSEXE', 'ICICIB22', 'BSE500IETF', 'SETFSN50',
            'UTISXN50', 'SNXT50BEES', 'SENSEXADD', 'SENSEXETF', 'SENSEXIETF', 'MOM100',
            'NIFTYIETF', 'NIF100IETF', 'NIF100BEES', 'NIFTY1', 'UTINIFTETF', 'LICNETFN50',
            'HDFCNIFTY', 'LICNFNHGP', 'NV20IETF', 'MIDSELIETF', 'LOWVOLIETF', 'UTINEXT50',
            'NEXT50IETF', 'NIFTYETF', 'ABSLNN50ET', 'BANKIETF', 'PVTBANIETF', 'NIESSPJ',
            'NIESSPC', 'NIEHSPI', 'NIESSPE', 'NIEHSPD', 'NIEHSPE', 'NIESSPK', 'NIEHSPG',
            'NIEHSPH', 'NIEHSPL', 'NIESSPL', 'NIESSPM', 'ABSLBANETF', 'MIDCAPIETF', 'NEXT50',
            '08MPD', '08GPG', '11DPR', '11GPG', '11MPD', '11MPR', '11QPD', '11AGG', '11AMD',
            '11DPD', 'ALPL30IETF', 'ITIETF', 'HDFCNIFBAN', 'UTIBANKETF', 'ESG', 'INFRABEES',
            'MAFANG', 'HEALTHIETF', 'BFSI', 'FMCGIETF', 'AXISTECETF', 'AXISHCETF', 'AXISCETF',
            'MASPTOP50', 'CONSUMIETF', 'EQUAL50ADD', 'MAHKTECH', 'MONQ50', 'MIDQ50ADD',
            'NIFTY50ADD', 'AUTOIETF', 'MAKEINDIA', 'MOMOMENTUM', 'TECH', 'HEALTHY', 'BSLNIFTY',
            'MIDCAPETF', 'MOLOWVOL', 'MOHEALTH', 'MOM30IETF', 'HDFCNIF100', 'HDFCNEXT50',
            'INFRAIETF', 'NIFTYQLITY', 'MOMENTUM', 'MOVALUE', 'MOQUALITY', 'HDFCQUAL',
            'HDFCGROWTH', 'HDFCVALUE', 'HDFCLOWVOL', 'HDFCMOMENT', 'HDFCNIFIT', 'HDFCPVTBAN',
            'FINIETF', 'COMMOIETF', 'BANKETFADD', 'HDFCBSE500', 'HDFCSML250', 'HDFCMID150',
            'PSUBNKIETF', 'AXSENSEX', 'LOWVOL', 'ITETFADD', 'BANKETF', 'PSUBANKADD',
            'PVTBANKADD', 'QUAL30IETF', 'NIFMID150', 'NAVINIFTY', 'ITETF', 'ALPHAETF',
            'NIFTYBETF', 'BANKBETF', 'NIFITETF', 'HDFCPSUBK', 'LICNMID100', 'SMALLCAP',
            'MIDSMALL', 'MID150CASE', 'TOP100CASE', 'BBNPNBETF', 'EVINDIA', 'SBINEQWETF',
            'OILIETF', 'ABSLPSE', 'NIFTYBEES', 'JUNIORBEES', 'BANKBEES', 'PSUBANK',
            'PSUBNKBEES', 'SHARIABEES', 'QNIFTY', 'MOM50', 'BANKNIFTY1', 'SETFNIFBK',
            'SETFNIF50'
        ]

    def build_url(self, target_date: date) -> str:
        """Build BSE equity bhavcopy download URL"""
        return price_source("BSE", "EQ", target_date).url

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
            delivery_df = read_report(delivery_data, separator="|") if delivery_data else None
            transformed_df = self.transform_data(df, file_date, delivery_df)

            self.logger.info(f"Processed BSE EQ data for {file_date}: {len(transformed_df)} rows")
            return transformed_df

        except Exception as e:
            raise DataProcessingError(f"Error processing BSE EQ data for {file_date}: {e}")

    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from BSE filename format"""
        try:
            # BSE format: BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV
            if "BhavCopy_BSE_CM_0_0_0_" in filename:
                start_idx = filename.find("BhavCopy_BSE_CM_0_0_0_") + len("BhavCopy_BSE_CM_0_0_0_")
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
        Transform BSE equity data according to original logic

        Args:
            df: Input DataFrame
            file_date: Date of the data

        Returns:
            Transformed DataFrame
        """
        try:
            with self.memory_optimizer.memory_monitor("bse_eq_transform"):
                source = price_source("BSE", "EQ", file_date)
                internal = normalize_bse_equity(
                    df, file_date, era=source.era
                )
                if self.mutual_fund_symbols:
                    internal = internal[
                        ~internal["SYMBOL"].isin(self.mutual_fund_symbols)
                    ].copy()
                internal = merge_delivery(internal, delivery_df, "BSE")
                self.note_delivery_match(file_date, internal)
                self._internal_equity_data = internal
                output = public_equity(internal)
                output = self.memory_optimizer.optimize_dataframe(output)
                self.logger.info(
                    f"Transformed BSE EQ data: {len(output)} rows, "
                    f"{len(output.columns)} columns"
                )
                return output

        except Exception as e:
            raise DataProcessingError(f"Error transforming BSE EQ data: {e}")

    async def _download_implementation(self, working_days: List[date]) -> bool:
        """Download price and delivery reports through the shared cash workflow."""
        return await self._download_equity_implementation(working_days)
