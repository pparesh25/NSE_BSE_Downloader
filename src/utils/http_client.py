"""
HTTP client utilities built on aiohttp with sync wrappers.

Provides minimal helpers to fetch text/bytes and stream downloads while
keeping a synchronous facade for existing callers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Thread
from typing import Optional, Callable

import aiohttp


@dataclass
class HTTPStatusError(Exception):
    status: int
    message: str
    url: str

    def __str__(self) -> str:  # pragma: no cover
        return f"HTTP {self.status} for {self.url}: {self.message}"


def _default_headers() -> dict:
    # Conservative default headers suitable for GitHub/raw and general HTTP
    return {
        "User-Agent": (
            "NSE_BSE_Downloader/1.0 (+https://github.com/pparesh25/NSE_BSE_Downloader_PySide6)"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


async def _single_use_session(timeout: Optional[float] = None) -> aiohttp.ClientSession:
    client_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else aiohttp.ClientTimeout()
    connector = aiohttp.TCPConnector(ssl=False)
    return aiohttp.ClientSession(timeout=client_timeout, headers=_default_headers(), connector=connector)


async def fetch_text(url: str, timeout: Optional[float] = None, *, session: Optional[aiohttp.ClientSession] = None) -> str:
    owns_session = False
    if session is None:
        session = await _single_use_session(timeout)
        owns_session = True
    try:
        async with session.get(url) as resp:
            if resp.status == 404:
                raise HTTPStatusError(404, "Not Found", url)
            if resp.status >= 400:
                text = await resp.text()
                raise HTTPStatusError(resp.status, text[:200], url)
            return await resp.text()
    finally:
        if owns_session:
            await session.close()


async def fetch_bytes(url: str, timeout: Optional[float] = None, *, session: Optional[aiohttp.ClientSession] = None) -> bytes:
    owns_session = False
    if session is None:
        session = await _single_use_session(timeout)
        owns_session = True
    try:
        async with session.get(url) as resp:
            if resp.status == 404:
                raise HTTPStatusError(404, "Not Found", url)
            if resp.status >= 400:
                text = await resp.text()
                raise HTTPStatusError(resp.status, text[:200], url)
            return await resp.read()
    finally:
        if owns_session:
            await session.close()


async def download_to_file(url: str, file_path: str, timeout: Optional[float] = None, *, progress: Optional[Callable[[int, int], None]] = None, session: Optional[aiohttp.ClientSession] = None) -> None:
    owns_session = False
    if session is None:
        session = await _single_use_session(timeout)
        owns_session = True
    try:
        async with session.get(url) as resp:
            if resp.status == 404:
                raise HTTPStatusError(404, "Not Found", url)
            if resp.status >= 400:
                text = await resp.text()
                raise HTTPStatusError(resp.status, text[:200], url)
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(file_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(downloaded, total)
    finally:
        if owns_session:
            await session.close()


# ---- Sync wrappers ----

def _run_coro_blocking(coro):
    """Run coroutine safely even if an event loop is running elsewhere.
    Uses a dedicated thread with its own loop to avoid conflicts.
    """
    result_holder = {}
    error_holder = {}

    def runner():
        try:
            result_holder["result"] = asyncio.run(coro)
        except Exception as e:  # pragma: no cover
            error_holder["error"] = e

    t = Thread(target=runner, daemon=True)
    t.start()
    t.join()

    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("result")


def fetch_text_sync(url: str, timeout: Optional[float] = None) -> str:
    return _run_coro_blocking(fetch_text(url, timeout))


def fetch_bytes_sync(url: str, timeout: Optional[float] = None) -> bytes:
    return _run_coro_blocking(fetch_bytes(url, timeout))


def download_to_file_sync(url: str, file_path: str, timeout: Optional[float] = None, *, progress: Optional[Callable[[int, int], None]] = None) -> None:
    return _run_coro_blocking(download_to_file(url, file_path, timeout, progress=progress))
