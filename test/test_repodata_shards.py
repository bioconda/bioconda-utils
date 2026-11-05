"""Shard queries and SQLite fallback must share filtering and ordering semantics."""

import asyncio
import datetime
import json
import logging
import subprocess
import sys

import msgpack
import pytest
import zstandard

from bioconda_utils._types import Channel, Config, PackageSubdir
from bioconda_utils.conda import fetch, repodata, shards
from bioconda_utils.conda.fetch import FetchRequest
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.conda.shards import ShardClient
from bioconda_utils.support.caching import configure_cache_root, get_cache_root
from bioconda_utils.support.parallel import worker_pool


def _worker_versions():
    RepoData.platforms = ["noarch"]
    return RepoData().get_package_data("version", name="example")


def _pack_shard(data):
    return zstandard.ZstdCompressor().compress(msgpack.packb(data))


def index_url(repository, channel, subdir="noarch"):
    return ShardClient.shards_index_url(
        repository._make_repodata_url(Channel(channel), subdir)
    )


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"version": 2, "info": {}, "shards": {}},
        {"version": 1, "info": {}, "shards": None},
        {"version": 1, "info": {}, "shards": {"pkg": {}}},
    ],
)
def test_repodata_rejects_invalid_shard_indexes(data):
    with pytest.raises(shards.ShardError, match="Invalid shard index"):
        ShardClient._parse_shard_index(
            _pack_shard(data),
            "https://conda.anaconda.org/bioconda/linux-64/repodata_shards.msgpack.zst",
        )


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"packages": None, "packages.conda": {}},
        {"packages": {"pkg-1-0.tar.bz2": []}, "packages.conda": {}},
        {
            "packages": {"pkg-1-0.tar.bz2": {"depends": None}},
            "packages.conda": {},
        },
        {
            "packages": {
                "pkg-1-0.tar.bz2": {
                    "name": "pkg",
                    "version": "1",
                    "build": "0",
                    "build_number": 0,
                    "depends": "zlib",
                }
            },
            "packages.conda": {},
        },
        {
            "packages": {
                "pkg-1-0.tar.bz2": {
                    "name": "pkg",
                    "version": "1",
                    "build": "0",
                    "build_number": "0",
                    "depends": [],
                }
            },
            "packages.conda": {},
        },
    ],
)
def test_repodata_rejects_invalid_package_shards(data):
    with pytest.raises(shards.ShardError, match="Invalid package shard"):
        ShardClient._parse_package_shard(
            _pack_shard(data),
            (Channel("bioconda"), PackageSubdir.LINUX_64, "pkg"),
        )


@pytest.mark.parametrize(
    ("shards_base_url", "expected"),
    [
        ("", "https://conda.anaconda.org/bioconda/linux-64/hash.msgpack.zst"),
        (
            "./shards/",
            "https://conda.anaconda.org/bioconda/linux-64/shards/hash.msgpack.zst",
        ),
        ("/shards/", "https://conda.anaconda.org/shards/hash.msgpack.zst"),
        ("https://cdn.example/shards", "https://cdn.example/shards/hash.msgpack.zst"),
    ],
)
def test_repodata_resolves_shards_base_url(shards_base_url, expected):
    repository = RepoData()
    index = ShardClient._parse_shard_index(
        _pack_shard(
            {
                "version": 1,
                "info": {"shards_base_url": shards_base_url},
                "shards": {},
            }
        ),
        index_url(repository, "bioconda", PackageSubdir.LINUX_64),
    )
    assert ShardClient.shard_url(index, "hash") == expected


@pytest.fixture
def sharded(monkeypatch):
    monkeypatch.setattr(
        RepoData, "config", Config(channels=[Channel("high"), Channel("low")])
    )
    monkeypatch.setattr(RepoData, "platforms", ["noarch"])
    repository = RepoData()
    requests = []
    bodies = {}

    def download(downloads, **kwargs):
        result = []
        for request in downloads:
            requests.append(request.url)
            body = bodies.get(request.url)
            if isinstance(body, Exception) and not kwargs.get(
                "return_exceptions", False
            ):
                raise body
            result.append(body)
        return result

    monkeypatch.setattr(shards, "fetch", download)
    monkeypatch.setattr(repodata, "fetch", download)
    return repository, bodies, requests


def set_index(repository, bodies, channel="high", **shards):
    bodies[index_url(repository, channel)] = _pack_shard(
        {
            "version": 1,
            "info": {"shards_base_url": "./shards/"},
            "shards": shards,
        }
    )


def record(name="example", version="1", build="0", depends=None):
    return {
        "name": name,
        "version": version,
        "build": build,
        "build_number": 0,
        "depends": [] if depends is None else depends,
    }


def set_shard(repository, bodies, records, channel="high", shard_hash="hash"):
    url = (
        index_url(repository, channel).rsplit("/", 1)[0]
        + "/shards/"
        + shard_hash
        + ".msgpack.zst"
    )
    bodies[url] = _pack_shard(
        {
            "packages": {str(i): item for i, item in enumerate(records)},
            "packages.conda": {},
        }
    )


def fallback(repository, monkeypatch, records):
    loads = []

    def download(channel, subdir):
        loads.append((channel, subdir))
        return json.dumps(
            {
                "info": {"subdir": subdir},
                "packages": {str(i): item for i, item in enumerate(records)},
            }
        ).encode()

    monkeypatch.setattr(repository, "_download_repository", download)
    return loads


def test_shards_share_sql_filters_and_do_not_download_full_repodata(
    sharded, monkeypatch
):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="hash")
    set_shard(
        repository,
        bodies,
        [
            record(version="1", depends=["zlib"]),
            record(version="2"),
            record(name="unrelated"),
        ],
    )
    loads = fallback(repository, monkeypatch, [])
    rows = list(
        repository.get_package_data(
            ["name", "version", "depends", "channel", "subdir"],
            channels=Channel("high"),
            name="example",
            version=[1],
            build_number=[0],
        )
    )
    assert len(rows) == 1
    assert rows[0].name == "example"
    assert rows[0].version == "1"
    assert rows[0].depends == ["zlib"]
    assert rows[0].channel == "high"
    assert rows[0].subdir == "noarch"
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == [
        "1",
        "2",
    ]
    assert (
        repository.get_package_data(channels=Channel("high"), name="missing") is False
    )
    assert repository.get_package_data("name", channels=Channel("high"), name=[]) == []
    assert len(requests) == 2
    assert loads == []


@pytest.mark.parametrize("shared_shard", [False, True])
def test_repeated_names_and_shared_shards_do_not_duplicate_results(
    sharded, shared_shard
):
    repository, bodies, requests = sharded
    example_records = [record(version="1"), record(version="2")]
    other_records = [record(name="other")]
    set_index(
        repository, bodies, example="hash", other="hash" if shared_shard else "other"
    )
    set_shard(
        repository,
        bodies,
        example_records + other_records if shared_shard else example_records,
    )
    if not shared_shard:
        set_shard(repository, bodies, other_records, shard_hash="other")

    # Exercise both downloads and subsequent cache hits; retain distinct versions.
    for _ in range(2):
        rows = repository.get_package_data(
            ["name", "version"],
            channels=Channel("high"),
            name=["example", "example", "other", "other"],
        )
        assert sorted(rows) == [("example", "1"), ("example", "2"), ("other", "1")]
    assert len(requests) == (2 if shared_shard else 3)
    assert len(requests) == len(set(requests))


@pytest.mark.parametrize("failure", [None, b"corrupt", OSError("unavailable")])
@pytest.mark.parametrize("broken_channel", ["high", "low"])
def test_failed_shards_fall_back_once_and_preserve_channel_order(
    sharded, monkeypatch, failure, broken_channel, caplog
):
    repository, bodies, requests = sharded
    for channel in ["high", "low"]:
        set_index(repository, bodies, channel, example="hash")
        set_shard(repository, bodies, [record(version=channel)], channel)
    broken_url = (
        index_url(repository, broken_channel).rsplit("/", 1)[0]
        + "/shards/hash.msgpack.zst"
    )
    bodies[broken_url] = failure
    loads = fallback(repository, monkeypatch, [record(version="fallback")])
    expected = ["fallback", "low"] if broken_channel == "high" else ["high", "fallback"]
    for _ in range(2):
        assert repository.get_package_data("version", name="example") == expected
    assert loads == [(broken_channel, "noarch")]
    assert requests.count(broken_url) == 1
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "falling back to full repodata" in caplog.text
    assert broken_url in caplog.text


def test_partial_package_failure_replaces_repository_without_duplicates(
    sharded, monkeypatch
):
    repository, bodies, _ = sharded
    set_index(repository, bodies, example="good", other="bad")
    set_shard(repository, bodies, [record()], shard_hash="good")
    loads = fallback(repository, monkeypatch, [record(), record(name="other")])
    assert repository.get_package_data(
        "name", channels=Channel("high"), name=["example", "other"]
    ) == ["example", "other"]
    assert loads == [("high", "noarch")]


@pytest.mark.parametrize(
    "failure",
    [b"", b"corrupt", _pack_shard({}), OSError("connection failed")],
)
def test_failed_indexes_warn_once_and_fall_back(sharded, monkeypatch, caplog, failure):
    repository, bodies, requests = sharded
    url = index_url(repository, "high")
    bodies[url] = failure
    loads = fallback(repository, monkeypatch, [record()])
    for _ in range(2):
        assert repository.get_package_data(
            "name", channels=Channel("high"), name="example"
        ) == ["example"]
    assert requests == [url]
    assert loads == [("high", "noarch")]
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert url in caplog.text
    assert "falling back to full repodata" in caplog.text


def test_batch_download_failure_warns_once_and_backs_off(sharded, monkeypatch, caplog):
    repository, bodies, _requests = sharded
    set_index(repository, bodies, example="hash")
    download = shards.fetch
    batches = []

    def fail_shards(downloads, **kwargs):
        if kwargs.get("return_exceptions"):
            batches.append(downloads)
            raise OSError("batch failed")
        return download(downloads, **kwargs)

    monkeypatch.setattr(shards, "fetch", fail_shards)
    fallback(repository, monkeypatch, [record()])
    for _ in range(2):
        assert repository.get_package_data(
            "name", channels=Channel("high"), name="example"
        ) == ["example"]
    assert len(batches) == 1
    assert len(caplog.records) == 1
    assert "batch failed" in caplog.text
    assert "falling back to full repodata" in caplog.text


def test_failed_shard_retry_keeps_successful_siblings(sharded, monkeypatch, caplog):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="good", other="bad")
    set_shard(repository, bodies, [record()], shard_hash="good")
    loads = fallback(repository, monkeypatch, [record(), record(name="other")])
    for _ in range(2):
        assert repository.get_package_data(
            "name", channels=Channel("high"), name=["example", "other"]
        ) == ["example", "other"]
    assert len(caplog.records) == 1
    bad_url = next(iter(ShardClient._package_shards_unavailable))
    ShardClient._package_shards_unavailable[bad_url] -= datetime.timedelta(minutes=6)
    # Fresh full repodata now takes precedence. Remove it to exercise a shard
    # retry after the cooldown without redownloading the successful sibling.
    repository._cache_path(
        repository._make_repodata_url(Channel("high"), "noarch")
    ).unlink()
    set_shard(repository, bodies, [record(name="other")], shard_hash="bad")
    assert repository.get_package_data(
        "name", channels=Channel("high"), name=["example", "other"]
    ) == ["example", "other"]
    assert requests.count(bad_url) == 2
    assert len([url for url in requests if url.endswith("/good.msgpack.zst")]) == 1
    assert loads == [("high", "noarch")]
    assert not ShardClient._package_shards_unavailable
    assert len(caplog.records) == 1


def test_unexpected_programming_errors_propagate(sharded, monkeypatch):
    repository, bodies, _requests = sharded
    set_index(repository, bodies, example="hash")

    def broken_parser(*args):
        raise AssertionError("programming error")

    monkeypatch.setattr(ShardClient, "_parse_shard_index", broken_parser)
    with pytest.raises(AssertionError, match="programming error"):
        repository.get_package_data("name", channels=Channel("high"), name="example")


def test_expired_indexes_and_changed_hashes_refetch(sharded, monkeypatch):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="old")
    set_shard(repository, bodies, [record(version="old")], shard_hash="old")
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["old"]
    url = index_url(repository, "high")
    ShardClient._shard_index_cache[url].fetched_at -= datetime.timedelta(minutes=16)
    set_index(repository, bodies, example="new")
    set_shard(repository, bodies, [record(version="new")], shard_hash="new")
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["new"]
    assert requests.count(url) == 2
    RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["new"]
    assert requests.count(url) == 3


def test_shards_persist_across_cache_configuration(sharded, tmp_path):
    repository, bodies, requests = sharded
    root = tmp_path / "custom-cache"
    configure_cache_root(root)
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record()])
    for _ in range(2):
        RepoData.configure_cache()
        assert repository.get_package_data(
            "version", channels=Channel("high"), name="example"
        ) == ["1"]
    assert len(requests) == 2
    assert (root / "shards-v1").is_dir()


@pytest.mark.parametrize("refresh", [False, True])
def test_disk_index_refresh_reuses_unchanged_shards(sharded, refresh):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record()])
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["1"]
    if not refresh:
        with ShardClient._disk_cache() as cache:
            cache.touch(("index", index_url(repository, "high")), expire=-1)
    RepoData.configure_cache(refresh=refresh)
    # The refreshed index selects a new shard alongside an unchanged one.
    set_index(repository, bodies, example="hash", other="new")
    set_shard(repository, bodies, [record(name="other", version="2")], shard_hash="new")
    assert repository.get_package_data(
        "version", channels=Channel("high"), name=["example", "other"]
    ) == ["1", "2"]
    # Refresh must not repeatedly invalidate the newly fetched index.
    ShardClient.clear_caches()
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["1"]
    assert requests.count(index_url(repository, "high")) == 2
    assert sum(url.endswith("/hash.msgpack.zst") for url in requests) == 1
    assert sum(url.endswith("/new.msgpack.zst") for url in requests) == 1


@pytest.mark.parametrize("kind", ["index", "shard"])
def test_corrupt_disk_entries_are_redownloaded(sharded, kind, caplog):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record()])
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["1"]
    url = requests[0 if kind == "index" else 1]
    with ShardClient._disk_cache() as cache:
        cache.set((kind, url), b"corrupt", expire=900 if kind == "index" else None)
    for _ in range(2):
        ShardClient.clear_caches()
        assert repository.get_package_data(
            "version", channels=Channel("high"), name="example"
        ) == ["1"]
    assert requests.count(url) == 2
    assert len(requests) == 3
    assert "Ignoring invalid cached" in caplog.text


def test_explicit_cache_directory_isolated_from_other_caches(sharded, tmp_path):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record()])
    for directory in (tmp_path / "first", tmp_path / "second", tmp_path / "first"):
        RepoData.configure_cache(directory)
        assert repository.get_package_data(
            "version", channels=Channel("high"), name="example"
        ) == ["1"]
        assert (directory / "shards-v1").is_dir()
    assert len(requests) == 4


def test_negative_index_cache_expires(sharded, monkeypatch, caplog):
    repository, bodies, requests = sharded
    loads = fallback(repository, monkeypatch, [record()])
    for _ in range(2):
        assert repository.get_package_data(channels=Channel("high"), name="example")
    url = index_url(repository, "high")
    assert requests == [url]
    ShardClient._shards_unavailable[url] -= datetime.timedelta(minutes=6)
    repository._cache_path(
        repository._make_repodata_url(Channel("high"), "noarch")
    ).unlink()
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="sharded")])
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["sharded"]
    assert requests.count(url) == 2
    assert len(loads) == 1
    assert not caplog.records


def test_shard_cache_identity_includes_repository_url(sharded):
    repository, bodies, _ = sharded
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="first")])
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["first"]
    repository.REPODATA_URL = "https://other.example/{channel}/{subdir}/repodata.json"
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="second")])
    assert repository.get_package_data(
        "version", channels=Channel("high"), name="example"
    ) == ["second"]


def test_async_downloads_preserve_input_order_and_isolate_errors(monkeypatch):
    async def download(_session, url, *_args, **_kwargs):
        if url == "slow":
            await asyncio.sleep(0.02)
        if url == "broken":
            raise OSError("failed")
        return url

    monkeypatch.setattr(fetch, "_async_fetch_one", download)
    result = asyncio.run(
        fetch.async_fetch(
            [FetchRequest(url) for url in ["slow", "broken", "fast"]],
            return_exceptions=True,
        )
    )
    assert result[0] == "slow"
    assert isinstance(result[1], OSError)
    assert result[2] == "fast"


def test_http_shards_and_missing_index_fallback(monkeypatch):
    """Exercise actual downloads, decompression, SQL, and persistent fallback."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []
    shard = _pack_shard(
        {"packages": {"example.conda": record(version="sharded")}, "packages.conda": {}}
    )
    index = _pack_shard(
        {
            "version": 1,
            "info": {"shards_base_url": "./shards/"},
            "shards": {"example": "hash"},
        }
    )
    full = json.dumps(
        {
            "info": {"subdir": "noarch"},
            "packages": {"example.conda": record(version="fallback")},
        }
    ).encode()
    bodies = {
        "/high/noarch/repodata_shards.msgpack.zst": index,
        "/high/noarch/shards/hash.msgpack.zst": shard,
        "/low/noarch/repodata.json": full,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = bodies.get(self.path, b"")
            self.send_response(200 if self.path in bodies else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        monkeypatch.setattr(
            RepoData,
            "REPODATA_URL",
            f"http://127.0.0.1:{server.server_port}/{{channel}}/{{subdir}}/repodata.json",
        )
        monkeypatch.setattr(
            RepoData, "config", Config(channels=[Channel("high"), Channel("low")])
        )
        monkeypatch.setattr(RepoData, "platforms", ["noarch"])
        repository = RepoData()
        for _ in range(2):
            assert repository.get_package_data("version", name="example") == [
                "sharded",
                "fallback",
            ]
        assert requests == [
            "/high/noarch/repodata_shards.msgpack.zst",
            "/high/noarch/shards/hash.msgpack.zst",
            "/low/noarch/repodata_shards.msgpack.zst",
            "/low/noarch/repodata.json.zst",
            "/low/noarch/repodata.json",
        ]
        assert len(list(repository.get_cache_dir().glob("*.sqlite"))) == 1
        # A new interpreter has no in-memory shard caches. It must reuse the
        # parent's persistent entries without making another HTTP request.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "from bioconda_utils._types import Channel, Config\n"
                    "from bioconda_utils.conda.repodata import RepoData\n"
                    "from bioconda_utils.support.caching import configure_cache_root\n"
                    "configure_cache_root(Path(sys.argv[2]))\n"
                    "RepoData.register_config(Config(channels=[Channel(sys.argv[1])]))\n"
                    "RepoData.platforms = ['noarch']\n"
                    "assert RepoData().get_package_data('version', name='example') == ['sharded']\n"
                ),
                f"http://127.0.0.1:{server.server_port}/high",
                str(get_cache_root()),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        monkeypatch.setattr(
            RepoData,
            "config",
            Config(channels=[Channel(f"http://127.0.0.1:{server.server_port}/high")]),
        )
        with worker_pool(1) as pool:
            assert pool.submit(_worker_versions).result(timeout=30) == ["sharded"]
        assert len(requests) == 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_empty_name_filter_does_not_download(sharded):
    repository, _bodies, requests = sharded
    assert repository.get_package_data("name", name=[]) == []
    assert not repository.get_package_data(name=[])
    assert requests == []


def test_prefetch_batches_names_and_avoids_later_downloads(sharded, monkeypatch):
    repository, bodies, requests = sharded
    for channel in ("high", "low"):
        set_index(repository, bodies, channel, example="one", other="two")
        set_shard(repository, bodies, [record()], channel, shard_hash="one")
        set_shard(repository, bodies, [record(name="other")], channel, shard_hash="two")
    batches = []
    download = shards.fetch

    def batch_download(downloads, **kwargs):
        downloads = list(downloads)
        batches.append([request.url for request in downloads])
        return download(downloads, **kwargs)

    monkeypatch.setattr(shards, "fetch", batch_download)
    repository.prefetch(["example", "other", "example"])
    assert [len(batch) for batch in batches] == [1, 2, 1, 2]
    assert len(requests) == 6
    for name in ("example", "other"):
        assert repository.get_package_data("name", name=name) == [name, name]
    assert len(requests) == 6


def test_prefetch_threshold_is_per_repository_and_counts_distinct_urls(
    sharded, monkeypatch, caplog
):
    repository, bodies, requests = sharded
    monkeypatch.setattr(ShardClient, "max_missing_shards", 2)
    set_index(repository, bodies, "high", example="one", other="two")
    # Two names share one shard on the second channel, below the threshold.
    set_index(repository, bodies, "low", example="shared", other="shared")
    set_shard(repository, bodies, [record(), record(name="other")], "low", "shared")
    loads = fallback(repository, monkeypatch, [record(), record(name="other")])
    caplog.set_level(logging.INFO)

    repository.prefetch(["example", "other", "example", "missing"])
    assert loads == [("high", "noarch")]
    assert len(requests) == 3  # two indexes and the shared shard
    assert not any("/high/noarch/shards/" in url for url in requests)
    assert "2 uncached shards (threshold 2)" in caplog.text
    assert repository.get_package_data("name", name="other") == ["other", "other"]
    assert loads == [("high", "noarch")]
    assert len(requests) == 3


@pytest.mark.parametrize("new_shard", [False, True])
def test_threshold_excludes_persistently_cached_shards(sharded, monkeypatch, new_shard):
    repository, bodies, requests = sharded
    monkeypatch.setattr(RepoData, "config", Config(channels=[Channel("high")]))
    monkeypatch.setattr(ShardClient, "max_missing_shards", 3)
    set_index(repository, bodies, example="one", other="two", last="three")
    for name, shard_hash in (("example", "one"), ("other", "two"), ("last", "three")):
        set_shard(repository, bodies, [record(name=name)], shard_hash=shard_hash)
    loads = fallback(repository, monkeypatch, [])
    repository.prefetch(["example", "other"])
    assert len(requests) == 3

    ShardClient.clear_caches()
    monkeypatch.setattr(ShardClient, "max_missing_shards", 2)
    names = ["example", "other", "last"] if new_shard else ["example", "other"]
    repository.prefetch(names)
    assert len(requests) == (4 if new_shard else 3)
    assert not loads


def test_fresh_full_repodata_precedes_shards(sharded, monkeypatch):
    repository, _bodies, requests = sharded
    monkeypatch.setattr(RepoData, "config", Config(channels=[Channel("high")]))
    loads = fallback(repository, monkeypatch, [record()])
    with repository._open_repository(Channel("high"), "noarch"):
        pass
    repository.prefetch(["example", "other"])
    assert repository.get_package_data("version", name="example") == ["1"]
    assert loads == [("high", "noarch")]
    assert not requests


def test_empty_prefetch_and_local_channels_do_not_download(sharded, monkeypatch):
    repository, _bodies, requests = sharded
    monkeypatch.setattr(RepoData, "config", None)
    repository.prefetch([])
    monkeypatch.setattr(RepoData, "config", Config(channels=[Channel("file:///local")]))
    repository.prefetch(["example"])
    assert not requests
