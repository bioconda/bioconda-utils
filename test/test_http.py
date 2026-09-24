import asyncio
import logging
from typing import cast

import aiohttp

from bioconda_utils.aiopipe import AsyncRequests as PipelineRequests
from bioconda_utils.conda.repodata import AsyncRequests as RepodataRequests
from bioconda_utils.support import http, logsetup


def test_make_session_uses_requested_user_agent():
    async def check():
        async with http.make_session(user_agent="custom-agent") as session:
            assert session.headers["User-Agent"] == "custom-agent"

    asyncio.run(check())


def test_pipeline_requests_preserves_user_agent_override():
    class CustomRequests(PipelineRequests):
        USER_AGENT = "custom-pipeline-agent"

    async def check():
        async with CustomRequests() as requests:
            assert requests.session is not None
            assert requests.session.headers["User-Agent"] == CustomRequests.USER_AGENT

    asyncio.run(check())


def test_repodata_requests_preserves_user_agent_override(monkeypatch):
    class CustomRequests(RepodataRequests):
        USER_AGENT = "custom-repodata-agent"

    original_make_session = http.make_session
    observed_user_agents = []

    def make_session(**kwargs):
        observed_user_agents.append(kwargs["user_agent"])
        return original_make_session(**kwargs)

    monkeypatch.setattr(http, "make_session", make_session)
    asyncio.run(CustomRequests.async_fetch([]))

    assert observed_user_agents == [CustomRequests.USER_AGENT]


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

    class Progress:
        def __init__(self):
            self.updates = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def update(self, size):
            self.updates.append(size)

    progress = Progress()
    progress_options = {}

    def progress_factory(**kwargs):
        progress_options.update(kwargs)
        return progress

    monkeypatch.setattr(http, "tqdm", progress_factory)
    response = Response()

    async def download():
        return [
            block
            async for block in http.stream_download(
                cast(aiohttp.ClientResponse, response),
                "artifact",
                block_size=4,
                leave=False,
                disable=True,
            )
        ]

    assert asyncio.run(download()) == [b"first", b"second"]
    assert response.content.block_sizes == [4, 4, 4]
    assert progress.updates == [5, 6]
    assert progress_options == {
        "total": 11,
        "unit": "B",
        "unit_scale": True,
        "unit_divisor": 1024,
        "desc": "artifact",
        "miniters": 1,
        "leave": False,
        "disable": True,
    }


def test_retry_policy_gives_up_only_on_permanent_response_errors():
    request_info = cast(aiohttp.RequestInfo, None)
    permanent = aiohttp.ClientResponseError(request_info, (), status=404)
    transient = aiohttp.ClientResponseError(request_info, (), status=503)

    assert http._give_up_on_http_error(permanent)
    assert not http._give_up_on_http_error(transient)
    assert not http._give_up_on_http_error(aiohttp.ClientPayloadError())


def test_tqdm_explicit_disable_is_respected(monkeypatch):
    options = {}
    test_logger = logging.getLogger("test-http-progress")
    test_logger.setLevel(logging.INFO)

    class Terminal:
        @staticmethod
        def isatty():
            return True

    def make_progress(*_args, **kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setattr(logsetup.sys, "stderr", Terminal())
    monkeypatch.setattr(logsetup._tqdm, "tqdm", make_progress)
    for name in ("TERM", "CI", "CIRCLECI"):
        monkeypatch.delenv(name, raising=False)

    logsetup.tqdm(disable=True, logger=test_logger)

    assert options["disable"] is True
