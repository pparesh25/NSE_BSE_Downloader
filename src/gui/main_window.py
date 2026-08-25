"""
Main Window for NSE/BSE Data Downloader

PySide6-based main window with exchange selection, progress tracking,
and background download management.
"""

import asyncio
from datetime import date
from enum import Enum
from pathlib import Path
import time
from threading import Event
from typing import Any, Dict, List, Optional
import logging

from aiohttp import ClientError
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QCheckBox, QProgressBar, QTextEdit,
    QGroupBox, QFrame, QMessageBox, QStatusBar, QSizePolicy, QDateEdit,
    QScrollArea, QSpinBox,
)
from PySide6.QtCore import QDate, QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont, QAction

from ..core.config import Config
from ..core.data_manager import DataManager
from ..downloaders.nse_eq_downloader import NSEEQDownloader
from ..downloaders.nse_fo_downloader import NSEFODownloader
from ..downloaders.nse_sme_downloader import NSESMEDownloader
from ..downloaders.nse_index_downloader import NSEIndexDownloader
from ..downloaders.bse_eq_downloader import BSEEQDownloader
from ..downloaders.bse_index_downloader import BSEIndexDownloader
from ..utils.update_checker import UpdateChecker
from .update_dialog import UpdateDialog
from .donate_dialog import DonateDialog
from ..core.base_downloader import BaseDownloader, ProgressCallback
from ..core.exceptions import GUIError
from ..services.combined_file_builder import CombinedFileBuilder
from ..services.date_join_coordinator import DateJoinCoordinator
from ..services.history_batch import HistoryBatchCoordinator
from ..services.source_resolver import earliest_available
from ..services.pipeline_telemetry import (
    PipelineEvent,
    PipelineStatusPresenter,
    PipelineTelemetry,
)
from ..services.settings import SettingsService
from ..services.state_retention import (
    policies_from_settings,
    prune_state_directories,
)
from ..utils.transport_pool import TransportPool
from ..utils.stage_executor import BoundedStageExecutor

from .collapsible_section import CollapsibleSection



class GUIOutcome(str, Enum):
    """Stable outcome vocabulary shared by workers and GUI rendering."""

    SUCCESS = "success"
    PARTIAL = "partial"
    PENDING = "pending"
    WARNING = "warning"
    REPAIR_REQUIRED = "repair-required"
    CANCELLED = "cancelled"
    FAILED = "failed"


class UpdateCheckWorker(QThread):
    """Worker thread for checking updates"""

    update_checked = Signal(dict)  # Update result

    def __init__(self, update_checker: UpdateChecker):
        super().__init__()
        self.update_checker = update_checker
        self.logger = logging.getLogger(__name__)

    def request_stop(self) -> None:
        """Suppress results after a close request without killing the thread."""

        self.requestInterruption()

    def run(self):
        """Check for updates in background"""
        try:
            if self.isInterruptionRequested():
                return
            result = self.update_checker.check_for_updates()
            if not self.isInterruptionRequested():
                self.update_checked.emit(result)
        except Exception as e:
            self.logger.error(f"Error in update check worker: {e}")
            if not self.isInterruptionRequested():
                self.update_checked.emit({
                    "update_available": False,
                    "error": str(e),
                })


class DownloadWorker(QThread):
    """Background worker thread for downloads"""

    progress_updated = Signal(str, int, str)  # exchange, percentage, message
    status_updated = Signal(str, str)         # exchange, status
    error_occurred = Signal(str, str)         # exchange, error
    download_completed = Signal(str, bool)    # exchange, success
    all_downloads_completed = Signal(bool)    # overall success
    segment_outcome = Signal(str, str)        # exchange, GUIOutcome value
    overall_outcome = Signal(str)             # GUIOutcome value
    retry_candidates_ready = Signal(object)   # segment -> ISO date list

    def __init__(
        self,
        config: Config,
        selected_exchanges: List[str],
        include_weekends: bool = False,
        timeout_seconds: int = 5,
        custom_start_date: Optional[date] = None,
        custom_end_date: Optional[date] = None,
        append_options: Optional[Dict[str, bool]] = None,
        retry_dates: Optional[Dict[str, List[date]]] = None,
    ):
        super().__init__()
        self.config = config
        self.append_options = append_options or {}
        self.retry_dates = self.expand_retry_dates(
            retry_dates or {}, self.append_options
        )
        self.selected_exchanges = self.expand_selected_exchanges(
            list(dict.fromkeys([*selected_exchanges, *self.retry_dates])),
            self.append_options,
        )
        self.include_weekends = include_weekends
        self.timeout_seconds = timeout_seconds
        self.custom_start_date = custom_start_date
        self.custom_end_date = custom_end_date
        self.downloaders: Dict[str, BaseDownloader] = {}
        self.logger = logging.getLogger(__name__)

        # Cross-thread cancellation state.  QThread interruption alone does
        # not wake asyncio network operations, so request_stop also cancels
        # their tasks through the worker event loop.
        self.stop_requested = False
        self._cancel_event = Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self.final_outcome = GUIOutcome.FAILED
        self._status_presenter = PipelineStatusPresenter()

        # Update config timeout
        self.config.download_settings.timeout_seconds = timeout_seconds

        # Initialize downloaders
        self._initialize_downloaders()

    def is_cancel_requested(self) -> bool:
        return (
            self.stop_requested
            or self._cancel_event.is_set()
            or self.isInterruptionRequested()
        )

    def request_stop(self) -> None:
        """Cooperatively cancel active asyncio work from the GUI thread."""

        self.stop_requested = True
        self._cancel_event.set()
        self.requestInterruption()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_active_tasks)

    def _cancel_active_tasks(self) -> None:
        for task in tuple(self._tasks.values()):
            if not task.done():
                task.cancel()

    @staticmethod
    def expand_selected_exchanges(
        selected_exchanges: List[str], append_options: Dict[str, bool]
    ) -> List[str]:
        """Include every segment explicitly required by a combined-file option."""

        selected = list(dict.fromkeys(selected_exchanges))
        if "NSE_EQ" in selected:
            if append_options.get("sme_append_to_eq", False):
                selected.append("NSE_SME")
            if append_options.get("index_append_to_eq", False):
                selected.append("NSE_INDEX")
        if (
            "BSE_EQ" in selected
            and append_options.get("bse_index_append_to_eq", False)
        ):
            selected.append("BSE_INDEX")
        order = (
            "NSE_EQ", "NSE_FO", "NSE_SME", "NSE_INDEX", "BSE_EQ", "BSE_INDEX"
        )
        unique = set(selected)
        return [name for name in order if name in unique]

    @staticmethod
    def expand_retry_dates(
        retry_dates: Dict[str, List[date]], append_options: Dict[str, bool]
    ) -> Dict[str, List[date]]:
        """Close exact retry dates over required combined-file dependencies."""

        expanded = {
            name: set(values) for name, values in retry_dates.items() if values
        }
        markets = {
            "NSE": (
                ("sme_append_to_eq", "NSE_SME"),
                ("index_append_to_eq", "NSE_INDEX"),
            ),
            "BSE": (("bse_index_append_to_eq", "BSE_INDEX"),),
        }
        for market, option_segments in markets.items():
            eq_name = f"{market}_EQ"
            dependencies = [
                name for option, name in option_segments
                if append_options.get(option, False)
            ]
            relevant = [eq_name, *dependencies]
            dates = set().union(*(
                expanded.get(name, set()) for name in relevant
            ))
            if dates:
                for name in relevant:
                    expanded.setdefault(name, set()).update(dates)
        return {
            name: sorted(values) for name, values in expanded.items()
        }

    def update_timeout(self, new_timeout_seconds: int):
        """
        Update timeout for all downloaders and their async managers

        Args:
            new_timeout_seconds: New timeout value in seconds
        """
        self.timeout_seconds = new_timeout_seconds
        self.config.download_settings.timeout_seconds = new_timeout_seconds

        # Update timeout for all downloaders
        for downloader in self.downloaders.values():
            if hasattr(downloader, 'config'):
                downloader.config.download_settings.timeout_seconds = new_timeout_seconds

        self.logger.info(f"Updated timeout to {new_timeout_seconds}s for all downloaders")

    def _initialize_downloaders(self):
        """Initialize downloader instances"""
        downloader_classes: Dict[str, Any] = {
            'NSE_EQ': NSEEQDownloader,
            'NSE_FO': NSEFODownloader,
            'NSE_SME': NSESMEDownloader,
            'NSE_INDEX': NSEIndexDownloader,
            'BSE_EQ': BSEEQDownloader,
            'BSE_INDEX': BSEIndexDownloader
        }

        for exchange in self.selected_exchanges:
            if exchange in downloader_classes:
                try:
                    downloader: BaseDownloader = downloader_classes[exchange](
                        self.config
                    )

                    downloader.set_progress_callback(
                        self._progress_callback_for(exchange)
                    )
                    downloader.cancel_requested = self.is_cancel_requested

                    if exchange.endswith("_EQ"):
                        market = exchange.split("_", 1)[0]
                        dependencies = CombinedFileBuilder.dependencies_from_options(
                            market,
                            self.append_options,
                            self.selected_exchanges,
                        )
                        downloader.combined_dependencies = dependencies
                        downloader.combined_required = bool(dependencies)

                    self.downloaders[exchange] = downloader

                except Exception as e:
                    self.logger.error(f"Failed to initialize {exchange} downloader: {e}")

    def _progress_callback_for(self, exchange: str) -> ProgressCallback:
        """Bind downloader callbacks to the selected GUI segment."""

        def on_progress(
            _reported_exchange: str, percentage: int, message: str
        ) -> None:
            self.progress_updated.emit(exchange, percentage, message)

        def on_status(_reported_exchange: str, message: str) -> None:
            self.status_updated.emit(exchange, message)

        def on_error(_reported_exchange: str, error: str) -> None:
            self.error_occurred.emit(exchange, error)

        return ProgressCallback(on_progress, on_status, on_error)

    def _handle_pipeline_event(self, event: PipelineEvent) -> None:
        update = self._status_presenter.present(event)
        if update is not None:
            self.status_updated.emit(
                update.exchange_segment, update.message
            )

    def _prune_state_directories(self) -> None:
        """Keep the diagnostic copies under ``.state`` bounded.

        Runs once per run, after every download has released its files.  This
        is housekeeping and must never change what the run reports, so a
        failure is logged and dropped rather than raised.
        """

        base_data_path = getattr(self.config, "base_data_path", None)
        if base_data_path is None:
            return
        try:
            outcomes = prune_state_directories(
                Path(base_data_path) / ".state",
                policies_from_settings(
                    getattr(self.config, "retention_settings", None)
                ),
            )
        except Exception as error:
            self.logger.warning("State retention sweep failed: %s", error)
            return
        telemetry = getattr(self.config, "pipeline_telemetry", None)
        for outcome in outcomes:
            if telemetry is not None:
                telemetry.record(
                    "state_retention",
                    directory=outcome.directory,
                    removed_files=outcome.removed_files,
                    removed_bytes=outcome.removed_bytes,
                    kept_files=outcome.kept_files,
                    errors=outcome.errors,
                )
            if outcome.removed_files:
                self.logger.info(
                    "Retention removed %s files (%.1f MiB) from .state/%s",
                    outcome.removed_files,
                    outcome.removed_bytes / (1024 * 1024),
                    outcome.directory,
                )
            if outcome.errors:
                self.logger.warning(
                    "Retention could not remove %s files from .state/%s",
                    outcome.errors,
                    outcome.directory,
                )

    def run(self):
        """Run downloads in background thread"""
        loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            # Set up asyncio event loop for this thread
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)

            # Run downloads
            overall_success = loop.run_until_complete(self._run_downloads())

            # Emit completion signal
            self.all_downloads_completed.emit(overall_success)
            self.overall_outcome.emit(self.final_outcome.value)

        except Exception as e:
            self.logger.error(f"Error in download worker: {e}")
            self.final_outcome = (
                GUIOutcome.CANCELLED
                if self.is_cancel_requested() else GUIOutcome.FAILED
            )
            self.all_downloads_completed.emit(False)
            self.overall_outcome.emit(self.final_outcome.value)
        finally:
            # Clean up event loop
            try:
                if loop is not None:
                    loop.close()
            except Exception:
                pass
            self._loop = None
            self._tasks.clear()

    async def _run_downloads(self) -> bool:
        """Run all downloads asynchronously"""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._tasks = {}

        for exchange, downloader in self.downloaders.items():
            try:
                # Check if stop requested
                if self.is_cancel_requested():
                    self.logger.info("Download stopped by user request")
                    self.final_outcome = GUIOutcome.CANCELLED
                    return False
                # Create download task
                task = asyncio.create_task(
                    self._download_exchange_data(exchange, downloader)
                )
                self._tasks[exchange] = task

            except Exception as e:
                self.error_occurred.emit(exchange, f"Failed to start download: {e}")

        if not self._tasks:
            self.final_outcome = (
                GUIOutcome.CANCELLED
                if self.is_cancel_requested() else GUIOutcome.FAILED
            )
            return False

        # Share acquisition transport, bounded stage executors and staged
        # publication coordinators across all selected segments in this run.
        transport_pool = TransportPool(self.config)
        self.config.transport_pool = transport_pool
        self.config.pipeline_telemetry = PipelineTelemetry()
        self.config.pipeline_telemetry.subscribe(
            self._handle_pipeline_event
        )
        settings = self.config.download_settings
        dependencies = {
            exchange: CombinedFileBuilder.dependencies_from_options(
                exchange, self.append_options, self.selected_exchanges
            )
            for exchange in ("NSE", "BSE")
        }
        self.config.date_join_coordinator = DateJoinCoordinator(
            self.config,
            dependencies,
            max_cache_dates=getattr(settings, "prepared_cache_dates", 4),
            telemetry=self.config.pipeline_telemetry,
        )
        self.config.history_batch_coordinator = HistoryBatchCoordinator(
            self.config,
            telemetry=self.config.pipeline_telemetry,
        )
        self.config.stage_executors = {
            "prepare": BoundedStageExecutor(
                name="prepare",
                max_workers=getattr(settings, "prepare_workers", 2),
                queue_size=getattr(settings, "stage_queue_size", 2),
                telemetry=self.config.pipeline_telemetry,
            ),
            "persist": BoundedStageExecutor(
                name="persist",
                max_workers=getattr(settings, "persistence_workers", 1),
                queue_size=getattr(settings, "stage_queue_size", 2),
                telemetry=self.config.pipeline_telemetry,
            ),
        }
        try:
            await transport_pool.start()
            # Wait for all downloads to complete.
            results = await asyncio.gather(
                *self._tasks.values(), return_exceptions=True
            )
            if not self.is_cancel_requested():
                try:
                    await self._finalize_staged_histories()
                except Exception as error:
                    self.logger.exception(
                        "Staged history finalization failed: %s", error
                    )
                    self.error_occurred.emit(
                        "Symbol histories",
                        f"History batch failed; daily files remain available: {error}",
                    )
        finally:
            for executor in getattr(self.config, "stage_executors", {}).values():
                await executor.close()
            await transport_pool.close()
            # Before the telemetry export below, so the sweep's own events are
            # part of the file this run leaves behind.
            self._prune_state_directories()
            base_data_path = getattr(self.config, "base_data_path", None)
            if base_data_path is not None:
                try:
                    telemetry_path = (
                        base_data_path / ".state" / "transport_events.jsonl"
                    )
                    self.config.pipeline_telemetry.export_jsonl(telemetry_path)
                except Exception as error:
                    # Observability must never replace the real run outcome.
                    self.logger.warning(
                        "Could not persist pipeline telemetry: %s", error
                    )
            self.config.transport_pool = None
            self.config.stage_executors = {}
            self.config.pipeline_telemetry.unsubscribe(
                self._handle_pipeline_event
            )
        settled = dict(zip(self._tasks, results))

        if not self.is_cancel_requested():
            self._finalize_staged_outputs()
            self._refresh_staged_task_results(settled)
        self.config.date_join_coordinator = None
        self.config.history_batch_coordinator = None

        outcomes = []
        for exchange, result in settled.items():
            outcome = self._classify_segment_outcome(exchange, result)
            outcomes.append(outcome)
            self.segment_outcome.emit(exchange, outcome.value)
            success = outcome == GUIOutcome.SUCCESS
            self.download_completed.emit(exchange, success)
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                self.error_occurred.emit(exchange, f"Download failed: {result}")

        self.final_outcome = self._classify_overall_outcome(outcomes)
        self.retry_candidates_ready.emit(self._collect_retry_candidates())
        return self.final_outcome == GUIOutcome.SUCCESS

    def _collect_retry_candidates(self) -> Dict[str, List[str]]:
        """Return exact failed/pending dates; skipped dates stay terminal."""

        candidates: Dict[str, List[str]] = {}
        for name, downloader in self.downloaders.items():
            result = getattr(downloader, "last_segment_result", None)
            if result is None:
                continue
            dates = [
                item.target_date.isoformat()
                for item in result.dates
                if item.status not in {"success", "skipped"}
            ]
            if dates:
                candidates[name] = sorted(set(dates))
        return candidates

    def _publish_histories_from_database(self) -> None:
        """Write the run's symbol histories out of the EOD database.

        Phase 5 step 3, and only when the preference asks for it.  The batch
        above has already re-pointed the registry and resolved renames; what
        it did not do is read and rewrite every touched file, because this
        extends them instead.  A failure here is reported and leaves the
        published tree as the batch left it -- which, with the batch not
        writing, means the affected histories are simply not yet extended and
        the next run settles them.
        """

        store = getattr(self.config, "eod_store", None)
        if store is None or not store.touched_keys:
            return
        from ..services.settings import SettingsService

        if not SettingsService(self.config).get_download_option(
            "publish_histories_from_database", False
        ):
            return
        import json

        from ..services.eod_export import applied_actions
        from ..services.eod_publish import publish_histories

        base = Path(self.config.base_data_path)
        state = base / ".state"
        registry_path = state / "symbol_registry.json"
        try:
            registry = (
                json.loads(registry_path.read_text(encoding="utf-8"))
                if registry_path.is_file() else {}
            )
            result = publish_histories(
                store, base, registry, applied_actions(state),
                store.touched_keys, store.touched_dates,
            )
        except Exception as error:
            self.logger.exception("Database history publication failed")
            self.error_occurred.emit(
                "Symbol histories",
                f"Publishing histories from the database failed; daily files "
                f"remain available: {error}",
            )
            return
        self.status_updated.emit("Symbol histories", result.render())
        if result.failures:
            named = "; ".join(result.failures[:3])
            if len(result.failures) > 3:
                named += f"; and {len(result.failures) - 3} more"
            self.error_occurred.emit(
                "Symbol histories",
                f"{len(result.failures)} histories were not published "
                f"({named})",
            )

    async def _finalize_staged_histories(self) -> None:
        """Publish queued histories, then apply actions once per exchange."""

        coordinator = getattr(
            self.config, "history_batch_coordinator", None
        )
        if coordinator is None:
            return
        executor = getattr(self.config, "stage_executors", {}).get("persist")
        if executor is None:
            outcomes = coordinator.finalize()
        else:
            outcomes = await executor.run(
                coordinator.finalize, stage="history_batch"
            )
        for outcome in outcomes:
            result = outcome.result
            self.status_updated.emit(
                "Symbol histories",
                f"History batch published {result.symbols} symbols from "
                f"{result.entries} date/segment entries "
                f"({result.history_reads} reads, {result.history_writes} writes)",
            )
            if result.failures:
                # The rest of the batch is published; these dates were marked
                # failed and come back through the ordinary repair path.
                named = ", ".join(
                    failure.path for failure in result.failures[:3]
                )
                if len(result.failures) > 3:
                    named += f", and {len(result.failures) - 3} more"
                self.error_occurred.emit(
                    "Symbol histories",
                    f"{len(result.failures)} symbol histories were not "
                    f"published and their dates are queued for repair "
                    f"({named})",
                )
            if result.refused_merges:
                named = "; ".join(
                    f"{refusal.old_path} + {refusal.new_path}"
                    for refusal in result.refused_merges[:3]
                )
                if len(result.refused_merges) > 3:
                    named += (
                        f"; and {len(result.refused_merges) - 3} more"
                    )
                self.error_occurred.emit(
                    "Symbol histories",
                    f"{len(result.refused_merges)} history merges were "
                    f"refused: the files share trading dates, so they are "
                    f"two securities, not one renamed security. Both files "
                    f"were kept unchanged ({named})",
                )

        self._publish_histories_from_database()

        windows = coordinator.action_windows()
        if not windows:
            return
        from ..services.corporate_actions import (
            CorporateActionClient,
            CorporateActionEngine,
        )

        telemetry = getattr(self.config, "pipeline_telemetry", None)

        async def fetch_window(window):
            client = CorporateActionClient(timeout=window.timeout)
            started = time.monotonic_ns()
            for attempt in (1, 2):
                try:
                    actions = await client.fetch(
                        window.exchange,
                        window.segment,
                        min(window.dates),
                        max(window.dates),
                        add_sme_suffix=window.add_sme_suffix,
                    )
                except (asyncio.TimeoutError, ClientError) as error:
                    if attempt == 1:
                        if telemetry is not None:
                            telemetry.record(
                                "corporate_action_retry_scheduled",
                                exchange_segment=(
                                    f"{window.exchange}_{window.segment}"
                                ),
                                attempt=attempt,
                                error_type=type(error).__name__,
                                delay_seconds=0.5,
                            )
                        await asyncio.sleep(0.5)
                        continue
                    detail = str(error).strip() or type(error).__name__
                    terminal_error = RuntimeError(detail)
                    if telemetry is not None:
                        telemetry.record(
                            "corporate_action_fetch_finished",
                            exchange_segment=(
                                f"{window.exchange}_{window.segment}"
                            ),
                            outcome="error",
                            attempts=attempt,
                            error_type=type(error).__name__,
                            duration_ms=(
                                time.monotonic_ns() - started
                            ) / 1_000_000,
                        )
                    return terminal_error
                except Exception as error:
                    detail = str(error).strip() or type(error).__name__
                    terminal_error = RuntimeError(detail)
                    if telemetry is not None:
                        telemetry.record(
                            "corporate_action_fetch_finished",
                            exchange_segment=(
                                f"{window.exchange}_{window.segment}"
                            ),
                            outcome="error",
                            attempts=attempt,
                            error_type=type(error).__name__,
                            duration_ms=(
                                time.monotonic_ns() - started
                            ) / 1_000_000,
                        )
                    return terminal_error
                break
            if telemetry is not None:
                telemetry.record(
                    "corporate_action_fetch_finished",
                    exchange_segment=f"{window.exchange}_{window.segment}",
                    outcome="success",
                    attempts=attempt,
                    actions=len(actions),
                    duration_ms=(
                        time.monotonic_ns() - started
                    ) / 1_000_000,
                )
            return actions

        # Windows are independent read-only exchange requests. Gathering them
        # overlaps network setup/latency while preserving result order below.
        fetched_windows = await asyncio.gather(*(
            fetch_window(window) for window in windows
        ))

        actions_by_exchange: dict[str, list[Any]] = {}
        windows_by_exchange: dict[str, list[Any]] = {}
        for window, fetched in zip(windows, fetched_windows):
            if isinstance(fetched, Exception):
                coordinator.pipeline.mark_many([
                    (
                        window.exchange,
                        window.segment,
                        target_date,
                        "actions",
                        "failed",
                        {"error": str(fetched)},
                    )
                    for target_date in window.dates
                ])
                self.error_occurred.emit(
                    f"{window.exchange}_{window.segment}",
                    f"Corporate-action fetch failed: {fetched}",
                )
                continue
            actions_by_exchange.setdefault(window.exchange, []).extend(fetched)
            windows_by_exchange.setdefault(window.exchange, []).append(window)

        for exchange, actions in actions_by_exchange.items():
            engine = CorporateActionEngine(self.config.base_data_path)
            related = windows_by_exchange[exchange]
            started = time.monotonic_ns()
            try:
                if executor is None:
                    summary = engine.apply(actions)
                else:
                    summary = await executor.run(
                        engine.apply, actions, stage="corporate_actions"
                    )
                coordinator.pipeline.mark_many([
                    (
                        window.exchange,
                        window.segment,
                        target_date,
                        "actions",
                        "complete",
                        {
                            "applied": summary.get("applied", 0),
                            "manual_review": summary.get("manual_review", 0),
                            "deferred": summary.get("deferred", 0),
                        },
                    )
                    for window in related
                    for target_date in window.dates
                ])
                if summary.get("manual_review"):
                    self.status_updated.emit(
                        exchange,
                        f"{summary['manual_review']} corporate action(s) "
                        "require manual review",
                    )
                if summary.get("deferred"):
                    self.status_updated.emit(
                        exchange,
                        f"{summary['deferred']} corporate action(s) wait for "
                        "the ex-date bar and are retried on the next run",
                    )
                if summary.get("factor_conflicts"):
                    # The history already carries an older reading of these
                    # announcements, so nothing is re-divided; say so, because
                    # only a rebuild can adopt the corrected factor.
                    self.error_occurred.emit(
                        exchange,
                        f"{summary['factor_conflicts']} corporate action(s) "
                        "are recorded with a different factor than this run "
                        "reads; rebuild those symbols to adopt the new "
                        "reading",
                    )
            except Exception as error:
                if telemetry is not None:
                    telemetry.record(
                        "corporate_action_apply_finished",
                        exchange=exchange,
                        outcome="error",
                        error_type=type(error).__name__,
                        duration_ms=(
                            time.monotonic_ns() - started
                        ) / 1_000_000,
                    )
                coordinator.pipeline.mark_many([
                    (
                        window.exchange,
                        window.segment,
                        target_date,
                        "actions",
                        "failed",
                        {"error": str(error)},
                    )
                    for window in related
                    for target_date in window.dates
                ])
                self.error_occurred.emit(
                    exchange, f"Corporate-action apply failed: {error}"
                )
            else:
                if telemetry is not None:
                    telemetry.record(
                        "corporate_action_apply_finished",
                        exchange=exchange,
                        outcome="success",
                        actions=len(actions),
                        applied=summary.get("applied", 0),
                        manual_review=summary.get("manual_review", 0),
                        duration_ms=(
                            time.monotonic_ns() - started
                        ) / 1_000_000,
                    )

    def _refresh_staged_task_results(
        self, settled: Dict[str, object]
    ) -> None:
        """Reclassify tasks after optional staged work reaches a terminal state."""

        coordinator = getattr(
            self.config, "history_batch_coordinator", None
        )
        if coordinator is None:
            return
        for name, downloader in self.downloaders.items():
            if isinstance(settled.get(name), BaseException):
                continue
            current = getattr(downloader, "last_segment_result", None)
            if current is None:
                continue
            dates = [item.target_date for item in current.dates]
            refreshed = coordinator.pipeline.segment_result(
                downloader.exchange, downloader.segment, dates
            )
            downloader.last_segment_result = refreshed
            settled[name] = downloader._core_pipeline_ok(refreshed)

    def _finalize_staged_outputs(self) -> None:
        """Finalize unresolved staged dates and refresh EQ structured results."""

        coordinator = getattr(self.config, "date_join_coordinator", None)
        if coordinator is None:
            return
        results = coordinator.finalize()
        for result in results:
            name = f"{result.exchange}_EQ"
            if result.ok:
                self.status_updated.emit(
                    name,
                    f"Staged combined publication: {result.rows} rows from "
                    f"{', '.join(result.components)}",
                )
            else:
                self.error_occurred.emit(
                    name,
                    f"Staged combined publication failed for "
                    f"{result.target_date}: {result.error}",
                )
        for exchange in ("NSE", "BSE"):
            name = f"{exchange}_EQ"
            downloader = self.downloaders.get(name)
            current = getattr(downloader, "last_segment_result", None)
            if downloader is None or current is None:
                continue
            dates = [item.target_date for item in current.dates]
            downloader.last_segment_result = coordinator.builder.pipeline.segment_result(
                exchange, "EQ", dates
            )

    def _classify_segment_outcome(
        self, exchange: str, result: object
    ) -> GUIOutcome:
        if self.is_cancel_requested() or isinstance(
            result, asyncio.CancelledError
        ):
            return GUIOutcome.CANCELLED
        if isinstance(result, BaseException):
            if "repair required" in str(result).lower():
                return GUIOutcome.REPAIR_REQUIRED
            return GUIOutcome.FAILED

        structured = getattr(
            self.downloaders[exchange], "last_segment_result", None
        )
        if getattr(self.downloaders[exchange], "no_work", False):
            return GUIOutcome.WARNING
        if structured is None:
            return GUIOutcome.SUCCESS if bool(result) else GUIOutcome.FAILED
        errors = " ".join(
            item.error or "" for item in structured.dates
        ).lower()
        if "repair required" in errors:
            return GUIOutcome.REPAIR_REQUIRED
        if structured.ok and bool(result):
            if structured.skipped_count == len(structured.dates):
                return GUIOutcome.WARNING
            return GUIOutcome.SUCCESS
        has_daily = any(
            "daily" in item.completed_stages for item in structured.dates
        )
        has_pending_delivery = any(
            "delivery" in item.failed_stages for item in structured.dates
        )
        if has_daily and has_pending_delivery:
            return GUIOutcome.PENDING
        if structured.any_success or structured.partial_count or has_daily:
            return GUIOutcome.PARTIAL
        return GUIOutcome.FAILED

    @staticmethod
    def _classify_overall_outcome(
        outcomes: List[GUIOutcome],
    ) -> GUIOutcome:
        if not outcomes:
            return GUIOutcome.FAILED
        if GUIOutcome.CANCELLED in outcomes:
            return GUIOutcome.CANCELLED
        if all(value == GUIOutcome.SUCCESS for value in outcomes):
            return GUIOutcome.SUCCESS
        if all(value in {GUIOutcome.SUCCESS, GUIOutcome.WARNING} for value in outcomes):
            return GUIOutcome.WARNING
        if GUIOutcome.REPAIR_REQUIRED in outcomes:
            return GUIOutcome.REPAIR_REQUIRED
        if all(value == GUIOutcome.PENDING for value in outcomes):
            return GUIOutcome.PENDING
        if any(value in {
            GUIOutcome.SUCCESS,
            GUIOutcome.WARNING,
            GUIOutcome.PENDING,
            GUIOutcome.PARTIAL,
        } for value in outcomes):
            return GUIOutcome.PARTIAL
        return GUIOutcome.FAILED

    async def _download_exchange_data(self, exchange: str, downloader) -> bool:
        """Download data for a specific exchange"""
        try:
            # Check if stop requested
            if self.is_cancel_requested():
                self.status_updated.emit(exchange, "Download stopped")
                return False

            self.status_updated.emit(exchange, "Starting download...")

            # Update downloader timeout to match current setting
            if hasattr(downloader, 'config'):
                downloader.config.download_settings.timeout_seconds = self.timeout_seconds

            forced_dates = self.retry_dates.get(exchange)
            if forced_dates is not None:
                working_days = forced_dates
                self.status_updated.emit(
                    exchange,
                    f"Retrying {len(working_days)} failed/pending date(s)",
                )
            else:
                start_date, end_date = downloader.get_date_range(
                    self.custom_start_date, self.custom_end_date
                )

                if self.is_cancel_requested():
                    self.status_updated.emit(exchange, "Download stopped")
                    return False

                working_days = []
                if start_date <= end_date:
                    working_days = downloader.get_working_days(
                        start_date, end_date, self.include_weekends
                    )
                working_days = downloader._with_pending_delivery_days(
                    working_days
                )
                if hasattr(downloader, "data_manager"):
                    gap_days = downloader.data_manager.get_missing_file_dates(
                        downloader.exchange, downloader.segment
                    )
                    working_days = sorted(set(working_days).union(gap_days))
                if hasattr(downloader, "_with_incomplete_pipeline_days"):
                    working_days = downloader._with_incomplete_pipeline_days(
                        working_days
                    )

            if not working_days:
                downloader.no_work = True
                self.status_updated.emit(exchange, "No working days in date range")
                return True

            # Update total files for progress tracking
            downloader.total_files = len(working_days)
            downloader.completed_files = 0
            downloader._progress_started_at = None

            # Start download with working days
            success = await downloader._download_implementation(working_days)

            result = getattr(downloader, "last_segment_result", None)
            detail = f" ({result.summary()})" if result is not None else ""
            waiting_for_finalization = bool(
                success
                and result is not None
                and not result.ok
                and all(not item.failed_stages for item in result.dates)
            )
            if waiting_for_finalization:
                self.status_updated.emit(
                    exchange,
                    f"Daily files ready; waiting for staged finalization{detail}",
                )
            elif success and (result is None or result.ok):
                self.status_updated.emit(
                    exchange, f"Segment data ready{detail}"
                )
            else:
                self.status_updated.emit(
                    exchange, f"Download completed with errors{detail}"
                )

            if waiting_for_finalization:
                return success
            return success and (result is None or result.ok)

        except asyncio.CancelledError:
            self.status_updated.emit(exchange, "Download cancelled safely")
            raise
        except Exception as e:
            self.error_occurred.emit(exchange, f"Download error: {e}")
            return False


class MainWindow(QMainWindow):
    """
    Main application window

    Provides GUI interface for NSE/BSE data downloader with:
    - Exchange selection checkboxes
    - Progress tracking
    - Status updates
    - Download management
    """

    def __init__(self, config: Config):
        super().__init__()

        self.config = config
        self.data_manager = DataManager(config)
        self.logger = logging.getLogger(__name__)

        # GUI components
        self.exchange_checkboxes: Dict[str, QCheckBox] = {}
        self.progress_bars: Dict[str, QProgressBar] = {}
        self.status_labels: Dict[str, QLabel] = {}
        self.weekend_checkbox: QCheckBox

        # Dynamic options (shown based on exchange selection)
        self.sme_suffix_checkbox: QCheckBox
        self.sme_append_checkbox: QCheckBox
        self.index_append_checkbox: QCheckBox
        self.bse_index_append_checkbox: QCheckBox
        self.delivery_checkbox: QCheckBox
        self.fo_oi_checkbox: QCheckBox
        self.symbol_files_checkbox: QCheckBox
        self.corporate_actions_checkbox: QCheckBox
        self.custom_date_checkbox: QCheckBox
        self.start_date_edit: QDateEdit
        self.end_date_edit: QDateEdit
        self.collapsible_sections: Dict[str, CollapsibleSection] = {}

        # Timeout option
        self.timeout_spinbox: QSpinBox

        # Update checker (debug mode disabled to test real update checking)
        # UpdateChecker will auto-detect version from version.py
        self.update_checker = UpdateChecker(debug=False)
        self.update_worker: Optional[UpdateCheckWorker] = None

        # User preferences
        self.settings = SettingsService(config)
        self.user_prefs = self.settings.preferences
        self.logger.info(f"User preferences loaded from: {self.user_prefs.get_config_file_path()}")

        # Download management
        self.download_worker: Optional[DownloadWorker] = None
        self.download_button: QPushButton
        self.stop_button: QPushButton
        self.retry_button: QPushButton
        self.donate_button: QPushButton

        # Status tracking
        self.download_status: Dict[str, str] = {}
        self.segment_outcomes: Dict[str, GUIOutcome] = {}
        self.successful_downloads: List[str] = []
        self.selected_exchanges_for_download: List[str] = []
        self._retry_candidates: Dict[str, List[str]] = {}
        self._retry_override: Optional[Dict[str, List[date]]] = None
        self._close_after_workers = False
        self._update_check_forced = False

        # Update throttling to prevent flickering
        self.last_update_time: Dict[str, float] = {}
        self.update_interval = 0.5  # Minimum 0.5 seconds between updates

        # Batch updates to reduce flickering
        self.pending_updates: Dict[str, tuple] = {}
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.process_pending_updates)
        self.update_timer.start(100)  # Process updates every 100ms

        # Initialize UI
        self.init_ui()
        self.load_data_summary()

        # Update dynamic options based on initial selection
        self.update_dynamic_options()

        # Keep delayed update startup owned by the window so an early close
        # can cancel it instead of starting a thread after teardown begins.
        self.update_check_timer = QTimer(self)
        self.update_check_timer.setSingleShot(True)
        self.update_check_timer.timeout.connect(self.check_for_updates)
        self.update_check_timer.start(3000)

        # Set up status update timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.start(1000)  # Update every second
        QTimer.singleShot(0, self._fit_window_to_sections)

    def init_ui(self):
        """Initialize user interface"""
        try:
            # Set window properties
            gui_settings = self.config.gui_settings
            self.setWindowTitle(gui_settings.window_title)

            # Load window size from user preferences
            width, height = self.user_prefs.get_window_size()
            self.logger.info(f"Loading window size from preferences: {width}x{height}")

            # Set window size constraints
            preference_gui_settings = self.user_prefs.get_gui_settings()
            min_width = preference_gui_settings.get('min_window_width', 500)
            max_width = preference_gui_settings.get('max_window_width', 1200)
            min_height = preference_gui_settings.get('min_window_height', 600)
            max_height = preference_gui_settings.get('max_window_height', 1400)

            self.setMinimumSize(min_width, min_height)
            self.setMaximumSize(max_width, max_height)
            self.setGeometry(100, 100, width, height)

            # Keep every section reachable on smaller displays even when all
            # disclosure panels are expanded.
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setFrameShape(QFrame.Shape.NoFrame)
            self.setCentralWidget(scroll_area)

            # Create central widget
            central_widget = QWidget()
            scroll_area.setWidget(central_widget)

            # Create main layout
            main_layout = QVBoxLayout(central_widget)

            # Create menu bar
            self.create_menu_bar()

            # Create exchange selection area
            exchange_group = self.create_exchange_selection()
            self._add_collapsible_section(
                main_layout, "exchanges", "Exchange Selection", exchange_group
            )

            # Create automatic/custom date range area
            date_group = self.create_date_selection_area()
            self._add_collapsible_section(
                main_layout, "date_range", "Date Range", date_group
            )

            # Create options area
            options_group = self.create_options_area()
            self._add_collapsible_section(
                main_layout, "options", "Download Options", options_group
            )

            # Create progress tracking area
            progress_group = self.create_progress_tracking()
            self._add_collapsible_section(
                main_layout, "progress", "Download Progress", progress_group
            )

            # Create control buttons
            button_layout = self.create_control_buttons()
            main_layout.addLayout(button_layout, 0)  # No stretch

            # Create status area (expandable)
            status_group = self.create_status_area()
            self._add_collapsible_section(
                main_layout, "status", "Status and Information", status_group
            )
            main_layout.addStretch(1)

            # Create status bar
            self.create_status_bar()

            self.logger.info("GUI initialized successfully")

        except Exception as e:
            raise GUIError(f"Failed to initialize GUI: {e}")

    def _add_collapsible_section(
        self,
        layout: QVBoxLayout,
        key: str,
        title: str,
        content: QWidget,
        stretch: int = 0,
    ) -> CollapsibleSection:
        """Wrap a main area in a persisted disclosure section."""
        expanded = self.user_prefs.get_section_states().get(key, True)
        section = CollapsibleSection(
            key,
            title,
            content,
            expanded,
            fill_available=stretch > 0,
            parent=self,
        )
        section.toggled.connect(self.on_section_toggled)
        self.collapsible_sections[key] = section
        layout.addWidget(section, stretch)
        return section

    def on_section_toggled(self, section: str, expanded: bool) -> None:
        """Remember which panels the user wants open."""
        self.user_prefs.set_section_state(section, expanded)
        QTimer.singleShot(0, self._fit_window_to_sections)

    def _fit_window_to_sections(self) -> None:
        """Fit utility-window height to visible panels within screen bounds."""

        if self.isMaximized() or self.isFullScreen():
            return
        scroll_area = self.centralWidget()
        if not isinstance(scroll_area, QScrollArea):
            return
        content = scroll_area.widget()
        layout = content.layout() if content is not None else None
        if layout is None:
            return
        layout.activate()
        chrome_height = (
            self.menuBar().sizeHint().height()
            + self.statusBar().sizeHint().height()
            + 12
        )
        target = layout.sizeHint().height() + chrome_height
        screen = self.screen()
        screen_limit = (
            int(screen.availableGeometry().height() * 0.92)
            if screen is not None
            else self.maximumHeight()
        )
        target = max(
            self.minimumHeight(),
            min(target, self.maximumHeight(), screen_limit),
        )
        if abs(self.height() - target) > 1:
            self.resize(self.width(), target)

    def expand_all_sections(self) -> None:
        for section in self.collapsible_sections.values():
            section.set_expanded(True)

    def collapse_all_sections(self) -> None:
        for section in self.collapsible_sections.values():
            section.set_expanded(False)

    def create_menu_bar(self):
        """Create application menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu('File')

        refresh_action = QAction('Refresh Data Summary', self)
        refresh_action.triggered.connect(self.load_data_summary)
        file_menu.addAction(refresh_action)

        file_menu.addSeparator()

        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu('View')
        expand_action = QAction('Expand All Sections', self)
        expand_action.triggered.connect(self.expand_all_sections)
        view_menu.addAction(expand_action)

        collapse_action = QAction('Collapse All Sections', self)
        collapse_action.triggered.connect(self.collapse_all_sections)
        view_menu.addAction(collapse_action)

        settings_menu = menubar.addMenu('Settings')
        auto_update_action = QAction('Check for Updates Automatically', self)
        auto_update_action.setCheckable(True)
        auto_update_action.setChecked(
            self.user_prefs.get_auto_check_updates()
        )
        auto_update_action.toggled.connect(
            self.user_prefs.set_auto_check_updates
        )
        settings_menu.addAction(auto_update_action)

        clear_skip_action = QAction('Reset Skipped Update Version', self)
        clear_skip_action.triggered.connect(
            lambda: self.user_prefs.set_skipped_update_version("")
        )
        settings_menu.addAction(clear_skip_action)

        # Help menu
        help_menu = menubar.addMenu('Help')

        check_update_action = QAction('Check for Updates', self)
        check_update_action.triggered.connect(
            lambda: self.check_for_updates(force=True)
        )
        help_menu.addAction(check_update_action)

        open_logs_action = QAction('Open Log Folder', self)
        open_logs_action.triggered.connect(self.open_log_folder)
        help_menu.addAction(open_logs_action)

        about_action = QAction('About', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def open_log_folder(self) -> None:
        """Open the folder holding the log files, for attaching to a report.

        A packaged application has no console, so this is the only way a user
        can hand over what the application recorded.  The folder is opened
        rather than the file itself, because the rotated copies next to it are
        usually the interesting ones after a crash.
        """

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from runtime_paths import log_directory

        folder = log_directory()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.warning(
                self,
                "Log Folder",
                f"The log folder could not be created:\n\n{folder}\n\n{error}",
            )
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(
                self, "Log Folder", f"Logs are written to:\n\n{folder}"
            )

    def create_exchange_selection(self) -> QGroupBox:
        """Create exchange selection area"""
        group = QGroupBox("Select Exchanges to Download")
        layout = QGridLayout(group)

        # Get available exchanges
        available_exchanges = self.config.get_available_exchanges()
        default_exchanges = self.config.gui_settings.default_exchanges

        row, col = 0, 0
        for exchange in available_exchanges:
            checkbox = QCheckBox(exchange.replace('_', ' '))

            # Set selection based on user preferences (fallback to config defaults)
            if self.user_prefs.is_exchange_selected(exchange):
                checkbox.setChecked(True)
            elif exchange in default_exchanges:
                checkbox.setChecked(True)

            # Connect to update dynamic options and save preferences
            checkbox.stateChanged.connect(self.on_exchange_selection_changed)

            self.exchange_checkboxes[exchange] = checkbox
            layout.addWidget(checkbox, row, col)

            col += 1
            if col >= 2:  # 2 columns
                col = 0
                row += 1

        return group

    def create_date_selection_area(self) -> QGroupBox:
        """Create automatic/custom calendar controls for the download range."""
        group = QGroupBox("Date Range")
        layout = QGridLayout(group)

        saved = self.user_prefs.get_date_selection()
        self.custom_date_checkbox = QCheckBox(
            "Use custom date range (otherwise download only new dates)"
        )
        self.custom_date_checkbox.setObjectName("useCustomDateRange")
        self.custom_date_checkbox.setChecked(
            bool(saved.get("use_custom_range", False))
        )
        layout.addWidget(self.custom_date_checkbox, 0, 0, 1, 4)

        # 1990 offered dates every one of these sources predates.  Where the
        # floors are known, the picker starts at the earliest of them instead.
        minimum = QDate(1990, 1, 1)
        floor = earliest_available(
            (value.split("_", 1)[0], value.split("_", 1)[1])
            for value in self.config.get_available_exchanges()
        )
        if floor is not None:
            minimum = QDate(floor.year, floor.month, floor.day)
        maximum = QDate.currentDate()
        default_start = QDate.fromString(
            self.config.date_settings.base_start_date, "yyyy-MM-dd"
        )
        saved_start = QDate.fromString(
            str(saved.get("start_date", "")), "yyyy-MM-dd"
        )
        saved_end = QDate.fromString(
            str(saved.get("end_date", "")), "yyyy-MM-dd"
        )
        if not saved_start.isValid():
            saved_start = default_start if default_start.isValid() else maximum.addDays(-7)
        if not saved_end.isValid():
            saved_end = maximum

        self.start_date_edit = QDateEdit(saved_start)
        self.start_date_edit.setObjectName("customStartDate")
        self.start_date_edit.setAccessibleName("Custom start date")
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDateRange(minimum, maximum)
        self.start_date_edit.setMinimumWidth(145)
        self.start_date_edit.setMaximumWidth(220)
        self.start_date_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self.end_date_edit = QDateEdit(saved_end)
        self.end_date_edit.setObjectName("customEndDate")
        self.end_date_edit.setAccessibleName("Custom end date")
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDateRange(minimum, maximum)
        self.end_date_edit.setMinimumWidth(145)
        self.end_date_edit.setMaximumWidth(220)
        self.end_date_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        layout.addWidget(QLabel("Start:"), 1, 0)
        layout.addWidget(self.start_date_edit, 1, 1)
        layout.addWidget(QLabel("End:"), 1, 2)
        layout.addWidget(self.end_date_edit, 1, 3)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnMinimumWidth(1, 145)
        layout.setColumnMinimumWidth(3, 145)

        self.date_mode_label = QLabel()
        self.date_mode_label.setStyleSheet("color: #666666;")
        layout.addWidget(self.date_mode_label, 2, 0, 1, 4)

        self.custom_date_checkbox.stateChanged.connect(
            self.on_date_selection_changed
        )
        self.start_date_edit.dateChanged.connect(self.on_date_selection_changed)
        self.end_date_edit.dateChanged.connect(self.on_date_selection_changed)
        self._update_date_controls()
        return group

    @staticmethod
    def _python_date(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())

    def get_selected_date_range(self):
        """Return custom Python dates, or ``(None, None)`` in auto mode."""
        if not self.custom_date_checkbox.isChecked():
            return None, None
        return (
            self._python_date(self.start_date_edit.date()),
            self._python_date(self.end_date_edit.date()),
        )

    def _update_date_controls(self) -> None:
        custom = self.custom_date_checkbox.isChecked()
        self.start_date_edit.setEnabled(custom)
        self.end_date_edit.setEnabled(custom)
        self.date_mode_label.setText(
            "Custom range will re-download and atomically update those dates."
            if custom
            else "Automatic mode continues from the last downloaded market date."
        )

    def on_date_selection_changed(self, *args) -> None:
        """Validate control state and persist the selected range."""
        self._update_date_controls()
        self.user_prefs.set_date_selection(
            self.custom_date_checkbox.isChecked(),
            self._python_date(self.start_date_edit.date()),
            self._python_date(self.end_date_edit.date()),
        )

    def create_options_area(self) -> QGroupBox:
        """Create download options area"""
        group = QGroupBox("Download Options")
        layout = QVBoxLayout(group)

        # Basic options row
        basic_row = QHBoxLayout()

        # Weekend download option
        self.weekend_checkbox = QCheckBox("Include Weekend Downloads")
        self.weekend_checkbox.setToolTip("Check this to attempt downloads on weekends (for rare cases when markets are open)")
        self.weekend_checkbox.setChecked(self.user_prefs.get_include_weekends())  # Load from preferences
        self.weekend_checkbox.stateChanged.connect(self.on_weekend_option_changed)
        basic_row.addWidget(self.weekend_checkbox)

        # Response timeout option
        timeout_label = QLabel("Response Timeout (sec):")
        self.timeout_spinbox = QSpinBox()
        self.timeout_spinbox.setMinimum(1)
        # This is the responsiveness dial, not the whole-transfer budget.  The
        # connect, read and attempt budgets live in config.yaml, so a large
        # report is no longer bounded by this value.
        self.timeout_spinbox.setMaximum(120)
        self.timeout_spinbox.setValue(self.user_prefs.get_timeout_seconds())  # Load from preferences
        self.timeout_spinbox.setToolTip(
            "How long to wait for a server to start responding, in seconds "
            "(default: 5). Large downloads are governed by the separate read "
            "and attempt budgets in config.yaml."
        )
        self.timeout_spinbox.valueChanged.connect(self.on_timeout_changed)

        basic_row.addWidget(timeout_label)
        basic_row.addWidget(self.timeout_spinbox)
        basic_row.addStretch()

        layout.addLayout(basic_row)

        data_options = self.user_prefs.get_data_options()
        extended_grid = QGridLayout()

        self.delivery_checkbox = QCheckBox("Include NSE/BSE delivery data")
        self.delivery_checkbox.setToolTip(
            "Merge delivery quantity and percentage into equity/SME output"
        )
        self.delivery_checkbox.setChecked(data_options["include_delivery_data"])
        self.delivery_checkbox.stateChanged.connect(self.on_data_option_changed)
        extended_grid.addWidget(self.delivery_checkbox, 0, 0)

        self.fo_oi_checkbox = QCheckBox("Include NSE FO open interest")
        self.fo_oi_checkbox.setToolTip(
            "Keep OPEN_INTEREST and CHANGE_IN_OI in futures output"
        )
        self.fo_oi_checkbox.setChecked(data_options["include_fo_open_interest"])
        self.fo_oi_checkbox.stateChanged.connect(self.on_data_option_changed)
        extended_grid.addWidget(self.fo_oi_checkbox, 0, 1)

        self.symbol_files_checkbox = QCheckBox("Create symbol-wise .txt histories")
        self.symbol_files_checkbox.setToolTip(
            "Create files such as NSE/SYMBOLS/reliance.txt"
        )
        self.symbol_files_checkbox.setChecked(data_options["generate_symbol_files"])
        self.symbol_files_checkbox.stateChanged.connect(self.on_data_option_changed)
        extended_grid.addWidget(self.symbol_files_checkbox, 1, 0)

        self.corporate_actions_checkbox = QCheckBox("Apply corporate actions")
        self.corporate_actions_checkbox.setToolTip(
            "Adjust pre-ex-date OHLC in symbol-wise history files"
        )
        self.corporate_actions_checkbox.setChecked(
            data_options["apply_corporate_actions"]
        )
        self.corporate_actions_checkbox.setEnabled(
            data_options["generate_symbol_files"]
        )
        self.corporate_actions_checkbox.stateChanged.connect(
            self.on_data_option_changed
        )
        extended_grid.addWidget(self.corporate_actions_checkbox, 1, 1)

        layout.addLayout(extended_grid)

        # Dynamic options for NSE SME (initially hidden)
        self.sme_options_row = QHBoxLayout()

        self.sme_suffix_checkbox = QCheckBox("Add '_SME' suffix to NSE SME symbol")
        self.sme_suffix_checkbox.setToolTip("Add '_SME' suffix to symbol names in NSE SME data")
        self.sme_suffix_checkbox.setChecked(self.user_prefs.get_sme_add_suffix())  # Load from preferences
        self.sme_suffix_checkbox.setVisible(False)
        self.sme_suffix_checkbox.stateChanged.connect(self.on_append_option_changed)
        self.sme_options_row.addWidget(self.sme_suffix_checkbox)

        self.sme_append_checkbox = QCheckBox("Append NSE SME data to NSE EQ file")
        self.sme_append_checkbox.setToolTip("Combine NSE SME data with NSE EQ data in single file")
        self.sme_append_checkbox.setChecked(self.user_prefs.get_sme_append_to_eq())  # Load from preferences
        self.sme_append_checkbox.setVisible(False)
        self.sme_append_checkbox.stateChanged.connect(self.on_append_option_changed)
        self.sme_options_row.addWidget(self.sme_append_checkbox)

        self.sme_options_row.addStretch()
        layout.addLayout(self.sme_options_row)

        # Dynamic options for NSE INDEX (initially hidden)
        self.index_options_row = QHBoxLayout()

        self.index_append_checkbox = QCheckBox("Add NSE Index data to NSE EQ file")
        self.index_append_checkbox.setToolTip("Append NSE Index data to NSE EQ files")
        self.index_append_checkbox.setChecked(self.user_prefs.get_index_append_to_eq())  # Load from preferences
        self.index_append_checkbox.setVisible(False)
        self.index_append_checkbox.stateChanged.connect(self.on_append_option_changed)
        self.index_options_row.addWidget(self.index_append_checkbox)

        self.index_options_row.addStretch()
        layout.addLayout(self.index_options_row)

        # Dynamic options for BSE INDEX (initially hidden)
        self.bse_index_options_row = QHBoxLayout()

        self.bse_index_append_checkbox = QCheckBox("Add BSE Index data to BSE EQ file")
        self.bse_index_append_checkbox.setToolTip("Append BSE Index data to BSE EQ files")
        self.bse_index_append_checkbox.setChecked(self.user_prefs.get_bse_index_append_to_eq())  # Load from preferences
        self.bse_index_append_checkbox.setVisible(False)
        self.bse_index_append_checkbox.stateChanged.connect(self.on_append_option_changed)
        self.bse_index_options_row.addWidget(self.bse_index_append_checkbox)

        self.bse_index_options_row.addStretch()
        layout.addLayout(self.bse_index_options_row)

        return group

    def update_dynamic_options(self):
        """Update visibility of dynamic options based on exchange selection"""
        # Append controls stay visible when EQ is selected so a persisted
        # dependency preference never becomes hidden and surprising.
        nse_eq_selected = self.exchange_checkboxes.get(
            'NSE_EQ', QCheckBox()
        ).isChecked()

        # Check if NSE SME is selected
        nse_sme_selected = self.exchange_checkboxes.get('NSE_SME', QCheckBox()).isChecked()

        # Show/hide NSE SME options
        self.sme_suffix_checkbox.setVisible(nse_sme_selected)
        self.sme_append_checkbox.setVisible(nse_sme_selected or nse_eq_selected)

        # Check if NSE INDEX is selected
        nse_index_selected = self.exchange_checkboxes.get('NSE_INDEX', QCheckBox()).isChecked()

        # Show/hide NSE INDEX options
        self.index_append_checkbox.setVisible(nse_index_selected or nse_eq_selected)

        # Check if BSE INDEX is selected
        bse_index_selected = self.exchange_checkboxes.get('BSE_INDEX', QCheckBox()).isChecked()

        # Show/hide BSE INDEX options
        bse_eq_selected = self.exchange_checkboxes.get(
            'BSE_EQ', QCheckBox()
        ).isChecked()
        self.bse_index_append_checkbox.setVisible(
            bse_index_selected or bse_eq_selected
        )

        # Update layout to accommodate changes
        self.update()

    def create_progress_tracking(self) -> QGroupBox:
        """Create progress tracking area"""
        group = QGroupBox("Download Progress")
        layout = QGridLayout(group)

        # Set fixed column widths to prevent layout changes
        layout.setColumnMinimumWidth(0, 100)  # Exchange name column
        layout.setColumnMinimumWidth(1, 200)  # Progress bar column
        layout.setColumnMinimumWidth(2, 300)  # Status text column
        layout.setColumnStretch(0, 0)  # Don't stretch exchange column
        layout.setColumnStretch(1, 0)  # Don't stretch progress column
        layout.setColumnStretch(2, 1)  # Allow status column to expand

        # Create progress bars and status labels for each exchange
        available_exchanges = self.config.get_available_exchanges()

        for i, exchange in enumerate(available_exchanges):
            # Exchange label with fixed width
            exchange_label = QLabel(exchange.replace('_', ' '))
            exchange_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            exchange_label.setMinimumWidth(100)  # Fixed width to prevent layout changes
            layout.addWidget(exchange_label, i, 0)

            # Progress bar with fixed size
            progress_bar = QProgressBar()
            progress_bar.setVisible(False)  # Hidden initially
            progress_bar.setMinimumWidth(200)  # Fixed width
            progress_bar.setMaximumHeight(20)   # Fixed height
            self.progress_bars[exchange] = progress_bar
            layout.addWidget(progress_bar, i, 1)

            # Status label with fixed width and alignment
            status_label = QLabel("Ready")
            status_label.setStyleSheet("color: gray;")
            status_label.setMinimumWidth(300)  # Fixed width to prevent text jumping
            status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)  # Left align
            self.status_labels[exchange] = status_label
            layout.addWidget(status_label, i, 2)

        return group

    def create_control_buttons(self) -> QHBoxLayout:
        """Create control buttons"""
        layout = QHBoxLayout()

        # Download button
        self.download_button = QPushButton("Start Download")
        self.download_button.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.download_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.download_button.clicked.connect(self.start_download)
        layout.addWidget(self.download_button)

        # Stop button
        self.stop_button = QPushButton("Stop Download")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_download)
        layout.addWidget(self.stop_button)

        self.retry_button = QPushButton("Retry Failed/Pending")
        self.retry_button.setEnabled(False)
        self.retry_button.setToolTip(
            "Retry only incomplete dates for the currently selected segments"
        )
        self.retry_button.clicked.connect(self.retry_incomplete)
        layout.addWidget(self.retry_button)

        # Refresh button
        refresh_button = QPushButton("Refresh Status")
        refresh_button.clicked.connect(self.load_data_summary)
        layout.addWidget(refresh_button)

        # Keep Donate beside the other actions so it stays visible in the
        # default window rather than being pushed beyond the viewport.
        self.donate_button = QPushButton("🤍 Donate")
        self.donate_button.setObjectName("donateButton")
        self.donate_button.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.donate_button.setStyleSheet("""
            QPushButton {
                background-color: #ff6b6b;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
                min-width: 120px;
            }
            QPushButton:hover {
                background-color: #ff5252;
            }
            QPushButton:pressed {
                background-color: #e53935;
            }
        """)
        self.donate_button.clicked.connect(self.show_donate_dialog)
        layout.addWidget(self.donate_button)
        layout.addStretch()

        return layout

    def create_status_area(self) -> QGroupBox:
        """Create status display area"""
        group = QGroupBox("Status & Information")
        layout = QVBoxLayout(group)

        # Status text area
        self.status_text = QTextEdit()
        self.status_text.setMinimumHeight(100)  # Minimum height
        # Remove maximum height to allow expansion
        self.status_text.setReadOnly(True)

        # Set size policy to expand vertically
        self.status_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.status_text.setStyleSheet("""
            QTextEdit {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                font-family: 'Courier New', monospace;
                font-size: 9pt;
            }
        """)
        layout.addWidget(self.status_text)

        return group

    def create_status_bar(self):
        """Create status bar"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def load_data_summary(self, clear_console: bool = True):
        """Load and display data summary"""
        try:
            summary = self.data_manager.get_data_summary()

            status_text = "Data Summary:\n"
            status_text += "=" * 50 + "\n"

            for exchange, info in summary.items():
                if 'error' in info:
                    status_text += f"{exchange}: ERROR - {info['error']}\n"
                else:
                    last_date = info['last_date'] or 'No data'
                    file_count = info['file_count']
                    is_first = "Yes" if info['is_first_run'] else "No"

                    status_text += f"{exchange}:\n"
                    status_text += f"  Last Date: {last_date}\n"
                    status_text += f"  File Count: {file_count}\n"
                    status_text += f"  First Run: {is_first}\n"
                    status_text += "\n"

            # Only clear console if explicitly requested
            if clear_console:
                self.status_text.setText(status_text)
            else:
                # Append data summary without clearing existing content
                self.append_status_message("\n" + status_text)

            self.status_bar.showMessage("Data summary loaded")
            self._show_history_revision_notice()

        except Exception as e:
            self.logger.error(f"Error loading data summary: {e}")
            self.status_text.setText(f"Error loading data summary: {e}")

    def _show_history_revision_notice(self) -> None:
        """Tell the user when existing symbol files predate the current rule.

        Adjusted bars are sticky: an audited action is never applied twice, so
        corrected arithmetic reaches old bars only through an explicit rebuild.
        """

        try:
            from ..services.history_revision import HistoryRevisionStore

            notice = HistoryRevisionStore(self.config.base_data_path).notice()
        except Exception as error:
            # A rebuild hint must never be the reason the window fails to open.
            self.logger.warning(f"History revision check skipped: {error}")
            return
        if notice:
            self.append_status_message(notice)

    def get_selected_exchanges(self) -> List[str]:
        """Get list of selected exchanges"""
        selected = []
        for exchange, checkbox in self.exchange_checkboxes.items():
            if checkbox.isChecked():
                selected.append(exchange)
        return selected

    def start_download(self):
        """Start download process"""
        try:
            retry_dates = self._retry_override
            self._retry_override = None
            selected_exchanges = (
                list(retry_dates) if retry_dates
                else self.get_selected_exchanges()
            )

            if not selected_exchanges:
                QMessageBox.warning(self, "Warning", "Please select at least one exchange to download.")
                return

            append_options = {
                "sme_append_to_eq": self.sme_append_checkbox.isChecked(),
                "index_append_to_eq": self.index_append_checkbox.isChecked(),
                "bse_index_append_to_eq": (
                    self.bse_index_append_checkbox.isChecked()
                ),
            }
            if retry_dates:
                retry_dates = DownloadWorker.expand_retry_dates(
                    retry_dates, append_options
                )
                selected_exchanges = list(retry_dates)
            selected_exchanges = DownloadWorker.expand_selected_exchanges(
                selected_exchanges, append_options
            )

            custom_start, custom_end = (
                (None, None) if retry_dates
                else self.get_selected_date_range()
            )
            if custom_start and custom_end and custom_start > custom_end:
                QMessageBox.warning(
                    self,
                    "Invalid Date Range",
                    "Start date cannot be after end date.",
                )
                return

            # Automatic mode can stop early when every selected database is
            # current.  Custom mode intentionally permits historical reruns.
            if custom_start is None and not retry_dates:
                data_manager = DataManager(self.config)
                all_up_to_date, status_message = (
                    data_manager.check_all_databases_status(selected_exchanges)
                )
                if all_up_to_date:
                    message = f"Database is Up-to-Date!\n\n{status_message}"
                    QMessageBox.information(self, "Database Status", message)
                    return

            # Store selected exchanges for completion message
            self.selected_exchanges_for_download = selected_exchanges.copy()
            self.successful_downloads = []
            self.segment_outcomes = {}

            # Disable download button and enable stop button
            self.download_button.setEnabled(False)
            self.download_button.setText("Downloading...")
            self.stop_button.setEnabled(True)
            self.retry_button.setEnabled(False)

            progress_section = self.collapsible_sections.get("progress")
            if progress_section:
                progress_section.set_expanded(True)

            # Show progress bars for selected exchanges with stable layout
            for exchange in selected_exchanges:
                if exchange in self.progress_bars:
                    # Set initial state without causing layout changes
                    progress_bar = self.progress_bars[exchange]
                    progress_bar.setValue(0)
                    progress_bar.setVisible(True)
                    progress_bar.setFormat("%p% - Preparing...")  # Fixed format

                if exchange in self.status_labels:
                    # Use fixed-width text to prevent jumping
                    self.status_labels[exchange].setText("  0% - Preparing...          ")
                    self.status_labels[exchange].setStyleSheet("color: blue;")

            # Get weekend option
            include_weekends = self.weekend_checkbox.isChecked() if self.weekend_checkbox else False

            # Get timeout option
            timeout_seconds = self.timeout_spinbox.value() if self.timeout_spinbox else 5

            # Create and start download worker
            self.download_worker = DownloadWorker(
                self.config,
                selected_exchanges,
                include_weekends,
                timeout_seconds,
                custom_start,
                custom_end,
                append_options,
                retry_dates,
            )

            # Connect signals
            self.download_worker.progress_updated.connect(self.update_progress)
            self.download_worker.status_updated.connect(self.update_status)
            self.download_worker.error_occurred.connect(self.handle_error)
            self.download_worker.segment_outcome.connect(
                self.handle_segment_outcome
            )
            self.download_worker.overall_outcome.connect(
                self.handle_overall_outcome
            )
            self.download_worker.retry_candidates_ready.connect(
                self.handle_retry_candidates
            )
            self.download_worker.finished.connect(self._maybe_finish_close)

            # Start worker thread
            self.download_worker.start()

            self.status_bar.showMessage("Download started...")
            range_message = (
                f"{sum(len(values) for values in retry_dates.values())} "
                "failed/pending date(s)"
                if retry_dates
                else f"custom range {custom_start} to {custom_end}"
                if custom_start
                else "automatic date range"
            )
            self.append_status_message(
                f"Download started for selected exchanges ({range_message})"
            )

        except Exception as e:
            self.logger.error(f"Error starting download: {e}")
            QMessageBox.critical(self, "Error", f"Failed to start download: {e}")
            self.reset_download_ui()

    def retry_incomplete(self) -> None:
        """Retry exact incomplete dates for user-selected failed segments."""

        selected = set(self.get_selected_exchanges())
        retry = {
            segment: [date.fromisoformat(value) for value in values]
            for segment, values in self._retry_candidates.items()
            if segment in selected
        }
        if not retry:
            QMessageBox.information(
                self,
                "Nothing Selected to Retry",
                "Select at least one segment that has failed or pending dates.",
            )
            return
        details = "\n".join(
            f"{segment}: {', '.join(value.isoformat() for value in values)}"
            for segment, values in retry.items()
        )
        answer = QMessageBox.question(
            self,
            "Retry Failed/Pending Dates",
            "Retry these exact dates?\n\n" + details,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._retry_override = retry
        self.start_download()

    def stop_download(self):
        """Stop download process gracefully"""
        if self.download_worker and self.download_worker.isRunning():
            try:
                self.download_worker.request_stop()
                self.append_status_message(
                    "Cancellation requested; waiting for the current atomic "
                    "operation to finish safely..."
                )

                # Disable stop button to prevent multiple clicks
                self.stop_button.setEnabled(False)
                self.stop_button.setText("Stopping...")

                QTimer.singleShot(5000, self._report_slow_safe_stop)

            except Exception as e:
                self.logger.error(f"Error stopping download: {e}")

    def _report_slow_safe_stop(self) -> None:
        if self.download_worker and self.download_worker.isRunning():
            self.append_status_message(
                "Still waiting for safe cancellation; no thread will be "
                "forcibly terminated."
            )

    def check_for_updates(self, force: bool = False):
        """Check for application updates in background"""
        try:
            if self._close_after_workers:
                return
            if not force and not self.user_prefs.get_auto_check_updates():
                self.logger.info("Automatic update checks are disabled")
                return
            if self.update_worker and self.update_worker.isRunning():
                self.logger.debug("Update check is already running")
                return
            self.logger.info("Checking for updates...")
            self._update_check_forced = force

            # Create update worker thread
            self.update_worker = UpdateCheckWorker(self.update_checker)
            self.update_worker.update_checked.connect(self.handle_update_result)
            self.update_worker.finished.connect(self._maybe_finish_close)
            self.update_worker.start()

        except Exception as e:
            self.logger.error(f"Error starting update check: {e}")

    def handle_update_result(self, result: dict):
        """Handle update check result"""
        try:
            if self._close_after_workers:
                return
            self.logger.info(f"🔍 DEBUG: Update check result received: {result}")

            if result.get("update_available", False):
                update_info = result.get("update_info")
                if update_info:
                    latest_version = update_info.get('latest_version', 'Unknown')
                    if (
                        not self._update_check_forced
                        and
                        latest_version
                        == self.user_prefs.get_skipped_update_version()
                    ):
                        self.logger.info(
                            "Skipping update notification for version %s",
                            latest_version,
                        )
                        return
                    self.logger.info(f"🔍 DEBUG: Update available - showing dialog for version {latest_version}")
                    self.show_update_dialog(update_info)
                else:
                    self.logger.warning("🔍 DEBUG: Update available but no update_info provided")
            else:
                error_msg = result.get("error", "No error specified")
                self.logger.info(f"🔍 DEBUG: No updates available. Error: {error_msg}")

        except Exception as e:
            self.logger.error(f"🔍 DEBUG: Error handling update result: {e}")
            import traceback
            self.logger.error(f"🔍 DEBUG: Traceback: {traceback.format_exc()}")
        finally:
            self._update_check_forced = False

    def show_update_dialog(self, update_info: dict):
        """Show update dialog to user"""
        try:
            dialog = UpdateDialog(
                update_info,
                self,
                self.update_checker,
                preferences=self.user_prefs,
            )
            dialog.exec()

        except Exception as e:
            self.logger.error(f"Error showing update dialog: {e}")
            # Fallback to simple message box
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Update Available",
                f"A new version is available: {update_info.get('latest_version', 'Unknown')}\n"
                f"Please visit GitHub to download the update."
            )

    def on_append_option_changed(self):
        """Handle append option checkbox changes"""
        try:
            # Save all append options to user preferences
            append_options = {
                "sme_add_suffix": self.sme_suffix_checkbox.isChecked(),
                "sme_append_to_eq": self.sme_append_checkbox.isChecked(),
                "index_append_to_eq": self.index_append_checkbox.isChecked(),
                "bse_index_append_to_eq": self.bse_index_append_checkbox.isChecked()
            }

            self.user_prefs.set_append_options(append_options)
            self.logger.info(f"Saved append options: {append_options}")

        except Exception as e:
            self.logger.error(f"Error saving append options: {e}")

    def on_data_option_changed(self):
        """Persist canonical output, delivery and symbol-history settings."""
        try:
            options = {
                "include_delivery_data": self.delivery_checkbox.isChecked(),
                "include_fo_open_interest": self.fo_oi_checkbox.isChecked(),
                "generate_symbol_files": self.symbol_files_checkbox.isChecked(),
                "apply_corporate_actions": self.corporate_actions_checkbox.isChecked(),
            }
            self.user_prefs.set_data_options(options)
            self.corporate_actions_checkbox.setEnabled(
                self.symbol_files_checkbox.isChecked()
            )
            self.logger.info(f"Saved extended data options: {options}")
        except Exception as e:
            self.logger.error(f"Error saving extended data options: {e}")

    def on_exchange_selection_changed(self):
        """Handle exchange selection changes"""
        try:
            # Get current selections
            selections = {}
            for exchange, checkbox in self.exchange_checkboxes.items():
                selections[exchange] = checkbox.isChecked()

            # Save to user preferences
            self.user_prefs.set_exchange_selection(selections)

            # Update dynamic options
            self.update_dynamic_options()

        except Exception as e:
            self.logger.error(f"Error handling exchange selection change: {e}")

    def on_weekend_option_changed(self):
        """Handle weekend option change"""
        try:
            include_weekends = self.weekend_checkbox.isChecked()
            self.user_prefs.set_include_weekends(include_weekends)
            self.logger.debug(f"Weekend option changed: {include_weekends}")
        except Exception as e:
            self.logger.error(f"Error handling weekend option change: {e}")

    def on_timeout_changed(self):
        """Handle timeout option change"""
        try:
            timeout = self.timeout_spinbox.value()
            self.user_prefs.set_timeout_seconds(timeout)
            self.logger.debug(f"Timeout changed: {timeout}")
        except Exception as e:
            self.logger.error(f"Error handling timeout change: {e}")

    def closeEvent(self, event):
        """Handle window close event"""
        try:
            download_worker = self.download_worker
            update_worker = self.update_worker
            download_running = bool(
                download_worker and download_worker.isRunning()
            )
            update_running = bool(
                update_worker and update_worker.isRunning()
            )
            if download_running and not self._close_after_workers:
                reply = QMessageBox.question(
                    self,
                    "Confirm Exit",
                    "Download is in progress. Are you sure you want to exit?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )

                if reply == QMessageBox.StandardButton.No:
                    event.ignore()
                    return

            self.update_check_timer.stop()
            self.update_timer.stop()
            self.status_timer.stop()

            if download_running or update_running:
                self._close_after_workers = True
                if download_running and download_worker is not None:
                    download_worker.request_stop()
                if update_running and update_worker is not None:
                    update_worker.request_stop()
                self.status_bar.showMessage(
                    "Closing after background work stops safely..."
                )
                event.ignore()
                QTimer.singleShot(0, self._maybe_finish_close)
                return

            self._save_exit_preferences()

        except Exception as e:
            self.logger.error(f"Error saving preferences on exit: {e}")

        # Accept the close event
        event.accept()

    def _save_exit_preferences(self) -> None:
        size = self.size()
        self.user_prefs.set_window_size(size.width(), size.height())
        self.user_prefs.set_download_options({
            "include_weekends": self.weekend_checkbox.isChecked(),
            "timeout_seconds": self.timeout_spinbox.value(),
        })
        self.logger.info(
            "Saved user preferences on exit - Window size: %sx%s",
            size.width(),
            size.height(),
        )

    def _maybe_finish_close(self) -> None:
        if not self._close_after_workers:
            return
        download_running = bool(
            self.download_worker and self.download_worker.isRunning()
        )
        update_running = bool(
            self.update_worker and self.update_worker.isRunning()
        )
        if not download_running and not update_running:
            self._close_after_workers = False
            QTimer.singleShot(0, self.close)

    def update_progress(self, exchange: str, percentage: int, message: str):
        """Update progress for specific exchange with batching"""
        # Add to pending updates for batched processing
        self.pending_updates[exchange] = ("progress", (percentage, message))

    def update_status(self, exchange: str, status: str):
        """Update status for specific exchange with batching"""
        self.download_status[exchange] = status

        # Add to pending updates for batched processing
        self.pending_updates[f"{exchange}_status"] = ("status", status)

        # Still append to status message immediately for logging
        self.append_status_message(f"[{exchange}] {status}")

    def handle_error(self, exchange: str, error: str):
        """Handle error for specific exchange"""
        if exchange in self.status_labels:
            self.status_labels[exchange].setText(f"Error: {error}")
            self.status_labels[exchange].setStyleSheet("color: red;")

        self.append_status_message(f"[{exchange}] ERROR: {error}")

    def handle_download_completed(self, exchange: str, success: bool):
        """Handle completion of download for specific exchange"""
        outcome = GUIOutcome.SUCCESS if success else GUIOutcome.FAILED
        self.handle_segment_outcome(exchange, outcome.value)

    def handle_segment_outcome(self, exchange: str, raw_outcome: str):
        """Render a typed segment outcome without inferring from log text."""

        outcome = GUIOutcome(raw_outcome)
        self.segment_outcomes[exchange] = outcome
        labels = {
            GUIOutcome.SUCCESS: ("Completed", "green"),
            GUIOutcome.PARTIAL: ("Partial", "#d97706"),
            GUIOutcome.PENDING: ("Pending", "#d97706"),
            GUIOutcome.WARNING: ("Warning", "#b45309"),
            GUIOutcome.REPAIR_REQUIRED: ("Repair required", "#7e22ce"),
            GUIOutcome.CANCELLED: ("Cancelled", "gray"),
            GUIOutcome.FAILED: ("Failed", "red"),
        }
        text, color = labels[outcome]
        if exchange in self.status_labels:
            self.status_labels[exchange].setText(text)
            self.status_labels[exchange].setStyleSheet(f"color: {color};")

        if outcome in {GUIOutcome.SUCCESS, GUIOutcome.WARNING}:
            if exchange in self.status_labels:
                self.status_labels[exchange].setText(text)
            if exchange in self.progress_bars:
                self.progress_bars[exchange].setValue(100)
            if exchange not in self.successful_downloads:
                self.successful_downloads.append(exchange)
        self.append_status_message(f"[{exchange}] Outcome: {outcome.value}")

    def handle_all_downloads_completed(self, overall_success: bool):
        """Handle completion of all downloads"""
        outcome = GUIOutcome.SUCCESS if overall_success else GUIOutcome.FAILED
        self.handle_overall_outcome(outcome.value)

    def handle_overall_outcome(self, raw_outcome: str):
        """Finish the run using the worker's typed aggregate outcome."""

        outcome = GUIOutcome(raw_outcome)
        self.reset_download_ui(preserve_status=True)

        if self._close_after_workers:
            return

        # Generate smart completion message
        data_manager = DataManager(self.config)
        completion_message = data_manager.get_download_completion_message(
            self.selected_exchanges_for_download,
            self.successful_downloads
        )
        attention_messages = {
            GUIOutcome.PENDING: (
                "Price data was saved, but one or more enabled reports are "
                "pending and will be retried."
            ),
            GUIOutcome.PARTIAL: (
                "Only part of the requested pipeline completed. Incomplete "
                "dates remain scheduled for repair."
            ),
            GUIOutcome.WARNING: (
                "The request completed with a warning, such as data not yet "
                "being published for the selected date."
            ),
            GUIOutcome.REPAIR_REQUIRED: (
                "Validated state is damaged or inconsistent. Existing data "
                "was preserved; run the documented repair command."
            ),
        }
        if outcome in attention_messages:
            completion_message = (
                attention_messages[outcome] + "\n\n" + completion_message
            )

        if outcome == GUIOutcome.SUCCESS:
            self.status_bar.showMessage("All downloads completed successfully")
            self.append_status_message("All downloads completed successfully")
            QMessageBox.information(self, "Download Complete", completion_message)
        elif outcome == GUIOutcome.CANCELLED:
            self.status_bar.showMessage("Download cancelled safely")
            self.append_status_message("Download cancelled safely")
        elif outcome in {
            GUIOutcome.PARTIAL, GUIOutcome.PENDING, GUIOutcome.WARNING
        }:
            self.status_bar.showMessage("Downloads completed with some errors")
            self.append_status_message(f"Download outcome: {outcome.value}")
            QMessageBox.warning(
                self, "Download Needs Attention", completion_message
            )
        elif outcome == GUIOutcome.REPAIR_REQUIRED:
            self.status_bar.showMessage("Data repair is required")
            self.append_status_message("Download stopped: data repair required")
            QMessageBox.critical(
                self, "Repair Required", completion_message
            )
        else:
            self.status_bar.showMessage("Downloads completed with errors")
            self.append_status_message("Downloads completed with errors")
            QMessageBox.critical(self, "Download Failed", completion_message)

        # Refresh data summary without clearing console
        self.load_data_summary(clear_console=False)

    def handle_retry_candidates(self, candidates: object) -> None:
        """Expose exact retryable dates without including terminal skips."""

        if not isinstance(candidates, dict):
            return
        self._retry_candidates = {
            str(segment): [str(value) for value in values]
            for segment, values in candidates.items()
            if isinstance(values, list)
        }
        self.retry_button.setEnabled(bool(self._retry_candidates))
        if self._retry_candidates:
            count = sum(len(values) for values in self._retry_candidates.values())
            self.append_status_message(
                f"{count} failed/pending date(s) are available for exact retry."
            )

    def reset_download_ui(self, preserve_status: bool = False):
        """Reset download UI to initial state"""
        self.download_button.setEnabled(True)
        self.download_button.setText("Start Download")
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stop Download")
        self.retry_button.setEnabled(bool(self._retry_candidates))

        # Reset progress bars and status labels to initial state
        for exchange, progress_bar in self.progress_bars.items():
            progress_bar.setVisible(False)
            progress_bar.setValue(0)

        if not preserve_status:
            for exchange, status_label in self.status_labels.items():
                status_label.setText("Ready")
                status_label.setStyleSheet("color: gray;")

        # Clear update throttling and pending updates
        self.last_update_time.clear()
        self.pending_updates.clear()

    def process_pending_updates(self):
        """Process batched updates to reduce flickering"""
        if not self.pending_updates:
            return

        # Process all pending updates at once
        for exchange, (update_type, data) in self.pending_updates.items():
            if update_type == "progress":
                percentage, message = data
                self._update_progress_immediate(exchange, percentage, message)
            elif update_type == "status":
                status = data
                self._update_status_immediate(exchange, status)

        # Clear processed updates
        self.pending_updates.clear()

    def _update_progress_immediate(self, exchange: str, percentage: int, message: str):
        """Immediate progress update without throttling"""
        if exchange in self.progress_bars:
            progress_bar = self.progress_bars[exchange]
            progress_bar.setValue(percentage)
            # Set fixed format to prevent size changes
            progress_bar.setFormat(f"%p% - {message[:20]:<20}")  # Truncate and pad message

        if exchange in self.status_labels:
            # Use fixed-width formatting to prevent text jumping
            truncated_message = message[:30] if len(message) > 30 else message
            status_text = f"{percentage:3d}% - {truncated_message:<30}"
            self.status_labels[exchange].setText(status_text)

    def _update_status_immediate(self, exchange: str, status: str):
        """Immediate status update without throttling"""
        if exchange in self.status_labels:
            # Use fixed width to prevent layout changes
            truncated_status = status[:40] if len(status) > 40 else status
            padded_status = f"{truncated_status:<40}"  # Left-align with padding
            self.status_labels[exchange].setText(padded_status)
            self.status_labels[exchange].setStyleSheet("color: blue;")

    def append_status_message(self, message: str):
        """Append message to status text area"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"

        self.status_text.append(formatted_message)

        # Auto-scroll to bottom
        cursor = self.status_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.status_text.setTextCursor(cursor)

    def update_status_display(self):
        """Update status display periodically"""
        # This can be used for periodic updates if needed
        pass

    def show_about(self):
        """Show about dialog with dynamic version info"""
        try:
            # Keep the About dialog aligned with version.py via UpdateChecker.
            app_info = self.config.get_app_settings()
            version = self.update_checker.get_current_version()
            features = app_info.get('features', [
                'NSE and BSE multi-segment downloads',
                'Smart append operations',
                'Automatic update notifications'
            ])
            release_date = app_info.get('release_date', '2026-07-31')

            features_text = '\n'.join([f"• {feature}" for feature in features])

            about_text = f"""
NSE/BSE Data Downloader v{version}

A professional data downloader for NSE and BSE market data.
Release Date: {release_date}

Key Features:
{features_text}

Developed with PySide6 and modern Python architecture.
Built for traders, analysts, and financial professionals.

© 2026 Paresh Patel. All rights reserved.
            """

            QMessageBox.about(self, f"About NSE/BSE Data Downloader v{version}", about_text.strip())

        except Exception as e:
            self.logger.error(f"Error showing about dialog: {e}")
            # Fallback about text
            # Deliberately version-free: this path runs when reading the
            # version failed, and a hardcoded number here goes stale silently.
            fallback_text = """
NSE/BSE Data Downloader

A comprehensive data downloader for NSE and BSE market data.
            """
            QMessageBox.about(self, "About NSE/BSE Data Downloader", fallback_text.strip())

    def show_donate_dialog(self):
        """Show donate dialog"""
        try:
            dialog = DonateDialog(self)
            dialog.exec()
            self.logger.info("Donate dialog shown")
        except Exception as e:
            self.logger.error(f"Error showing donate dialog: {e}")
            QMessageBox.warning(
                self,
                "Error",
                f"Could not open donate dialog: {str(e)}",
                QMessageBox.StandardButton.Ok
            )
