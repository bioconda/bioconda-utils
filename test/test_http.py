import asyncio
import logging
from hashlib import sha256
from io import StringIO
from typing import cast

import aiohttp
import pytest
from aiohttp import web
from rich.console import Console
from rich.logging import RichHandler

from bioconda_utils.support import http, logsetup
from bioconda_utils.support.progress import ProgressDisplay


@pytest.fixture
def download_display(monkeypatch, caplog):
    display = ProgressDisplay(Console(file=StringIO()))
    monkeypatch.setattr(http, "progress_display", display)
    caplog.set_level(logging.INFO, logger=http.__name__)
    yield display
    assert display.downloads.tasks == []


class ResponseBody:
    def __init__(self, blocks, headers=None):
        self.headers = headers or {}
        self.content = self
        self.blocks = iter(blocks)

    async def read(self, _size):
        block = next(self.blocks, b"")
        if isinstance(block, BaseException):
            raise block
        return block


def download_messages(caplog):
    return [
        record.getMessage() for record in caplog.records if record.name == http.__name__
    ]


@pytest.mark.parametrize(
    ("headers", "expected_total"),
    [
        ({}, None),
        ({"Content-Length": "11"}, 11),
        ({"Content-Length": "5", "Content-Encoding": "gzip"}, None),
    ],
)
def test_download_logs_completion_and_actual_body_size(
    download_display, caplog, monkeypatch, headers, expected_total
):
    clock = iter([10.0, 12.5])
    monkeypatch.setattr(http, "monotonic", lambda: next(clock))
    response = cast(
        aiohttp.ClientResponse, ResponseBody([b"first", b"second"], headers)
    )

    async def run():
        async with http.stream_download(response, "artifact") as blocks:
            assert download_display.downloads.tasks[0].total == expected_total
            assert b"".join([block async for block in blocks]) == b"firstsecond"
            # Reaching EOF alone does not imply successful consumption: the
            # caller's scope must also finish without an exception.
            assert download_messages(caplog) == ["Downloading artifact"]
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert download_messages(caplog) == [
        "Downloading artifact",
        "Downloaded artifact: 11 bytes in 2.5 s",
    ]


def test_empty_download_logs_zero_bytes(download_display, caplog):
    response = cast(aiohttp.ClientResponse, ResponseBody([], {"Content-Length": "0"}))

    async def run():
        async with http.stream_download(response, "empty") as blocks:
            assert download_display.downloads.tasks[0].total == 0
            assert [block async for block in blocks] == []

    asyncio.run(run())
    assert download_messages(caplog)[1].startswith("Downloaded empty: 0 bytes in ")


def test_truncated_download_does_not_log_success(download_display, caplog):
    error = aiohttp.ClientPayloadError("truncated response")
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first", error]))

    async def run():
        with pytest.raises(aiohttp.ClientPayloadError, match="truncated response"):
            async with http.stream_download(response, "artifact") as blocks:
                async for _block in blocks:
                    pass
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert download_messages(caplog) == [
        "Downloading artifact",
        "Download failed: artifact (5 bytes received): truncated response",
    ]


@pytest.mark.parametrize("consume_eof", [False, True])
def test_consumer_failure_does_not_log_success(download_display, caplog, consume_eof):
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first"]))

    async def run():
        with pytest.raises(OSError, match="disk full"):
            async with http.stream_download(response, "artifact") as blocks:
                if consume_eof:
                    _ = [block async for block in blocks]
                else:
                    assert await anext(blocks) == b"first"
                raise OSError("disk full")
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert (
        download_messages(caplog)[-1]
        == "Download failed: artifact (5 bytes received): disk full"
    )
    assert not any(
        message.startswith("Downloaded ") for message in download_messages(caplog)
    )


def test_early_stop_closes_iterator_and_task_immediately(download_display, caplog):
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first", b"second"]))

    async def run():
        async with http.stream_download(response, "artifact") as blocks:
            assert await anext(blocks) == b"first"
        assert download_display.downloads.tasks == []
        with pytest.raises(StopAsyncIteration):
            await anext(blocks)

    asyncio.run(run())
    assert download_messages(caplog) == [
        "Downloading artifact",
        "Download stopped: artifact (5 bytes received)",
    ]


@pytest.mark.parametrize("in_consumer", [False, True])
def test_cancelled_download_is_logged_and_cleaned_up(
    download_display, caplog, in_consumer
):
    async def run():
        started = asyncio.Event()
        never = asyncio.Event()

        class Response:
            def __init__(self):
                self.headers = {}

            @property
            def content(self):
                return self

            async def read(self, _size):
                if in_consumer:
                    return b"first"
                started.set()
                await never.wait()

        async def consume():
            async with http.stream_download(
                cast(aiohttp.ClientResponse, Response()), "artifact"
            ) as blocks:
                async for _block in blocks:
                    started.set()
                    await never.wait()

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    size = 5 if in_consumer else 0
    assert download_messages(caplog) == [
        "Downloading artifact",
        f"Download cancelled: artifact ({size} bytes received)",
    ]


def test_download_logs_are_permanent_with_transient_progress(
    download_display, monkeypatch
):
    output = download_display.live.console.file
    handler = RichHandler(
        console=download_display.live.console, show_time=False, show_path=False
    )
    monkeypatch.setattr(http.logger, "handlers", [handler])
    monkeypatch.setattr(http.logger, "propagate", False)
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first"]))

    async def run():
        async with http.stream_download(response, "artifact") as blocks:
            _ = [block async for block in blocks]

    with download_display.live:
        asyncio.run(run())
    text = output.getvalue()
    assert text.count("Downloading artifact") == 1
    assert text.count("Downloaded artifact: 5 bytes") == 1
    assert "━" not in text


@pytest.mark.parametrize("consumer", ["checksum", "file", "repodata"])
def test_download_callers_retry_truncated_bodies(
    download_display, caplog, tmp_path, consumer
):
    from bioconda_utils.aiopipe import AsyncRequests
    from bioconda_utils.conda.repodata import async_fetch

    async def run():
        attempts = 0

        async def serve(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                response = web.StreamResponse(headers={"Content-Length": "11"})
                await response.prepare(request)
                await response.write(b"first")
                assert request.transport is not None
                request.transport.close()
                return response
            return web.Response(body=b"firstsecond")

        app = web.Application()
        app.router.add_get("/artifact", serve)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            url = f"http://127.0.0.1:{runner.addresses[0][1]}/artifact"
            if consumer == "repodata":
                assert await async_fetch([url], ["artifact"]) == [b"firstsecond"]
            else:
                async with AsyncRequests() as requests:
                    if consumer == "checksum":
                        assert (
                            await requests.get_checksum_from_http(url, "artifact")
                            == sha256(b"firstsecond").hexdigest()
                        )
                    else:
                        path = tmp_path / "artifact"
                        await requests.get_file_from_url(str(path), url, "artifact")
                        assert path.read_bytes() == b"firstsecond"
            assert attempts == 2
        finally:
            await runner.cleanup()

    asyncio.run(run())
    messages = download_messages(caplog)
    assert messages.count("Downloading artifact") == 2
    assert (
        sum(message.startswith("Download failed: artifact") for message in messages)
        == 1
    )
    assert (
        sum(message.startswith("Downloaded artifact: 11 bytes") for message in messages)
        == 1
    )


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
        async with http.stream_download(
            cast(aiohttp.ClientResponse, response), "artifact", block_size=4
        ) as stream:
            async for block in stream:
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
