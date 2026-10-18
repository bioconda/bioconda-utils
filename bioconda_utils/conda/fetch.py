"""Parallel HTTP downloader used to fetch repodata and shards.

:func:`fetch` downloads many URLs at once using asyncio, retrying on
transient HTTP error codes. It is shared by :mod:`.repodata` and
:mod:`.shards`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any, cast

import aiofiles
import aiohttp

from ..support import http
from ..support.logsetup import progress_display

logger = logging.getLogger(__name__)


#: Max connections to each server
CONNECTIONS_PER_HOST = 4


@dataclass(frozen=True)
class FetchRequest:
    """One download performed by :func:`fetch`.

    Args:
      url: The URL (or ``file://`` path) to download.
      description: Label for the progress display. Falls back to **url**
          when left empty.
    """

    url: str
    description: str = ""


def fetch(
    requests: Iterable[FetchRequest],
    *,
    allow_404: bool = False,
    return_exceptions: bool = False,
) -> list[Any]:
    """Fetch data from URLs.

    This will use asyncio to manage a pool of connections at once, speeding
    up download as compared to iterative use of ``requests`` significantly.
    It will also retry on non-permanent HTTP error codes (i.e. 429, 502,
    503 and 504).

    Args:
      requests: The downloads to perform.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("Running fetch from within running loop")
        # Workaround the fact that asyncio's loop is marked as not-reentrant
        # (it is apparently easy to patch, but not desired by the devs,
        with ThreadPool(1) as pool:
            res = pool.apply(
                lambda: fetch(
                    requests,
                    allow_404=allow_404,
                    return_exceptions=return_exceptions,
                )
            )
        return res

    # asyncio.run cancels the fetch on SIGINT before raising
    # KeyboardInterrupt, so pending connections close cleanly
    return cast(
        list[Any],
        asyncio.run(
            async_fetch(
                requests,
                allow_404=allow_404,
                return_exceptions=return_exceptions,
            )
        ),
    )


async def async_fetch(
    requests: Iterable[FetchRequest] = (),
    *,
    allow_404: bool = False,
    return_exceptions: bool = False,
) -> list[Any | bytes]:
    conn = aiohttp.TCPConnector(limit_per_host=CONNECTIONS_PER_HOST)
    async with http.make_session(connector=conn) as session:

        async def fetch_indexed(index, request):
            try:
                value = await _async_fetch_one(
                    session,
                    request.url,
                    request.description or request.url,
                    allow_404=allow_404,
                )
            except Exception as exc:
                if not return_exceptions:
                    raise
                value = exc
            return index, value

        coros = [
            asyncio.create_task(fetch_indexed(index, request))
            for index, request in enumerate(requests)
        ]
        try:
            with progress_display.count_task("Downloading", total=len(coros)) as (
                progress,
                task,
            ):
                result = [None] * len(coros)
                for coro in progress.track(
                    asyncio.as_completed(coros), total=len(coros), task_id=task
                ):
                    index, value = await coro
                    result[index] = value
        finally:
            for coro in coros:
                if not coro.done():
                    coro.cancel()
            await asyncio.gather(*coros, return_exceptions=True)
    return result


@http.retry_on_transient
async def _async_fetch_one(
    session: aiohttp.ClientSession,
    url: str,
    description: str,
    *,
    allow_404: bool = False,
) -> bytes | None:
    if url.startswith("file://"):
        local_path = Path(url[7:])
        if local_path.exists():
            async with aiofiles.open(local_path, mode="rb") as f:
                raw = await f.read()
        elif allow_404:
            return None
        else:
            subdir = url.split("/")[-2]
            d = {
                "info": {"subdir": subdir},
                "packages": {},
                "packages.conda": {},
                "removed": [],
                "repodata_version": 1,
            }
            raw = json.dumps(d).encode("UTF-8")
    else:
        async with session.get(url) as resp:
            if allow_404 and resp.status == 404:
                return None
            resp.raise_for_status()
            raw = await http.download_to_bytes(
                resp,
                description,
                block_size=1024 * 16,
            )
    return raw
