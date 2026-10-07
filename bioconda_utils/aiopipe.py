"""Utilities for Asynchronous Processing"""

from __future__ import annotations

import abc
import asyncio
import logging
import pickle
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

import aiofiles
import aioftp
import aiohttp

from .support import http
from .support.logsetup import progress_display
from .support.parallel import threads_to_use

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class EndProcessing(BaseException):
    """Raised by `AsyncFilter` to tell `AsyncPipeline` to stop processing"""


class EndProcessingItem(Exception):
    """Raised to indicate that an item should not be processed further

    This is the carrier of the per-item skip reason (e.g. the subclasses
    in :mod:`bioconda_utils.autobump` recording why a recipe was not
    updated). ``AsyncPipeline.process`` logs and re-raises it; a subclass
    that wants to record the reason must catch it in its ``process``
    override (see ``autobump.Scanner.process``). Letting it escape
    uncaught aborts the whole run.
    """

    __slots__ = ["args", "item"]
    template = "broken: %s"
    level = logging.INFO

    def __init__(self, item: Any, *args) -> None:
        super().__init__(item, *args)
        self.item = item
        self.args = args

    def log(self, uselogger=logger, level=None):
        """Print message using provided logging func"""
        if not level:
            level = self.level
        uselogger.log(level, str(self.item) + " " + self.template, *self.args)

    def __str__(self):
        return (str(self.item) + " " + self.template) % tuple(self.args)

    @property
    def name(self):
        """Name of class"""
        return self.__class__.__name__


class AsyncFilter[ITEM](abc.ABC):
    """Function object type called by Scanner"""

    def __init__(self, pipeline: AsyncPipeline[ITEM], *_args, **_kwargs) -> None:
        self.pipeline = pipeline

    @abc.abstractmethod
    async def apply(self, recipe: ITEM):
        """Process a recipe

        Raise ``EndProcessingItem`` to skip this item (with a reason),
        or ``EndProcessing`` to terminate the whole run.
        """

    def get_info(self) -> str:
        """Return description of filter for logging"""
        doc = self.__class__.__doc__ or ""
        docline, _, _ = doc.partition("\n")
        return docline

    async def async_init(self) -> None:
        """Called inside loop before processing"""

    def finalize(self) -> None:
        """Called at the end of a run"""


class AsyncPipeline[ITEM]:
    """Processes items in an asyncio pipeline"""

    def __init__(self, threads: int | None = None) -> None:
        #: number of threads to use
        self.threads = threads or threads_to_use()
        #: semaphore to limit io parallelism
        self.io_sem: asyncio.Semaphore = asyncio.Semaphore(1)
        #: must never run more than one conda at the same time
        #: (used by PyPi when running skeleton)
        self.conda_sem: asyncio.Semaphore = asyncio.Semaphore(1)
        #: the filters successively applied to each item
        self.filters: list[AsyncFilter[ITEM]] = []
        #: executor running things in separate python processes
        self.proc_pool_executor = ProcessPoolExecutor(self.threads)

        self._shutting_down = False

    def add(self, filt: type[AsyncFilter[ITEM]], *args, **kwargs) -> None:
        """Adds `Filter` to this `Scanner`"""
        self.filters.append(filt(self, *args, **kwargs))

    def run(self) -> None:
        """Enters the asyncio loop and manages shutdown.

        KeyboardInterrupt (Ctrl-C) and fatal worker errors propagate to
        the caller, so the process exits with a non-zero status. Only
        ``EndProcessing`` -- the filters' deliberate "stop here" signal
        -- terminates the run with a success status.
        """
        try:
            asyncio.run(self._async_run())
            logger.warning("Finished update")
        except* KeyboardInterrupt as eg:
            # asyncio.Runner (used by asyncio.run) turns SIGINT into
            # cancellation of the pipeline, then raises KeyboardInterrupt
            # once everything has unwound. Re-raise the naked exception
            # (bare raise inside except* would wrap it in a group) so
            # plain KeyboardInterrupt handlers keep working and the
            # interpreter exits non-zero instead of reporting success.
            self._shutting_down = True
            logger.error("Ctrl-C pressed - aborting...")

            def first_interrupt(group: BaseExceptionGroup) -> KeyboardInterrupt:
                for exc in group.exceptions:
                    if isinstance(exc, KeyboardInterrupt):
                        return exc
                    if isinstance(exc, BaseExceptionGroup):
                        return first_interrupt(exc)
                raise AssertionError("KeyboardInterrupt subgroup is empty")

            raise first_interrupt(eg)
        except* EndProcessing:
            self._shutting_down = True
            logger.error("Terminating...")
        finally:
            self.proc_pool_executor.shutdown(cancel_futures=True)
            for filt in self.filters:
                filt.finalize()

    @abc.abstractmethod
    async def queue_items(self, send_q, return_q):
        pass

    def get_item_count(self) -> int:
        return 0

    async def _async_run(self) -> None:
        """Runner within async loop"""
        try:
            # call init functions on filters
            async with asyncio.TaskGroup() as tg:
                for filt in self.filters:
                    tg.create_task(filt.async_init())

            # setup queues
            source_q: asyncio.Queue[ITEM] = asyncio.Queue()
            progress_q: asyncio.Queue[ITEM] = asyncio.Queue()
            return_q: asyncio.Queue[ITEM] = asyncio.Queue()

            async with asyncio.TaskGroup() as tg:
                # setup progress monitor
                tg.create_task(self.show_progress(progress_q, return_q))

                # setup workers and produce items from this task while
                # the workers process concurrently. The producer consumes
                # return_q to schedule dependent work, so returning from
                # it means all items were sent and all results handed back
                async with asyncio.TaskGroup() as workers:
                    for _n in range(self.threads):
                        workers.create_task(self.worker(source_q, progress_q))
                    await self.queue_items(source_q, return_q)
                    # tell workers to exit once the queue has drained
                    source_q.shutdown()

                # all items processed; tell the progress monitor to exit
                progress_q.shutdown()
        except asyncio.CancelledError:
            self._shutting_down = True
            raise

    async def show_progress(
        self, in_q: asyncio.Queue[ITEM], out_q: asyncio.Queue[ITEM]
    ) -> None:
        with progress_display.count_task("processing", total=self.get_item_count()) as (
            progress,
            task,
        ):
            while True:
                try:
                    item = await in_q.get()
                except asyncio.QueueShutDown:
                    return
                progress.update(task, advance=1)
                await out_q.put(item)
                in_q.task_done()

    async def worker(
        self, in_q: asyncio.Queue[ITEM], out_q: asyncio.Queue[ITEM]
    ) -> None:
        try:
            while True:
                try:
                    item = await in_q.get()
                except asyncio.QueueShutDown:
                    return
                await self.process(item)
                await out_q.put(item)
                in_q.task_done()
        except asyncio.CancelledError:
            # flag before the unwind continues, so concurrent workers
            # suppress their error logging during the abort (see process)
            self._shutting_down = True
            raise

    async def process(self, item: ITEM) -> bool:
        """Applies the filters to an item

        Returns True if the item passed all filters, False if it failed
        one (logged) or was skipped via EndProcessingItem. Exceptions are
        propagated -- EndProcessingItem (skip reason, handled by process
        overrides), EndProcessing / BrokenExecutor (abort the run).
        """
        try:
            for filt in self.filters:
                await filt.apply(item)
        except asyncio.CancelledError:
            raise
        except EndProcessing:
            self._shutting_down = True
            raise
        except EndProcessingItem as item_error:
            item_error.log(logger)
            raise
        except BrokenExecutor:
            logger.exception("Fatal exception while processing %s", item)
            # can't fix this - if one of the pools is done for, so are we
            raise
        except Exception:  # pylint: disable=broad-except
            if not self._shutting_down:
                logger.exception("While processing %s", item)
            return False
        return True

    async def run_io(self, func, *args):
        """Run **func** in a thread using **args**"""
        async with self.io_sem:
            return await asyncio.to_thread(func, *args)

    async def run_sp(self, func, *args):
        """Run **func** in process pool executor using **args**"""
        return await asyncio.get_running_loop().run_in_executor(
            self.proc_pool_executor, func, *args
        )


class AsyncRequests:
    """Provides helpers for async access to URLs"""

    def __init__(self, cache_file: Path | None = None) -> None:
        #: aiohttp session (only exists while running)
        self.session: aiohttp.ClientSession | None = None
        self.cache_file = cache_file
        #: cache
        self.cache: dict[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> Self:
        session = http.make_session()
        await session.__aenter__()
        self.session = session
        if self.cache_file is not None:
            if self.cache_file.exists():
                cache_data = await asyncio.to_thread(self.cache_file.read_bytes)
                self.cache = pickle.loads(cache_data)
            else:
                self.cache = {}
            for key in ("url_text", "url_checksum", "ftp_list"):
                self.cache.setdefault(key, {})
        return self

    async def __aexit__(self, ext_type, exc, trace):
        assert self.session is not None
        await self.session.__aexit__(ext_type, exc, trace)
        self.session = None
        if self.cache_file is not None:
            cache_data = pickle.dumps(self.cache)
            await asyncio.to_thread(self.cache_file.write_bytes, cache_data)

    @http.retry_on_transient
    async def get_text_from_url(self, url: str) -> str:
        """Fetch content at **url** and return as text

        - On non-permanent errors (429, 502, 503, 504), the GET is attempted up to
          20 times with increasing waits according to the Fibonacci series.
        - Permanent errors raise a ClientResponseError
        """
        if self.cache and url in self.cache["url_text"]:
            return self.cache["url_text"][url]

        assert self.session is not None
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            res = await resp.text()

        if self.cache:
            self.cache["url_text"][url] = res

        return res

    async def get_checksum_from_url(self, url: str, desc: str) -> str:
        """Compute sha256 checksum of content at **url**

        - Shows progress and logs transfer outcomes for HTTP downloads.
        - Caches result
        """
        if self.cache and url in self.cache["url_checksum"]:
            return self.cache["url_checksum"][url]

        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            res = await self.get_checksum_from_http(url, desc)
        elif parsed.scheme == "ftp":
            res = await self.get_checksum_from_ftp(url, desc)

        if self.cache:
            self.cache["url_checksum"][url] = res

        return res

    @http.retry_on_transient
    async def get_checksum_from_http(self, url: str, desc: str) -> str:
        """Compute sha256 checksum of content at http **url**

        Shows progress monitor with label **desc**.
        """
        checksum = sha256()
        assert self.session is not None
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            async with http.stream_download(resp, desc) as blocks:
                async for block in blocks:
                    checksum.update(block)
        return checksum.hexdigest()

    @http.retry_on_transient
    async def get_file_from_url(self, fname: Path, url: str, desc: str) -> None:
        """Fetch file at **url** into **fname**

        Shows progress monitor with label **desc**.
        """
        assert self.session is not None
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            async with (
                aiofiles.open(fname, "wb") as out,
                http.stream_download(resp, desc) as blocks,
            ):
                async for block in blocks:
                    await out.write(block)

    async def get_ftp_listing(self, url):
        """Returns list of files at FTP **url**"""
        logger.debug("FTP: listing %s", url)
        if self.cache and url in self.cache["ftp_list"]:
            return self.cache["ftp_list"][url]

        parsed = urlparse(url)
        async with aioftp.Client.context(
            parsed.netloc, password=http.USER_AGENT + "@", trust_env=True
        ) as client:
            res = [str(path) for path, _info in await client.list(parsed.path)]
        if self.cache:
            self.cache["ftp_list"][url] = res
        return res

    async def get_checksum_from_ftp(self, url, _desc=None):
        """Compute sha256 checksum of content at ftp **url**

        Does not show progress monitor at this time (would need to
        get file size first)
        """
        parsed = urlparse(url)
        checksum = sha256()
        async with (
            aioftp.Client.context(
                parsed.netloc, password=http.USER_AGENT + "@", trust_env=True
            ) as client,
            client.download_stream(parsed.path) as stream,
        ):
            async for block in stream.iter_by_block():
                checksum.update(block)
        return checksum.hexdigest()
