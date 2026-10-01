"""Progress lifetimes may overlap without following stack order."""

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from multiprocessing import get_all_start_methods, get_context
from threading import Event
from typing import cast

import aiohttp
import pytest
from rich.console import Console
from rich.progress import Progress

from bioconda_utils.support import http, logsetup
from bioconda_utils.support.progress import ProgressDisplay


@pytest.fixture
def display(monkeypatch):
    stdout, stderr = sys.stdout, sys.stderr
    configured = ProgressDisplay(Console(file=StringIO(), force_terminal=True))
    # All application modules share this instance; replace its configuration
    # rather than individual modules' references to it.
    display = logsetup.progress_display
    for name in ("counts", "downloads", "statuses", "live"):
        monkeypatch.setattr(display, name, getattr(configured, name))
    with display.live:
        yield display
    assert (
        display.counts.tasks == display.downloads.tasks == display.statuses.tasks == []
    )
    assert display.live.console._live_stack == []
    assert sys.stdout is stdout
    assert sys.stderr is stderr


def rendered(display):
    output = StringIO()
    Console(file=output, width=160).print(display.live.renderable)
    return output.getvalue()


def test_overlapping_tasks_finish_in_start_order(display):
    outer = display.count_task("processing", total=2)
    first = display.download_task("first download", total=10)
    second = display.download_task("second download", total=20)
    outer.__enter__()
    first_progress, _ = first.__enter__()
    second_progress, _ = second.__enter__()
    assert first_progress is second_progress is display.downloads
    for progress in (display.counts, display.downloads, display.statuses):
        assert type(progress) is Progress
        assert not progress.live.is_started
    assert display.live.console._live_stack == [display.live]
    assert "first download" in rendered(display)
    assert "second download" in rendered(display)
    first.__exit__(None, None, None)
    assert "first download" not in rendered(display)
    assert "second download" in rendered(display)
    outer.__exit__(None, None, None)
    assert display.live.is_started
    assert "processing" not in rendered(display)
    assert "second download" in rendered(display)
    second.__exit__(None, None, None)


def test_finished_tasks_do_not_leave_permanent_output():
    output = StringIO()
    display = ProgressDisplay(Console(file=output, width=160))
    with display.live, display.count_task("outer", total=1):
        for description in ("first run", "second run"):
            with display.count_task(description, total=1) as (progress, task):
                progress.update(task, advance=1)
        with display.download_task("unknown size") as (progress, task):
            progress.update(task, advance=10)
        assert display.downloads.tasks == []
    assert output.getvalue() == ""
    assert display.counts.tasks == []
    assert not display.live.is_started


def test_tracking_updates_counts_while_sharing_renderer(display):
    with (
        display.count_task("processing", total=1),
        display.count_task("loading", total=5) as (progress, task),
    ):
        assert list(progress.track(range(5), task_id=task)) == list(range(5))
        assert progress.tasks[1].completed == 5
        assert progress.tasks[1].finished
        assert display.live.console._live_stack == [display.live]
    assert display.counts.tasks == []


def test_status_is_transient_and_shares_renderer(display):
    with display.count_task("processing", total=1):
        with display.status("command [literal]"):
            assert display.live.console._live_stack == [display.live]
            assert "command [literal]" in rendered(display)
        assert "command" not in rendered(display)


def test_status_does_not_leave_output_headless():
    output = StringIO()
    display = ProgressDisplay(Console(file=output))
    with display.live, display.status("running"):
        pass
    assert output.getvalue() == ""


def test_task_does_not_start_renderer():
    display = ProgressDisplay(Console(file=StringIO()))
    with display.count_task("processing", total=1) as (progress, task):
        progress.update(task, advance=1)
        assert not display.live.is_started
        assert not progress.live.is_started
    assert display.counts.tasks == []


class DownloadResponse:
    def __init__(self):
        self.headers = {"Content-Length": "1"}
        self.content = self
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.sent = False

    async def read(self, _size):
        self.started.set()
        await self.finish.wait()
        if self.sent:
            return b""
        self.sent = True
        return b"x"


async def download(response, description):
    return [
        block
        async for block in http.stream_download(
            cast(aiohttp.ClientResponse, response), description
        )
    ]


@pytest.mark.parametrize("cancel_second", [False, True])
def test_concurrent_downloads_cleanup_independently(display, cancel_second):
    async def run():
        first, second = DownloadResponse(), DownloadResponse()
        async with asyncio.TaskGroup() as tasks:
            first_task = tasks.create_task(download(first, "first download"))
            await first.started.wait()
            second_task = tasks.create_task(download(second, "second download"))
            await second.started.wait()
            first.finish.set()
            assert await first_task == [b"x"]
            assert "first download" not in rendered(display)
            assert "second download" in rendered(display)
            if cancel_second:
                second_task.cancel()
            else:
                second.finish.set()
                assert await second_task == [b"x"]
        assert display.downloads.tasks == []
        assert display.live.is_started

    asyncio.run(run())


def test_progress_exception_cleans_up(display):
    with (
        pytest.raises(ValueError, match="failed"),
        display.count_task("processing", total=1),
    ):
        raise ValueError("failed")
    assert display.counts.tasks == []


def test_thread_progress_can_outlive_async_progress(display):
    started, finish = Event(), Event()

    def threaded_work():
        with display.download_task("thread download", total=1):
            started.set()
            assert finish.wait(timeout=5)

    with ThreadPoolExecutor(1) as executor:
        try:
            with display.count_task("processing", total=1):
                work = executor.submit(threaded_work)
                assert started.wait(timeout=5)
            assert display.live.is_started
            assert "thread download" in rendered(display)
        finally:
            finish.set()
        work.result(timeout=5)


def worker_progress():
    output = StringIO()
    display = ProgressDisplay(Console(file=output, force_terminal=True))
    with display.count_task("worker", total=2) as (progress, task):
        list(progress.track([1, 2], task_id=task))
        return progress.disable, display.live.is_started, output.getvalue()


@pytest.mark.parametrize("start_method", ["spawn", "fork"])
def test_process_worker_leaves_terminal_progress_to_parent(start_method, display):
    # Spawn imports the module afresh, so this also checks that the policy does
    # not depend on inheriting the parent's renderer or console.
    if start_method not in get_all_start_methods():
        pytest.skip(f"{start_method} is not supported")
    with display.count_task("processing", total=1):
        with get_context(start_method).Pool(1) as pool:
            assert pool.apply(worker_progress) == (True, False, "")
        assert display.live.is_started
