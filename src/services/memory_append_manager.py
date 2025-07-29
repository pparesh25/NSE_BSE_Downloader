"""
Simple Memory-Based Append Manager

Handles data append operations in memory with file availability resilience.
Simple, robust approach without complex state management.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Set

try:
    import pandas as pd
    HAS_PANDAS = True
    DataFrame = pd.DataFrame
except ImportError:
    HAS_PANDAS = False
    # Create a dummy DataFrame type for type annotations
    class DataFrame:
        pass
    pd = None

from ..core.config import Config
from ..core.exceptions import FileOperationError


class MemoryAppendManager:
    """
    Simple memory-based data append manager
    
    Features:
    - Stores data in memory as it becomes available
    - Handles file availability gracefully  
    - Performs append operations when ready
    - No complex state management or timing dependencies
    """
    
    def __init__(self, config: Config):
        """Initialize memory append manager"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Simple memory storage: key -> DataFrame
        self.memory_store: Dict[str, DataFrame] = {}
        
        # Track what data we have for each date
        self.available_data: Dict[str, Set[str]] = {}  # date -> set of data types
        
        self.logger.info("Memory Append Manager initialized")
    
    def _get_data_key(self, exchange: str, segment: str, target_date: date) -> str:
        """Generate storage key for data"""
        return f"{exchange}_{segment}_{target_date}"
    
    def _get_date_key(self, target_date: date) -> str:
        """Generate date key for tracking"""
        return str(target_date)
    
    def store_data(self, exchange: str, segment: str, target_date: date, data: DataFrame) -> bool:
        """
        Store data in memory for append operations
        
        Args:
            exchange: Exchange name (NSE/BSE)
            segment: Segment name (EQ/SME/INDEX)
            target_date: Date of the data
            data: DataFrame to store
            
        Returns:
            True if stored successfully
        """
        try:
            if not HAS_PANDAS:
                self.logger.error("Pandas not available - cannot store data")
                return False

            data_key = self._get_data_key(exchange, segment, target_date)
            date_key = self._get_date_key(target_date)

            # Store data copy in memory
            self.memory_store[data_key] = data.copy()
            
            # Track available data
            if date_key not in self.available_data:
                self.available_data[date_key] = set()
            self.available_data[date_key].add(f"{exchange}_{segment}")
            
            self.logger.info(f"Stored {exchange} {segment} data in memory: {len(data)} rows for {target_date}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error storing data in memory: {e}")
            return False
    
    def has_data(self, exchange: str, segment: str, target_date: date) -> bool:
        """Check if data is available in memory"""
        data_key = self._get_data_key(exchange, segment, target_date)
        return data_key in self.memory_store
    
    def get_data(self, exchange: str, segment: str, target_date: date) -> Optional[DataFrame]:
        """Get data from memory"""
        data_key = self._get_data_key(exchange, segment, target_date)
        return self.memory_store.get(data_key)
    
    def get_available_data_types(self, target_date: date) -> Set[str]:
        """Get available data types for a date"""
        date_key = self._get_date_key(target_date)
        return self.available_data.get(date_key, set())
    
    def is_append_enabled(self, option_name: str) -> bool:
        """Check if append option is enabled"""
        download_options = self.config.get_download_options()
        return download_options.get(option_name, False)
    
    def try_append_operations(self, target_date: date) -> Dict[str, bool]:
        """
        Try all possible append operations for the given date

        Args:
            target_date: Date to process append operations for

        Returns:
            Dictionary with operation results
        """
        results = {}

        try:
            if not HAS_PANDAS:
                self.logger.warning("Pandas not available - skipping append operations")
                return {'pandas_unavailable': False}

            self.logger.info(f"Trying append operations for {target_date}")
            available_types = self.get_available_data_types(target_date)
            self.logger.info(f"Available data types: {available_types}")
            
            # NSE SME + NSE Index → NSE EQ
            if 'NSE_EQ' in available_types:
                results['nse_eq_append'] = self._try_nse_eq_append(target_date)
            
            # BSE Index → BSE EQ  
            if 'BSE_EQ' in available_types:
                results['bse_eq_append'] = self._try_bse_eq_append(target_date)
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in append operations: {e}")
            return {}
    
    def _try_nse_eq_append(self, target_date: date) -> bool:
        """Try NSE EQ append operations (SME + Index)"""
        try:
            # Get base NSE EQ data
            eq_data = self.get_data('NSE', 'EQ', target_date)
            if eq_data is None:
                self.logger.warning(f"NSE EQ data not available for {target_date}")
                return False
            
            combined_data = eq_data.copy()
            append_count = 0
            
            # Add SME data if available and enabled
            if (self.is_append_enabled('sme_append_to_eq') and 
                self.has_data('NSE', 'SME', target_date)):
                
                sme_data = self.get_data('NSE', 'SME', target_date)
                if sme_data is not None and not sme_data.empty:
                    # Ensure SME data has same columns as EQ data
                    aligned_sme_data = self._align_columns_for_append(sme_data, combined_data)
                    combined_data = pd.concat([combined_data, aligned_sme_data], ignore_index=True)
                    append_count += len(aligned_sme_data)
                    self.logger.info(f"Appended {len(aligned_sme_data)} SME rows to NSE EQ")
            
            # Add Index data if available and enabled
            if (self.is_append_enabled('index_append_to_eq') and 
                self.has_data('NSE', 'INDEX', target_date)):
                
                index_data = self.get_data('NSE', 'INDEX', target_date)
                if index_data is not None and not index_data.empty:
                    # Ensure Index data has same columns as EQ data
                    aligned_index_data = self._align_columns_for_append(index_data, combined_data)
                    combined_data = pd.concat([combined_data, aligned_index_data], ignore_index=True)
                    append_count += len(aligned_index_data)
                    self.logger.info(f"Appended {len(aligned_index_data)} Index rows to NSE EQ")
            
            # Append to real NSE EQ file if any data was appended
            if append_count > 0:
                success = self._append_to_real_file('NSE', 'EQ', combined_data, target_date)
                if success:
                    self.logger.info(f"Appended {append_count} additional rows to real NSE EQ file")
                return success
            else:
                self.logger.info("No data to append to NSE EQ")
                return True
                
        except Exception as e:
            self.logger.error(f"Error in NSE EQ append: {e}")
            return False
    
    def _try_bse_eq_append(self, target_date: date) -> bool:
        """Try BSE EQ append operations (Index)"""
        try:
            # Get base BSE EQ data
            eq_data = self.get_data('BSE', 'EQ', target_date)
            if eq_data is None:
                self.logger.warning(f"BSE EQ data not available for {target_date}")
                return False
            
            combined_data = eq_data.copy()
            append_count = 0
            
            # Add Index data if available and enabled
            if (self.is_append_enabled('bse_index_append_to_eq') and 
                self.has_data('BSE', 'INDEX', target_date)):
                
                index_data = self.get_data('BSE', 'INDEX', target_date)
                if index_data is not None and not index_data.empty:
                    combined_data = pd.concat([combined_data, index_data], ignore_index=True)
                    append_count += len(index_data)
                    self.logger.info(f"Appended {len(index_data)} Index rows to BSE EQ")
            
            # Append to real BSE EQ file if any data was appended
            if append_count > 0:
                success = self._append_to_real_file('BSE', 'EQ', combined_data, target_date)
                if success:
                    self.logger.info(f"Appended {append_count} additional rows to real BSE EQ file")
                return success
            else:
                self.logger.info("No data to append to BSE EQ")
                return True
                
        except Exception as e:
            self.logger.error(f"Error in BSE EQ append: {e}")
            return False

    def _align_columns_for_append(self, append_data: DataFrame, base_data: DataFrame) -> DataFrame:
        """
        Align columns of append data to match base data structure

        Args:
            append_data: Data to be appended
            base_data: Base data with target column structure

        Returns:
            DataFrame with aligned columns
        """
        try:
            if not HAS_PANDAS:
                return append_data

            # Get base columns
            base_columns = list(base_data.columns)

            # Create aligned DataFrame with same columns as base
            aligned_data = pd.DataFrame(columns=base_columns)

            # Copy data from append_data to aligned_data for matching columns
            for col in append_data.columns:
                if col in base_columns:
                    aligned_data[col] = append_data[col].values

            # Fill NaN values with empty strings to maintain consistency
            aligned_data = aligned_data.fillna('')

            self.logger.info(f"Aligned {len(append_data)} rows to match base column structure")
            return aligned_data

        except Exception as e:
            self.logger.error(f"Error aligning columns: {e}")
            return append_data

    def _append_to_real_file(self, exchange: str, segment: str, combined_data: DataFrame, target_date: date) -> bool:
        """
        Append data to the real EQ file instead of creating separate combined file

        Args:
            exchange: Exchange name (NSE/BSE)
            segment: Segment name (EQ)
            combined_data: Combined data including EQ + SME + Index
            target_date: Date of the data

        Returns:
            True if append successful
        """
        try:
            if not HAS_PANDAS:
                self.logger.error("Pandas not available - cannot append to real file")
                return False

            # Find the real EQ file
            output_dir = Path(self.config.get_output_directory()) / exchange / segment
            date_str = target_date.strftime("%d%m%Y")

            # Look for existing EQ file (both .csv and .txt formats)
            possible_files = [
                output_dir / f"{exchange}_{segment}_{date_str}.csv",
                output_dir / f"{exchange}_{segment}_{date_str}.txt",
                output_dir / f"{target_date.strftime('%Y-%m-%d')}-{exchange}-{segment}.txt",
                output_dir / f"{target_date.strftime('%Y-%m-%d')}-{exchange}-{segment}.csv"
            ]

            real_file = None
            for file_path in possible_files:
                if file_path.exists():
                    real_file = file_path
                    break

            if not real_file:
                self.logger.warning(f"Real {exchange} {segment} file not found for {target_date}")
                return False

            self.logger.info(f"Found real file: {real_file}")

            # Get only the appended data (exclude original EQ data)
            eq_data = self.get_data(exchange, segment, target_date)
            if eq_data is None:
                return False

            original_count = len(eq_data)
            total_count = len(combined_data)
            append_data = combined_data.iloc[original_count:]  # Get only appended rows

            if len(append_data) == 0:
                self.logger.info("No data to append")
                return True

            # Append to real file without headers
            with open(real_file, 'a', encoding='utf-8') as f:
                # Convert DataFrame to CSV format without headers
                csv_content = append_data.to_csv(index=False, header=False)
                f.write(csv_content)

            self.logger.info(f"Successfully appended {len(append_data)} rows to {real_file}")
            return True

        except Exception as e:
            self.logger.error(f"Error appending to real file: {e}")
            return False

    def _save_combined_file(self, exchange: str, segment: str, data: DataFrame, target_date: date) -> bool:
        """Save combined data to file"""
        try:
            if not HAS_PANDAS:
                self.logger.error("Pandas not available - cannot save combined file")
                return False

            # Get output directory from config
            output_dir = Path(self.config.get_output_directory()) / exchange / segment
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate filename
            date_str = target_date.strftime("%d%m%Y")
            filename = f"{exchange}_{segment}_{date_str}_combined.csv"
            output_path = output_dir / filename
            
            # Save combined data
            data.to_csv(output_path, index=False)
            self.logger.info(f"Saved combined file: {output_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving combined file: {e}")
            return False
    
    def cleanup_memory(self, target_date: date) -> None:
        """Clean up memory for a specific date"""
        try:
            date_key = self._get_date_key(target_date)
            
            # Remove data for this date
            keys_to_remove = [key for key in self.memory_store.keys() if str(target_date) in key]
            for key in keys_to_remove:
                del self.memory_store[key]
            
            # Remove from available data tracking
            if date_key in self.available_data:
                del self.available_data[date_key]
            
            self.logger.info(f"Cleaned up memory for {target_date}")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up memory: {e}")
    
    def get_memory_usage_info(self) -> Dict[str, int]:
        """Get memory usage information"""
        return {
            'stored_dataframes': len(self.memory_store),
            'tracked_dates': len(self.available_data),
            'total_rows': sum(len(df) for df in self.memory_store.values())
        }
