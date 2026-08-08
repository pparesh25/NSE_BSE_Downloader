import asyncio
from datetime import date
import logging
import ssl
from types import SimpleNamespace

import aiohttp
import pytest

from src.core.config import Config
from src.core.exceptions import CircuitOpenError, NetworkError
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


def _manager_for_classification():
    """A manager built without __init__, enough for pure classification."""
    manager = object.__new__(AsyncDownloadManager)
    manager.logger = logging.getLogger("test.transport")
    return manager


def _task() -> DownloadTask:
    return DownloadTask(
        url="https://nsearchives.nseindia.com/content/cm/"
            "BhavCopy_NSE_CM_0_0_0_20240404_F_0000.csv.zip",
        date_str="2024-04-04",
        target_date=date(2024, 4, 4),
    )


def test_connection_refused_is_network_not_ssl():
    # aiohttp renders ClientConnectorError as
    # "Cannot connect to host ... ssl:default [Connect call failed ...]",
    # so substring matching used to classify a refused connection as a
    # terminal certificate failure and skip every retry.
    manager = _manager_for_classification()
    connection_key = SimpleNamespace(
        host="nsearchives.nseindia.com", port=443, is_ssl=True, ssl=True
    )
    error = aiohttp.ClientConnectorError(
        connection_key, OSError(61, "Connect call failed")
    )
    assert "ssl" in str(error).lower(), "precondition: message mentions ssl"

    info = manager._classify_error(str(error), _task(), exception=error)

    assert info["type"] == "network"
    assert info["should_retry"] is True


def test_certificate_failure_is_still_terminal():
    manager = _manager_for_classification()
    connection_key = SimpleNamespace(
        host="nsearchives.nseindia.com", port=443, is_ssl=True, ssl=True
    )
    error = aiohttp.ClientConnectorCertificateError(
        connection_key, ssl.SSLCertVerificationError("bad certificate")
    )

    info = manager._classify_error(str(error), _task(), exception=error)

    assert info["type"] == "ssl_error"
    assert info["should_retry"] is False


def test_timeout_exception_is_retryable():
    manager = _manager_for_classification()
    error = asyncio.TimeoutError()

    info = manager._classify_error(str(error), _task(), exception=error)

    assert info["type"] == "timeout"
    assert info["should_retry"] is True


def test_circuit_rejection_is_typed_and_not_a_status_code():
    # The message carries the URL, whose date "20240404" contains "404".
    # Substring matching read that as "file not published yet".
    manager = _manager_for_classification()
    error = CircuitOpenError(
        "Host circuit open for nsearchives.nseindia.com", url=_task().url
    )
    assert "404" in str(error), "precondition: the URL date contains 404"

    info = manager._classify_error(str(error), _task(), exception=error)

    assert info["type"] == "circuit_open"
    assert info["should_retry"] is False


def test_url_in_message_is_not_matched_as_a_status_code():
    manager = _manager_for_classification()
    message = f"Download error: something went wrong (URL: {_task().url})"

    info = manager._classify_error(message, _task())

    assert info["type"] != "file_not_found"


def test_split_timeout_budget_is_read_from_settings():
    manager = object.__new__(AsyncDownloadManager)
    manager.download_settings = SimpleNamespace(
        timeout_seconds=5,
        connect_timeout_seconds=10,
        read_timeout_seconds=60,
        attempt_timeout_seconds=300,
    )

    budget = manager._get_timeout_budget()

    assert budget.total == 300
    assert budget.connect == 10
    assert budget.sock_read == 60


def test_shipped_config_does_not_leave_a_five_second_total_budget():
    # The defect: config.yaml defined only timeout_seconds, so aiohttp received
    # total=5 for the whole transfer and a multi-megabyte ZIP could never finish.
    settings = Config("config.yaml").download_settings
    manager = object.__new__(AsyncDownloadManager)
    manager.download_settings = settings

    budget = manager._get_timeout_budget()

    assert budget.total is not None and budget.total >= 60, (
        f"attempt budget is {budget.total}s; a multi-MB ZIP needs more"
    )
    assert budget.connect is not None and budget.connect <= 30


def test_circuit_rejection_does_not_re_arm_the_breaker():
    # The latch: a rejection by an open circuit was recorded as a failure
    # against that same circuit.  With max_concurrent_downloads=1 the cooldown
    # was renewed by every rejected date, so the breaker never closed and one
    # slow date could dead-end every remaining segment of the run.
    async def run():
        config = _config()
        config.download_settings.retry_attempts = 1
        pool = TransportPool(config)
        config.transport_pool = pool
        url = "https://example.test/report.zip"

        for _ in range(3):
            pool.record(url, success=False, status_code=500)
        opened_at = pool.policies["example.test"].circuit_open_until
        assert opened_at > 0, "precondition: circuit is open"

        manager = AsyncDownloadManager(config)
        manager._get_retry_delay = lambda *_a, **_k: 0

        async def attempt(_task):  # pragma: no cover - must never run
            raise AssertionError("request was sent while the circuit was open")

        manager._attempt_download = attempt

        for day in range(5):
            await manager.download_file(
                DownloadTask(url, f"2026-08-0{day + 1}", date(2026, 8, day + 1))
            )

        return opened_at, pool.policies["example.test"]

    opened_at, policy = asyncio.run(run())
    assert policy.circuit_open_until == opened_at, (
        "rejected dates extended the cooldown; the breaker cannot close"
    )
    assert policy.consecutive_failures == 3, (
        f"rejections were counted as failures: {policy.consecutive_failures}"
    )
