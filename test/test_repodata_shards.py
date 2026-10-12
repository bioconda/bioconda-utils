"""Shard queries and SQLite fallback must share filtering and ordering semantics."""

import asyncio
import datetime
import json

import msgpack
import pytest
import zstandard

from bioconda_utils._types import PackageSubdir
from bioconda_utils.conda import fetch, repodata, shards
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.conda.shards import ShardClient


def _pack_shard(data):
    return zstandard.ZstdCompressor().compress(msgpack.packb(data))


def index_url(repository, channel, subdir="noarch"):
    return ShardClient.shards_index_url(repository._make_repodata_url(channel, subdir))


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
    assert (
        ShardClient._parse_shard_index(
            _pack_shard(data), ("bioconda", PackageSubdir.LINUX_64)
        )
        is None
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
    ],
)
def test_repodata_rejects_invalid_package_shards(data):
    assert (
        ShardClient._parse_package_shard(
            _pack_shard(data),
            ("bioconda", PackageSubdir.LINUX_64, "pkg"),
        )
        is None
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
def test_repodata_resolves_shards_base_url(monkeypatch, shards_base_url, expected):
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda"]})
    repository = RepoData()
    index = shards._CachedShardIndex(
        shards={},
        base_url="",
        shards_base_url=shards_base_url,
        fetched_at=datetime.datetime.now(datetime.UTC),
    )

    assert (
        ShardClient.shard_url(
            repository._make_repodata_url("bioconda", PackageSubdir.LINUX_64),
            index,
            "hash",
        )
        == expected
    )


@pytest.fixture
def sharded(monkeypatch):
    monkeypatch.setattr(RepoData, "config", {"channels": ["high", "low"]})
    monkeypatch.setattr(RepoData, "platforms", ["noarch"])
    repository = RepoData()
    requests = []
    bodies = {}

    def download(urls, descriptions, transform, metadata, **kwargs):
        result = []
        for url, meta in zip(urls, metadata):
            requests.append(url)
            body = bodies.get(url)
            result.append(
                body
                if isinstance(body, Exception) or transform is None
                else transform(body, meta)
            )
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
            channels="high",
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
    assert repository.get_package_data("version", channels="high", name="example") == [
        "1",
        "2",
    ]
    assert repository.get_package_data(channels="high", name="missing") is False
    assert repository.get_package_data("name", channels="high", name=[]) == []
    assert len(requests) == 2
    assert loads == []


@pytest.mark.parametrize("failure", [None, b"corrupt", OSError("unavailable")])
@pytest.mark.parametrize("broken_channel", ["high", "low"])
def test_failed_shards_fall_back_once_and_preserve_channel_order(
    sharded, monkeypatch, failure, broken_channel
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


def test_partial_package_failure_replaces_repository_without_duplicates(
    sharded, monkeypatch
):
    repository, bodies, _ = sharded
    set_index(repository, bodies, example="good", other="bad")
    set_shard(repository, bodies, [record()], shard_hash="good")
    loads = fallback(repository, monkeypatch, [record(), record(name="other")])
    assert repository.get_package_data(
        "name", channels="high", name=["example", "other"]
    ) == ["example", "other"]
    assert loads == [("high", "noarch")]


def test_expired_indexes_and_changed_hashes_refetch(sharded, monkeypatch):
    repository, bodies, requests = sharded
    set_index(repository, bodies, example="old")
    set_shard(repository, bodies, [record(version="old")], shard_hash="old")
    assert repository.get_package_data("version", channels="high", name="example") == [
        "old"
    ]
    url = index_url(repository, "high")
    ShardClient._shard_index_cache[url].fetched_at -= datetime.timedelta(minutes=16)
    set_index(repository, bodies, example="new")
    set_shard(repository, bodies, [record(version="new")], shard_hash="new")
    assert repository.get_package_data("version", channels="high", name="example") == [
        "new"
    ]
    assert requests.count(url) == 2
    RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
    assert repository.get_package_data("version", channels="high", name="example") == [
        "new"
    ]
    assert requests.count(url) == 3


def test_negative_index_cache_expires(sharded, monkeypatch):
    repository, bodies, requests = sharded
    loads = fallback(repository, monkeypatch, [record()])
    for _ in range(2):
        assert repository.get_package_data(channels="high", name="example")
    url = index_url(repository, "high")
    assert requests == [url]
    ShardClient._shards_unavailable[url] -= datetime.timedelta(minutes=6)
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="sharded")])
    assert repository.get_package_data("version", channels="high", name="example") == [
        "sharded"
    ]
    assert requests.count(url) == 2
    assert len(loads) == 1


def test_shard_cache_identity_includes_repository_url(sharded):
    repository, bodies, _ = sharded
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="first")])
    assert repository.get_package_data("version", channels="high", name="example") == [
        "first"
    ]
    repository.REPODATA_URL = "https://other.example/{channel}/{subdir}/repodata.json"
    set_index(repository, bodies, example="hash")
    set_shard(repository, bodies, [record(version="second")])
    assert repository.get_package_data("version", channels="high", name="example") == [
        "second"
    ]


def test_async_downloads_preserve_input_order_and_isolate_errors(monkeypatch):
    async def download(_session, url, *_args, **_kwargs):
        if url == "slow":
            await asyncio.sleep(0.02)
        if url == "broken":
            raise OSError("failed")
        return url

    monkeypatch.setattr(fetch, "_async_fetch_one", download)
    result = asyncio.run(
        fetch.async_fetch(["slow", "broken", "fast"], return_exceptions=True)
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
        monkeypatch.setattr(RepoData, "config", {"channels": ["high", "low"]})
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
            "/low/noarch/repodata.json",
        ]
        assert len(list(repository.get_cache_dir().glob("*.sqlite"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_empty_name_filter_does_not_download(sharded):
    repository, _bodies, requests = sharded
    assert repository.get_package_data("name", name=[]) == []
    assert not repository.get_package_data(name=[])
    assert requests == []
