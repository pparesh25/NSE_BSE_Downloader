import asyncio
import aiohttp
from datetime import date
from types import SimpleNamespace

from src.core.config import Config
from src.utils.async_downloader import (
    AsyncDownloadManager,
    DownloadResult,
    DownloadTask,
)
from src.utils.http_client import _single_use_session


def test_config_download_settings_are_mutable_for_the_runtime(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_paths:
  base_folder: "{base}"
download_settings:
  max_concurrent_downloads: 1
  retry_attempts: 3
  timeout_seconds: 5
  chunk_size: 1024
  rate_limit_delay: 0
exchange_config:
  NSE:
    EQ:
      base_url: https://example.test
      filename_pattern: test
      date_format: "%Y%m%d"
      file_suffix: -NSE-EQ
""".format(base=tmp_path / "data"),
        encoding="utf-8",
    )

    config = Config(str(config_path))
    assert config.download_settings is config.download_settings
    config.download_settings.timeout_seconds = 30
    assert config.download_settings.timeout_seconds == 30

    config.reload_config()
    assert config.download_settings.timeout_seconds == 5


def test_http_sessions_use_certificate_verification():
    async def check():
        session = await _single_use_session(timeout=1)
        try:
            assert session.connector is not None
            assert session.connector._ssl is True
        finally:
            await session.close()

    asyncio.run(check())


def _manager(retry_attempts=3):
    config = SimpleNamespace(
        download_settings=SimpleNamespace(
            timeout_seconds=12,
            max_concurrent_downloads=1,
            retry_attempts=retry_attempts,
            chunk_size=1024,
            rate_limit_delay=0,
        )
    )
    manager = AsyncDownloadManager(config)
    manager._get_retry_delay = lambda *args, **kwargs: 0
    return manager


def test_timeout_is_retried_and_keeps_useful_final_error():
    manager = _manager(retry_attempts=3)
    task = DownloadTask("https://example.test/data", "2026-07-31", date(2026, 7, 31))
    attempts = 0

    async def timeout_attempt(_task):
        nonlocal attempts
        attempts += 1
        raise asyncio.TimeoutError()

    manager._attempt_download = timeout_attempt
    result = asyncio.run(manager.download_file(task))

    assert attempts == 3
    assert not result.success
    assert "12s" in result.error_message
    assert "3 attempts" in result.error_message
    assert manager.download_stats["retry_count"] == 2


def test_retryable_http_status_result_is_retried():
    manager = _manager(retry_attempts=3)
    task = DownloadTask("https://example.test/data", "2026-07-31", date(2026, 7, 31))
    attempts = 0

    async def status_attempt(_task):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return DownloadResult(
                task=task,
                success=False,
                error_message="HTTP 503: Service Unavailable",
                status_code=503,
            )
        return DownloadResult(task=task, success=True, file_data=b"ok", file_size=2)

    manager._attempt_download = status_attempt
    result = asyncio.run(manager.download_file(task))

    assert result.success
    assert attempts == 3
    assert manager.download_stats["retry_count"] == 2


def test_connection_error_is_retried_but_certificate_error_is_not():
    task = DownloadTask("https://example.test/data", "2026-07-31", date(2026, 7, 31))

    manager = _manager(retry_attempts=3)
    attempts = 0

    async def connection_attempt(_task):
        nonlocal attempts
        attempts += 1
        raise aiohttp.ClientConnectionError("connection reset")

    manager._attempt_download = connection_attempt
    result = asyncio.run(manager.download_file(task))
    assert not result.success
    assert attempts == 3

    manager = _manager(retry_attempts=3)
    attempts = 0

    async def certificate_attempt(_task):
        nonlocal attempts
        attempts += 1
        raise aiohttp.ClientConnectionError("certificate verify failed")

    manager._attempt_download = certificate_attempt
    result = asyncio.run(manager.download_file(task))
    assert not result.success
    assert attempts == 1


def test_retry_after_supports_seconds_and_http_dates():
    assert AsyncDownloadManager._parse_retry_after("12") == 12
    assert (
        AsyncDownloadManager._parse_retry_after(
            "Wed, 21 Oct 2015 07:28:00 GMT"
        )
        == 0
    )
    assert AsyncDownloadManager._parse_retry_after("invalid") is None


def test_batch_download_propagates_cancellation():
    manager = _manager(retry_attempts=3)
    task = DownloadTask(
        "https://example.test/data", "2026-07-31", date(2026, 7, 31)
    )

    async def cancelled_attempt(_task):
        raise asyncio.CancelledError()

    manager._attempt_download = cancelled_attempt

    async def run():
        try:
            await manager.download_multiple([task])
        except asyncio.CancelledError:
            return "cancelled"
        return "completed"

    assert asyncio.run(run()) == "cancelled"
