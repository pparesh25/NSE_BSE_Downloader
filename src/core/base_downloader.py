"""
Base Downloader Class for NSE/BSE Data Downloader

Abstract base class providing common functionality for all exchange downloaders.
Includes date management, folder operations, and data processing interfaces.
"""

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any, Set, Tuple

import pandas as pd

from .config import Config
from .data_manager import DataManager
from .exceptions import DataProcessingError, FileOperationError
from ..services.combined_file_builder import CombinedFileBuilder
from ..services.pipeline_state import PipelineManifest, SegmentResult
from ..services.settings import SettingsService


#: How long after a date the exchange's silence stops meaning "not yet".
#: Reports are published the same evening, so three days is generous; the cost
#: of being early is that a late report is retired as absent, and the cost of
#: being late is one extra 404 on the next run.
ABSENT_REPORT_SETTLE_DAYS = 3


@dataclass
class AbsentReportLedger:
    """Dates the exchange answered 404 for, and the evidence to believe it.

    A holiday before 2013 cannot be skipped in advance -- the official API
    serves no calendar that far back -- so the exchange's own 404 is the only
    evidence the date was not traded.  Left as a failure it stays at
    ``complete=0`` and is re-downloaded on every future run forever.

    But a 404 alone proves nothing: a wrong URL era would 404 every date in its
    window, and silently retiring those is precisely how a source defect hides.
    So a candidate is settled only when the same segment successfully
    downloaded some other date in the same year during this run.  A broken era
    downloads nothing, retires nothing, and stays loud.
    """

    candidates: Dict[date, str] = field(default_factory=dict)
    downloaded_years: Set[int] = field(default_factory=set)

    def note_absent(self, target_date: date, reason: str) -> None:
        self.candidates[target_date] = reason

    def note_downloaded(self, target_date: date) -> None:
        self.downloaded_years.add(target_date.year)

    def settled(self) -> List[Tuple[date, str]]:
        return sorted(
            (target_date, reason)
            for target_date, reason in self.candidates.items()
            if target_date.year in self.downloaded_years
        )


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

        self.combined_builder = CombinedFileBuilder(config)
        self.settings = SettingsService(config)
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
        self._progress_started_at: Optional[float] = None
        self.cancel_requested: Optional[Callable[[], bool]] = None
        self.combined_dependencies: tuple[str, ...] = ()
        self.combined_required = False

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Set progress callback for tracking download progress"""
        self.progress_callback = callback

    def _update_progress(self, message: str = "") -> None:
        """Update progress percentage"""
        if self.progress_callback and self.total_files > 0:
            now = time.monotonic()
            started = getattr(self, "_progress_started_at", None)
            if started is None:
                started = now
                self._progress_started_at = started
            percentage = int((self.completed_files / self.total_files) * 100)
            remaining = max(0, self.total_files - self.completed_files)
            if self.completed_files > 0 and remaining:
                elapsed = max(0.0, now - started)
                eta_seconds = elapsed / self.completed_files * remaining
                eta = (
                    f"{eta_seconds / 60:.1f}m"
                    if eta_seconds >= 60
                    else f"{eta_seconds:.0f}s"
                )
                message = f"{message} · {remaining} remaining · ETA {eta}"
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

        settings = getattr(self, "settings", None)
        if settings is None:
            settings = SettingsService(self.config)
            self.settings = settings
        return settings.get_download_option(name, default)

    async def _run_pipeline_stage(
        self, stage: str, function: Callable[..., Any], *args: Any
    ) -> Any:
        """Run blocking prepare/persistence work off the asyncio loop."""
        executors = getattr(self.config, "stage_executors", None)
        executor = executors.get(stage) if executors else None
        if executor is not None:
            return await executor.run(
                function,
                *args,
                stage=f"{self.exchange_segment}:{stage}",
            )
        # Direct service use and older integrations still receive loop
        # isolation, with no global lifecycle assumption.
        return await asyncio.to_thread(function, *args)

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
    def process_downloaded_data(
        self,
        file_data: bytes,
        file_date: date,
        delivery_data: Optional[bytes] = None,
    ) -> Optional[pd.DataFrame]:
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
    def transform_data(
        self, df: pd.DataFrame, file_date: date
    ) -> pd.DataFrame:
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
        disabled = []
        if self.segment == "EQ" and getattr(
            self, "combined_required", False
        ):
            required.append("combined")
        else:
            disabled.append("combined")
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

    def _core_pipeline_ok(self, result: SegmentResult) -> bool:
        """Return true when every pre-reconciliation enabled stage is complete."""

        required, _ = self._pipeline_requirements()
        deferred = {"combined"}
        if getattr(self.config, "history_batch_coordinator", None) is not None:
            deferred.update({"symbols", "actions"})
        core_required = set(required).difference(deferred)
        if not result.dates:
            return False
        for item in result.dates:
            if item.status == "skipped":
                continue
            if not core_required.issubset(item.completed_stages):
                return False
            if set(item.failed_stages).difference(deferred):
                return False
        return True

    def _is_cancel_requested(self) -> bool:
        callback = getattr(self, "cancel_requested", None)
        return bool(callback and callback())

    def _pipeline(self) -> PipelineManifest:
        """Return the manifest, including for lightweight test subclasses."""

        manifest = getattr(self, "pipeline_manifest", None)
        if manifest is None:
            manifest = PipelineManifest(self.config.base_data_path)
            self.pipeline_manifest = manifest
        return manifest

    def _combined_builder(self) -> CombinedFileBuilder:
        """Return the persisted-component builder for lightweight subclasses."""

        builder = getattr(self, "combined_builder", None)
        if builder is None:
            builder = CombinedFileBuilder(self.config)
            self.combined_builder = builder
        return builder

    def _begin_pipeline_date(self, target_date: date) -> None:
        required, disabled = self._pipeline_requirements()
        pipeline = self._pipeline()
        pipeline.begin(
            self.exchange,
            self.segment,
            target_date,
            required,
            disabled,
        )
        # A historical rerun may already have a completed combined stage.
        # Reset it before replacing any component so a crash cannot make the
        # previous recipe look current.
        if "combined" in required:
            pipeline.require_stage(
                self.exchange, self.segment, target_date, "combined"
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
        telemetry = getattr(self.config, "pipeline_telemetry", None)
        if telemetry is not None:
            telemetry.record(
                "pipeline_stage",
                exchange_segment=self.exchange_segment,
                exchange=self.exchange,
                segment=self.segment,
                date=target_date.isoformat(),
                stage=stage,
                status=status,
            )

    def _is_absent_report(self, result: Any, target_date: date) -> bool:
        """Whether the exchange answered "this report does not exist"."""

        from ..utils.date_utils import DateUtils

        if getattr(result, "outcome", None) != "file_not_found":
            return False
        age = (DateUtils.today_ist() - target_date).days
        return age >= ABSENT_REPORT_SETTLE_DAYS

    def _settle_absent_reports(self, ledger: AbsentReportLedger) -> List[date]:
        """Retire dates the exchange never published so they stop returning.

        Unsettled candidates keep their failed stage, so a date the run could
        not explain is still queued for repair rather than quietly dropped.
        """

        settled = ledger.settled()
        for target_date, reason in settled:
            self._pipeline().skip_date(
                self.exchange, self.segment, target_date, reason
            )
        if settled:
            dates = ", ".join(
                target_date.isoformat() for target_date, _ in settled[:5]
            )
            if len(settled) > 5:
                dates += f", and {len(settled) - 5} more"
            self._report_notice(
                f"{self.exchange_segment}: the exchange published no report "
                f"for {len(settled)} date(s), which will not be retried: "
                f"{dates}"
            )
        unsettled = len(ledger.candidates) - len(settled)
        if unsettled:
            self._report_error(
                f"{self.exchange_segment}: {unsettled} date(s) returned "
                "'not found' and no other date in that year downloaded, so "
                "the source itself may be wrong; they remain queued for repair"
            )
        return [target_date for target_date, _ in settled]

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

    def save_processed_data(self, df: pd.DataFrame, target_date: date) -> Path:
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
            component = None
            if self.segment in {"EQ", "SME", "INDEX"}:
                component = self._combined_builder().save_component(
                    self.exchange, self.segment, target_date, df
                )

            publication_deferred = bool(
                self.segment == "EQ"
                and getattr(self, "combined_required", False)
            )
            if not publication_deferred:
                # Save without header and index (as per original code), but
                # never expose a partially-written file if interrupted.
                temporary = output_path.with_suffix(output_path.suffix + ".tmp")
                try:
                    df.to_csv(temporary, index=False, header=False)
                    temporary.replace(output_path)
                finally:
                    temporary.unlink(missing_ok=True)
            component_metadata = {}
            if component is not None:
                component_metadata = {
                    "component_path": str(component.path),
                    "component_sha256": component.sha256,
                }
            published_sha256 = (
                component.sha256
                if publication_deferred and component is not None
                else self._file_sha256(output_path)
            )
            self._mark_pipeline(
                target_date,
                "daily",
                "complete",
                path=str(output_path),
                sha256=published_sha256,
                rows=len(df),
                publication_deferred=publication_deferred,
                **component_metadata,
            )

            coordinator = getattr(self.config, "date_join_coordinator", None)
            if coordinator is not None and component is not None:
                join_result = coordinator.offer(
                    self.exchange, self.segment, target_date, df
                )
                if join_result is not None:
                    if join_result.ok:
                        self.logger.info(
                            "Staged date join published %s rows for %s %s",
                            join_result.rows,
                            self.exchange,
                            target_date,
                        )
                    else:
                        self.logger.error(
                            "Staged date join failed for %s %s: %s",
                            self.exchange,
                            target_date,
                            join_result.error,
                        )

            if publication_deferred:
                self.logger.info(
                    f"Staged processed data for combined publication: {filename}"
                )
            else:
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
                history_batch = getattr(
                    self.config, "history_batch_coordinator", None
                )
                if history_batch is None:
                    raise RuntimeError(
                        "Staged history coordinator is unavailable for this run"
                    )
                snapshot = history_batch.offer(
                    self.exchange,
                    self.segment,
                    target_date,
                    internal_equity,
                )
                self._mark_pipeline(
                    target_date,
                    "symbols",
                    "pending",
                    queued=True,
                    snapshot_path=str(snapshot),
                )
                self.logger.info(
                    "Queued %s %s rows for run-scoped history batch",
                    len(internal_equity),
                    self.exchange,
                )

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
        absent = AbsentReportLedger()

        for target_date in days:
            if self._is_cancel_requested():
                raise asyncio.CancelledError
            if (
                target_date == DateUtils.today_ist()
                and self.data_manager.is_trading_day(target_date)
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
                exchange_segment=self.exchange_segment,
            )]
            if include_delivery:
                tasks.append(DownloadTask(
                    url=delivery_source(self.exchange, target_date).url,
                    date_str=f"{target_date.isoformat()} delivery",
                    target_date=target_date,
                    exchange_segment=self.exchange_segment,
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
                    if self._is_absent_report(price_result, target_date):
                        absent.note_absent(
                            target_date,
                            "The exchange published no report for this date",
                        )
                    else:
                        self._report_error(
                            f"{self.exchange_segment} price report failed for "
                            f"{target_date}: {error}"
                        )
                    self._mark_pipeline(
                        target_date, "downloaded", "failed", error=error
                    )
                    continue

                source = price_source(self.exchange, self.segment, target_date)
                absent.note_downloaded(target_date)
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

                processed = await self._run_pipeline_stage(
                    "prepare",
                    self.process_downloaded_data,
                    price_result.file_data,
                    target_date,
                    delivery_data,
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
                if self._is_cancel_requested():
                    raise asyncio.CancelledError
                await self._run_pipeline_stage(
                    "persist", self.save_processed_data, processed, target_date
                )
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

        self._settle_absent_reports(absent)
        self.logger.info(f"Successfully processed {success_count}/{len(days)} files")
        should_apply_actions = (
            processed_days
            and not self._is_cancel_requested()
            and self.get_download_option("apply_corporate_actions", True)
            and self.get_download_option("generate_symbol_files", True)
        )
        history_batch = getattr(
            self.config, "history_batch_coordinator", None
        )
        if should_apply_actions:
            if history_batch is None:
                raise RuntimeError(
                    "Staged history coordinator is unavailable for corporate actions"
                )
            add_sme_suffix = SettingsService(
                self.config
            ).preferences.get_sme_add_suffix()
            history_batch.register_action_window(
                self.exchange,
                self.segment,
                processed_days,
                add_sme_suffix=add_sme_suffix,
                timeout=max(
                    30, self.config.download_settings.timeout_seconds
                ),
            )
        self.last_segment_result = self._pipeline().segment_result(
            self.exchange, self.segment, days
        )
        return self._core_pipeline_ok(self.last_segment_result)

    async def _download_price_implementation(
        self, working_days: List[date]
    ) -> bool:
        """Shared validated workflow for FO and index price-only reports."""

        from ..services.source_resolver import price_source
        from ..utils.async_downloader import AsyncDownloadManager, DownloadTask
        from ..utils.date_utils import DateUtils

        days = self._with_incomplete_pipeline_days(working_days)
        self.total_files = len(days)
        absent = AbsentReportLedger()
        for target_date in days:
            if self._is_cancel_requested():
                raise asyncio.CancelledError
            if (
                target_date == DateUtils.today_ist()
                and self.data_manager.is_trading_day(target_date)
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
                    exchange_segment=self.exchange_segment,
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
                    if self._is_absent_report(result, target_date):
                        absent.note_absent(
                            target_date,
                            "The exchange published no report for this date",
                        )
                    else:
                        self._report_error(
                            f"{self.exchange_segment} report failed for "
                            f"{target_date}: {error}"
                        )
                    continue

                payload = result.file_data
                absent.note_downloaded(target_date)
                self._mark_pipeline(
                    target_date,
                    "downloaded",
                    "complete",
                    sha256=hashlib.sha256(payload).hexdigest(),
                    source_era=source.era,
                    source_url=source.url,
                )
                processed = await self._run_pipeline_stage(
                    "prepare", self.process_downloaded_data, payload, target_date
                )
                if processed is None or processed.empty:
                    raise DataProcessingError("Processor returned no data")
                self._mark_pipeline(
                    target_date, "validated", "complete", rows=len(processed)
                )
                validated = True
                if self._is_cancel_requested():
                    raise asyncio.CancelledError
                await self._run_pipeline_stage(
                    "persist", self.save_processed_data, processed, target_date
                )
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

        self._settle_absent_reports(absent)
        self.last_segment_result = self._pipeline().segment_result(
            self.exchange, self.segment, days
        )
        self.logger.info(self.last_segment_result.summary())
        return self._core_pipeline_ok(self.last_segment_result)

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
