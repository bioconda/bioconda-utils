"""
Helpers for parallel iteration over recipes and other work items.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from functools import partial
from logging.handlers import QueueHandler, QueueListener
from multiprocessing import get_context, parent_process
from multiprocessing.connection import wait
from pathlib import Path

from .logsetup import progress_display

_max_threads = 1


def set_max_threads(n):
    global _max_threads
    _max_threads = n


def threads_to_use():
    """Returns the number of cores we are allowed to run on"""
    if hasattr(os, "sched_getaffinity"):
        cores = len(os.sched_getaffinity(0))
    else:
        cores = os.cpu_count()
    return min(_max_threads, cores)


class _ParentLogHandler(logging.Handler):
    """Dispatch worker records through the parent's ordinary logging setup."""

    def emit(self, record: logging.LogRecord) -> None:
        logging.getLogger(record.name).handle(record)


def _exit_with_parent() -> None:
    """Do not retain a worker's memory or queued jobs after its parent dies."""
    parent = parent_process()
    assert parent is not None
    wait([parent.sentinel])
    # Finalizers can block on queues whose readers died with the parent.
    os._exit(1)


def _initialize_worker(
    queue,
    config,
    cache_dir: Path,
    cache_root: Path,
    cache_timeout: float,
    refresh_after,
    threads: int,
    loglevel: int,
) -> None:
    from ..conda.repodata import RepoData
    from .caching import configure_cache_root

    threading.Thread(target=_exit_with_parent, daemon=True).start()
    configure_cache_root(cache_root)
    # Ctrl-C belongs to the parent. Workers never render Rich output or inherit
    # parent threads/locks: both process pools use a fresh spawn context.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    RepoData.register_config(config)
    RepoData.configure_cache(cache_dir)
    RepoData.cache_timeout = cache_timeout
    RepoData.refresh_after = refresh_after
    set_max_threads(threads)
    root = logging.getLogger()
    root.handlers[:] = [QueueHandler(queue)]
    root.setLevel(loglevel)


@contextmanager
def worker_pool(workers: int) -> Iterator[ProcessPoolExecutor]:
    """Start independent workers with explicit configuration and parent logging."""
    from ..conda.repodata import RepoData
    from .caching import get_cache_root

    context = get_context("spawn")
    queue = context.Queue()
    listener = QueueListener(queue, _ParentLogHandler())
    listener.start()
    try:
        pool = ProcessPoolExecutor(
            workers,
            mp_context=context,
            initializer=_initialize_worker,
            initargs=(
                queue,
                RepoData.config,
                RepoData.get_cache_dir(),
                get_cache_root(),
                RepoData.cache_timeout,
                RepoData.refresh_after,
                workers,
                logging.getLogger().level,
            ),
        )
        try:
            yield pool
        finally:
            pool.shutdown(cancel_futures=True)
    finally:
        listener.stop()
        queue.close()
        queue.join_thread()


def parallel_iter(func, items, description, *args, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    with (
        worker_pool(threads_to_use()) as pool,
        progress_display.count_task(description, total=len(items)) as (progress, task),
    ):
        futures = [pool.submit(pfunc, item) for item in items]
        for future in progress.track(
            as_completed(futures), total=len(items), task_id=task
        ):
            yield future.result()
