"""
Data Manager for NSE/BSE Data Downloader

Centralized data management system for:
- Folder structure creation and management
- Last file date detection
- Smart date range calculation
- Data validation and cleanup
"""

import re
import math
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any
import logging

from .config import Config
from .exceptions import DataProcessingError, FileOperationError, DateRangeError
from ..utils.date_utils import DateUtils


#: The published filename contract, one pattern per exchange segment.  Kept at
#: module scope so a read-only caller such as ``--audit`` can recognise a
#: published file without constructing a ``DataManager``, whose ``__init__``
#: creates the folder structure.
DAILY_FILE_PATTERNS = {
    'NSE_EQ': r'(\d{4}-\d{2}-\d{2})-NSE-EQ\.(?:txt|csv)',
    'NSE_FO': r'(\d{4}-\d{2}-\d{2})-NSE-FO\.(?:txt|csv)',
    'NSE_SME': r'(\d{4}-\d{2}-\d{2})-NSE-SME\.(?:txt|csv)',
    'NSE_INDEX': r'(\d{4}-\d{2}-\d{2})-NSE-INDEX\.(?:txt|csv)',
    'BSE_EQ': r'(\d{4}-\d{2}-\d{2})-BSE-EQ\.(?:txt|csv)',
    'BSE_INDEX': r'(\d{4}-\d{2}-\d{2})-BSE-INDEX\.(?:txt|csv)',
}


class DataManager:
    """
    Centralized data management system

    Handles all data-related operations including folder management,
    date calculations, and file operations.
    """

    def __init__(self, config: Config):
        """
        Initialize data manager

        Args:
            config: Configuration object
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self._validation_cache: Dict[Path, tuple[int, int, bool]] = {}
        self._expected_row_cache: Dict[tuple[str, str], Dict[date, int]] = {}

        # Date patterns for different exchanges
        self.date_patterns = dict(DAILY_FILE_PATTERNS)
        self._ensure_folder_structure()

    def is_trading_day(
        self, target_date: date, include_weekends: bool = False
    ) -> bool:
        """Use configured weekend and official holiday policies."""

        if (
            getattr(self.config.date_settings, "weekend_skip", True)
            and not include_weekends
            and target_date.weekday() >= 5
        ):
            return False
        if (
            getattr(self.config.date_settings, "holiday_skip", True)
            and self.config.holiday_manager.is_holiday(target_date)
        ):
            return False
        return True

    def get_expected_last_trading_date(self) -> date:
        """Return the IST-aware expected date using the configured calendar."""

        today = DateUtils.today_ist()
        if not self.is_trading_day(today):
            current = today
            while not self.is_trading_day(current):
                current -= timedelta(days=1)
            return current
        if DateUtils.is_data_available_time():
            return today
        current = today - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current

    def _ensure_folder_structure(self) -> None:
        """Ensure all required folders exist"""
        try:
            # Create base data directory
            self.config.base_data_path.mkdir(parents=True, exist_ok=True)

            # Create exchange-specific directories
            for exchange_segment in self.config.get_available_exchanges():
                exchange, segment = exchange_segment.split('_', 1)
                data_path = self.config.get_data_path(exchange, segment)
                data_path.mkdir(parents=True, exist_ok=True)

            self.logger.info("Folder structure created successfully")

        except Exception as e:
            raise FileOperationError(
                f"Failed to create folder structure: {e}",
                operation="create_folders"
            )

    def get_last_file_date(self, exchange: str, segment: str) -> Optional[date]:
        """
        Get the date of the last available file for an exchange/segment

        Args:
            exchange: Exchange name (e.g., 'NSE', 'BSE')
            segment: Segment name (e.g., 'EQ', 'FO', 'SME')

        Returns:
            Date of last file, or None if no files exist

        Raises:
            DataProcessingError: If there's an error reading files
        """
        data_path = self.config.get_data_path(exchange, segment)
        try:
            exchange_segment = f"{exchange}_{segment}"

            if exchange_segment not in self.date_patterns:
                raise DataProcessingError(f"No date pattern defined for {exchange_segment}")

            dates = self.get_available_file_dates(
                exchange, segment, validate_contents=True
            )

            if dates:
                last_date = max(dates)
                self.logger.info(f"Last file date for {exchange_segment}: {last_date}")
                return last_date

            self.logger.info(f"No files found for {exchange_segment}")
            return None

        except Exception as e:
            raise DataProcessingError(
                f"Error getting last file date for {exchange}_{segment}: {e}",
                file_path=str(data_path)
            )

    def _matching_files(
        self, exchange: str, segment: str
    ) -> Dict[date, Path]:
        """Return only filenames that exactly match the public contract."""

        key = f"{exchange.upper()}_{segment.upper()}"
        pattern = self.date_patterns.get(key)
        if pattern is None:
            raise DataProcessingError(f"No date pattern defined for {key}")
        result: Dict[date, Path] = {}
        for path in self.config.get_data_path(exchange, segment).iterdir():
            if not path.is_file():
                continue
            match = re.fullmatch(pattern, path.name)
            if not match:
                continue
            try:
                target_date = date.fromisoformat(match.group(1))
            except ValueError:
                self.logger.warning(f"Invalid date in filename: {path.name}")
                continue
            result[target_date] = path
        return result

    @staticmethod
    def _count_rows(path: Path) -> int:
        """Count non-empty lines without holding the file in memory."""

        count = 0
        trailing_newline = True
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                count += block.count(b"\n")
                trailing_newline = block.endswith(b"\n")
        # A final line without its newline is still a row.
        return count if trailing_newline else count + 1

    def _expected_rows(
        self, exchange: str, segment: str, target_date: date
    ) -> Optional[int]:
        """Rows the pipeline recorded for the file it published that day.

        Only a value the writer itself recorded is used, so this cannot
        invent an expectation for a data root written by an older build --
        there the structural checks stand alone.
        """

        key = (exchange.upper(), segment.upper())
        recorded = self._expected_row_cache.get(key)
        if recorded is None:
            try:
                from ..services.pipeline_state import PipelineManifest

                manifest = PipelineManifest(self.config.base_data_path)
                recorded = manifest.published_row_counts(*key)
            except Exception as error:
                # A manifest that cannot be read must not invalidate every
                # file on disk; the structural checks still apply.
                self.logger.warning(
                    "Row-count expectations unavailable for %s_%s: %s",
                    *key, error,
                )
                recorded = {}
            self._expected_row_cache[key] = recorded
        return recorded.get(target_date)

    def validate_daily_output(
        self, exchange: str, segment: str, target_date: date, path: Path
    ) -> bool:
        """Check boundary rows, row count, column contract, date and numbers."""

        try:
            import csv

            if not path.is_file() or path.stat().st_size == 0:
                return False
            stat = path.stat()
            cache_key = (stat.st_mtime_ns, stat.st_size)
            cached = self._validation_cache.get(path)
            if cached and cached[:2] == cache_key:
                return cached[2]

            # Boundary rows alone accepted a truncated file: a five-row
            # placeholder parses perfectly at both ends and then counts as a
            # complete trading day that nothing ever downloads again.
            rows_on_disk = self._count_rows(path)
            expected_rows = self._expected_rows(exchange, segment, target_date)
            if expected_rows is not None and rows_on_disk != expected_rows:
                self.logger.warning(
                    "Daily output %s has %d rows; %d were published",
                    path, rows_on_disk, expected_rows,
                )
                self._validation_cache[path] = (*cache_key, False)
                return False

            with path.open("rb") as handle:
                first = handle.readline()
                offset = max(0, stat.st_size - 8192)
                handle.seek(offset)
                tail = handle.read()
            tail_lines = [line for line in tail.splitlines() if line.strip()]
            raw_rows = [first]
            if tail_lines and tail_lines[-1] != first.rstrip(b"\r\n"):
                raw_rows.append(tail_lines[-1])
            rows = []
            for raw in raw_rows:
                decoded = raw.decode("utf-8-sig").strip("\r\n")
                parsed = next(csv.reader([decoded]))
                rows.append(parsed)

            allowed_counts = {7}
            if segment.upper() in {"EQ", "SME", "FO"}:
                allowed_counts.add(9)
            valid = True
            for row in rows:
                if len(row) not in allowed_counts or not row[0].strip():
                    valid = False
                    break
                if row[1].strip().removesuffix(".0") != target_date.strftime("%Y%m%d"):
                    valid = False
                    break
                try:
                    if segment.upper() == "INDEX":
                        numeric_columns = [5]
                    elif (
                        segment.upper() == "EQ"
                        and all(not row[column].strip() for column in (2, 3, 4))
                    ):
                        # Combined EQ files may end with an official close-only
                        # index row.  Blank OHLC is allowed only as one explicit
                        # profile; CLOSE remains mandatory and VOLUME may be 0.
                        numeric_columns = [5]
                        if row[6].strip():
                            numeric_columns.append(6)
                        if len(row) == 9 and (
                            row[7].strip() or row[8].strip()
                        ):
                            valid = False
                            break
                    else:
                        numeric_columns = [2, 3, 4, 5, 6]
                    required_values = [
                        float(row[column].replace(",", ""))
                        for column in numeric_columns
                    ]
                    if any(
                        not math.isfinite(value) or value < 0
                        for value in required_values
                    ):
                        valid = False
                        break
                    if segment.upper() == "FO" and len(row) == 9:
                        open_interest = float(row[7].replace(",", ""))
                        change_in_oi = float(row[8].replace(",", ""))
                        if (
                            not math.isfinite(open_interest)
                            or open_interest < 0
                            or not math.isfinite(change_in_oi)
                        ):
                            valid = False
                            break
                except (IndexError, TypeError, ValueError):
                    valid = False
                    break
            self._validation_cache[path] = (
                stat.st_mtime_ns, stat.st_size, valid
            )
            return valid
        except Exception as error:
            self.logger.warning(f"Invalid daily output {path}: {error}")
            return False

    def get_available_file_dates(
        self,
        exchange: str,
        segment: str,
        *,
        validate_contents: bool = True,
    ) -> List[date]:
        files = self._matching_files(exchange, segment)
        if not validate_contents:
            return sorted(files)
        return sorted(
            target_date
            for target_date, path in files.items()
            if self.validate_daily_output(exchange, segment, target_date, path)
        )

    def get_invalid_file_dates(
        self, exchange: str, segment: str
    ) -> List[date]:
        files = self._matching_files(exchange, segment)
        return sorted(
            target_date
            for target_date, path in files.items()
            if not self.validate_daily_output(
                exchange, segment, target_date, path
            )
        )

    def get_missing_file_dates(
        self, exchange: str, segment: str
    ) -> List[date]:
        """Find invalid or absent trading dates between first and last file."""

        named_dates = self.get_available_file_dates(
            exchange, segment, validate_contents=False
        )
        if len(named_dates) < 2:
            return self.get_invalid_file_dates(exchange, segment)
        valid = set(self.get_available_file_dates(exchange, segment))
        expected = set(self.get_working_days(named_dates[0], named_dates[-1]))
        return sorted(expected.difference(valid))

    def get_repair_dates(self, exchange: str, segment: str) -> List[date]:
        """Return missing/corrupt files and dates with incomplete enabled stages."""

        from ..services.pipeline_state import PipelineManifest

        file_repairs = self.get_missing_file_dates(exchange, segment)
        pipeline_repairs = PipelineManifest(
            self.config.base_data_path
        ).incomplete_dates(exchange, segment)
        return sorted(set(file_repairs).union(pipeline_repairs))

    def is_first_run(self, exchange: str, segment: str) -> bool:
        """
        Check if this is the first run for an exchange/segment

        Args:
            exchange: Exchange name
            segment: Segment name

        Returns:
            True if no data files exist, False otherwise
        """
        return self.get_last_file_date(exchange, segment) is None

    def calculate_date_range(self, exchange: str, segment: str,
                           custom_start: Optional[date] = None,
                           custom_end: Optional[date] = None) -> Tuple[date, date]:
        """
        Calculate the date range for downloading data

        Args:
            exchange: Exchange name
            segment: Segment name
            custom_start: Custom start date (optional)
            custom_end: Custom end date (optional)

        Returns:
            Tuple of (start_date, end_date)

        Raises:
            DateRangeError: If date calculation fails
        """
        try:
            # Use custom dates if provided
            if custom_start and custom_end:
                if custom_start > custom_end:
                    raise DateRangeError(
                        "Start date cannot be after end date",
                        start_date=str(custom_start),
                        end_date=str(custom_end)
                    )
                return custom_start, custom_end

            # Default end date calculation based on market hours
            if custom_end:
                end_date = custom_end
            else:
                today = DateUtils.today_ist()
                # If today is a trading day and it's before 6:00 PM, exclude today
                if (self.is_trading_day(today) and
                    not DateUtils.is_data_available_time()):
                    # Use previous trading day as end date
                    end_date = today - timedelta(days=1)
                    while not self.is_trading_day(end_date):
                        end_date -= timedelta(days=1)
                else:
                    end_date = today

            # Calculate start date
            if custom_start:
                start_date = custom_start
            elif self.is_first_run(exchange, segment):
                # First run: use base start date from config
                base_start_str = self.config.date_settings.base_start_date
                start_date = datetime.strptime(base_start_str, '%Y-%m-%d').date()
            else:
                # Subsequent run: continue from last file date + 1
                last_date = self.get_last_file_date(exchange, segment)
                if last_date:
                    start_date = last_date + timedelta(days=1)
                else:
                    # Fallback to base start date
                    base_start_str = self.config.date_settings.base_start_date
                    start_date = datetime.strptime(base_start_str, '%Y-%m-%d').date()

            # Validate date range
            if start_date > end_date:
                self.logger.info(f"No new data to download for {exchange}_{segment} (start: {start_date}, end: {end_date})")
                return start_date, end_date

            self.logger.info(f"Date range for {exchange}_{segment}: {start_date} to {end_date}")
            return start_date, end_date

        except Exception as e:
            raise DateRangeError(
                f"Error calculating date range for {exchange}_{segment}: {e}",
                start_date=str(custom_start) if custom_start else None,
                end_date=str(custom_end) if custom_end else None
            )

    def get_working_days(self, start_date: date, end_date: date, include_weekends: bool = False) -> List[date]:
        """
        Get list of working days in date range

        Args:
            start_date: Start date
            end_date: End date
            include_weekends: Override weekend skip setting

        Returns:
            List of working days
        """
        working_days = []
        current_date = start_date

        # Determine if weekends should be skipped
        skip_weekends = (
            getattr(self.config.date_settings, "weekend_skip", True)
            and not include_weekends
        )

        while current_date <= end_date:
            # Skip weekends if configured and not overridden
            if skip_weekends and current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # Skip market holidays only when configured.
            if (
                getattr(self.config.date_settings, "holiday_skip", True)
                and self.config.holiday_manager.is_holiday(current_date)
            ):
                self.logger.debug(f"Skipping holiday: {current_date}")
                current_date += timedelta(days=1)
                continue

            working_days.append(current_date)
            current_date += timedelta(days=1)

        return working_days

    def get_file_count(self, exchange: str, segment: str) -> int:
        """
        Get count of data files for an exchange/segment

        Args:
            exchange: Exchange name
            segment: Segment name

        Returns:
            Number of data files
        """
        try:
            exchange_segment = f"{exchange}_{segment}"

            if exchange_segment not in self.date_patterns:
                return 0

            return len(self.get_available_file_dates(exchange, segment))

        except Exception as e:
            self.logger.error(f"Error counting files for {exchange}_{segment}: {e}")
            return 0

    def get_data_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Get summary of available data for all exchanges

        Returns:
            Dictionary with data summary for each exchange/segment
        """
        summary = {}

        for exchange_segment in self.config.get_available_exchanges():
            exchange, segment = exchange_segment.split('_', 1)

            try:
                last_date = self.get_last_file_date(exchange, segment)
                file_count = self.get_file_count(exchange, segment)
                is_first = self.is_first_run(exchange, segment)

                summary[exchange_segment] = {
                    'last_date': last_date.strftime('%Y-%m-%d') if last_date else None,
                    'file_count': file_count,
                    'is_first_run': is_first,
                    'data_path': str(self.config.get_data_path(exchange, segment))
                }

            except Exception as e:
                summary[exchange_segment] = {
                    'error': str(e),
                    'last_date': None,
                    'file_count': 0,
                    'is_first_run': True,
                    'data_path': str(self.config.get_data_path(exchange, segment))
                }

        return summary

    def is_database_up_to_date(self, exchange: str, segment: str) -> tuple[bool, str]:
        """
        Check if database is up-to-date for given exchange/segment

        Args:
            exchange: Exchange name
            segment: Segment name

        Returns:
            Tuple of (is_up_to_date, message)
        """
        try:
            last_file_date = self.get_last_file_date(exchange, segment)
            expected_last_date = self.get_expected_last_trading_date()
            repair_dates = self.get_repair_dates(exchange, segment)

            if last_file_date is None:
                return False, f"No data files found for {exchange}_{segment}"

            if repair_dates:
                preview = ", ".join(
                    value.isoformat() for value in repair_dates[:5]
                )
                suffix = "..." if len(repair_dates) > 5 else ""
                return False, (
                    f"Database needs repair for {exchange}_{segment}: "
                    f"{len(repair_dates)} date(s) ({preview}{suffix})"
                )

            if last_file_date >= expected_last_date:
                # Base message
                base_message = f"Database is up-to-date. Last file date: {last_file_date}"

                # Add today's data availability info if relevant
                today = DateUtils.today_ist()
                if self.is_trading_day(today) and not DateUtils.is_data_available_time():
                    # Today is a trading day and it's before 6:00 PM
                    base_message += f"\n\nNote: Today's data ({today.strftime('%Y-%m-%d')}) will be available after 6:00 PM."

                return True, base_message
            else:
                missing_days = self.get_working_days(
                    last_file_date + timedelta(days=1), expected_last_date
                )
                return False, f"Database needs update. Missing {len(missing_days)} trading days since {last_file_date}"

        except Exception as e:
            self.logger.error(f"Error checking database status for {exchange}_{segment}: {e}")
            return False, f"Error checking database status: {e}"

    def check_all_databases_status(self, selected_exchanges: List[str]) -> tuple[bool, str]:
        """
        Check if all selected databases are up-to-date and return a clean summary message

        Args:
            selected_exchanges: List of exchange_segment strings (e.g., ['NSE_EQ', 'BSE_EQ'])

        Returns:
            Tuple of (all_up_to_date, summary_message)
        """
        all_up_to_date = True
        last_file_dates = []
        error_exchanges = []

        for exchange_segment in selected_exchanges:
            try:
                exchange, segment = exchange_segment.split('_', 1)

                # Get last file date for this exchange
                last_file_date = self.get_last_file_date(exchange, segment)
                expected_last_date = self.get_expected_last_trading_date()
                repair_dates = self.get_repair_dates(exchange, segment)

                if last_file_date is None:
                    all_up_to_date = False
                    error_exchanges.append(f"{exchange_segment} (No data found)")
                elif repair_dates:
                    all_up_to_date = False
                    error_exchanges.append(
                        f"{exchange_segment} ({len(repair_dates)} repair date(s))"
                    )
                elif last_file_date < expected_last_date:
                    all_up_to_date = False
                    missing_days = self.get_working_days(
                        last_file_date + timedelta(days=1), expected_last_date
                    )
                    error_exchanges.append(f"{exchange_segment} (Missing {len(missing_days)} days)")
                else:
                    # Up-to-date, collect the date
                    if last_file_date not in last_file_dates:
                        last_file_dates.append(last_file_date)

            except ValueError:
                all_up_to_date = False
                error_exchanges.append(f"{exchange_segment} (Invalid format)")
            except Exception as e:
                all_up_to_date = False
                error_exchanges.append(f"{exchange_segment} (Error: {e})")

        # Generate clean summary message
        if all_up_to_date:
            # All databases are up-to-date
            if last_file_dates:
                latest_date = max(last_file_dates)
                message = f"Database is up-to-date. Last file date: {latest_date}"

                # Add today's data availability info if relevant
                today = DateUtils.today_ist()
                if self.is_trading_day(today) and not DateUtils.is_data_available_time():
                    message += f"\n\nNote: Today's data ({today.strftime('%Y-%m-%d')}) will be available after 6:00 PM."
            else:
                message = "Database status could not be determined."
        else:
            # Some databases need updates
            message = f"Database needs update for: {', '.join(error_exchanges)}"

        return all_up_to_date, message

    def get_download_completion_message(self, selected_exchanges: List[str],
                                      successful_downloads: List[str]) -> str:
        """
        Generate appropriate completion message based on market hours and successful downloads

        Args:
            selected_exchanges: List of selected exchange segments
            successful_downloads: List of successfully downloaded exchange segments

        Returns:
            Completion message string
        """
        today = DateUtils.today_ist()
        is_trading_day = self.is_trading_day(today)
        is_data_available = DateUtils.is_data_available_time()

        # Base success message
        success_count = len(successful_downloads)
        total_count = len(selected_exchanges)

        if success_count == 0:
            message = "Download completed with errors. No data was downloaded successfully."
        elif success_count == total_count:
            message = f"Download completed successfully! Downloaded data for {success_count} exchange(s)."
        else:
            message = f"Download partially completed. {success_count} out of {total_count} exchanges downloaded successfully."

        # Add market hours information if relevant
        if is_trading_day and not is_data_available:
            message += f"\n\nNote: Today's data ({today.strftime('%Y-%m-%d')}) will be available after 6:00 PM."

        return message

    def cleanup_temp_files(self, exchange: Optional[str] = None, segment: Optional[str] = None) -> None:
        """
        Clean up temporary files (no longer needed with memory-based processing)

        Args:
            exchange: Specific exchange to clean (optional)
            segment: Specific segment to clean (optional)
        """
        # No temp files to clean up with memory-based processing
        self.logger.debug("Temp file cleanup not needed with memory-based processing")

    def _cleanup_directory(self, directory: Path) -> None:
        """Clean up files in a directory"""
        if directory.exists():
            for file_path in directory.iterdir():
                if file_path.is_file():
                    file_path.unlink()
                elif file_path.is_dir():
                    self._cleanup_directory(file_path)
                    file_path.rmdir()

    def validate_data_integrity(self, exchange: str, segment: str) -> bool:
        """
        Validate data integrity for an exchange/segment

        Args:
            exchange: Exchange name
            segment: Segment name

        Returns:
            True if data is valid, False otherwise
        """
        try:
            data_path = self.config.get_data_path(exchange, segment)

            # Check if directory exists
            if not data_path.exists():
                return False

            # Check if files follow naming convention
            exchange_segment = f"{exchange}_{segment}"
            if exchange_segment not in self.date_patterns:
                return False

            pattern = self.date_patterns[exchange_segment]
            valid_files = 0

            for file_path in data_path.iterdir():
                if file_path.is_file():
                    if re.match(pattern, file_path.name):
                        # Check if file is not empty
                        if file_path.stat().st_size > 0:
                            valid_files += 1

            self.logger.info(f"Data integrity check for {exchange_segment}: {valid_files} valid files")
            return valid_files > 0

        except Exception as e:
            self.logger.error(f"Error validating data integrity for {exchange}_{segment}: {e}")
            return False
