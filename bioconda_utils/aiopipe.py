"""Utilities for Asynchronous Processing"""

from __future__ import annotations

import abc
import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Any, Concatenate, Self
from urllib.parse import unquote, urlparse

import aioftp
import aiohttp

from .support import http
from .support.logsetup import progress_display
from .support.parallel import threads_to_use, worker_pool
from .support.upstreamcache import fetch_cached

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class EndProcessing(BaseException):
    """Raised by `AsyncFilter` to tell `AsyncPipeline` to stop processing"""


class UnsupportedUrlScheme(ValueError):
    """Raised when a URL's scheme cannot be fetched

    Only ``http``/``https`` and ``ftp`` are supported. Subclasses
    :class:`ValueError` so that callers can catch either, while still
    remaining distinguishable from unrelated ``ValueError`` errors raised
    deeper in the fetch path.
    """


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

    def __init__(self, item: object, *args) -> None:
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


class AsyncFilter[ITEM, PIPELINE](abc.ABC):
    """Function object type called by Scanner"""

    def __init__(self, pipeline: PIPELINE, *_args, **_kwargs) -> None:
        self._pipeline = pipeline

    @property
    def pipeline(self) -> PIPELINE:
        """The pipeline that owns this filter."""
        return self._pipeline

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
        self.filters: list[AsyncFilter[ITEM, object]] = []
        #: executor running things in separate python processes
        self.proc_pool_executor: ProcessPoolExecutor | None = None

        self._shutting_down = False

    def add[**P](
        self,
        filt: Callable[Concatenate[Self, P], AsyncFilter[ITEM, object]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
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
            with worker_pool(self.threads) as pool:
                self.proc_pool_executor = pool
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
            self.proc_pool_executor = None
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
            self.io_sem = asyncio.Semaphore(1)
            self.conda_sem = asyncio.Semaphore(1)
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
        one with an unexpected exception (logged). Control-flow exceptions are
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
        assert self.proc_pool_executor is not None, (
            "Process jobs require a running pipeline"
        )
        return await asyncio.get_running_loop().run_in_executor(
            self.proc_pool_executor, func, *args
        )


class AsyncRequests:
    """Provides helpers for async access to URLs"""

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        # Reuse within a scan; persistent entries are validated by the HTTP cache.
        self.cache: dict[str, dict[str, Any]] = {
            key: {} for key in ("url_text", "url_checksum", "ftp_list")
        }

    async def __aenter__(self) -> Self:
        self.session = http.make_session()
        # Explicit defaults make Vary matching deterministic before sending a request.
        for name, value in {
            "User-Agent": http.USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
        }.items():
            self.session.headers.setdefault(name, value)
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, trace):
        assert self.session is not None
        await self.session.__aexit__(exc_type, exc, trace)
        self.session = None
        self.cache = {key: {} for key in self.cache}

    @http.retry_on_transient
    async def get_text_from_url(self, url: str) -> str:
        """Fetch content at **url** and return as text

        - On non-permanent errors (429, 502, 503, 504), the GET is attempted up to
          20 times with increasing waits according to the Fibonacci series.
        - Permanent errors raise a ClientResponseError
        """
        if url in self.cache["url_text"]:
            return self.cache["url_text"][url]

        assert self.session is not None
        res = await fetch_cached(self.session, url, "text")

        self.cache["url_text"][url] = res

        return res

    async def get_checksum_from_url(self, url: str, desc: str) -> str:
        """Compute sha256 checksum of content at **url**

        - Shows progress and logs transfer outcomes for HTTP downloads.
        - Caches result
        """
        if url in self.cache["url_checksum"]:
            return self.cache["url_checksum"][url]

        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            res = await self.get_checksum_from_http(url, desc)
        elif parsed.scheme == "ftp":
            res = await self.get_checksum_from_ftp(url, desc)
        else:
            raise UnsupportedUrlScheme(
                f"Cannot compute checksum for {url}: "
                f"unsupported scheme {parsed.scheme!r}"
            )

        self.cache["url_checksum"][url] = res

        return res

    @http.retry_on_transient
    async def get_checksum_from_http(self, url: str, desc: str) -> str:
        """Compute sha256 checksum of content at http **url**

        Shows progress monitor with label **desc**.
        """
        assert self.session is not None
        return await fetch_cached(self.session, url, "checksum", desc)

    @http.retry_on_transient
    async def get_file_from_url(self, fname: Path, url: str, desc: str) -> None:
        """Fetch file at **url** into **fname**

        Shows progress monitor with label **desc**.
        """
        assert self.session is not None
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            await http.download_to_file(resp, fname, desc)

    async def get_ftp_listing(self, url):
        """Returns list of files at FTP **url**"""
        logger.debug("FTP: listing %s", url)
        if url in self.cache["ftp_list"]:
            return self.cache["ftp_list"][url]

        parsed = urlparse(url)
        async with self._ftp_client(url) as client:
            res = [str(path) for path, _info in await client.list(unquote(parsed.path))]
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
            self._ftp_client(url) as client,
            client.download_stream(unquote(parsed.path)) as stream,
        ):
            async for block in stream.iter_by_block():
                checksum.update(block)
        return checksum.hexdigest()

    @staticmethod
    def _ftp_client(url: str):
        parsed = urlparse(url)
        if parsed.scheme != "ftp" or parsed.hostname is None:
            raise ValueError("FTP URL must include a hostname")
        return aioftp.Client.context(
            parsed.hostname,
            port=parsed.port if parsed.port is not None else 21,
            user=unquote(parsed.username)
            if parsed.username is not None
            else "anonymous",
            password=(
                unquote(parsed.password)
                if parsed.password is not None
                else http.USER_AGENT + "@"
            ),
        )
