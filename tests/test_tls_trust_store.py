"""The trust store the application carries, and the sites that must use it.

The withdrawn v1.1.0 artifacts compiled, passed every packaging check and could
not open a single HTTPS connection, because the bundle had no CA certificates in
it.  These tests assert the two halves of that: a real bundle travels with the
application, and every outbound session verifies against it rather than against
whatever OpenSSL was compiled to look for.
"""

import asyncio
from datetime import date
import ssl
import sys
from types import SimpleNamespace

import aiohttp
import pytest

import build_nuitka_cross_platform as packaging
from src.services.corporate_actions import CorporateActionClient
from src.services.pipeline_telemetry import PipelineTelemetry
from src.utils import tls
from src.utils.http_client import _single_use_session
from src.utils.tls import certificate_bundle_path, default_ssl_context
from src.utils.transport_pool import TransportPool


@pytest.fixture(autouse=True)
def _uncached_context():
    """Each test builds its own context, so monkeypatching is not defeated."""

    tls.default_ssl_context.cache_clear()
    yield
    tls.default_ssl_context.cache_clear()


def _pool_config():
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


def test_application_carries_its_own_certificate_bundle():
    bundle = certificate_bundle_path()
    assert bundle is not None, "certifi must remain a runtime dependency"
    assert bundle.is_file()
    assert bundle.stat().st_size >= packaging.MINIMUM_CA_BUNDLE_BYTES


def test_default_context_verifies_against_loaded_authorities():
    context = default_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs(), "context must carry certificates to verify against"


def test_context_is_built_once_and_shared():
    assert default_ssl_context() is default_ssl_context()


def test_a_missing_certifi_still_leaves_verification_on(monkeypatch):
    # A None entry in sys.modules makes ``import certifi`` raise ImportError,
    # which is the state of a source install predating the dependency.
    monkeypatch.setitem(sys.modules, "certifi", None)
    context = default_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize(
    "content", ["", "-----BEGIN CERTIFICATE-----\nnot a certificate\n"]
)
def test_an_unusable_bundle_is_reported_and_does_not_crash(
    monkeypatch, tmp_path, caplog, content
):
    stub = tmp_path / "cacert.pem"
    stub.write_text(content, encoding="utf-8")
    monkeypatch.setattr(tls, "certificate_bundle_path", lambda: stub)

    with caplog.at_level("WARNING", logger=tls.logger.name):
        context = default_ssl_context()

    assert "certificate bundle" in caplog.text
    assert str(stub) in caplog.text
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_single_use_sessions_verify_against_the_shared_context():
    async def check():
        session = await _single_use_session(timeout=1)
        try:
            assert session.connector._ssl is default_ssl_context()
        finally:
            await session.close()

    asyncio.run(check())


def test_the_download_pool_verifies_against_the_shared_context():
    async def check():
        pool = TransportPool(_pool_config())
        await pool.start()
        try:
            assert pool.session is not None
            assert pool.session.connector._ssl is default_ssl_context()
        finally:
            await pool.close()

    asyncio.run(check())


@pytest.mark.parametrize("exchange", ["NSE", "BSE"])
def test_corporate_action_requests_verify_against_the_shared_context(
    monkeypatch, exchange
):
    captured = {}

    class _StopBeforeNetwork(RuntimeError):
        pass

    def fake_session(*_args, **kwargs):
        captured["connector"] = kwargs.get("connector")
        raise _StopBeforeNetwork

    monkeypatch.setattr(aiohttp, "ClientSession", fake_session)
    client = CorporateActionClient(timeout=1)
    day = date(2026, 8, 14)

    with pytest.raises(_StopBeforeNetwork):
        if exchange == "NSE":
            asyncio.run(client._fetch_nse("EQ", day, day))
        else:
            asyncio.run(client._fetch_bse(day, day))

    connector = captured["connector"]
    assert connector is not None, "the exchange endpoints must not use the default"
    try:
        assert connector._ssl is default_ssl_context()
    finally:
        asyncio.run(connector.close())
