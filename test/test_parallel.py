"""Process workers are independent of the parent's terminal threads."""

import logging
import os
import threading
from multiprocessing import get_start_method

import pytest

from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.support import logsetup
from bioconda_utils.support.parallel import parallel_iter, worker_pool


def worker_info(_item=None):
    logging.getLogger("bioconda_utils.worker_test").warning(
        "Worker [linux-64] [/literal]"
    )
    return (
        os.getpid(),
        get_start_method(),
        RepoData.config,
        str(RepoData.get_cache_dir()),
    )


def test_worker_logging_cannot_inherit_locked_console(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda"]})
    held, release = threading.Event(), threading.Event()

    def hold_console():
        with logsetup.err_console._lock:
            held.set()
            release.wait(30)

    thread = threading.Thread(target=hold_console)
    thread.start()
    assert held.wait(5)
    try:
        with worker_pool(1) as pool:
            try:
                pid, method, config, cache_dir = pool.submit(worker_info).result(
                    timeout=20
                )
                assert pid != os.getpid()
                assert method == "spawn"
                assert config == {"channels": ["bioconda"]}
                assert cache_dir == str(RepoData.get_cache_dir())
            finally:
                release.set()
                thread.join(5)
        assert "Worker [linux-64] [/literal]" in caplog.text
    finally:
        release.set()
        thread.join(5)


def test_parallel_iteration_uses_initialized_workers(monkeypatch):
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda"]})
    with logsetup.progress_display.live:
        (result,) = parallel_iter(worker_info, [1], "Processing")
    assert result[0] != os.getpid()
    assert result[1] == "spawn"
    assert result[2] == {"channels": ["bioconda"]}


def test_async_pipeline_uses_spawned_workers(monkeypatch, caplog):
    from bioconda_utils.aiopipe import AsyncFilter, AsyncPipeline

    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda"]})
    results = []

    class ProcessFilter(AsyncFilter):
        async def apply(self, recipe):
            results.append(await self.pipeline.run_sp(worker_info, recipe))

    class Pipeline(AsyncPipeline):
        async def queue_items(self, send_q, return_q):
            await send_q.put(1)
            await return_q.get()
            return_q.task_done()

        def get_item_count(self):
            return 1

    pipeline = Pipeline(threads=1)
    pipeline.add(ProcessFilter)
    with logsetup.progress_display.live:
        pipeline.run()
    assert len(results) == 1
    assert results[0][0] != os.getpid()
    assert results[0][1] == "spawn"
    assert "Worker [linux-64] [/literal]" in caplog.text


def crash_worker():
    os._exit(23)


def test_worker_crash_propagates():
    from concurrent.futures.process import BrokenProcessPool

    import pytest

    with worker_pool(1) as pool, pytest.raises(BrokenProcessPool):
        pool.submit(crash_worker).result(timeout=20)


@pytest.mark.parametrize("kill", [False, True])
def test_workers_exit_when_parent_is_terminated(tmp_path, kill):
    import subprocess
    import sys
    import time

    import psutil

    marker = tmp_path / "worker.pid"
    script = """
import os, time
from pathlib import Path
from bioconda_utils.support.parallel import worker_pool
with worker_pool(1) as pool:
    pid = pool.submit(os.getpid).result(timeout=20)
    Path(__import__('sys').argv[1]).write_text(str(pid))
    pool.submit(time.sleep, 120).result()
"""
    parent = subprocess.Popen([sys.executable, "-c", script, str(marker)])
    worker = None
    try:
        deadline = time.monotonic() + 25
        while (
            not marker.exists()
            and parent.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert marker.exists()
        worker = psutil.Process(int(marker.read_text()))
        (parent.kill if kill else parent.terminate)()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while (
            worker.is_running()
            and worker.status() != psutil.STATUS_ZOMBIE
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not worker.is_running() or worker.status() == psutil.STATUS_ZOMBIE
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if worker is not None and worker.is_running():
            try:
                worker.kill()
            except psutil.NoSuchProcess:
                pass
