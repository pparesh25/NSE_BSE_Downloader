import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from src.core.exceptions import NetworkError
from src.services.pipeline_telemetry import PipelineTelemetry
from src.utils.async_downloader import (
    AsyncDownloadManager,
    DownloadResult,
    DownloadTask,
)
from src.utils.transport_pool import TransportPool
from tests.fixtures.transport_failures import fixture_response


def _config():
    return SimpleNamespace(
        download_settings=SimpleNamespace(
            max_concurrent_downloads=1,
            rate_limit_delay=0,
            timeout_seconds=5,
            connect_timeout_seconds=2,
            read_timeout_seconds=3,
            attempt_timeout_seconds=4,
        ),
        pipeline_telemetry=PipelineTelemetry(),
    )


def test_shared_pool_reuses_session_and_enforces_host_limit():
    async def run():
        pool = TransportPool(_config())
        await pool.start()
        active = 0
        peak = 0

        async def one():
            nonlocal active, peak
            async with pool.slot("https://example.test/data"):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.001)
                active -= 1

        await asyncio.gather(one(), one(), one())
        first = pool.session
        await pool.close()
        return peak, first

    peak, session = asyncio.run(run())
    assert peak == 1
    assert session is not None


def test_pool_circuit_opens_after_repeated_transient_failures():
    async def run():
        pool = TransportPool(_config())
        url = "https://example.test/data"
        for _ in range(3):
            pool.record(url, success=False, status_code=500)
        with pytest.raises(NetworkError, match="circuit open"):
            async with pool.slot(url):
                pass

    asyncio.run(run())


def test_manager_uses_split_timeout_budget():
    manager = AsyncDownloadManager(_config())
    timeout = manager._get_timeout_budget()
    assert timeout.total == 4
    assert timeout.connect == 2
    assert timeout.sock_read == 3


def test_two_managers_share_run_pool_without_closing_it():
    async def run():
        config = _config()
        pool = TransportPool(config)
        config.transport_pool = pool
        await pool.start()
        first = AsyncDownloadManager(config)
        second = AsyncDownloadManager(config)
        async with first:
            first_session = first.session
        async with second:
            second_session = second.session
        still_open = pool.session is not None and not pool.session.closed
        await pool.close()
        return first_session, second_session, still_open

    first, second, still_open = asyncio.run(run())
    assert first is second
    assert still_open


def test_empty_and_crc_zip_payloads_are_rejected_before_processing():
    for case in ("empty_zip", "crc"):
        with pytest.raises(NetworkError, match="ZIP"):
            AsyncDownloadManager._validate_payload_envelope(
                fixture_response(case).body,
                "https://example.test/report.zip",
            )


def test_payload_validation_failure_retries_and_can_recover():
    async def run():
        config = _config()
        config.download_settings.retry_attempts = 3
        manager = AsyncDownloadManager(config)
        manager._get_retry_delay = lambda *_args, **_kwargs: 0
        task = DownloadTask(
            "https://example.test/report.zip",
            "2026-08-04",
            date(2026, 8, 4),
        )
        attempts = 0

        async def attempt(_task):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise NetworkError("Server returned an empty ZIP report")
            return DownloadResult(task=_task, success=True, file_data=b"ok", file_size=2)

        manager._attempt_download = attempt
        result = await manager.download_file(task)
        return result, attempts

    result, attempts = asyncio.run(run())
    assert result.success
    assert attempts == 2


def test_403_gets_one_controlled_retry_but_401_is_terminal():
    async def attempts_for(status):
        config = _config()
        config.download_settings.retry_attempts = 3
        manager = AsyncDownloadManager(config)
        manager._get_retry_delay = lambda *_args, **_kwargs: 0
        task = DownloadTask(
            "https://example.test/report",
            "2026-08-04",
            date(2026, 8, 4),
        )
        attempts = 0

        async def attempt(_task):
            nonlocal attempts
            attempts += 1
            return DownloadResult(
                task=_task,
                success=False,
                error_message=f"HTTP {status}",
                status_code=status,
            )

        manager._attempt_download = attempt
        await manager.download_file(task)
        return attempts

    assert asyncio.run(attempts_for(403)) == 2
    assert asyncio.run(attempts_for(401)) == 1
