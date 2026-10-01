import asyncio
import logging
from io import StringIO
from typing import cast

import aiohttp
from rich.console import Console

from bioconda_utils.support import http, logsetup
from bioconda_utils.support.progress import ProgressDisplay


def test_make_session_uses_requested_user_agent():
    async def check():
        async with http.make_session(user_agent="custom-agent") as session:
            assert session.headers["User-Agent"] == "custom-agent"

    asyncio.run(check())


def test_make_session_defaults_to_bioconda_user_agent():
    async def check():
        async with http.make_session() as session:
            assert session.headers["User-Agent"] == http.USER_AGENT

    asyncio.run(check())


def test_stream_download_yields_blocks_and_reports_progress(monkeypatch):
    class Content:
        def __init__(self):
            self.blocks = [b"first", b"second", b""]
            self.block_sizes = []

        async def read(self, block_size):
            self.block_sizes.append(block_size)
            return self.blocks.pop(0)

    class Response:
        def __init__(self):
            self.headers = {"Content-Length": "11"}
            self.content = Content()

    display = ProgressDisplay(Console(file=StringIO()))
    monkeypatch.setattr(http, "progress_display", display)
    completed = []
    response = Response()

    async def download():
        blocks = []
        async for block in http.stream_download(
            cast(aiohttp.ClientResponse, response), "artifact", block_size=4
        ):
            task = display.downloads.tasks[0]
            assert task.description == "artifact"
            assert task.total == 11
            completed.append(task.completed)
            blocks.append(block)
        return blocks

    assert asyncio.run(download()) == [b"first", b"second"]
    assert response.content.block_sizes == [4, 4, 4]
    assert completed == [5, 11]
    assert display.downloads.tasks == []


def test_retry_policy_gives_up_only_on_permanent_response_errors():
    request_info = cast(aiohttp.RequestInfo, None)
    permanent = aiohttp.ClientResponseError(request_info, (), status=404)
    transient = aiohttp.ClientResponseError(request_info, (), status=503)

    assert http._give_up_on_http_error(permanent)
    assert not http._give_up_on_http_error(transient)
    assert not http._give_up_on_http_error(aiohttp.ClientPayloadError())


def test_progress_uses_item_columns_for_counts_and_byte_columns_for_downloads():
    from rich.progress import DownloadColumn, MofNCompleteColumn, TransferSpeedColumn

    def column_types(progress):
        return {type(column) for column in progress.columns}

    assert MofNCompleteColumn in column_types(logsetup.progress_display.counts)
    assert DownloadColumn not in column_types(logsetup.progress_display.counts)
    assert TransferSpeedColumn not in column_types(logsetup.progress_display.counts)
    assert DownloadColumn in column_types(logsetup.progress_display.downloads)
    assert TransferSpeedColumn in column_types(logsetup.progress_display.downloads)


def test_progress_track_iterates_headless():
    with logsetup.progress_display.count_task("x", total=2) as (progress, task):
        assert list(progress.track([1, 2], task_id=task)) == [1, 2]


def test_logger_treats_subprocess_output_as_literal_text():
    logger = logsetup.setup_logger("test-literal-logging", logging.INFO)

    # Rich markup would suppress the first value and raise MarkupError for the
    # second one. Logging arbitrary command output must never interpret either.
    logger.info("[not-a-style]")
    logger.info("unmatched closing tag [/bold]")
