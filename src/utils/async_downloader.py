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
import ssl
import logging
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any, Tuple
from dataclasses import dataclass
import time

from ..core.config import Config
from ..core.exceptions import NetworkError, FileOperationError


@dataclass
class DownloadTask:
    """Represents a single download task"""
    url: str
    date_str: str
    target_date: Any  # date object
    retry_count: int = 0

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
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self._create_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self._close_session()
    
    async def _create_session(self) -> None:
        """Create aiohttp session with appropriate settings"""
        timeout = aiohttp.ClientTimeout(total=self.download_settings.timeout_seconds)

        # Enhanced headers optimized for NSE/BSE servers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1'
        }

        # Enhanced connector with NSE-specific optimizations
        connector = aiohttp.TCPConnector(
            limit=self.download_settings.max_concurrent_downloads * 2,
            limit_per_host=self.download_settings.max_concurrent_downloads,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60,  # Keep connections alive longer for NSE
            enable_cleanup_closed=True,  # Clean up closed connections
            force_close=False,  # Reuse connections when possible
            ssl=False  # Allow both HTTP and HTTPS
        )

        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            connector=connector
        )

        self.logger.info(f"Async session created with timeout: {self.download_settings.timeout_seconds}s")

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
        if self.session:
            await self.session.close()
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

    def _get_retry_delay(self, task: DownloadTask, attempt: int) -> float:
        """
        Get consistent retry delay for all servers

        Args:
            task: Download task to analyze
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds
        """
        # Simple progressive delay: 1s, 2s, 3s, 4s
        return min(5.0, 1.0 * (attempt + 1))

    def _classify_error(self, error_message: str, task: DownloadTask) -> dict:
        """
        Classify error for better user feedback

        Args:
            error_message: Error message to classify
            task: Download task that failed

        Returns:
            Dictionary with error classification
        """
        if not error_message:
            return {"type": "unknown", "user_message": "Unknown error occurred", "should_retry": False}

        error_lower = error_message.lower()

        # Timeout errors
        if "timeout" in error_lower:
            return {
                "type": "timeout",
                "user_message": f"Server response timeout for {task.date_str} - file may not be available yet",
                "should_retry": True,
                "technical_details": error_message
            }

        # Network connectivity issues
        if any(term in error_lower for term in ["connection", "network", "reset", "refused"]):
            return {
                "type": "network",
                "user_message": f"Network connectivity issue for {task.date_str} - will retry",
                "should_retry": True,
                "technical_details": error_message
            }

        # Server errors (5xx)
        if any(code in error_lower for code in ["500", "502", "503", "504"]):
            return {
                "type": "server_error",
                "user_message": f"Server error for {task.date_str} - server may be temporarily unavailable",
                "should_retry": True,
                "technical_details": error_message
            }

        # File not found (404)
        if "404" in error_lower or "not found" in error_lower:
            return {
                "type": "file_not_found",
                "user_message": f"File not available for {task.date_str} - may not be published yet",
                "should_retry": False,
                "technical_details": error_message
            }

        # Access denied (403, 401)
        if any(code in error_lower for code in ["403", "401", "forbidden", "unauthorized"]):
            return {
                "type": "access_denied",
                "user_message": f"Access denied for {task.date_str} - server may be blocking requests",
                "should_retry": False,
                "technical_details": error_message
            }

        # SSL/Certificate issues
        if any(term in error_lower for term in ["ssl", "certificate", "cert"]):
            return {
                "type": "ssl_error",
                "user_message": f"SSL certificate issue for {task.date_str} - server configuration problem",
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
                # Simple rate limiting
                delay = self._calculate_delay(task)
                if delay > 0:
                    await asyncio.sleep(delay)

                # Simple retry logic for all servers
                max_attempts = self._get_retry_attempts(task)
                last_error = None

                for attempt in range(max(1, max_attempts)):
                    try:
                        result = await self._attempt_download(task)
                        if result.success:
                            self.download_stats['successful_downloads'] += 1
                            self.download_stats['total_bytes'] += result.file_size
                            if attempt > 0:
                                self.logger.info(f"✅ Success on retry {attempt + 1} for {task.date_str}")
                            return result
                        else:
                            # If download failed but no exception, classify error and decide retry
                            last_error = result.error_message
                            error_info = self._classify_error(result.error_message, task)

                            if error_info["should_retry"] and attempt < max_attempts - 1:
                                wait_time = self._get_retry_delay(task, attempt)
                                self.logger.info(f"🔄 {error_info['type'].title()} retry {task.date_str} in {wait_time}s (attempt {attempt + 2}/{max_attempts})")
                                await asyncio.sleep(wait_time)
                                self.download_stats['retry_count'] += 1
                                continue
                            else:
                                # Don't retry for this type of error or max attempts reached
                                if not error_info["should_retry"]:
                                    self.logger.info(f"❌ {error_info['type'].title()}: {error_info['user_message']}")
                                break

                    except asyncio.TimeoutError:
                        timeout_value = self._get_timeout(task)
                        last_error = f"Server timeout after {timeout_value}s"
                        if attempt < max_attempts - 1:
                            wait_time = self._get_retry_delay(task, attempt)
                            self.logger.info(f"⏱️ Timeout retry {task.date_str} in {wait_time}s (attempt {attempt + 2}/{max_attempts})")
                            await asyncio.sleep(wait_time)
                            self.download_stats['retry_count'] += 1
                            continue
                        else:
                            last_error = f"Server timeout after {timeout_value}s (all {max_attempts} attempts failed)"
                            break

                    except Exception as e:
                        last_error = f"Download error: {e}"
                        error_info = self._classify_error(str(e), task)

                        if error_info["should_retry"] and attempt < max_attempts - 1:
                            wait_time = self._get_retry_delay(task, attempt)
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
                    download_time=time.time() - start_time
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
                self.logger.info(f"  SSL Verification: Disabled (BSE compatibility)")

            # Make HTTP request with SSL handling for BSE
            ssl_context = None
            if is_bse_request:
                # Disable SSL verification for BSE servers
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            async with self.session.get(task.url, ssl=ssl_context) as response:
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
                    raise NetworkError(
                        f"HTTP {response.status}: {response.reason}",
                        url=task.url,
                        status_code=response.status
                    )
                
                # Download to memory
                file_data = bytearray()
                file_size = 0
                async for chunk in response.content.iter_chunked(self.download_settings.chunk_size):
                    file_data.extend(chunk)
                    file_size += len(chunk)

                download_time = time.time() - start_time

                if is_bse_request:
                    request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                    self.logger.info(f"  ✅ {request_type} Download Success:")
                    self.logger.info(f"    File Size: {file_size} bytes")
                    self.logger.info(f"    Download Time: {download_time:.2f}s")
                    # Preview first 100 characters
                    try:
                        preview = file_data[:100].decode('utf-8', errors='ignore')
                        self.logger.info(f"    Content Preview: {preview}")
                    except Exception as e:
                        self.logger.warning(f"    Could not preview content: {e}")

                self.logger.info(f"Downloaded {task.date_str} ({file_size} bytes, {download_time:.2f}s)")

                return DownloadResult(
                    task=task,
                    success=True,
                    file_data=bytes(file_data),
                    file_size=file_size,
                    download_time=download_time
                )
                
        except Exception as e:
            download_time = time.time() - start_time
            error_msg = f"Download attempt failed: {e}"

            return DownloadResult(
                task=task,
                success=False,
                error_message=error_msg,
                download_time=download_time
            )
    
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
        
        try:
            # Create download coroutines
            download_coroutines = [self.download_file(task) for task in tasks]
            
            # Execute downloads concurrently
            results = await asyncio.gather(*download_coroutines, return_exceptions=True)
            
            # Process results and handle exceptions
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
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
            
            self.logger.info(
                f"Download completed: {successful} successful, {failed} failed, "
                f"{total_bytes} bytes, {total_time:.2f}s"
            )
            
            self._update_progress("Downloads completed")
            
            return processed_results
            
        except Exception as e:
            self.logger.error(f"Error in concurrent download: {e}")
            raise NetworkError(f"Concurrent download failed: {e}")
    
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
