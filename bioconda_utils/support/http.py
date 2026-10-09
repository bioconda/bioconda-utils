"""Shared helpers for asynchronous HTTP access

This module centralizes the pieces shared between the async download
helpers in :py:mod:`bioconda_utils.conda.repodata` and
:py:mod:`bioconda_utils.aiopipe`: the user agent we identify ourselves
with, the retry policy applied to transient HTTP errors and the progress
monitor used while streaming response bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Any

import aiofiles
import aiohttp
import backoff
from rich.filesize import decimal

from .logsetup import progress_display

logger = logging.getLogger(__name__)

# Used as user agent in http requests and as requester in github API requests
USER_AGENT = "bioconda/bioconda-utils"

# HTTP status codes indicating transient errors (retried with backoff)
TRANSIENT_STATUS_CODES = (429, 502, 503, 504)


def _give_up_on_http_error(ex: Exception) -> bool:
    """Return whether retrying **ex** cannot resolve the HTTP failure."""
    return (
        isinstance(ex, aiohttp.ClientResponseError)
        and ex.status not in TRANSIENT_STATUS_CODES
    )


# Retry requests on transient errors (429, 502, 503, 504), waiting according
# to the fibonacci series, at most 20 times. Truncated transfers
# (ClientPayloadError) are retried as well.
retry_on_transient = backoff.on_exception(
    backoff.fibo,
    (aiohttp.ClientResponseError, aiohttp.ClientPayloadError),
    max_tries=20,
    giveup=_give_up_on_http_error,
)


def make_session(
    *,
    user_agent: str = USER_AGENT,
    connector: aiohttp.BaseConnector | None = None,
) -> aiohttp.ClientSession:
    """Create an :py:class:`aiohttp.ClientSession` identifying ourselves

    ``user_agent`` is configurable so callers can retain their own identity.
    Proxy settings from the environment are honored (``trust_env=True``).
    """
    return aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": user_agent},
        trust_env=True,
    )


def _parse_content_length(resp: aiohttp.ClientResponse) -> int | None:
    """Return the uncompressed body size, or None if unknown, invalid, or encoded."""
    if resp.headers.get("Content-Encoding", "identity") != "identity":
        return None
    length = resp.headers.get("Content-Length")
    if length is None:
        return None
    try:
        size = int(length)
        return size if size >= 0 else None
    except (ValueError, TypeError):
        return None


async def stream_to_sink(
    resp: aiohttp.ClientResponse,
    desc: str,
    sink: Callable[[bytes], Any] | Callable[[bytes], Awaitable[Any]],
    *,
    block_size: int = 1024 * 1024,
) -> int:
    """Stream response body to a sink callable while reporting progress.

    The sink can be either a synchronous function (e.g. ``hasher.update``)
    or an asynchronous coroutine function (e.g. ``aiofile.write``).

    Returns the total number of bytes received.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    size = _parse_content_length(resp)
    received = 0
    started = monotonic()
    logger.info("Downloading %s", desc)
    try:
        with progress_display.download_task(desc, total=size) as (progress, task):
            while True:
                block = await resp.content.read(block_size)
                if not block:
                    break
                received += len(block)
                progress.update(task, advance=len(block))
                res = sink(block)
                if inspect.isawaitable(res):
                    await res
    except asyncio.CancelledError:
        logger.info(
            "Download cancelled: %s (%s received in %.1f s)",
            desc,
            decimal(received),
            monotonic() - started,
        )
        raise
    except Exception as exc:
        logger.warning(
            "Download failed: %s (%s received in %.1f s): %s",
            desc,
            decimal(received),
            monotonic() - started,
            exc,
        )
        raise
    else:
        logger.info(
            "Downloaded %s: %s in %.1f s",
            desc,
            decimal(received),
            monotonic() - started,
        )
    return received


async def download_to_file(
    resp: aiohttp.ClientResponse,
    fname: Path | str,
    desc: str,
    *,
    block_size: int = 1024 * 1024,
) -> int:
    """Download response body to **fname** while reporting progress."""
    async with aiofiles.open(fname, "wb") as f:
        return await stream_to_sink(resp, desc, f.write, block_size=block_size)


async def download_to_checksum(
    resp: aiohttp.ClientResponse,
    desc: str,
    *,
    algorithm: str = "sha256",
    block_size: int = 1024 * 1024,
) -> str:
    """Download response body, computing its hash digest with progress reporting."""
    hasher = hashlib.new(algorithm)
    await stream_to_sink(resp, desc, hasher.update, block_size=block_size)
    return hasher.hexdigest()


async def download_to_bytes(
    resp: aiohttp.ClientResponse,
    desc: str,
    *,
    block_size: int = 1024 * 1024,
) -> bytes:
    """Download response body into memory as bytes with progress reporting."""
    chunks: list[bytes] = []
    await stream_to_sink(resp, desc, chunks.append, block_size=block_size)
    return b"".join(chunks)
