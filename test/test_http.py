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
    monkeypatch.setattr(http, "monotonic", lambda: 10.0)
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
        ({"Content-Length": "invalid"}, None),
        ({"Content-Length": "-1"}, None),
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
        chunks = []

        def sink(block):
            assert download_display.downloads.tasks[0].total == expected_total
            chunks.append(block)

        bytes_count = await http.stream_to_sink(response, "artifact", sink)
        assert bytes_count == 11
        assert b"".join(chunks) == b"firstsecond"
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert download_messages(caplog) == [
        "Downloading artifact",
        "Downloaded artifact: 11 bytes in 2.5 s",
    ]


def test_empty_download_logs_zero_bytes(download_display, caplog):
    response = cast(aiohttp.ClientResponse, ResponseBody([], {"Content-Length": "0"}))

    async def run():
        res = await http.download_to_bytes(response, "empty")
        assert res == b""

    asyncio.run(run())
    assert download_messages(caplog)[1].startswith("Downloaded empty: 0 bytes in ")


@pytest.mark.parametrize("block_size", [0, -1])
def test_invalid_block_size_does_not_start_download(
    download_display, caplog, block_size
):
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first"]))

    async def run():
        with pytest.raises(ValueError, match="block_size must be positive"):
            await http.download_to_bytes(response, "artifact", block_size=block_size)

    asyncio.run(run())
    assert download_messages(caplog) == []


def test_truncated_download_does_not_log_success(download_display, caplog):
    error = aiohttp.ClientPayloadError("truncated response")
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first", error]))

    async def run():
        with pytest.raises(aiohttp.ClientPayloadError, match="truncated response"):
            await http.download_to_bytes(response, "artifact")
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert download_messages(caplog) == [
        "Downloading artifact",
        "Download failed: artifact (5 bytes received in 0.0 s): truncated response",
    ]


def test_consumer_failure_does_not_log_success(download_display, caplog):
    response = cast(aiohttp.ClientResponse, ResponseBody([b"first"]))

    def failing_sink(_block):
        raise OSError("disk full")

    async def run():
        with pytest.raises(OSError, match="disk full"):
            await http.stream_to_sink(response, "artifact", failing_sink)
        assert download_display.downloads.tasks == []

    asyncio.run(run())
    assert (
        download_messages(caplog)[-1]
        == "Download failed: artifact (5 bytes received in 0.0 s): disk full"
    )
    assert not any(
        message.startswith("Downloaded ") for message in download_messages(caplog)
    )


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

        async def sink(block):
            started.set()
            await never.wait()

        async def consume():
            await http.stream_to_sink(
                cast(aiohttp.ClientResponse, Response()),
                "artifact",
                sink if in_consumer else lambda _b: None,
            )

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
        f"Download cancelled: artifact ({size} bytes received in 0.0 s)",
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
        await http.download_to_bytes(response, "artifact")

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
                        await requests.get_file_from_url(path, url, "artifact")
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


def test_stream_to_sink_reports_progress(monkeypatch):
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

    def sink(block):
        task = display.downloads.tasks[0]
        assert task.description == "artifact"
        assert task.total == 11
        completed.append(task.completed)

    async def download():
        return await http.stream_to_sink(
            cast(aiohttp.ClientResponse, response), "artifact", sink, block_size=4
        )

    assert asyncio.run(download()) == 11
    assert response.content.block_sizes == [4, 4, 4]
    assert completed == [5, 11]
    assert display.downloads.tasks == []


def test_download_to_file(tmp_path):
    response = cast(aiohttp.ClientResponse, ResponseBody([b"hello ", b"world"]))
    dest = tmp_path / "out.txt"
    bytes_count = asyncio.run(http.download_to_file(response, dest, "file download"))
    assert bytes_count == 11
    assert dest.read_bytes() == b"hello world"
    assert not (tmp_path / "out.txt.part").exists()


def test_failed_download_to_file_leaves_no_partial_target(tmp_path):
    error = aiohttp.ClientPayloadError("truncated response")
    response = cast(aiohttp.ClientResponse, ResponseBody([b"partial", error]))
    dest = tmp_path / "out.txt"

    async def run():
        with pytest.raises(aiohttp.ClientPayloadError, match="truncated response"):
            await http.download_to_file(response, dest, "file download")

    asyncio.run(run())
    assert not dest.exists()
    assert not (tmp_path / "out.txt.part").exists()


def test_failed_download_to_file_keeps_existing_target(tmp_path):
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"previous")
    error = aiohttp.ClientPayloadError("truncated response")
    response = cast(aiohttp.ClientResponse, ResponseBody([b"partial", error]))

    async def run():
        with pytest.raises(aiohttp.ClientPayloadError):
            await http.download_to_file(response, dest, "file download")

    asyncio.run(run())
    assert dest.read_bytes() == b"previous"
    assert not (tmp_path / "out.txt.part").exists()


def test_concurrent_downloads_to_same_target(tmp_path):
    """Concurrent workers may fetch the same destination (shared src cache)."""
    dest = tmp_path / "out.txt"

    class YieldingBody(ResponseBody):
        """Body that lets the other download run between reads."""

        async def read(self, _size):
            await asyncio.sleep(0)
            return await super().read(_size)

    async def run():
        first = cast(aiohttp.ClientResponse, YieldingBody([b"hello ", b"world"]))
        second = cast(aiohttp.ClientResponse, YieldingBody([b"hello ", b"world"]))
        return await asyncio.gather(
            http.download_to_file(first, dest, "first"),
            http.download_to_file(second, dest, "second"),
        )

    assert asyncio.run(run()) == [11, 11]
    assert dest.read_bytes() == b"hello world"
    assert list(tmp_path.iterdir()) == [dest]


def test_download_to_checksum():
    response = cast(aiohttp.ClientResponse, ResponseBody([b"hello ", b"world"]))
    digest = asyncio.run(http.download_to_checksum(response, "hash download"))
    assert digest == sha256(b"hello world").hexdigest()


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


@pytest.mark.parametrize("descriptions", [[], ["first"], ["", "second"]])
def test_async_fetch_defaults_missing_descriptions(monkeypatch, descriptions):
    from bioconda_utils.conda import repodata

    seen = {}

    async def fetch_one(session, url, description, **kwargs):
        seen[url] = description
        return b"data"

    monkeypatch.setattr(repodata, "_async_fetch_one", fetch_one)
    urls = ["https://example.com/first", "https://example.com/second"]
    results = asyncio.run(repodata.async_fetch(urls, descriptions))
    assert results == [b"data", b"data"]
    assert seen == {
        url: descriptions[i] or url if i < len(descriptions) else url
        for i, url in enumerate(urls)
    }


def test_async_fetch_cancels_pending_tasks_on_failure(monkeypatch):
    from bioconda_utils.conda import repodata

    cancelled = asyncio.Event()

    async def slow_fetch(session, url, description, **kwargs):
        if url == "fail":
            raise RuntimeError("task failed")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(repodata, "_async_fetch_one", slow_fetch)
    with pytest.raises(RuntimeError, match="task failed"):
        asyncio.run(repodata.async_fetch(["https://example.com/slow", "fail"]))
    assert cancelled.is_set()


def test_async_fetch_ignores_excess_descriptions_and_metadata(monkeypatch):
    from bioconda_utils.conda import repodata

    fetched = []

    async def mock_fetch_one(session, url, description, **kwargs):
        fetched.append(url)
        return b"data"

    monkeypatch.setattr(repodata, "_async_fetch_one", mock_fetch_one)
    urls = ["https://example.com/only"]
    descriptions = ["first", "excess"]
    results = asyncio.run(repodata.async_fetch(urls, descriptions))
    assert results == [b"data"]
    assert fetched == ["https://example.com/only"]


def test_retry_budget_includes_backoff_waits(monkeypatch, caplog):
    monkeypatch.setattr(http, "HTTP_OPERATION_TIMEOUT", 0.02)
    monkeypatch.setattr("random.uniform", lambda low, high: high)
    attempts = []

    @http.retry_on_transient
    async def unavailable():
        attempts.append(1)
        raise aiohttp.ClientPayloadError("incomplete")

    with pytest.raises(TimeoutError):
        asyncio.run(unavailable())
    assert len(attempts) == 1
    assert "exceeded" in caplog.text


def test_retry_budget_includes_unresponsive_request(monkeypatch):
    monkeypatch.setattr(http, "HTTP_OPERATION_TIMEOUT", 0.02)

    @http.retry_on_transient
    async def unresponsive():
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        asyncio.run(unresponsive())
