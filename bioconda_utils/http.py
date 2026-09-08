"""Shared helpers for asynchronous HTTP access

This module centralizes the pieces shared between the async download
helpers in :py:mod:`bioconda_utils.utils` and
:py:mod:`bioconda_utils.aiopipe`: the user agent we identify ourselves
with, the retry policy applied to transient HTTP errors and the progress
monitor used while streaming response bodies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp
import backoff

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


async def stream_download(
    resp: aiohttp.ClientResponse,
    desc: str,
    *,
    progress_factory: Callable[..., Any],
    block_size: int = 1024 * 1024,
    leave: bool = True,
    disable: bool | None = None,
) -> AsyncIterator[bytes]:
    """Stream the body of **resp** in blocks, showing a progress monitor

    Args:
      resp: Response to read from
      desc: Progress monitor label
      progress_factory: Callable returning a progress-monitor context manager
      block_size: Size of the blocks yielded
      leave: Keep the progress monitor visible after completion
      disable: Disable the progress monitor
    """
    size = int(resp.headers.get("Content-Length", 0))
    with progress_factory(
        total=size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=desc,
        miniters=1,
        leave=leave,
        disable=disable,
    ) as progress:
        while True:
            block = await resp.content.read(block_size)
            if not block:
                break
            progress.update(len(block))
            yield block
