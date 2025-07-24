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
        
        # Custom headers to mimic browser requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        connector = aiohttp.TCPConnector(
            limit=self.download_settings.max_concurrent_downloads * 2,
            limit_per_host=self.download_settings.max_concurrent_downloads,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            connector=connector
        )
        
        self.logger.info("Async session created")
    
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
                # Rate limiting (reduced for fast mode)
                delay = self.download_settings.rate_limit_delay
                if hasattr(self.download_settings, 'fast_mode') and self.download_settings.fast_mode:
                    delay = min(delay, 0.1)  # Maximum 0.1s delay in fast mode

                if delay > 0:
                    await asyncio.sleep(delay)
                
                # Fast download strategy - single attempt with full timeout wait
                if hasattr(self.download_settings, 'fast_mode') and self.download_settings.fast_mode:
                    # Fast mode: single attempt but wait for full timeout before giving up
                    try:
                        result = await self._attempt_download(task)
                        if result.success:
                            self.download_stats['successful_downloads'] += 1
                            self.download_stats['total_bytes'] += result.file_size
                        else:
                            self.download_stats['failed_downloads'] += 1
                        return result

                    except asyncio.TimeoutError:
                        # Timeout occurred - this is expected behavior, not an error
                        error_msg = f"Server timeout after {self.download_settings.timeout_seconds}s (expected for unavailable files)"
                        self.download_stats['failed_downloads'] += 1

                        return DownloadResult(
                            task=task,
                            success=False,
                            error_message=error_msg,
                            download_time=time.time() - start_time
                        )
                    except Exception as e:
                        # Other errors (network issues, etc.)
                        error_msg = f"Download error: {e}"
                        self.download_stats['failed_downloads'] += 1

                        return DownloadResult(
                            task=task,
                            success=False,
                            error_message=error_msg,
                            download_time=time.time() - start_time
                        )

                else:
                    # Original retry logic for non-fast mode
                    for attempt in range(self.download_settings.retry_attempts):
                        try:
                            result = await self._attempt_download(task)
                            if result.success:
                                self.download_stats['successful_downloads'] += 1
                                self.download_stats['total_bytes'] += result.file_size
                                return result

                            # If not successful and not the last attempt, wait before retry
                            if attempt < self.download_settings.retry_attempts - 1:
                                wait_time = 2 ** attempt  # Exponential backoff
                                self.logger.warning(f"Retry {attempt + 1} for {task.url} in {wait_time}s")
                                await asyncio.sleep(wait_time)
                                self.download_stats['retry_count'] += 1

                        except Exception as e:
                            if attempt == self.download_settings.retry_attempts - 1:
                                # Last attempt failed
                                error_msg = f"Download failed after {self.download_settings.retry_attempts} attempts: {e}"
                                self.logger.error(f"{task.url}: {error_msg}")
                                self.download_stats['failed_downloads'] += 1

                                return DownloadResult(
                                    task=task,
                                    success=False,
                                    error_message=error_msg,
                                    download_time=time.time() - start_time
                                )
                
                # All retries exhausted
                error_msg = f"All {self.download_settings.retry_attempts} download attempts failed"
                self.download_stats['failed_downloads'] += 1
                
                return DownloadResult(
                    task=task,
                    success=False,
                    error_message=error_msg,
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

            if is_bse_request:
                request_type = "BSE INDEX" if is_bse_index else "BSE EQ" if is_bse_eq else "BSE"
                self.logger.info(f"🔍 {request_type} HTTP Request Debug:")
                self.logger.info(f"  URL: {task.url}")
                self.logger.info(f"  Timeout: {self.download_settings.timeout_seconds}s")
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
