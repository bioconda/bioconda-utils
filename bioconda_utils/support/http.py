"""Shared helpers for asynchronous HTTP access

This module centralizes the pieces shared between the async download
helpers in :py:mod:`bioconda_utils.conda.repodata` and
:py:mod:`bioconda_utils.aiopipe`: the user agent we identify ourselves
with, the retry policy applied to transient HTTP errors and the progress
monitor used while streaming response bodies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from time import monotonic

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


@asynccontextmanager
async def stream_download(
    resp: aiohttp.ClientResponse,
    desc: str,
    block_size: int = 1024 * 1024,
) -> AsyncIterator[AsyncIterator[bytes]]:
    """Scope a streamed response body, its progress task, and its outcome log.

    Use ``async with stream_download(response, description) as blocks:`` and
    iterate ``blocks`` inside that scope. The caller owns the response itself.
    Leaving the scope closes the iterator and removes the progress task even
    after an early break, consumer error, or cancellation. Only consuming EOF
    successfully produces a "Downloaded" record.

    Byte counts describe the body yielded by aiohttp, which decompresses HTTP
    content by default. An encoded Content-Length cannot describe that body.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    length = resp.headers.get("Content-Length")
    size = (
        int(length)
        if length is not None
        and resp.headers.get("Content-Encoding", "identity") == "identity"
        else None
    )
    received = 0
    complete = False
    started = monotonic()
    logger.info("Downloading %s", desc)
    try:
        with progress_display.download_task(desc, total=size) as (progress, task):

            async def read_blocks() -> AsyncGenerator[bytes]:
                nonlocal received, complete
                while True:
                    block = await resp.content.read(block_size)
                    if not block:
                        complete = True
                        return
                    received += len(block)
                    progress.update(task, advance=len(block))
                    yield block

            async with aclosing(read_blocks()) as blocks:
                yield blocks
    except asyncio.CancelledError:
        logger.info("Download cancelled: %s (%s received)", desc, decimal(received))
        raise
    except Exception as exc:
        logger.warning(
            "Download failed: %s (%s received): %s", desc, decimal(received), exc
        )
        raise
    else:
        if complete:
            logger.info(
                "Downloaded %s: %s in %.1f s",
                desc,
                decimal(received),
                monotonic() - started,
            )
        else:
            logger.info("Download stopped: %s (%s received)", desc, decimal(received))
