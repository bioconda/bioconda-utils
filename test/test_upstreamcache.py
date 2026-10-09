"""Persistent HTTP caching must validate changed representations."""

import asyncio
import json
from contextlib import asynccontextmanager
from hashlib import sha256

import aiohttp
import pytest
from aiohttp import web

from bioconda_utils.aiopipe import AsyncRequests
from bioconda_utils.support.caching import async_file_lock, get_cache_root


@asynccontextmanager
async def server(handler):
    app = web.Application()
    app.router.add_get("/artifact", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        yield f"http://127.0.0.1:{runner.addresses[0][1]}/artifact"
    finally:
        await runner.cleanup()


def expire_entries():
    for path in (get_cache_root() / "upstream-v1").glob("*.json"):
        value = json.loads(path.read_text())
        value["expires"] = 0
        path.write_text(json.dumps(value))


async def fetch(url, kind="text"):
    async with AsyncRequests() as requests:
        if kind == "text":
            return await requests.get_text_from_url(url)
        return await requests.get_checksum_from_http(url, "artifact")


@pytest.mark.parametrize("kind", ["text", "checksum"])
def test_fresh_reuse_304_revalidation_and_changed_body(kind):
    async def run():
        requests = []
        body = b"first"

        async def serve(request):
            etag = '"' + sha256(body).hexdigest() + '"'
            requests.append(request.headers.get("If-None-Match"))
            headers = {"etag": etag, "cache-control": "max-age=60"}
            if request.headers.get("If-None-Match") == etag:
                return web.Response(status=304, headers=headers)
            return web.Response(body=body, headers=headers)

        async with server(serve) as url:
            expected = "first" if kind == "text" else sha256(body).hexdigest()
            assert await fetch(url, kind) == expected
            assert await fetch(url, kind) == expected
            assert len(requests) == 1
            expire_entries()
            assert await fetch(url, kind) == expected
            assert len(requests) == 2 and requests[-1] is not None
            expire_entries()
            body = b"changed"
            expected = "changed" if kind == "text" else sha256(body).hexdigest()
            assert await fetch(url, kind) == expected
            assert len(requests) == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "headers",
    [
        {"Cache-Control": "no-store"},
        {"Vary": "*"},
        {"Vary": "Cookie"},
    ],
)
def test_uncacheable_responses_are_not_persisted(headers):
    async def run():
        calls = []

        async def serve(request):
            calls.append(1)
            return web.Response(text="value", headers=headers)

        async with server(serve) as url:
            assert await fetch(url) == "value"
            assert await fetch(url) == "value"
            assert len(calls) == 2
            assert not list((get_cache_root() / "upstream-v1").glob("*.json"))

    asyncio.run(run())


def test_last_modified_revalidation_and_no_cache():
    async def run():
        calls = []
        modified = "Mon, 01 Jan 2024 00:00:00 GMT"

        async def serve(request):
            calls.append(request.headers.get("If-Modified-Since"))
            headers = {"Last-Modified": modified, "Cache-Control": "no-cache"}
            return (
                web.Response(status=304, headers=headers)
                if calls[-1]
                else web.Response(text="value", headers=headers)
            )

        async with server(serve) as url:
            assert await fetch(url) == "value"
            assert await fetch(url) == "value"
            assert calls == [None, modified]

    asyncio.run(run())


@pytest.mark.parametrize("etag", [None, 'W/"weak"'])
def test_checksums_without_strong_validators_are_downloaded_again(etag):
    async def run():
        calls = []

        async def serve(request):
            calls.append(request.headers.get("If-None-Match"))
            return web.Response(
                body=str(len(calls)).encode(), headers={"ETag": etag} if etag else {}
            )

        async with server(serve) as url:
            assert await fetch(url, "checksum") == sha256(b"1").hexdigest()
            assert await fetch(url, "checksum") == sha256(b"2").hexdigest()
            assert calls == [None, None]

    asyncio.run(run())


def test_concurrent_clients_share_one_download():
    async def run():
        calls = []

        async def serve(request):
            calls.append(1)
            await asyncio.sleep(0.1)
            return web.Response(text="value")

        async with server(serve) as url:
            assert await asyncio.gather(fetch(url), fetch(url)) == ["value", "value"]
            assert len(calls) == 1

    asyncio.run(run())


def test_corrupt_cache_is_replaced():
    async def run():
        async def serve(request):
            return web.Response(text="value")

        async with server(serve) as url:
            await fetch(url)
            next((get_cache_root() / "upstream-v1").glob("*.json")).write_text(
                "broken {"
            )
            assert await fetch(url) == "value"

    asyncio.run(run())


def test_errors_do_not_reuse_stale_response():
    async def run():
        status = 200

        async def serve(request):
            return web.Response(text="value", status=status)

        async with server(serve) as url:
            await fetch(url)
            expire_entries()
            status = 500
            with pytest.raises(aiohttp.ClientResponseError):
                await fetch(url)

    asyncio.run(run())


def test_cancellation_while_waiting_does_not_orphan_lock(tmp_path):
    async def run():
        path = tmp_path / "lock"

        async def wait():
            async with async_file_lock(path):
                pytest.fail("should still be waiting")

        async with async_file_lock(path):
            task = asyncio.create_task(wait())
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with async_file_lock(path, timeout=0):
            pass

    asyncio.run(run())


def test_private_cache_and_request_header_variants():
    async def run():
        calls = []

        async def serve(request):
            calls.append(request.headers.get("X-Mode", "default"))
            return web.Response(
                text=calls[-1],
                headers={
                    "Vary": "X-Mode, Accept-Encoding",
                    "Cache-Control": "private, max-age=60",
                },
            )

        async with server(serve) as url:
            assert await fetch(url) == "default"
            assert await fetch(url) == "default"
            async with AsyncRequests() as requests:
                assert requests.session is not None
                requests.session.headers["X-Mode"] = "different"
                assert await requests.get_text_from_url(url) == "different"
            assert calls == ["default", "different"]

    asyncio.run(run())


def test_cookies_for_another_host_do_not_disable_archive_cache():
    from yarl import URL

    async def run():
        calls = []

        async def serve(request):
            calls.append(1)
            return web.Response(
                body=b"archive", headers={"Cache-Control": "max-age=60"}
            )

        async with server(serve) as url:
            for _ in range(2):
                async with AsyncRequests() as requests:
                    assert requests.session is not None
                    requests.session.cookie_jar.update_cookies(
                        {"session": "other-host"}, URL("https://elsewhere.invalid/")
                    )
                    assert (
                        await requests.get_checksum_from_http(url, "archive")
                        == sha256(b"archive").hexdigest()
                    )
            assert len(calls) == 1

    asyncio.run(run())


def test_authenticated_request_cannot_reuse_anonymous_entry():
    async def run():
        async def serve(request):
            return web.Response(
                text="authenticated"
                if "Authorization" in request.headers
                else "anonymous"
            )

        async with server(serve) as url:
            assert await fetch(url) == "anonymous"
            async with AsyncRequests() as requests:
                assert requests.session is not None
                requests.session.headers["Authorization"] = "Bearer test"
                assert await requests.get_text_from_url(url) == "authenticated"

    asyncio.run(run())


def test_pruning_bounds_size_and_removes_expired_entries(tmp_path):
    import os
    import time

    from bioconda_utils.support.upstreamcache import prune

    old = tmp_path / "old.json"
    old.write_text("{}")
    expired = time.time() - 8 * 86400
    os.utime(old, (expired, expired))
    large = tmp_path / "large.json"
    with large.open("wb") as file:
        file.truncate(256 * 1024 * 1024 + 1)
    prune(tmp_path)
    assert not old.exists()
    assert not large.exists()


def test_cookie_bearing_checksums_require_validation_even_while_fresh():
    async def run():
        calls = []
        body = b"archive"

        async def serve(request):
            calls.append(request.headers.get("If-None-Match"))
            headers = {"ETag": '"archive"', "Cache-Control": "max-age=60"}
            return (
                web.Response(status=304, headers=headers)
                if calls[-1]
                else web.Response(body=body, headers=headers)
            )

        async with server(serve) as url:
            for _ in range(2):
                async with AsyncRequests() as requests:
                    assert requests.session is not None
                    requests.session.headers["Cookie"] = "guest=1"
                    assert (
                        await requests.get_checksum_from_http(url, "artifact")
                        == sha256(body).hexdigest()
                    )
            assert calls == [None, '"archive"']

    asyncio.run(run())
