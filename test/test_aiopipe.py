"""Tests for AsyncPipeline mechanics

These cover the invariants of the asyncio.TaskGroup based pipeline in
:mod:`bioconda_utils.aiopipe`, in particular that raising ``EndProcessing``
from a filter terminates the pipeline promptly (it used to deadlock the
event loop on a ``Queue.join``) and that ``EndProcessingItem`` skips single
items without aborting the run.
"""

import asyncio
import logging
import threading

import pytest

from bioconda_utils.aiopipe import (
    AsyncFilter,
    AsyncPipeline,
    EndProcessing,
    EndProcessingItem,
)

# keep test output readable
logging.getLogger("asyncio").setLevel(logging.WARNING)


class ListPipeline(AsyncPipeline[int]):
    """Feeds a fixed list of items into the pipeline"""

    def __init__(self, items: list[int], threads: int = 3) -> None:
        super().__init__(threads=threads)
        self.items = items

    async def queue_items(self, send_q, return_q) -> None:
        for item in self.items:
            await send_q.put(item)
        # drain the return queue like RecipeSource does
        for _n in range(len(self.items)):
            await return_q.get()
            return_q.task_done()

    def get_item_count(self) -> int:
        return len(self.items)


class Collect(AsyncFilter[int]):
    """Records every item reaching this filter"""

    def __init__(self, pipeline, seen: list[int]) -> None:
        super().__init__(pipeline)
        self.seen = seen

    async def apply(self, recipe: int) -> None:
        self.seen.append(recipe)


class StopAfter(AsyncFilter[int]):
    """Raises EndProcessing once **limit** items were seen"""

    def __init__(self, pipeline, seen: list[int], limit: int) -> None:
        super().__init__(pipeline)
        self.seen = seen
        self.limit = limit

    async def apply(self, recipe: int) -> None:
        self.seen.append(recipe)
        if len(self.seen) >= self.limit:
            raise EndProcessing()


def run_with_watchdog(pipeline: AsyncPipeline, timeout: float = 60.0) -> None:
    """Run the pipeline in a daemon thread so a regression that hangs
    the event loop fails the test instead of hanging the test suite"""
    outcome_done = [False]
    outcome_exc: list[BaseException] = []

    def target() -> None:
        try:
            pipeline.run()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            outcome_exc.append(exc)
        else:
            outcome_done[0] = True

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        pytest.fail(f"pipeline did not terminate within {timeout}s (hang?)")
    if outcome_exc:
        raise outcome_exc[0]


def test_pipeline_processes_all_items() -> None:
    seen: list[int] = []
    finalized: list[bool] = []

    class CollectWithFinalize(AsyncFilter[int]):
        def __init__(self, pipeline) -> None:
            super().__init__(pipeline)

        async def apply(self, recipe: int) -> None:
            pass

        def finalize(self) -> None:
            finalized.append(True)

    pipeline = ListPipeline(list(range(20)))
    pipeline.add(Collect, seen)
    pipeline.add(CollectWithFinalize)

    run_with_watchdog(pipeline)

    assert sorted(seen) == list(range(20))
    assert finalized == [True]


def test_end_processing_terminates_pipeline() -> None:
    """EndProcessing from a filter must stop the pipeline promptly.

    Regression test: this used to keep processing every remaining item,
    then deadlock forever on Queue.join, making --max-updates unusable.
    """
    total = 100
    seen: list[int] = []
    pipeline = ListPipeline(list(range(total)))
    pipeline.add(StopAfter, seen, 3)

    run_with_watchdog(pipeline)

    assert seen, "pipeline stopped before processing anything"
    assert len(seen) < total, "EndProcessing did not stop item processing"


def test_keyboard_interrupt_exits_nonzero(tmp_path) -> None:
    """Ctrl-C must escape run() so the process exits non-zero.

    Regression test: run() used to swallow KeyboardInterrupt, making
    interrupted runs report success (exit code 0) to the shell. Runs a
    real pipeline in a subprocess and SIGINTs it mid-flight, because
    signals cannot be tested safely inside the pytest process itself.
    """
    import subprocess
    import sys

    script = tmp_path / "sigint_pipeline.py"
    script.write_text(
        """
import asyncio, os, signal, sys, threading, time
from bioconda_utils.aiopipe import AsyncFilter, AsyncPipeline

seen = []
first_item = threading.Event()
sigint_sent = threading.Event()

class Collect(AsyncFilter):
    async def apply(self, recipe):
        seen.append(recipe)
        first_item.set()
        if len(seen) == 1:
            # hold the pipeline open until the SIGINT was sent, so the
            # interrupt lands inside the running event loop
            while not sigint_sent.is_set():
                await asyncio.sleep(0.05)

class Pipeline(AsyncPipeline):
    def __init__(self):
        super().__init__(threads=2)
        self.items = list(range(100))

    async def queue_items(self, send_q, return_q):
        for i in self.items:
            await send_q.put(i)
        for _ in self.items:
            await return_q.get()
            return_q.task_done()

    def get_item_count(self):
        return len(self.items)

def send_sigint():
    if not first_item.wait(30):
        raise RuntimeError("pipeline never processed an item")
    os.kill(os.getpid(), signal.SIGINT)
    sigint_sent.set()

pipeline = Pipeline()
pipeline.add(Collect, seen)
threading.Thread(target=send_sigint, daemon=True).start()
try:
    pipeline.run()
except KeyboardInterrupt:
    print(f"KI-RAISED processed={len(seen)}")
    raise
"""
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0, (
        f"interrupted pipeline exited successfully; stderr:\n{proc.stderr}"
    )
    # the child must have been interrupted *by our signal*, not crashed
    assert "KI-RAISED" in proc.stdout, (
        f"pipeline did not handle SIGINT; stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "KI-RAISED processed=0" not in proc.stdout


def test_end_processing_item_skips_single_item() -> None:
    class SkipOdd(AsyncFilter[int]):
        async def apply(self, recipe: int) -> None:
            if recipe % 2:
                raise EndProcessingItem(recipe, "odd")

    class ScannerLike(ListPipeline):
        """mirrors autobump.Scanner.process exception handling"""

        def __init__(self, items: list[int]) -> None:
            super().__init__(items)
            self.skipped: list[int] = []

        async def process(self, item: int) -> bool:
            try:
                return await AsyncPipeline.process(self, item)
            except EndProcessingItem:
                self.skipped.append(item)
                return False

    seen: list[int] = []
    pipeline = ScannerLike(list(range(10)))
    pipeline.add(SkipOdd)
    pipeline.add(Collect, seen)

    run_with_watchdog(pipeline)

    assert sorted(seen) == [0, 2, 4, 6, 8]
    assert sorted(pipeline.skipped) == [1, 3, 5, 7, 9]


def test_other_filter_errors_fail_the_run() -> None:
    """BrokenExecutor (a dead process pool) is fatal: it escapes the
    per-item error handling and fails the whole run"""

    from concurrent.futures import BrokenExecutor

    class Explode(AsyncFilter[int]):
        async def apply(self, recipe: int) -> None:
            if recipe == 1:
                raise BrokenExecutor()

    pipeline = ListPipeline(list(range(10)), threads=1)
    pipeline.add(Explode)

    # fatal errors propagate out of run(), wrapped in an
    # ExceptionGroup by the TaskGroup
    with pytest.raises(ExceptionGroup) as info:
        run_with_watchdog(pipeline)
    assert info.value.subgroup(BrokenExecutor) is not None


def test_producer_feedback_loop() -> None:
    """A producer that only sends the next item after the previous one
    came back (like RecipeGraphSource) completes"""

    class ChainPipeline(ListPipeline):
        async def queue_items(self, send_q, return_q) -> None:
            await send_q.put(0)
            sent, done = 1, 0
            while done < len(self.items):
                await return_q.get()
                return_q.task_done()
                done += 1
                if sent < len(self.items):
                    await send_q.put(sent)
                    sent += 1

    seen: list[int] = []
    pipeline = ChainPipeline(list(range(10)))
    pipeline.add(Collect, seen)

    run_with_watchdog(pipeline)

    assert sorted(seen) == list(range(10))


def test_async_requests_cache_init_uses_setdefault(tmp_path) -> None:
    """cache files lacking some sections are completed on load"""
    import pickle

    from bioconda_utils.aiopipe import AsyncRequests

    cache_fn = tmp_path / "cache.pkl"
    cache_fn.write_bytes(pickle.dumps({"url_text": {"u": "x"}}))

    async def check():
        async with AsyncRequests(cache_fn) as req:
            assert req.cache is not None
            assert req.cache["url_text"] == {"u": "x"}
            assert req.cache["url_checksum"] == {}
            assert req.cache["ftp_list"] == {}

    asyncio.run(check())
