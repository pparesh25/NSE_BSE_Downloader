"""The trust store every outbound HTTPS request uses.

A packaged build ships its own OpenSSL, compiled with an ``OPENSSLDIR`` that
exists on the build runner and not on a user's machine.  Carrying no CA bundle
of its own, that OpenSSL starts with zero trusted roots, so every request fails
certificate verification and the downloader -- correctly treating a certificate
failure as terminal -- reports an SSL error for every date and then opens the
host circuit breaker.  That is what withdrew the v1.1.0 release; the record is
in ``docs/engineering/RELEASE_BUILD_EVIDENCE.md``.

The trust store is therefore chosen here, explicitly and in one place, rather
than inherited from whatever the interpreter happened to be compiled against.
``certifi`` ships the bundle as an ordinary file inside the package, so the same
certificates are used whether the application runs from source or from a
compiled artifact.
"""

from __future__ import annotations

import functools
import logging
import ssl
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def certificate_bundle_path() -> Optional[Path]:
    """Return the bundled CA certificate file, or ``None`` if it is absent.

    ``None`` means the packaging step failed to include ``certifi``'s data file,
    or the dependency is missing from an older source install.  The caller falls
    back to the interpreter's own default store, which is right for a source run
    and is the failure that must not pass unnoticed in a packaged one.
    """

    try:
        import certifi
    except ImportError:
        logger.warning("certifi is not installed; falling back to the system trust store")
        return None

    path = Path(certifi.where())
    if not path.is_file():
        logger.warning(f"certifi bundle is missing from the installation: {path}")
        return None
    return path


@functools.lru_cache(maxsize=1)
def default_ssl_context() -> ssl.SSLContext:
    """Return the shared verifying TLS context for outbound requests.

    Cached because loading a bundle of roots per request is measurable, and
    because aiohttp is happy to share one context across sessions.  Hostname
    checking and certificate verification stay at their defaults: this exists to
    give OpenSSL certificates to verify against, never to skip verification.
    """

    bundle = certificate_bundle_path()
    if bundle is not None:
        # A truncated or corrupt bundle is worse than no bundle: it looks like a
        # configured trust store while trusting nothing, and OpenSSL raises here
        # rather than returning an empty one.  Neither may take the application
        # down at its first request.
        try:
            context = ssl.create_default_context(cafile=str(bundle))
        except (ssl.SSLError, OSError) as error:
            logger.warning(f"certificate bundle could not be loaded: {bundle}: {error}")
        else:
            if context.get_ca_certs():
                return context
            logger.warning(f"certificate bundle loaded no certificates: {bundle}")

    context = ssl.create_default_context()
    if not context.get_ca_certs() and not _system_store_looks_usable():
        logger.error(
            "no trusted CA certificates are available; HTTPS requests will fail "
            "certificate verification"
        )
    return context


def _system_store_looks_usable() -> bool:
    """Whether OpenSSL's own default store holds anything.

    A directory store is loaded lazily, so an empty ``get_ca_certs()`` does not
    by itself prove the system store is unusable; this keeps the diagnostic above
    from crying wolf on the Linux layouts that use a hashed directory.  The
    directory has to contain something, though -- macOS ships an empty
    ``/etc/ssl/certs``, and an empty directory trusts nothing.
    """

    paths = ssl.get_default_verify_paths()
    if paths.cafile and Path(paths.cafile).is_file():
        return True
    if not paths.capath:
        return False
    try:
        return any(Path(paths.capath).iterdir())
    except OSError:
        return False
