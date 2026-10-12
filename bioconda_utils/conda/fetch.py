"""Parallel HTTP downloader used to fetch repodata and shards.

:func:`fetch` downloads many URLs at once using asyncio, retrying on
transient HTTP error codes. It is shared by :mod:`.repodata` and
:mod:`.shards`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from itertools import zip_longest
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any, cast

import aiofiles
import aiohttp

from .._types import RepoDataKey
from ..support import http
from ..support.logsetup import progress_display

logger = logging.getLogger(__name__)


#: Max connections to each server
CONNECTIONS_PER_HOST = 4


def fetch(
    urls: Iterable[str],
    descriptions: Iterable[str],
    transform: Callable[[bytes, RepoDataKey], Any] | None,
    metadata: Iterable[RepoDataKey],
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
      urls: List of URLS
      descriptions: Matching list of descriptions (for progress display)
      transform: As each download completes, the raw bytes and the matching
          entry from **metadata** are passed through this function, e.g. to
          offload json parsing into the download loop.
      metadata: Per-URL context handed to **transform** alongside the bytes.
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
                    urls,
                    descriptions,
                    transform,
                    metadata,
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
                urls,
                descriptions,
                transform,
                metadata,
                allow_404=allow_404,
                return_exceptions=return_exceptions,
            )
        ),
    )


async def async_fetch(
    urls: Iterable[str] = (),
    descriptions: Iterable[str] = (),
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: Iterable[RepoDataKey] | None = None,
    *,
    allow_404: bool = False,
    return_exceptions: bool = False,
) -> list[Any | bytes]:
    if metadata is None:
        metadata = []
    conn = aiohttp.TCPConnector(limit_per_host=CONNECTIONS_PER_HOST)
    async with http.make_session(connector=conn) as session:

        async def fetch_indexed(index, url, description, datum):
            try:
                value = await _async_fetch_one(
                    session,
                    url,
                    description or url,
                    transform=transform,
                    metadata=datum,
                    allow_404=allow_404,
                )
            except Exception as exc:
                if not return_exceptions:
                    raise
                value = exc
            return index, value

        coros = [
            asyncio.create_task(fetch_indexed(index, url, description, datum))
            for index, (url, description, datum) in enumerate(
                zip_longest(urls, descriptions, metadata)
            )
            if url is not None
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
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: RepoDataKey | None = None,
    *,
    allow_404: bool = False,
) -> Any | bytes:
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
    if transform is None:
        return raw
    assert metadata is not None
    return transform(raw, metadata)
