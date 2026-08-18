"""The trust store the application carries, and the sites that must use it.

The withdrawn v1.1.0 artifacts compiled, passed every packaging check and could
not open a single HTTPS connection, because the bundle had no CA certificates in
it.  These tests assert the two halves of that: a real bundle travels with the
application, and every outbound session verifies against it rather than against
whatever OpenSSL was compiled to look for.
"""

import asyncio
from datetime import date
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace

import aiohttp
import pytest

import build_nuitka_cross_platform as packaging
import main as application
import package_release_artifact as packager
from src.services.corporate_actions import CorporateActionClient
from src.services.pipeline_telemetry import PipelineTelemetry
from src.utils import tls
from src.utils.http_client import HTTPStatusError, _single_use_session
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


def test_tls_check_passes_when_a_bundle_is_present_and_the_request_succeeds(
    monkeypatch, capsys
):
    requested = {}

    def fake_fetch(url, timeout=None, **_kwargs):
        requested["url"] = url
        return "__version__ = \"1.1.0\"\n"

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", fake_fetch)
    assert application.run_tls_check() == 0
    assert requested["url"].startswith("https://")
    assert "TLS verification passed" in capsys.readouterr().out


def test_tls_check_fails_when_the_request_cannot_be_verified(monkeypatch, capsys):
    def refuse(_url, timeout=None, **_kwargs):
        raise ssl.SSLCertVerificationError("unable to get local issuer certificate")

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", refuse)
    assert application.run_tls_check() == 1
    assert "TLS verification FAILED" in capsys.readouterr().out


def test_an_http_status_still_proves_the_certificate_verified(monkeypatch, capsys):
    # A rate limit, a 404 or an outage arrives only after a completed handshake.
    # Treating it as a certificate failure would fail release builds at random.
    def rate_limited(url, timeout=None, **_kwargs):
        raise HTTPStatusError(429, "Too Many Requests", url)

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", rate_limited)
    assert application.run_tls_check() == 0
    output = capsys.readouterr().out
    assert "TLS verified" in output
    assert "429" in output


def test_an_unreachable_endpoint_is_reported_as_inconclusive(monkeypatch, capsys):
    def unreachable(_url, timeout=None, **_kwargs):
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", unreachable)
    assert application.run_tls_check() == 1
    output = capsys.readouterr().out
    assert "INCONCLUSIVE" in output
    assert "FAILED" not in output


def test_a_certificate_error_is_not_mistaken_for_a_connection_error(
    monkeypatch, capsys
):
    # aiohttp reports a missing trust store as ClientConnectorCertificateError,
    # which subclasses ClientConnectorError.  Classified by connection first, the
    # very defect this check exists for would be called "inconclusive".
    def no_trust_store(_url, timeout=None, **_kwargs):
        raise aiohttp.ClientConnectorCertificateError(
            aiohttp.client_reqrep.ConnectionKey(
                host="raw.githubusercontent.com",
                port=443,
                is_ssl=True,
                ssl=None,
                proxy=None,
                proxy_auth=None,
                proxy_headers_hash=None,
            ),
            ssl.SSLCertVerificationError("unable to get local issuer certificate"),
        )

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", no_trust_store)
    assert application.run_tls_check() == 1
    assert "TLS verification FAILED" in capsys.readouterr().out


def test_tls_check_fails_when_no_bundle_travelled_with_the_build(monkeypatch, capsys):
    monkeypatch.setattr(tls, "certificate_bundle_path", lambda: None)

    def unreachable(_url, timeout=None, **_kwargs):  # pragma: no cover
        raise AssertionError("the network must not be reached without a bundle")

    monkeypatch.setattr("src.utils.http_client.fetch_text_sync", unreachable)
    assert application.run_tls_check() == 1
    assert "no certificate bundle travelled with this build" in capsys.readouterr().out


def test_the_packaged_smoke_test_verifies_tls(monkeypatch, tmp_path, capsys):
    commands = []

    def record(_executable, arguments, _cwd, _environment):
        commands.append(list(arguments))
        if "--config" in arguments:
            # Stand in for the GUI startup the real command performs.
            config = Path(arguments[arguments.index("--config") + 1])
            (config.parent / "market-data").mkdir(exist_ok=True)
        if "--verify-tls" in arguments:
            return "Trusted certificate authorities loaded: 137\nTLS verification passed.\n"
        return ""

    monkeypatch.setattr(packager, "executable_path", lambda *_args: tmp_path / "app")
    monkeypatch.setattr(packager, "_run_smoke_command", record)

    packager.smoke_test(tmp_path / "package", "linux")

    assert ["--verify-tls"] in commands
    assert ["--help"] in commands
    assert any("--smoke-gui" in command for command in commands)
    # The evidence has to reach the build log, not just the exit code.
    assert "TLS verification passed." in capsys.readouterr().out
