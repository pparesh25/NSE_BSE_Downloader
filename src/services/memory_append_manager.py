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
from ..utils.user_preferences import UserPreferences


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
        self.user_prefs = UserPreferences()
        self.logger = logging.getLogger(__name__)
        
        # Simple memory storage: key -> DataFrame
        self.memory_store: Dict[str, DataFrame] = {}

        # Track what data we have for each date
        self.available_data: Dict[str, Set[str]] = {}  # date -> set of data types

        # Track completed append operations to prevent duplicates
        self.completed_appends: Dict[str, Set[str]] = {}  # date -> set of completed append types

        # Track pending append operations (for delayed execution when data becomes available)
        self.pending_appends: Dict[str, Set[str]] = {}  # date -> set of pending operations

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

            # Check if this storage enables any pending append operations
            self._check_pending_appends(target_date)

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
        """Check if append option is enabled from user preferences"""
        # First check user preferences, then fallback to config
        append_options = self.user_prefs.get_append_options()
        self.logger.debug(f"User preferences append options: {append_options}")

        if option_name in append_options:
            result = append_options[option_name]
            self.logger.debug(f"Option '{option_name}' from user preferences: {result}")
            return result

        # Fallback to config if not in user preferences
        download_options = self.config.get_download_options()
        result = download_options.get(option_name, False)
        self.logger.debug(f"Option '{option_name}' from config (fallback): {result}")
        return result

    def _mark_append_as_pending(self, target_date: date, append_type: str) -> None:
        """Mark an append operation as pending"""
        date_key = self._get_date_key(target_date)
        if date_key not in self.pending_appends:
            self.pending_appends[date_key] = set()
        self.pending_appends[date_key].add(append_type)
        self.logger.info(f"Marked {append_type} as pending for {target_date}")

    def _check_pending_appends(self, target_date: date) -> None:
        """Check and execute any pending append operations for this date"""
        date_key = self._get_date_key(target_date)
        if date_key not in self.pending_appends:
            return

        pending_ops = self.pending_appends[date_key].copy()
        self.logger.info(f"Checking pending append operations for {target_date}: {pending_ops}")

        for append_type in pending_ops:
            success = False
            if append_type == 'bse_eq_append':
                # Check if both BSE EQ and BSE INDEX are now available
                if self.has_data('BSE', 'EQ', target_date) and self.has_data('BSE', 'INDEX', target_date):
                    self.logger.info(f"Both BSE EQ and INDEX data available - executing pending BSE append")
                    success = self._try_bse_eq_append(target_date)
            elif append_type == 'nse_eq_append':
                # Check if NSE EQ is available (SME and INDEX are optional)
                if self.has_data('NSE', 'EQ', target_date):
                    self.logger.info(f"NSE EQ data available - executing pending NSE append")
                    success = self._try_nse_eq_append(target_date)

            if success:
                self.pending_appends[date_key].discard(append_type)
                self.logger.info(f"Successfully executed pending {append_type} for {target_date}")

        # Clean up empty pending sets
        if not self.pending_appends[date_key]:
            del self.pending_appends[date_key]
    
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

            self.logger.info(f"🔍 DEBUGGING: Trying append operations for {target_date}")
            available_types = self.get_available_data_types(target_date)
            self.logger.info(f"🔍 DEBUGGING: Available data types: {available_types}")

            # Check BSE specific data availability
            has_bse_eq = self.has_data('BSE', 'EQ', target_date)
            has_bse_index = self.has_data('BSE', 'INDEX', target_date)
            self.logger.info(f"🔍 DEBUGGING: BSE data availability - EQ: {has_bse_eq}, INDEX: {has_bse_index}")

            # NSE SME + NSE Index → NSE EQ
            if 'NSE_EQ' in available_types:
                results['nse_eq_append'] = self._try_nse_eq_append(target_date)

            # BSE Index → BSE EQ
            self.logger.info(f"🔍 DEBUGGING: Checking BSE append conditions...")
            if 'BSE_EQ' in available_types:
                self.logger.info(f"🔍 DEBUGGING: BSE_EQ available, calling _try_bse_eq_append")
                results['bse_eq_append'] = self._try_bse_eq_append(target_date)
                self.logger.info(f"🔍 DEBUGGING: BSE append result: {results.get('bse_eq_append', 'NOT_SET')}")
            else:
                self.logger.info(f"🔍 DEBUGGING: BSE_EQ not available in available_types")
                # Mark BSE append as pending if BSE EQ is not available yet
                if 'BSE_INDEX' in available_types:
                    self.logger.info(f"🔍 DEBUGGING: BSE_INDEX available, marking BSE append as pending")
                    self._mark_append_as_pending(target_date, 'bse_eq_append')
                else:
                    self.logger.info(f"🔍 DEBUGGING: BSE_INDEX also not available")

            return results
            
        except Exception as e:
            self.logger.error(f"Error in append operations: {e}")
            return {}
    
    def _try_nse_eq_append(self, target_date: date) -> bool:
        """Try NSE EQ append operations (SME + Index)"""
        try:
            # Check if append already completed for this date
            date_key = self._get_date_key(target_date)
            if date_key in self.completed_appends and 'nse_eq_append' in self.completed_appends[date_key]:
                self.logger.info(f"NSE EQ append already completed for {target_date}")
                return True

            # Get base NSE EQ data
            eq_data = self.get_data('NSE', 'EQ', target_date)
            if eq_data is None:
                self.logger.warning(f"NSE EQ data not available for {target_date}")
                return False

            self.logger.info(f"Starting NSE EQ append for {target_date} with {len(eq_data)} base rows")
            combined_data = eq_data.copy()
            append_count = 0

            # Check append options
            sme_append_enabled = self.is_append_enabled('sme_append_to_eq')
            index_append_enabled = self.is_append_enabled('index_append_to_eq')
            self.logger.info(f"Append options - SME: {sme_append_enabled}, Index: {index_append_enabled}")

            # Add SME data if available and enabled
            if sme_append_enabled and self.has_data('NSE', 'SME', target_date):
                sme_data = self.get_data('NSE', 'SME', target_date)
                if sme_data is not None and not sme_data.empty:
                    self.logger.info(f"Found SME data with {len(sme_data)} rows")
                    # Ensure SME data has same columns as EQ data
                    aligned_sme_data = self._align_columns_for_append(sme_data, combined_data)
                    if not aligned_sme_data.empty:  # Only concat if data is not empty
                        # Use sort=False to avoid FutureWarning about column sorting
                        combined_data = pd.concat([combined_data, aligned_sme_data], ignore_index=True, sort=False)
                        append_count += len(aligned_sme_data)
                        self.logger.info(f"Appended {len(aligned_sme_data)} SME rows to NSE EQ")
                    else:
                        self.logger.warning("SME data alignment resulted in empty DataFrame")
                else:
                    self.logger.warning("SME data is None or empty")
            else:
                if not sme_append_enabled:
                    self.logger.info("SME append is disabled")
                else:
                    self.logger.info("No SME data available for append")

            # Add Index data if available and enabled
            if index_append_enabled and self.has_data('NSE', 'INDEX', target_date):
                index_data = self.get_data('NSE', 'INDEX', target_date)
                if index_data is not None and not index_data.empty:
                    self.logger.info(f"Found Index data with {len(index_data)} rows")
                    # Ensure Index data has same columns as EQ data
                    aligned_index_data = self._align_columns_for_append(index_data, combined_data)
                    if not aligned_index_data.empty:  # Only concat if data is not empty
                        # Use sort=False to avoid FutureWarning about column sorting
                        combined_data = pd.concat([combined_data, aligned_index_data], ignore_index=True, sort=False)
                        append_count += len(aligned_index_data)
                        self.logger.info(f"Appended {len(aligned_index_data)} Index rows to NSE EQ")
                    else:
                        self.logger.warning("Index data alignment resulted in empty DataFrame")
                else:
                    self.logger.warning("Index data is None or empty")
            else:
                if not index_append_enabled:
                    self.logger.info("Index append is disabled")
                else:
                    self.logger.info("No Index data available for append")

            # Append to real NSE EQ file if any data was appended
            if append_count > 0:
                self.logger.info(f"Attempting to append {append_count} rows to real NSE EQ file")
                success = self._append_to_real_file('NSE', 'EQ', combined_data, target_date)
                if success:
                    self.logger.info(f"Successfully appended {append_count} additional rows to real NSE EQ file")
                    # Mark append as completed
                    if date_key not in self.completed_appends:
                        self.completed_appends[date_key] = set()
                    self.completed_appends[date_key].add('nse_eq_append')
                else:
                    self.logger.error(f"Failed to append {append_count} rows to real NSE EQ file")
                return success
            else:
                self.logger.info("No data to append to NSE EQ")
                # Mark as completed even if no data to append
                if date_key not in self.completed_appends:
                    self.completed_appends[date_key] = set()
                self.completed_appends[date_key].add('nse_eq_append')
                return True

        except Exception as e:
            self.logger.error(f"Error in NSE EQ append: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _try_bse_eq_append(self, target_date: date) -> bool:
        """Try BSE EQ append operations (Index) - Fresh implementation based on NSE pattern"""
        try:
            self.logger.info(f"🔍 BSE DEBUG: Starting _try_bse_eq_append for {target_date}")

            # Check if append already completed for this date
            date_key = self._get_date_key(target_date)
            if date_key in self.completed_appends and 'bse_eq_append' in self.completed_appends[date_key]:
                self.logger.info(f"🔍 BSE DEBUG: BSE EQ append already completed for {target_date}")
                return True

            # Get base BSE EQ data
            self.logger.info(f"🔍 BSE DEBUG: Getting BSE EQ data for {target_date}")
            eq_data = self.get_data('BSE', 'EQ', target_date)
            if eq_data is None:
                self.logger.warning(f"🔍 BSE DEBUG: BSE EQ data not available for {target_date}")
                return False

            self.logger.info(f"🔍 BSE DEBUG: Starting BSE EQ append for {target_date} with {len(eq_data)} base rows")
            self.logger.debug(f"🔍 BSE DEBUG: BSE EQ data columns: {list(eq_data.columns)}")
            combined_data = eq_data.copy()
            append_count = 0

            # Check append options
            index_append_enabled = self.is_append_enabled('bse_index_append_to_eq')
            self.logger.info(f"🔍 BSE DEBUG: BSE Index append enabled: {index_append_enabled}")

            # Debug append options in detail
            user_append_options = self.user_prefs.get_append_options()
            config_download_options = self.config.get_download_options()
            self.logger.debug(f"🔍 BSE DEBUG: User preferences BSE append: {user_append_options.get('bse_index_append_to_eq', 'NOT_SET')}")
            self.logger.debug(f"🔍 BSE DEBUG: Config BSE append: {config_download_options.get('bse_index_append_to_eq', 'NOT_SET')}")

            # Add Index data if available and enabled
            self.logger.info(f"🔍 BSE DEBUG: Checking BSE Index data availability...")
            has_bse_index_data = self.has_data('BSE', 'INDEX', target_date)
            self.logger.info(f"🔍 BSE DEBUG: has_data('BSE', 'INDEX', {target_date}): {has_bse_index_data}")

            if index_append_enabled and has_bse_index_data:
                self.logger.info(f"🔍 BSE DEBUG: Both conditions met - getting BSE Index data")
                index_data = self.get_data('BSE', 'INDEX', target_date)
                self.logger.info(f"🔍 BSE DEBUG: Retrieved BSE Index data: {index_data is not None}")

                if index_data is not None and not index_data.empty:
                    self.logger.info(f"🔍 BSE DEBUG: Found BSE Index data with {len(index_data)} rows")
                    self.logger.debug(f"🔍 BSE DEBUG: BSE Index columns: {list(index_data.columns)}")
                    self.logger.debug(f"🔍 BSE DEBUG: BSE EQ columns: {list(combined_data.columns)}")
                    self.logger.debug(f"🔍 BSE DEBUG: Sample BSE Index data:\n{index_data.head()}")

                    # BSE specific column alignment
                    self.logger.info(f"🔍 BSE DEBUG: Starting BSE column alignment...")
                    aligned_index_data = self._align_bse_index_columns(index_data, combined_data)
                    self.logger.info(f"🔍 BSE DEBUG: Alignment result: {len(aligned_index_data)} rows")

                    if not aligned_index_data.empty:  # Only concat if data is not empty
                        self.logger.info(f"🔍 BSE DEBUG: Concatenating {len(aligned_index_data)} aligned rows")
                        # Use sort=False to avoid FutureWarning about column sorting
                        combined_data = pd.concat([combined_data, aligned_index_data], ignore_index=True, sort=False)
                        append_count += len(aligned_index_data)
                        self.logger.info(f"🔍 BSE DEBUG: Successfully appended {len(aligned_index_data)} Index rows to BSE EQ")
                        self.logger.info(f"🔍 BSE DEBUG: Total combined data rows: {len(combined_data)}")
                    else:
                        self.logger.warning("🔍 BSE DEBUG: BSE Index data alignment resulted in empty DataFrame")
                else:
                    self.logger.warning(f"🔍 BSE DEBUG: BSE Index data is None or empty - data: {index_data}")
            else:
                if not index_append_enabled:
                    self.logger.info("🔍 BSE DEBUG: BSE Index append is disabled")
                else:
                    self.logger.info(f"🔍 BSE DEBUG: No BSE Index data available for append - has_data: {has_bse_index_data}")

            # Append to real BSE EQ file if any data was appended
            self.logger.info(f"🔍 BSE DEBUG: Checking if data needs to be appended - append_count: {append_count}")
            if append_count > 0:
                self.logger.info(f"🔍 BSE DEBUG: Attempting to append {append_count} rows to real BSE EQ file")
                self.logger.debug(f"🔍 BSE DEBUG: Combined data shape: {combined_data.shape}")
                self.logger.debug(f"🔍 BSE DEBUG: Combined data columns: {list(combined_data.columns)}")

                success = self._append_to_real_file('BSE', 'EQ', combined_data, target_date)
                self.logger.info(f"🔍 BSE DEBUG: _append_to_real_file result: {success}")

                if success:
                    self.logger.info(f"🔍 BSE DEBUG: Successfully appended {append_count} additional rows to real BSE EQ file")
                    # Mark append as completed
                    if date_key not in self.completed_appends:
                        self.completed_appends[date_key] = set()
                    self.completed_appends[date_key].add('bse_eq_append')
                    self.logger.info(f"🔍 BSE DEBUG: Marked BSE append as completed for {target_date}")
                else:
                    self.logger.error(f"🔍 BSE DEBUG: Failed to append {append_count} rows to real BSE EQ file")
                return success
            else:
                self.logger.info("🔍 BSE DEBUG: No data to append to BSE EQ")
                # Mark as completed even if no data to append
                if date_key not in self.completed_appends:
                    self.completed_appends[date_key] = set()
                self.completed_appends[date_key].add('bse_eq_append')
                self.logger.info(f"🔍 BSE DEBUG: Marked BSE append as completed (no data) for {target_date}")
                return True

        except Exception as e:
            self.logger.error(f"Error in BSE EQ append: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _align_bse_index_columns(self, index_data: DataFrame, eq_data: DataFrame) -> DataFrame:
        """
        Align BSE Index columns to match BSE EQ column structure

        BSE Index columns: IndexName, Date, OpenPrice, HighPrice, LowPrice, ClosePrice, Volume
        BSE EQ columns: TckrSymb, TradDt, OpnPric, HghPric, LwPric, ClsPric, TtlTradgVol

        Args:
            index_data: BSE Index DataFrame
            eq_data: BSE EQ DataFrame (for column reference)

        Returns:
            Aligned DataFrame with BSE EQ column structure
        """
        try:
            if not HAS_PANDAS:
                self.logger.warning("🔍 BSE ALIGN DEBUG: Pandas not available")
                return index_data

            self.logger.info(f"🔍 BSE ALIGN DEBUG: Starting BSE Index data alignment to BSE EQ format")
            self.logger.info(f"🔍 BSE ALIGN DEBUG: Input Index data shape: {index_data.shape}")
            self.logger.debug(f"🔍 BSE ALIGN DEBUG: Index columns: {list(index_data.columns)}")
            self.logger.debug(f"🔍 BSE ALIGN DEBUG: EQ columns: {list(eq_data.columns)}")
            self.logger.debug(f"🔍 BSE ALIGN DEBUG: Index data sample:\n{index_data.head()}")

            # Create aligned DataFrame with BSE EQ column structure
            aligned_data = pd.DataFrame()

            # Map BSE Index columns to BSE EQ columns
            column_mapping = {
                'IndexName': 'TckrSymb',      # Symbol mapping
                'Date': 'TradDt',             # Date mapping
                'OpenPrice': 'OpnPric',       # Open price mapping
                'HighPrice': 'HghPric',       # High price mapping
                'LowPrice': 'LwPric',         # Low price mapping
                'ClosePrice': 'ClsPric',      # Close price mapping
                'Volume': 'TtlTradgVol'       # Volume mapping
            }

            self.logger.debug(f"🔍 BSE ALIGN DEBUG: Using column mapping: {column_mapping}")

            # Apply column mapping
            mapping_success_count = 0
            for index_col, eq_col in column_mapping.items():
                if index_col in index_data.columns and eq_col in eq_data.columns:
                    aligned_data[eq_col] = index_data[index_col]
                    mapping_success_count += 1
                    self.logger.debug(f"🔍 BSE ALIGN DEBUG: Successfully mapped {index_col} -> {eq_col}")
                else:
                    index_col_exists = index_col in index_data.columns
                    eq_col_exists = eq_col in eq_data.columns
                    self.logger.warning(f"🔍 BSE ALIGN DEBUG: Column mapping failed: {index_col} -> {eq_col} (index_exists: {index_col_exists}, eq_exists: {eq_col_exists})")

            self.logger.info(f"🔍 BSE ALIGN DEBUG: Successfully mapped {mapping_success_count}/{len(column_mapping)} columns")

            # Ensure all BSE EQ columns are present
            missing_columns = []
            for eq_col in eq_data.columns:
                if eq_col not in aligned_data.columns:
                    # Fill missing columns with appropriate default values
                    if 'Vol' in eq_col or 'Qty' in eq_col:
                        aligned_data[eq_col] = 0  # Volume columns get 0
                        default_val = 0
                    elif 'Pric' in eq_col or 'Price' in eq_col:
                        aligned_data[eq_col] = 0.0  # Price columns get 0.0
                        default_val = 0.0
                    else:
                        aligned_data[eq_col] = ''  # Other columns get empty string
                        default_val = ''
                    missing_columns.append(f"{eq_col}={default_val}")
                    self.logger.debug(f"🔍 BSE ALIGN DEBUG: Added missing column {eq_col} with default value {default_val}")

            if missing_columns:
                self.logger.info(f"🔍 BSE ALIGN DEBUG: Added {len(missing_columns)} missing columns: {missing_columns}")

            # Reorder columns to match BSE EQ structure
            aligned_data = aligned_data[eq_data.columns]

            self.logger.info(f"🔍 BSE ALIGN DEBUG: Successfully aligned {len(aligned_data)} BSE Index rows to BSE EQ format")
            self.logger.debug(f"🔍 BSE ALIGN DEBUG: Final aligned columns: {list(aligned_data.columns)}")
            self.logger.debug(f"🔍 BSE ALIGN DEBUG: Final aligned data sample:\n{aligned_data.head()}")

            return aligned_data

        except Exception as e:
            self.logger.error(f"Error aligning BSE Index columns: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return pd.DataFrame()  # Return empty DataFrame on error
    


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

            self.logger.debug(f"Aligning columns - Base columns: {list(base_data.columns)}")
            self.logger.debug(f"Aligning columns - Append columns: {list(append_data.columns)}")

            # If both DataFrames have the same number of columns, assume they match
            if len(append_data.columns) == len(base_data.columns):
                # Check if columns are exactly the same
                if list(append_data.columns) == list(base_data.columns):
                    self.logger.info(f"Columns match exactly - using data as-is for {len(append_data)} rows")
                    return append_data.copy()
                else:
                    # Create a copy with base column names (assume same order)
                    aligned_data = append_data.copy()
                    aligned_data.columns = base_data.columns
                    self.logger.info(f"Aligned {len(append_data)} rows by matching column count (renamed columns)")
                    return aligned_data

            # Get base columns
            base_columns = list(base_data.columns)

            # Create aligned DataFrame with same columns as base
            aligned_data = pd.DataFrame(columns=base_columns)

            # Copy data from append_data to aligned_data for matching columns
            matched_columns = 0
            for col in append_data.columns:
                if col in base_columns:
                    aligned_data[col] = append_data[col].values
                    matched_columns += 1
                else:
                    self.logger.warning(f"Column '{col}' from append data not found in base columns")

            self.logger.debug(f"Matched {matched_columns} out of {len(append_data.columns)} columns")

            # Fill NaN values with empty strings to maintain consistency
            aligned_data = aligned_data.fillna('')

            # If no columns matched, try a different approach - assume same order
            if matched_columns == 0 and len(append_data.columns) == len(base_columns):
                self.logger.warning("No column names matched, but same count - assuming same order")
                aligned_data = append_data.copy()
                aligned_data.columns = base_columns
                self.logger.info(f"Applied column mapping by position for {len(aligned_data)} rows")
                return aligned_data

            # Debug: Check for empty rows before removal
            empty_rows_count = (aligned_data == '').all(axis=1).sum()
            self.logger.debug(f"Found {empty_rows_count} completely empty rows out of {len(aligned_data)}")

            # Remove rows that are completely empty (all columns are empty strings)
            aligned_data = aligned_data.loc[~(aligned_data == '').all(axis=1)]

            self.logger.info(f"Aligned {len(aligned_data)} rows (from {len(append_data)}) to match base column structure")

            # Debug: Log sample of aligned data
            if len(aligned_data) > 0:
                self.logger.debug(f"Sample aligned data (first row): {aligned_data.iloc[0].to_dict()}")
            else:
                self.logger.warning("All rows were removed during alignment - possible column mismatch issue")
            return aligned_data

        except Exception as e:
            self.logger.error(f"Error aligning columns: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
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

            # Find the real EQ file using the same naming convention as build_filename
            output_dir = Path(self.config.get_output_directory()) / exchange / segment

            # Get the file suffix from exchange config
            exchange_config = self.config.get_exchange_config(exchange, segment)
            file_suffix = exchange_config.file_suffix if exchange_config else f"-{exchange}-{segment}"

            # Build filename using the same pattern as BaseDownloader.build_filename
            date_str = target_date.strftime('%Y-%m-%d')

            # Look for existing EQ file (both .csv and .txt formats)
            possible_files = [
                output_dir / f"{date_str}{file_suffix}.txt",
                output_dir / f"{date_str}{file_suffix}.csv",
                # Legacy patterns for backward compatibility
                output_dir / f"{exchange}_{segment}_{target_date.strftime('%d%m%Y')}.csv",
                output_dir / f"{exchange}_{segment}_{target_date.strftime('%d%m%Y')}.txt"
            ]

            real_file = None
            for file_path in possible_files:
                if file_path.exists():
                    real_file = file_path
                    break

            if not real_file:
                self.logger.warning(f"Real {exchange} {segment} file not found for {target_date}")
                self.logger.debug(f"Searched for files: {[str(f) for f in possible_files]}")
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
                # Convert DataFrame to CSV format without headers, with proper float formatting
                csv_content = append_data.to_csv(index=False, header=False, float_format='%.2f')
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
