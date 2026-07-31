"""
Base Downloader Class for NSE/BSE Data Downloader

Abstract base class providing common functionality for all exchange downloaders.
Includes date management, folder operations, and data processing interfaces.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any
try:
    import pandas as pd
    HAS_PANDAS = True
    DataFrame = pd.DataFrame
except ImportError:
    HAS_PANDAS = False
    pd = None
    # Create a dummy DataFrame type for type annotations
    class DataFrame:
        pass

from .config import Config
from .data_manager import DataManager
from .exceptions import DataProcessingError, FileOperationError
from ..services.memory_append_manager import MemoryAppendManager
from ..services.pipeline_state import PipelineManifest, SegmentResult


class ProgressCallback:
    """Progress callback interface for download progress tracking"""

    def __init__(self,
                 on_progress: Optional[Callable[[str, int, str], None]] = None,
                 on_status: Optional[Callable[[str, str], None]] = None,
                 on_error: Optional[Callable[[str, str], None]] = None):
        """
        Initialize progress callback

        Args:
            on_progress: Callback for progress updates (exchange_segment, percentage, message)
            on_status: Callback for status updates (exchange_segment, status_message)
            on_error: Callback for error notifications (exchange_segment, error_message)
        """
        self.on_progress = on_progress or self._default_progress
        self.on_status = on_status or self._default_status
        self.on_error = on_error or self._default_error

    def _default_progress(self, exchange_segment: str, percentage: int, message: str):
        print(f"[{exchange_segment}] {percentage}% - {message}")

    def _default_status(self, exchange_segment: str, message: str):
        print(f"[{exchange_segment}] {message}")

    def _default_error(self, exchange_segment: str, error: str):
        print(f"[{exchange_segment}] ERROR: {error}")


class BaseDownloader(ABC):
    """
    Abstract base class for all exchange downloaders

    Provides common functionality including:
    - Configuration management
    - Date range calculation
    - Progress tracking
    - Error handling
    - File operations
    """

    def __init__(self, exchange: str, segment: str, config: Config):
        """
        Initialize base downloader

        Args:
            exchange: Exchange name (e.g., 'NSE', 'BSE')
            segment: Segment name (e.g., 'EQ', 'FO', 'SME')
            config: Configuration object
        """
        self.exchange = exchange
        self.segment = segment
        self.config = config
        self.exchange_segment = f"{exchange}_{segment}"

        # Initialize components
        self.data_manager = DataManager(config)
        self.logger = logging.getLogger(f"{__name__}.{self.exchange_segment}")

        # Initialize memory append manager (shared instance)
        if not hasattr(BaseDownloader, '_memory_append_manager'):
            BaseDownloader._memory_append_manager = MemoryAppendManager(config)
        self.memory_append_manager = BaseDownloader._memory_append_manager
        self.pipeline_manifest = PipelineManifest(config.base_data_path)
        self.last_segment_result: Optional[SegmentResult] = None

        # Get exchange-specific configuration
        self.exchange_config = config.get_exchange_config(exchange, segment)

        # Setup paths
        self.data_path = config.get_data_path(exchange, segment)

        # Progress tracking
        self.progress_callback: Optional[ProgressCallback] = None
        self.total_files = 0
        self.completed_files = 0

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Set progress callback for tracking download progress"""
        self.progress_callback = callback

    def _update_progress(self, message: str = "") -> None:
        """Update progress percentage"""
        if self.progress_callback and self.total_files > 0:
            percentage = int((self.completed_files / self.total_files) * 100)
            self.progress_callback.on_progress(self.exchange_segment, percentage, message)

    def _update_status(self, message: str) -> None:
        """Update status message"""
        if self.progress_callback:
            self.progress_callback.on_status(self.exchange_segment, message)
        self.logger.info(message)

    def _report_error(self, error: str) -> None:
        """Report error message to both application console and IDE console"""
        # Send to application console (GUI)
        if self.progress_callback:
            self.progress_callback.on_error(self.exchange_segment, error)

        # Send to IDE console (logger) - ensure same message appears in both places
        self.logger.error(error)

    def _report_notice(self, notice: str) -> None:
        """Report notice message to both application console and IDE console"""
        # Send to application console (GUI) as error (for visibility)
        if self.progress_callback:
            self.progress_callback.on_error(self.exchange_segment, notice)

        # Send to IDE console as warning (appropriate level for notices)
        self.logger.warning(notice)

    def get_download_option(self, name: str, default: Any = None) -> Any:
        """Return a user preference, falling back to application config."""

        try:
            from ..utils.user_preferences import UserPreferences

            user_options = UserPreferences().get_download_options()
            if name in user_options:
                return user_options[name]
        except Exception:
            pass
        return self.config.get_download_options().get(name, default)

    @abstractmethod
    def build_url(self, target_date: date) -> str:
        """
        Build download URL for specific date

        Args:
            target_date: Date for which to build URL

        Returns:
            Complete download URL
        """
        pass

    @abstractmethod
    def process_downloaded_data(self, file_data: bytes, file_date: date) -> Optional[DataFrame]:
        """
        Process downloaded file data in memory

        Args:
            file_data: Downloaded file data as bytes
            file_date: Date of the data

        Returns:
            Processed DataFrame, or None if processing failed
        """
        pass

    @abstractmethod
    def transform_data(self, df: DataFrame, file_date: date) -> DataFrame:
        """
        Transform DataFrame according to exchange-specific requirements

        Args:
            df: Input DataFrame
            file_date: Date of the data

        Returns:
            Transformed DataFrame
        """
        pass

    def get_date_range(self,
                      custom_start: Optional[date] = None,
                      custom_end: Optional[date] = None) -> tuple[date, date]:
        """
        Get date range for downloading

        Args:
            custom_start: Custom start date (optional)
            custom_end: Custom end date (optional)

        Returns:
            Tuple of (start_date, end_date)
        """
        return self.data_manager.calculate_date_range(
            self.exchange,
            self.segment,
            custom_start,
            custom_end
        )

    def get_working_days(self, start_date: date, end_date: date, include_weekends: bool = False) -> List[date]:
        """Get list of working days in date range"""
        return self.data_manager.get_working_days(start_date, end_date, include_weekends)

    def build_filename(self, target_date: date, extension: str = "txt") -> str:
        """
        Build standardized filename for processed data

        Args:
            target_date: Date for the file
            extension: File extension (default: txt)

        Returns:
            Standardized filename
        """
        date_str = target_date.strftime('%Y-%m-%d')
        suffix = self.exchange_config.file_suffix
        return f"{date_str}{suffix}.{extension}"

    def _pipeline_requirements(self) -> tuple[list[str], list[str]]:
        """Return required and disabled stages for the current user options."""

        required = ["downloaded", "validated", "daily"]
        disabled = ["combined"]
        if self.segment in ("EQ", "SME"):
            if self.get_download_option("include_delivery_data", True):
                required.append("delivery")
            else:
                disabled.append("delivery")
            if self.get_download_option("generate_symbol_files", True):
                required.append("symbols")
                if self.get_download_option("apply_corporate_actions", True):
                    required.append("actions")
                else:
                    disabled.append("actions")
            else:
                disabled.extend(["symbols", "actions"])
        else:
            disabled.extend(["symbols", "delivery", "actions"])
        return required, disabled

    def _pipeline(self) -> PipelineManifest:
        """Return the manifest, including for lightweight test subclasses."""

        manifest = getattr(self, "pipeline_manifest", None)
        if manifest is None:
            manifest = PipelineManifest(self.config.base_data_path)
            self.pipeline_manifest = manifest
        return manifest

    def _begin_pipeline_date(self, target_date: date) -> None:
        required, disabled = self._pipeline_requirements()
        self._pipeline().begin(
            self.exchange,
            self.segment,
            target_date,
            required,
            disabled,
        )

    def _mark_pipeline(
        self,
        target_date: date,
        stage: str,
        status: str,
        **metadata: Any,
    ) -> None:
        self._pipeline().mark(
            self.exchange,
            self.segment,
            target_date,
            stage,
            status,
            **metadata,
        )

    def _quarantine_download_payload(
        self, payload: Optional[bytes], target_date: date, label: str
    ) -> Optional[Path]:
        """Keep an unexpected exchange response for diagnosis without publishing it."""

        if not payload:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        directory = (
            self.config.base_data_path
            / ".state"
            / "quarantine"
            / "source_reports"
        )
        directory.mkdir(parents=True, exist_ok=True)
        safe_label = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in label
        )
        target = directory / (
            f"{self.exchange_segment}-{target_date.isoformat()}-"
            f"{safe_label}-{digest[:12]}.bin"
        )
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
        return target

    def save_processed_data(self, df: DataFrame, target_date: date) -> Path:
        """
        Save processed DataFrame to final location

        Args:
            df: Processed DataFrame
            target_date: Date of the data

        Returns:
            Path to saved file

        Raises:
            FileOperationError: If save operation fails
        """
        current_stage = "daily"
        output_path: Optional[Path] = None
        try:
            filename = self.build_filename(target_date)
            output_path = self.data_path / filename

            # Save without header and index (as per original code), but never
            # expose a partially-written file if the app is interrupted.
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            df.to_csv(temporary, index=False, header=False)
            temporary.replace(output_path)
            self._mark_pipeline(
                target_date,
                "daily",
                "complete",
                path=str(output_path),
                sha256=self._file_sha256(output_path),
                rows=len(df),
            )

            self.logger.info(f"Saved processed data: {filename}")

            options = {
                "generate_symbol_files": self.get_download_option(
                    "generate_symbol_files", True
                )
            }
            internal_equity = getattr(self, "_internal_equity_data", None)
            if (
                options.get("generate_symbol_files", True)
                and internal_equity is not None
                and self.segment in ("EQ", "SME")
            ):
                current_stage = "symbols"
                from ..services.symbol_history import SymbolHistoryStore

                written = SymbolHistoryStore(self.config.base_data_path).upsert(
                    self.exchange, self.segment, target_date, internal_equity
                )
                self.logger.info(
                    f"Updated {written} {self.exchange} symbol history files"
                )
                self._mark_pipeline(
                    target_date, "symbols", "complete", files=written
                )

            current_stage = "combined"

            # Store data in memory for append operations
            self.memory_append_manager.store_data(
                exchange=self.exchange,
                segment=self.segment,
                target_date=target_date,
                data=df
            )

            # Try append operations (non-blocking)
            append_results = self.memory_append_manager.try_append_operations(target_date)
            if append_results:
                self.logger.info(f"Append operations completed: {append_results}")

            # Special handling for BSE EQ - try direct file append if memory append failed
            if self.exchange == 'BSE' and self.segment == 'EQ':
                self._try_direct_bse_append(target_date, output_path)

            return output_path

        except Exception as e:
            from ..services.state_store import StateStoreError

            try:
                self._mark_pipeline(
                    target_date, current_stage, "failed", error=str(e)
                )
            except Exception:
                pass

            if isinstance(e, StateStoreError):
                raise FileOperationError(
                    f"Repair required before symbol data can be updated: {e}",
                    file_path=str(output_path) if output_path else None,
                    operation="symbol_history_repair_required",
                    details=str(e),
                ) from e
            raise FileOperationError(
                f"Failed to save processed data for {target_date}",
                file_path=str(output_path) if output_path else None,
                operation="save_csv"
            ) from e

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _with_pending_delivery_days(self, working_days: List[date]) -> List[date]:
        """Include older dates whose delivery report was published late."""

        if not self.get_download_option("include_delivery_data", True):
            return working_days
        if self.segment not in ("EQ", "SME"):
            return working_days

        from ..services.delivery_state import PendingDeliveryStore

        pending = PendingDeliveryStore(self.config.base_data_path).dates(
            self.exchange, self.segment
        )
        return sorted(set(working_days).union(pending))

    def _with_incomplete_pipeline_days(
        self, working_days: List[date]
    ) -> List[date]:
        """Resume dates left partial by a prior crash or stage failure."""

        pending = self._pipeline().incomplete_dates(
            self.exchange, self.segment
        )
        return sorted(set(working_days).union(pending))

    async def _download_equity_implementation(self, working_days: List[date]) -> bool:
        """Shared price + optional delivery workflow for cash-market segments."""

        from ..services.delivery_state import PendingDeliveryStore
        from ..services.source_resolver import delivery_source, price_source
        from ..utils.async_downloader import AsyncDownloadManager, DownloadTask
        from ..utils.date_utils import DateUtils

        days = self._with_pending_delivery_days(working_days)
        days = self._with_incomplete_pipeline_days(days)
        self.total_files = len(days)
        pending_store = PendingDeliveryStore(self.config.base_data_path)
        include_delivery = self.get_download_option("include_delivery_data", True)
        success_count = 0
        processed_days: List[date] = []

        for target_date in days:
            if (
                target_date == date.today()
                and DateUtils.is_trading_day(target_date)
                and not DateUtils.is_data_available_time()
            ):
                self.logger.info(
                    f"Skipping {target_date} (current trading day; data is not ready)"
                )
                self._pipeline().skip_date(
                    self.exchange,
                    self.segment,
                    target_date,
                    "Current trading-day data is not available yet",
                )
                continue

            self._begin_pipeline_date(target_date)
            self._update_progress(f"Processing {target_date}")
            price_result = None
            delivery_data = None
            validated = False
            tasks = [DownloadTask(
                url=self.build_url(target_date),
                date_str=target_date.isoformat(),
                target_date=target_date,
            )]
            if include_delivery:
                tasks.append(DownloadTask(
                    url=delivery_source(self.exchange, target_date).url,
                    date_str=f"{target_date.isoformat()} delivery",
                    target_date=target_date,
                ))

            try:
                async with AsyncDownloadManager(self.config) as manager:
                    await self.update_async_session_timeout(
                        manager, self.config.download_settings.timeout_seconds
                    )
                    results = await manager.download_multiple(tasks)

                price_result = results[0] if results else None
                if not price_result or not price_result.success:
                    error = (
                        price_result.error_message if price_result
                        else "No download result returned"
                    )
                    self._report_error(
                        f"{self.exchange_segment} price report failed for "
                        f"{target_date}: {error}"
                    )
                    self._mark_pipeline(
                        target_date, "downloaded", "failed", error=error
                    )
                    continue

                source = price_source(self.exchange, self.segment, target_date)
                self._mark_pipeline(
                    target_date,
                    "downloaded",
                    "complete",
                    sha256=hashlib.sha256(price_result.file_data).hexdigest(),
                    source_era=source.era,
                    source_url=source.url,
                )

                delivery_ready = not include_delivery
                if include_delivery:
                    delivery_result = results[1] if len(results) > 1 else None
                    if delivery_result and delivery_result.success:
                        delivery_data = delivery_result.file_data
                        delivery_ready = True
                        self._mark_pipeline(
                            target_date,
                            "delivery",
                            "complete",
                            sha256=hashlib.sha256(delivery_data).hexdigest(),
                        )
                    else:
                        pending_store.add(self.exchange, self.segment, target_date)
                        detail = (
                            delivery_result.error_message if delivery_result
                            else "No delivery response returned"
                        )
                        self._report_notice(
                            f"{self.exchange_segment} delivery pending for "
                            f"{target_date}: {detail}"
                        )
                        self._mark_pipeline(
                            target_date,
                            "delivery",
                            "failed",
                            error=detail,
                            pending=True,
                        )

                processed = self.process_downloaded_data(
                    price_result.file_data, target_date, delivery_data
                )
                if processed is None:
                    self._mark_pipeline(
                        target_date,
                        "validated",
                        "failed",
                        error="Processor returned no data",
                    )
                    self._report_error(f"Failed to process data for {target_date}")
                    continue

                self._mark_pipeline(
                    target_date, "validated", "complete", rows=len(processed)
                )
                validated = True
                self.save_processed_data(processed, target_date)
                if delivery_ready and include_delivery:
                    pending_store.discard(self.exchange, self.segment, target_date)
                success_count += 1
                processed_days.append(target_date)
                self.completed_files += 1
                self._update_progress(f"Completed {target_date}")
            except Exception as error:
                price_payload = price_result.file_data if price_result else None
                quarantined = None
                if not validated:
                    quarantined = self._quarantine_download_payload(
                        price_payload, target_date, "price"
                    )
                    self._quarantine_download_payload(
                        delivery_data, target_date, "delivery"
                    )
                    try:
                        self._mark_pipeline(
                            target_date,
                            "validated",
                            "failed",
                            error=str(error),
                            quarantine=(
                                str(quarantined) if quarantined else None
                            ),
                        )
                    except Exception:
                        pass
                self._report_error(f"Error processing {target_date}: {error}")

        self.logger.info(f"Successfully processed {success_count}/{len(days)} files")
        if (
            processed_days
            and self.get_download_option("apply_corporate_actions", True)
            and self.get_download_option("generate_symbol_files", True)
        ):
            try:
                from ..services.corporate_actions import (
                    CorporateActionClient,
                    CorporateActionEngine,
                )
                from ..utils.user_preferences import UserPreferences

                add_sme_suffix = UserPreferences().get_sme_add_suffix()
                client = CorporateActionClient(
                    timeout=max(
                        30, self.config.download_settings.timeout_seconds
                    )
                )
                actions = await client.fetch(
                    self.exchange,
                    self.segment,
                    min(processed_days),
                    max(processed_days),
                    add_sme_suffix=add_sme_suffix,
                )
                summary = CorporateActionEngine(
                    self.config.base_data_path
                ).apply(actions)
                self.logger.info(f"Corporate-action summary: {summary}")
                for target_date in processed_days:
                    self._mark_pipeline(
                        target_date,
                        "actions",
                        "complete",
                        applied=summary.get("applied", 0),
                        manual_review=summary.get("manual_review", 0),
                    )
                if summary.get("manual_review"):
                    self._report_notice(
                        f"{summary['manual_review']} corporate action(s) require "
                        "manual review; symbol files were left unchanged"
                    )
            except Exception as error:
                for target_date in processed_days:
                    self._mark_pipeline(
                        target_date, "actions", "failed", error=str(error)
                    )
                self._report_notice(
                    f"Corporate-action update could not be completed: {error}"
                )
        self.last_segment_result = self._pipeline().segment_result(
            self.exchange, self.segment, days
        )
        return self.last_segment_result.ok

    async def _download_price_implementation(
        self, working_days: List[date]
    ) -> bool:
        """Shared validated workflow for FO and index price-only reports."""

        from ..services.source_resolver import price_source
        from ..utils.async_downloader import AsyncDownloadManager, DownloadTask
        from ..utils.date_utils import DateUtils

        days = self._with_incomplete_pipeline_days(working_days)
        self.total_files = len(days)
        for target_date in days:
            if (
                target_date == date.today()
                and DateUtils.is_trading_day(target_date)
                and not DateUtils.is_data_available_time()
            ):
                self._pipeline().skip_date(
                    self.exchange,
                    self.segment,
                    target_date,
                    "Current trading-day data is not available yet",
                )
                continue

            self._begin_pipeline_date(target_date)
            source = price_source(self.exchange, self.segment, target_date)
            payload: Optional[bytes] = None
            validated = False
            try:
                task = DownloadTask(
                    url=source.url,
                    date_str=target_date.isoformat(),
                    target_date=target_date,
                )
                async with AsyncDownloadManager(self.config) as manager:
                    await self.update_async_session_timeout(
                        manager, self.config.download_settings.timeout_seconds
                    )
                    results = await manager.download_multiple([task])
                result = results[0] if results else None
                if not result or not result.success:
                    error = result.error_message if result else "No result returned"
                    self._mark_pipeline(
                        target_date, "downloaded", "failed", error=error
                    )
                    self._report_error(
                        f"{self.exchange_segment} report failed for "
                        f"{target_date}: {error}"
                    )
                    continue

                payload = result.file_data
                self._mark_pipeline(
                    target_date,
                    "downloaded",
                    "complete",
                    sha256=hashlib.sha256(payload).hexdigest(),
                    source_era=source.era,
                    source_url=source.url,
                )
                processed = self.process_downloaded_data(payload, target_date)
                if processed is None or processed.empty:
                    raise DataProcessingError("Processor returned no data")
                self._mark_pipeline(
                    target_date, "validated", "complete", rows=len(processed)
                )
                validated = True
                self.save_processed_data(processed, target_date)
                self.completed_files += 1
                self._update_progress(f"Completed {target_date}")
            except Exception as error:
                quarantined = None
                if not validated:
                    quarantined = self._quarantine_download_payload(
                        payload, target_date, "price"
                    )
                    try:
                        self._mark_pipeline(
                            target_date,
                            "validated",
                            "failed",
                            error=str(error),
                            quarantine=(
                                str(quarantined) if quarantined else None
                            ),
                        )
                    except Exception:
                        pass
                self._report_error(f"Error processing {target_date}: {error}")

        self.last_segment_result = self._pipeline().segment_result(
            self.exchange, self.segment, days
        )
        self.logger.info(self.last_segment_result.summary())
        return self.last_segment_result.ok

    def _try_direct_bse_append(self, target_date: date, bse_eq_file_path: Path) -> None:
        """Try direct BSE INDEX to BSE EQ file append (fallback method)"""
        try:
            # Check if BSE append is enabled
            user_prefs = self.memory_append_manager.user_prefs
            if not user_prefs.get_bse_index_append_to_eq():
                self.logger.debug("BSE Index append disabled - skipping direct append")
                return

            # Look for BSE INDEX file for the same date
            bse_index_file_path = self.data_path.parent / "INDEX" / f"{target_date.strftime('%Y-%m-%d')}-BSE-INDEX.txt"

            if not bse_index_file_path.exists():
                self.logger.debug(f"BSE INDEX file not found for direct append: {bse_index_file_path}")
                return

            # Check if append already done (look for BSE SENSEX in EQ file)
            with open(bse_eq_file_path, 'r') as f:
                eq_content = f.read()

            if "BSE SENSEX" in eq_content:
                self.logger.debug("BSE INDEX data already appears to be in EQ file - skipping direct append")
                return

            # Read BSE INDEX data
            with open(bse_index_file_path, 'r') as f:
                index_lines = f.readlines()

            if not index_lines:
                self.logger.warning("BSE INDEX file is empty - skipping direct append")
                return

            if not self.get_download_option("legacy_seven_column_output", False):
                index_lines = [
                    f"{line.rstrip()},,\n" for line in index_lines if line.strip()
                ]

            # Rewrite through a sibling temporary file so the user never sees
            # a partially appended daily bhavcopy.
            temporary = bse_eq_file_path.with_suffix(
                bse_eq_file_path.suffix + ".tmp"
            )
            temporary.write_text(
                eq_content + "".join(index_lines), encoding="utf-8"
            )
            temporary.replace(bse_eq_file_path)

            self.logger.info(f"✅ Direct BSE append completed: Added {len(index_lines)} INDEX rows to {bse_eq_file_path.name}")

        except Exception as e:
            self.logger.error(f"Error in direct BSE append: {e}")

    def cleanup_temp_files(self) -> None:
        """Clean up temporary files for this downloader (no longer needed)"""
        pass  # No temp files to clean up with memory-based processing

    def validate_data_file(self, file_path: Path) -> bool:
        """
        Validate downloaded data file

        Args:
            file_path: Path to file to validate

        Returns:
            True if file is valid, False otherwise
        """
        try:
            if not file_path.exists():
                return False

            # Check file size
            if file_path.stat().st_size == 0:
                self.logger.warning(f"Empty file: {file_path}")
                return False

            # Try to read as CSV to validate format
            try:
                df = pd.read_csv(file_path, nrows=1)
                return len(df.columns) > 0
            except Exception:
                # If CSV read fails, check if it's a valid zip file
                if file_path.suffix.lower() == '.zip':
                    import zipfile
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                            return len(zip_ref.namelist()) > 0
                    except zipfile.BadZipFile:
                        return False
                return False

        except Exception as e:
            self.logger.error(f"Error validating file {file_path}: {e}")
            return False

    def get_download_summary(self) -> Dict[str, Any]:
        """
        Get summary of available data and download status

        Returns:
            Dictionary with download summary information
        """
        try:
            last_date = self.data_manager.get_last_file_date(self.exchange, self.segment)
            file_count = self.data_manager.get_file_count(self.exchange, self.segment)
            is_first_run = self.data_manager.is_first_run(self.exchange, self.segment)

            start_date, end_date = self.get_date_range()
            working_days = self.get_working_days(start_date, end_date)

            return {
                'exchange_segment': self.exchange_segment,
                'last_date': last_date.strftime('%Y-%m-%d') if last_date else None,
                'file_count': file_count,
                'is_first_run': is_first_run,
                'next_start_date': start_date.strftime('%Y-%m-%d'),
                'next_end_date': end_date.strftime('%Y-%m-%d'),
                'pending_days': len(working_days),
                'data_path': str(self.data_path)
            }

        except Exception as e:
            return {
                'exchange_segment': self.exchange_segment,
                'error': str(e),
                'last_date': None,
                'file_count': 0,
                'is_first_run': True
            }

    async def download_data_range(self,
                                start_date: Optional[date] = None,
                                end_date: Optional[date] = None) -> bool:
        """
        Download data for specified date range

        Args:
            start_date: Start date (optional, uses calculated range if None)
            end_date: End date (optional, uses calculated range if None)

        Returns:
            True if download completed successfully, False otherwise
        """
        try:
            # Calculate date range
            if start_date is None or end_date is None:
                calc_start, calc_end = self.get_date_range(start_date, end_date)
                start_date = start_date or calc_start
                end_date = end_date or calc_end

            # Get working days
            working_days = self.get_working_days(start_date, end_date)
            gap_days = self.data_manager.get_missing_file_dates(
                self.exchange, self.segment
            )
            working_days = sorted(set(working_days).union(gap_days))
            working_days = self._with_pending_delivery_days(working_days)
            working_days = self._with_incomplete_pipeline_days(working_days)

            if not working_days:
                self._update_status("No working days in date range")
                return True

            self.total_files = len(working_days)
            self.completed_files = 0

            self._update_status(f"Starting download for {len(working_days)} days")

            # This method should be implemented by concrete classes
            # to handle the actual download logic
            success = await self._download_implementation(working_days)

            result = self.last_segment_result
            if result is None:
                result = self._pipeline().segment_result(
                    self.exchange, self.segment, working_days
                )
                self.last_segment_result = result

            if success and result.ok:
                self._update_status(
                    f"Download completed successfully ({result.summary()})"
                )
            else:
                self._update_status(
                    f"Download completed with errors ({result.summary()})"
                )

            return success and result.ok

        except Exception as e:
            error_msg = f"Download failed: {e}"
            self._report_error(error_msg)
            return False
        finally:
            # Always cleanup temp files
            self.cleanup_temp_files()

    async def update_async_session_timeout(self, async_manager, new_timeout_seconds: int):
        """
        Update timeout for async download manager session

        Args:
            async_manager: AsyncDownloadManager instance
            new_timeout_seconds: New timeout value in seconds
        """
        try:
            if hasattr(async_manager, 'update_session_timeout'):
                await async_manager.update_session_timeout(new_timeout_seconds)
                self.logger.info(f"Updated async session timeout to {new_timeout_seconds}s for {self.exchange_segment}")
        except Exception as e:
            self.logger.warning(f"Failed to update async session timeout: {e}")

    @abstractmethod
    async def _download_implementation(self, working_days: List[date]) -> bool:
        """
        Implement actual download logic (to be implemented by concrete classes)

        Args:
            working_days: List of dates to download

        Returns:
            True if successful, False otherwise
        """
        pass
