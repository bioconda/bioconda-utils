"""All persistent caches follow the configured application cache root."""

import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import web

from bioconda_utils import cli
from bioconda_utils._types import Config
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.support.caching import get_cache_root, memoize
from bioconda_utils.support.parallel import worker_pool
from bioconda_utils.support.upstreamcache import fetch_cached


def worker_cache_paths():
    return get_cache_root(), RepoData.get_cache_dir()


def test_lookup_cache_follows_root_changes(tmp_path):
    calls = []

    @memoize(expire=60)
    def lookup(value):
        calls.append(value)
        return len(calls)

    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, expected in [(first, 1), (first, 1), (second, 2), (first, 1)]:
        cli._configure_caches(root, False)
        assert lookup("example") == expected
    assert calls == ["example", "example"]
    assert (first / "lookups-v1" / "cache.db").is_file()
    assert (second / "lookups-v1" / "cache.db").is_file()


def test_shared_cache_root_is_propagated_to_workers(tmp_path):
    root = tmp_path / "shared"
    cli._configure_caches(root, True)
    with worker_pool(1) as pool:
        assert pool.submit(worker_cache_paths).result(timeout=20) == (
            root,
            root / "repodata-v2",
        )


def test_cache_directory_expands_home_and_can_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cli._configure_caches(Path("~/shared"), False)
    assert get_cache_root() == tmp_path / "shared"
    assert RepoData.get_cache_dir() == tmp_path / "shared" / "repodata-v2"
    cli._configure_caches(None, False)
    assert get_cache_root() != tmp_path / "shared"
    assert RepoData.get_cache_dir() == get_cache_root() / "repodata-v2"


def test_upstream_downloads_use_shared_root(tmp_path):
    root = tmp_path / "shared"
    cli._configure_caches(root, False)
    requests = []

    async def run():
        async def serve(request):
            requests.append(request.path)
            return web.Response(text="cached", headers={"Cache-Control": "max-age=60"})

        app = web.Application()
        app.router.add_get("/artifact", serve)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            async with aiohttp.ClientSession() as session:
                for _ in range(2):
                    assert (
                        await fetch_cached(
                            session, f"http://127.0.0.1:{port}/artifact", "text"
                        )
                        == "cached"
                    )
        finally:
            await runner.cleanup()

    asyncio.run(run())
    assert requests == ["/artifact"]
    assert len(list((root / "upstream-v1").glob("*.json"))) == 1


def test_repodata_uses_shared_cache_root(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    cli._configure_caches(root, False)
    monkeypatch.setattr(RepoData, "config", Config({"channels": ["bioconda"]}))
    raw = json.dumps({"info": {"subdir": "noarch"}, "packages": {}}).encode()
    monkeypatch.setattr(RepoData, "_download_repository", lambda *_args: raw)
    assert RepoData().get_package_data("name", platform="noarch") == []
    assert len(list((root / "repodata-v2").glob("*.sqlite"))) == 1


def test_rattler_cache_uses_current_shared_root(tmp_path, monkeypatch):
    from bioconda_utils.rattler.rattler_build_bridge import (
        get_rattler_cache_dir_path,
        set_rattler_cache_to_dir,
    )

    monkeypatch.setenv("RATTLER_CACHE_DIR", "previous")
    for name in ["first", "second"]:
        root = tmp_path / name
        cli._configure_caches(root, False)
        path = get_rattler_cache_dir_path()
        set_rattler_cache_to_dir(path)
        assert path == root / "rattler_cache"
        assert path.is_dir()
        import os

        assert os.environ["RATTLER_CACHE_DIR"] == str(path)
