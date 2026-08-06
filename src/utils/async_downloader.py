"""
Async Download Manager for NSE/BSE Data Downloader

Provides concurrent download capabilities with:
- Rate limiting and retry mechanisms
- Progress tracking
- Error handling and recovery
- Memory-efficient streaming downloads
"""

import asyncio
import aiohttp
from io import BytesIO
import logging
import random
import re
import ssl
import zipfile
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Callable, Dict, Any
from dataclasses import dataclass
import time

from ..core.config import Config
from ..core.exceptions import CircuitOpenError, NetworkError
from ..services.pipeline_telemetry import EventLoopLagMonitor, PipelineTelemetry
from .transport_pool import TransportPool


@dataclass
class DownloadTask:
    """Represents a single download task"""
    url: str
    date_str: str
    target_date: Any  # date object
    retry_count: int = 0
    exchange_segment: str = ""

    def __str__(self) -> str:
        return f"DownloadTask(url={self.url}, date={self.date_str})"


@dataclass
class DownloadResult:
    """Result of a download operation"""
    task: DownloadTask
    success: bool
    file_data: Optional[bytes] = None
    file_size: int = 0
    error_message: Optional[str] = None
    download_time: float = 0.0
    status_code: Optional[int] = None
    retry_after: Optional[float] = None
    outcome: str = "success"

    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        return f"DownloadResult({status}, {self.file_size} bytes, {self.download_time:.2f}s)"


class AsyncDownloadManager:
    """
    Manages concurrent downloads with rate limiting and retry logic

    Features:
    - Concurrent downloads with semaphore control
    - Automatic retry with exponential backoff
    - Progress tracking and callbacks
    - Memory-efficient streaming downloads
    - Rate limiting to avoid server overload
    """

    def __init__(self, config: Config):
        """
        Initialize async download manager

        Args:
            config: Configuration object
        """
        self.config = config
        self.download_settings = config.download_settings
        self.logger = logging.getLogger(__name__)

        # Concurrency control
        self.semaphore = asyncio.Semaphore(self.download_settings.max_concurrent_downloads)

        # Session management
        self.session: Optional[aiohttp.ClientSession] = None

        # Progress tracking
        self.progress_callback: Optional[Callable[[int, int, str], None]] = None
        self.completed_downloads = 0
        self.total_downloads = 0

        # Statistics
        self.download_stats = {
            'total_files': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'total_bytes': 0,
            'total_time': 0.0,
            'retry_count': 0
        }
        self.telemetry = getattr(config, "pipeline_telemetry", None) or PipelineTelemetry()
        self._lag_monitor = EventLoopLagMonitor(self.telemetry)
        shared_pool = getattr(config, "transport_pool", None)
        self._owns_transport_pool = shared_pool is None
        self.transport_pool: TransportPool = shared_pool or TransportPool(config)

    async def __aenter__(self):
        """Async context manager entry"""
        if self._owns_transport_pool:
            await self.transport_pool.start()
        self.session = self.transport_pool.session
        await self._lag_monitor.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self._lag_monitor.stop()
        if self._owns_transport_pool:
            await self.transport_pool.close()

    async def _create_session(self) -> None:
        """Create aiohttp session with appropriate settings"""
        await self.transport_pool.start()
        self.session = self.transport_pool.session

    async def update_session_timeout(self, new_timeout_seconds: int) -> None:
        """
        Update session timeout by recreating the session

        Args:
            new_timeout_seconds: New timeout value in seconds
        """
        if new_timeout_seconds != self.download_settings.timeout_seconds:
            self.logger.info(f"Updating session timeout from {self.download_settings.timeout_seconds}s to {new_timeout_seconds}s")

            # Update the download settings
            self.download_settings.timeout_seconds = new_timeout_seconds

            # Close existing session if it exists
            if self.session:
                await self._close_session()

            # Create new session with updated timeout
            await self._create_session()

            self.logger.info(f"Session timeout updated successfully to {new_timeout_seconds}s")

    async def _close_session(self) -> None:
        """Close aiohttp session"""
        if not self._owns_transport_pool:
            self.session = self.transport_pool.session
            return
        await self.transport_pool.close()
        self.session = None
        self.logger.info("Async session closed")

    def set_progress_callback(self, callback: Callable[[int, int, str], None]) -> None:
        """
        Set progress callback function

        Args:
            callback: Function that receives (completed, total, message)
        """
        self.progress_callback = callback

    def _update_progress(self, message: str = "") -> None:
        """Update progress if callback is set"""
        if self.progress_callback:
            self.progress_callback(self.completed_downloads, self.total_downloads, message)

    def _should_retry_error(self, error_message: str) -> bool:
        """
        Determine if an error should trigger a retry

        Args:
            error_message: Error message to analyze

        Returns:
            True if should retry, False otherwise
        """
        if not error_message:
            return False

        error_lower = error_message.lower()

        # Retry for network/connection issues
        retry_conditions = [
            "connection" in error_lower,
            "network" in error_lower,
            "timeout" in error_lower,
            "temporary" in error_lower,
            "503" in error_lower,  # Service Unavailable
            "502" in error_lower,  # Bad Gateway
            "500" in error_lower,  # Internal Server Error
            "reset" in error_lower,
            "refused" in error_lower
        ]

        # Don't retry for these conditions
        no_retry_conditions = [
            "404" in error_lower,  # Not Found - file doesn't exist
            "403" in error_lower,  # Forbidden - access denied
            "401" in error_lower,  # Unauthorized
            "not available" in error_lower,
            "file not found" in error_lower
        ]

        # Check no-retry conditions first
        if any(no_retry_conditions):
            return False

        # Check retry conditions
        return any(retry_conditions)

    def _calculate_delay(self, task: DownloadTask) -> float:
        """
        Calculate consistent delay for rate limiting

        Args:
            task: Download task to analyze

        Returns:
            Delay in seconds
        """
        # Use consistent rate limit delay for all servers
        return self.download_settings.rate_limit_delay

    def _get_timeout(self, task: DownloadTask) -> int:
        """
        Get consistent timeout for all servers

        Args:
            task: Download task to analyze

        Returns:
            Timeout in seconds (user-configured value)
        """
        # Use user-configured timeout for all servers consistently
        return self.download_settings.timeout_seconds

    def _get_timeout_budget(self) -> aiohttp.ClientTimeout:
        """Return split Phase 7.1 budgets with legacy-compatible defaults."""
        total = float(
            getattr(self.download_settings, "attempt_timeout_seconds", None)
            or self.download_settings.timeout_seconds
        )
        connect = float(
            getattr(self.download_settings, "connect_timeout_seconds", None)
            or total
        )
        read = float(
            getattr(self.download_settings, "read_timeout_seconds", None)
            or total
        )
        return aiohttp.ClientTimeout(total=total, connect=connect, sock_read=read)

    def _get_retry_attempts(self, task: DownloadTask) -> int:
        """
        Get consistent retry attempts for all servers

        Args:
            task: Download task to analyze

        Returns:
            Maximum retry attempts (user-configured value)
        """
        # Use consistent retry attempts for all servers
        return self.download_settings.retry_attempts

    def _get_retry_delay(
        self,
        task: DownloadTask,
        attempt: int,
        retry_after: Optional[float] = None,
    ) -> float:
        """
        Get consistent retry delay for all servers

        Args:
            task: Download task to analyze
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds
        """
        if retry_after is not None and retry_after >= 0:
            return min(60.0, retry_after)
        # Bounded exponential backoff with a small jitter prevents every
        # selected segment from retrying the exchange at the same instant.
        return min(8.0, float(2 ** attempt)) + random.uniform(0.0, 0.25)

    #: Matches an absolute URL anywhere in an error message.  Messages carry the
    #: failing URL, and a date inside one ("..._20240404_...") would otherwise be
    #: substring-matched as an HTTP status by the checks further down.
    _URL_IN_MESSAGE = re.compile(r"https?://\S+", re.I)

    def _classify_by_exception(
        self, exception: BaseException, task: DownloadTask
    ) -> Optional[dict]:
        """Classify by exception type, which wording cannot defeat.

        Substring matching gets one case badly wrong:
        ``aiohttp.ClientConnectorError.__str__`` is
        ``"Cannot connect to host {host}:{port} ssl:{...} [{...}]"``, so a plain
        connection-refused message contains ``"ssl"`` and was classified as a
        terminal certificate failure.  The user was told their server was
        misconfigured when their network had blipped, and the retry that would
        have succeeded never ran.

        Returns ``None`` when the type is not decisive, leaving the caller to
        fall back to message inspection.
        """

        # Order matters: aiohttp's SSL errors subclass ClientConnectorError.
        if isinstance(exception, CircuitOpenError):
            return {
                "type": "circuit_open",
                "user_message": (
                    f"Skipped {task.date_str}: too many recent failures for this "
                    "host, pausing before further requests"
                ),
                "should_retry": False,
                "technical_details": str(exception),
            }

        if isinstance(exception, (ssl.SSLError, aiohttp.ClientSSLError)):
            return {
                "type": "ssl_error",
                "user_message": (
                    f"SSL certificate issue for {task.date_str} - server "
                    "configuration problem"
                ),
                "should_retry": False,
                "technical_details": str(exception),
            }

        if isinstance(
            exception, (asyncio.TimeoutError, aiohttp.ServerTimeoutError)
        ):
            return {
                "type": "timeout",
                "user_message": (
                    f"Server response timeout for {task.date_str}: {exception}"
                ),
                "should_retry": True,
                "technical_details": str(exception),
            }

        if isinstance(exception, (
            aiohttp.ClientConnectorError,
            aiohttp.ClientOSError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientPayloadError,
            ConnectionError,
        )):
            return {
                "type": "network",
                "user_message": (
                    f"Network connectivity issue for {task.date_str} - will retry"
                ),
                "should_retry": True,
                "technical_details": str(exception),
            }

        return None

    def _classify_error(
        self,
        error_message: str,
        task: DownloadTask,
        status_code: Optional[int] = None,
        exception: Optional[BaseException] = None,
    ) -> dict:
        """
        Classify error for better user feedback

        Args:
            error_message: Error message to classify
            task: Download task that failed
            status_code: HTTP status code, when the server answered
            exception: The raised exception, when there was one.  Preferred over
                the message, which is ambiguous.

        Returns:
            Dictionary with error classification
        """
        if exception is not None:
            classified = self._classify_by_exception(exception, task)
            if classified is not None:
                return classified

        if not error_message:
            return {"type": "unknown", "user_message": "Unknown error occurred", "should_retry": False}

        # Strip URLs before any substring matching; see _URL_IN_MESSAGE.
        error_lower = self._URL_IN_MESSAGE.sub(" ", error_message).lower()

        # Timeout errors
        if "timeout" in error_lower:
            return {
                "type": "timeout",
                "user_message": (
                    f"Server response timeout for {task.date_str}: {error_message}"
                ),
                "should_retry": True,
                "technical_details": error_message
            }

        if any(term in error_lower for term in (
            "html", "empty zip", "empty report", "crc", "truncated", "mime"
        )):
            return {
                "type": "retryable_payload",
                "user_message": f"Transient report payload for {task.date_str}: {error_message}",
                "should_retry": True,
                "technical_details": error_message,
            }

        # Certificate failures are security failures, not transient downloads.
        if any(term in error_lower for term in ["ssl", "certificate", "cert"]):
            return {
                "type": "ssl_error",
                "user_message": f"SSL certificate issue for {task.date_str} - server configuration problem",
                "should_retry": False,
                "technical_details": error_message
            }

        # Network connectivity issues
        if any(term in error_lower for term in [
            "connect", "connection", "network", "reset", "refused",
            "disconnected", "dns", "name resolution",
        ]):
            return {
                "type": "network",
                "user_message": f"Network connectivity issue for {task.date_str} - will retry",
                "should_retry": True,
                "technical_details": error_message
            }

        # Server errors (5xx)
        if status_code in {408, 425, 429} or any(
            code in error_lower for code in ["408", "425", "429"]
        ):
            return {
                "type": "server_busy",
                "user_message": (
                    f"Server asked to retry {task.date_str}: {error_message}"
                ),
                "should_retry": True,
                "technical_details": error_message,
            }

        if (
            status_code is not None and 500 <= status_code <= 599
        ) or any(code in error_lower for code in ["500", "502", "503", "504"]):
            return {
                "type": "server_error",
                "user_message": f"Server error for {task.date_str} - server may be temporarily unavailable",
                "should_retry": True,
                "technical_details": error_message
            }

        # File not found (404)
        if status_code == 404 or "404" in error_lower or "not found" in error_lower:
            return {
                "type": "file_not_found",
                "user_message": f"File not available for {task.date_str} - may not be published yet",
                "should_retry": False,
                "technical_details": error_message
            }

        if status_code == 401 or "401" in error_lower or "unauthorized" in error_lower:
            return {
                "type": "unauthorized",
                "user_message": f"Unauthorized request for {task.date_str}",
                "should_retry": False,
                "technical_details": error_message,
            }

        # One controlled retry is allowed for a transient access block.
        if status_code == 403 or "403" in error_lower or "forbidden" in error_lower:
            return {
                "type": "access_denied",
                "user_message": f"Access denied for {task.date_str} - server may be blocking requests",
                "should_retry": True,
                "technical_details": error_message
            }

        # Default classification
        return {
            "type": "unknown",
            "user_message": f"Download failed for {task.date_str} - {error_message}",
            "should_retry": False,
            "technical_details": error_message
        }

    async def download_file(self, task: DownloadTask) -> DownloadResult:
        """
        Download a single file with retry logic

        Args:
            task: Download task to execute

        Returns:
            DownloadResult with success status and details
        """
        start_time = time.time()

        async with self.semaphore:  # Limit concurrent downloads
            try:
                # Simple retry logic for all servers
                max_attempts = max(1, self._get_retry_attempts(task))
                last_error = None
                identity = (
                    {"exchange_segment": task.exchange_segment}
                    if task.exchange_segment else {}
                )

                for attempt in range(max_attempts):
                    attempt_started = time.monotonic_ns()
                    self.telemetry.record(
                        "download_attempt_started",
                        date=task.date_str,
                        url=task.url,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        **identity,
                    )
                    try:
                        # Only a real request outcome may feed the breaker.  A
                        # rejection by an already-open circuit sent nothing, so
                        # recording it would re-arm the cooldown that produced
                        # it and the circuit could never close.
                        async with self.transport_pool.slot(task.url):
                            try:
                                result = await self._attempt_download(task)
                            except Exception:
                                self.transport_pool.record(
                                    task.url, success=False
                                )
                                raise
                        self.transport_pool.record(
                            task.url,
                            success=result.success,
                            status_code=result.status_code,
                        )
                        self.telemetry.record(
                            "download_attempt_finished",
                            date=task.date_str,
                            url=task.url,
                            attempt=attempt + 1,
                            status_code=result.status_code,
                            success=result.success,
                            bytes=result.file_size,
                            duration_ms=(time.monotonic_ns() - attempt_started) / 1_000_000,
                            **identity,
                        )
                        if result.success:
                            self.download_stats['successful_downloads'] += 1
                            self.download_stats['total_bytes'] += result.file_size
                            if attempt > 0:
                                self.logger.info(f"✅ Success on retry {attempt + 1} for {task.date_str}")
                            return result
                        else:
                            # If download failed but no exception, classify error and decide retry
                            last_error = result.error_message
                            error_info = self._classify_error(
                                result.error_message or "Unknown error",
                                task,
                                result.status_code,
                            )

                            retry_limit = 2 if error_info["type"] == "access_denied" else max_attempts
                            if error_info["should_retry"] and attempt < min(max_attempts, retry_limit) - 1:
                                wait_time = self._get_retry_delay(
                                    task, attempt, result.retry_after
                                )
                                self.telemetry.record(
                                    "retry_scheduled",
                                    date=task.date_str,
                                    url=task.url,
                                    attempt=attempt + 1,
                                    next_attempt=attempt + 2,
                                    reason=error_info["type"],
                                    delay_seconds=wait_time,
                                    max_attempts=max_attempts,
                                    **identity,
                                )
                                self.logger.info(f"🔄 {error_info['type'].title()} retry {task.date_str} in {wait_time}s (attempt {attempt + 2}/{max_attempts})")
                                await asyncio.sleep(wait_time)
                                self.download_stats['retry_count'] += 1
                                continue
                            else:
                                # Don't retry for this type of error or max attempts reached
                                if not error_info["should_retry"]:
                                    self.logger.info(f"❌ {error_info['type'].title()}: {error_info['user_message']}")
                                break

                    except asyncio.CancelledError:
                        raise

                    except asyncio.TimeoutError:
                        self.telemetry.record(
                            "download_attempt_finished",
                            date=task.date_str,
                            url=task.url,
                            attempt=attempt + 1,
                            success=False,
                            error_type="timeout",
                            duration_ms=(time.monotonic_ns() - attempt_started) / 1_000_000,
                            **identity,
                        )
                        timeout_value = self._get_timeout(task)
                        last_error = f"Server timeout after {timeout_value}s"
                        if attempt < max_attempts - 1:
                            wait_time = self._get_retry_delay(task, attempt)
                            self.telemetry.record(
                                "retry_scheduled",
                                date=task.date_str,
                                url=task.url,
                                attempt=attempt + 1,
                                next_attempt=attempt + 2,
                                reason="timeout",
                                delay_seconds=wait_time,
                                max_attempts=max_attempts,
                                **identity,
                            )
                            self.logger.info(f"⏱️ Timeout retry {task.date_str} in {wait_time}s (attempt {attempt + 2}/{max_attempts})")
                            await asyncio.sleep(wait_time)
                            self.download_stats['retry_count'] += 1
                            continue
                        else:
                            last_error = f"Server timeout after {timeout_value}s (all {max_attempts} attempts failed)"
                            break

                    except Exception as e:
                        self.telemetry.record(
                            "download_attempt_finished",
                            date=task.date_str,
                            url=task.url,
                            attempt=attempt + 1,
                            success=False,
                            error_type=type(e).__name__,
                            duration_ms=(time.monotonic_ns() - attempt_started) / 1_000_000,
                            **identity,
                        )
                        last_error = f"Download error: {e}"
                        error_info = self._classify_error(
                            str(e), task, exception=e
                        )

                        retry_limit = 2 if error_info["type"] == "access_denied" else max_attempts
                        if error_info["should_retry"] and attempt < min(max_attempts, retry_limit) - 1:
                            wait_time = self._get_retry_delay(task, attempt)
                            self.telemetry.record(
                                "retry_scheduled",
                                date=task.date_str,
                                url=task.url,
                                attempt=attempt + 1,
                                next_attempt=attempt + 2,
                                reason=error_info["type"],
                                delay_seconds=wait_time,
                                max_attempts=max_attempts,
                                **identity,
                            )
                            self.logger.info(f"🔄 {error_info['type'].title()} retry {task.date_str} in {wait_time}s (attempt {attempt + 2}/{max_attempts}): {error_info['user_message']}")
                            await asyncio.sleep(wait_time)
                            self.download_stats['retry_count'] += 1
                            continue
                        else:
                            self.logger.error(f"❌ {error_info['type'].title()}: {error_info['user_message']}")
                            break

                # All attempts failed - provide classified error message
                self.download_stats['failed_downloads'] += 1
                final_error_info = self._classify_error(last_error or "Unknown error", task)

                return DownloadResult(
                    task=task,
                    success=False,
                    error_message=final_error_info["user_message"],
                    download_time=time.time() - start_time,
                    outcome=final_error_info["type"],
                )



            finally:
                self.completed_downloads += 1
                self._update_progress(f"Downloaded {task.date_str}")

    async def _attempt_download(self, task: DownloadTask) -> DownloadResult:
        """
        Single download attempt

        Args:
            task: Download task

        Returns:
            DownloadResult
        """
        start_time = time.time()

        try:
            if not self.session:
                raise NetworkError("Session not initialized")

            # Debug for BSE requests
            is_bse_request = "bseindia.com" in task.url
            is_bse_index = is_bse_request and "INDEXSummary" in task.url
            is_bse_eq = is_bse_request and "BhavCopy_BSE_CM" in task.url

            # Get timeout for this request
            timeout_value = self._get_timeout(task)

            if is_bse_request:
                request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                self.logger.info(f"🔍 {request_type} HTTP Request Debug:")
                self.logger.info(f"  URL: {task.url}")
                self.logger.info(f"  Timeout: {timeout_value}s")
                self.logger.info("  SSL Verification: Enabled")

            request_timeout = self._get_timeout_budget()
            async with self.session.get(
                task.url, timeout=request_timeout
            ) as response:
                if is_bse_request:
                    request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                    self.logger.info(f"  {request_type} Response Status: {response.status}")
                    self.logger.info(f"  {request_type} Response Reason: {response.reason}")
                    if response.status != 200:
                        self.logger.info(f"  {request_type} Response Headers: {dict(response.headers)}")

                # Check response status
                if response.status != 200:
                    if is_bse_request:
                        request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                        self.logger.error(f"❌ {request_type} HTTP Error: {response.status} - {response.reason}")
                    retry_after = self._parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                    return DownloadResult(
                        task=task,
                        success=False,
                        error_message=f"HTTP {response.status}: {response.reason}",
                        status_code=response.status,
                        retry_after=retry_after,
                        download_time=time.time() - start_time,
                        outcome=("retryable_transport" if response.status in {408, 425, 429} or response.status >= 500 else "access_blocked" if response.status in {401, 403} else "terminal_not_available" if response.status == 404 else "validation_failed"),
                    )

                # Download to memory
                file_data = bytearray()
                file_size = 0
                async for chunk in response.content.iter_chunked(self.download_settings.chunk_size):
                    file_data.extend(chunk)
                    file_size += len(chunk)

                # BSE frequently returns a branded HTML error page with HTTP
                # 200 for a missing report.  Treat it as a failed download so
                # parsers never mistake that page for a valid CSV.
                preview = bytes(file_data).lstrip()[:100].lower()
                if preview.startswith((b"<!doctype html", b"<html")):
                    raise NetworkError(
                        "Server returned HTML instead of the requested report",
                        url=task.url,
                        status_code=200,
                    )
                await asyncio.to_thread(
                    self._validate_payload_envelope, bytes(file_data), task.url
                )

                download_time = time.time() - start_time

                if is_bse_request:
                    request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                    self.logger.info(f"  ✅ {request_type} Download Success:")
                    self.logger.info(f"    File Size: {file_size} bytes")
                    self.logger.info(f"    Download Time: {download_time:.2f}s")
                    # Do not decode/log ZIP bytes.  A text preview is useful only
                    # at DEBUG level and only for a non-archive response.
                    if bytes(file_data[:2]) != b"PK":
                        preview_text = bytes(file_data[:100]).decode(
                            'utf-8', errors='replace'
                        )
                        self.logger.debug(f"    Content Preview: {preview_text!r}")

                self.logger.info(f"Downloaded {task.date_str} ({file_size} bytes, {download_time:.2f}s)")

                return DownloadResult(
                    task=task,
                    success=True,
                    file_data=bytes(file_data),
                    file_size=file_size,
                    download_time=download_time
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            # Preserve the concrete failure for the outer retry state machine.
            raise

    @staticmethod
    def _validate_payload_envelope(payload: bytes, url: str) -> None:
        """Reject retryable empty, truncated, or CRC-invalid ZIP payloads."""
        if not payload:
            raise NetworkError("Server returned an empty report payload", url=url)
        if not payload.startswith(b"PK"):
            return
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if not members or all(item.file_size == 0 for item in members):
                    raise NetworkError("Server returned an empty ZIP report", url=url)
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise NetworkError(
                        f"ZIP CRC failure in {bad_member}", url=url
                    )
        except NetworkError:
            raise
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as error:
            raise NetworkError(
                f"Server returned a truncated or CRC-invalid ZIP report: {error}",
                url=url,
            ) from error

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[float]:
        """Parse Retry-After delta seconds or an RFC-compliant HTTP date."""

        if value is None:
            return None
        try:
            return max(0.0, float(value.strip()))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                return None

    async def download_multiple(self, tasks: List[DownloadTask]) -> List[DownloadResult]:
        """
        Download multiple files concurrently

        Args:
            tasks: List of download tasks

        Returns:
            List of download results
        """
        if not tasks:
            return []

        self.total_downloads = len(tasks)
        self.completed_downloads = 0
        self.download_stats['total_files'] = len(tasks)

        start_time = time.time()

        self.logger.info(f"Starting concurrent download of {len(tasks)} files")
        self._update_progress("Starting downloads...")
        await self._lag_monitor.start()

        try:
            # Create download coroutines
            download_coroutines = [self.download_file(task) for task in tasks]

            # Execute downloads concurrently
            results = await asyncio.gather(*download_coroutines, return_exceptions=True)

            # Process results and handle exceptions
            processed_results: List[DownloadResult] = []
            for i, result in enumerate(results):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    # Handle exceptions that weren't caught in download_file
                    error_result = DownloadResult(
                        task=tasks[i],
                        success=False,
                        error_message=f"Unexpected error: {result}"
                    )
                    processed_results.append(error_result)
                    self.download_stats['failed_downloads'] += 1
                else:
                    processed_results.append(result)

            total_time = time.time() - start_time
            self.download_stats['total_time'] = total_time

            # Log summary
            successful = sum(1 for r in processed_results if r.success)
            failed = len(processed_results) - successful
            total_bytes = sum(r.file_size for r in processed_results if r.success)
            self.telemetry.record(
                "download_batch_finished",
                count=len(tasks),
                successful=successful,
                failed=failed,
                duration_ms=total_time * 1000,
                bytes=total_bytes,
            )

            self.logger.info(
                f"Download completed: {successful} successful, {failed} failed, "
                f"{total_bytes} bytes, {total_time:.2f}s"
            )

            self._update_progress("Downloads completed")

            return processed_results

        except Exception as e:
            self.logger.error(f"Error in concurrent download: {e}")
            raise NetworkError(f"Concurrent download failed: {e}")
        finally:
            await self._lag_monitor.stop()

    def get_download_stats(self) -> Dict[str, Any]:
        """
        Get download statistics

        Returns:
            Dictionary with download statistics
        """
        stats = self.download_stats.copy()

        if stats['total_time'] > 0:
            stats['average_speed'] = stats['total_bytes'] / stats['total_time']  # bytes per second
            stats['files_per_second'] = stats['successful_downloads'] / stats['total_time']
        else:
            stats['average_speed'] = 0
            stats['files_per_second'] = 0

        if stats['total_files'] > 0:
            stats['success_rate'] = (stats['successful_downloads'] / stats['total_files']) * 100
        else:
            stats['success_rate'] = 0

        return stats

    def reset_stats(self) -> None:
        """Reset download statistics"""
        self.download_stats = {
            'total_files': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'total_bytes': 0,
            'total_time': 0.0,
            'retry_count': 0
        }
