import asyncio
from types import SimpleNamespace

import pytest

from src.core.exceptions import NetworkError
from src.services.pipeline_telemetry import PipelineTelemetry
from src.utils.async_downloader import AsyncDownloadManager
from src.utils.transport_pool import TransportPool


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
