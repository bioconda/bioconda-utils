"""Full repodata uses precompressed files with accurate byte progress."""

import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest
import zstandard

from bioconda_utils._types import Channel, Config
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.support.logsetup import progress_display


def metadata(name):
    return json.dumps(
        {
            "info": {"subdir": "noarch"},
            "packages": {
                "example.conda": {
                    "name": name,
                    "version": "1",
                    "build": "0",
                    "build_number": 0,
                    "depends": [],
                }
            },
        }
    ).encode()


@pytest.fixture
def server_repository(monkeypatch):
    bodies = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Accept-Encoding")))
            status, body = bodies.get(self.path, (404, b""))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    channel = Channel(f"http://127.0.0.1:{server.server_port}/channel")
    monkeypatch.setattr(RepoData, "config", Config(channels=[channel]))
    monkeypatch.setattr(RepoData, "platforms", ["noarch"])
    try:
        yield RepoData(), bodies, requests, channel
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("framing", ["known-size", "unknown-size", "multiple-frames"])
def test_zstd_preferred_with_compressed_progress_and_disk_reuse(
    server_repository, monkeypatch, framing
):
    repository, bodies, requests, _channel = server_repository
    raw = metadata("compressed")
    compressor = zstandard.ZstdCompressor(write_content_size=framing != "unknown-size")
    if framing == "multiple-frames":
        compressed = compressor.compress(raw[:20]) + compressor.compress(raw[20:])
    else:
        compressed = compressor.compress(raw)
    bodies["/channel/noarch/repodata.json.zst"] = (200, compressed)
    bodies["/channel/noarch/repodata.json"] = (200, metadata("plain"))
    sizes = []
    statuses = []
    download_task = progress_display.download_task
    status_task = progress_display.status

    @contextmanager
    def track_download(description, *, total=None):
        with download_task(description, total=total) as (progress, task):
            yield progress, task
            sizes.append((total, progress.tasks[0].completed))

    @contextmanager
    def track_status(description):
        statuses.append(description)
        with status_task(description) as task:
            yield task

    monkeypatch.setattr(progress_display, "download_task", track_download)
    monkeypatch.setattr(progress_display, "status", track_status)
    for _ in range(2):
        assert repository.get_package_data("name") == ["compressed"]
    assert requests == [("/channel/noarch/repodata.json.zst", "identity")]
    assert sizes == [(len(compressed), len(compressed))]
    assert [status.split()[0] for status in statuses] == ["Decompressing", "Indexing"]
    assert len(list(repository.get_cache_dir().glob("*.sqlite"))) == 1


def test_missing_zstd_falls_back_to_json(server_repository):
    repository, bodies, requests, _channel = server_repository
    bodies["/channel/noarch/repodata.json"] = (200, metadata("plain"))
    for _ in range(2):
        assert repository.get_package_data("name") == ["plain"]
    assert [path for path, _encoding in requests] == [
        "/channel/noarch/repodata.json.zst",
        "/channel/noarch/repodata.json",
    ]
    assert requests[0][1] == "identity"


@pytest.mark.parametrize(
    "compressed",
    [
        b"",
        b"invalid",
        zstandard.ZstdCompressor(write_checksum=True).compress(metadata("truncated"))[
            :-1
        ],
    ],
)
def test_bad_zstd_preserves_old_cache_without_falling_back(
    server_repository, compressed
):
    repository, bodies, requests, channel = server_repository
    path = repository._cache_path(channel.repodata_url("noarch"))
    path.parent.mkdir(parents=True)
    repository._write_cache(path, metadata("old"), fetched_at=0)
    old_cache = path.read_bytes()
    bodies["/channel/noarch/repodata.json.zst"] = (200, compressed)
    bodies["/channel/noarch/repodata.json"] = (200, metadata("plain"))

    with pytest.raises(zstandard.ZstdError):
        repository.get_package_data("name")

    assert requests == [("/channel/noarch/repodata.json.zst", "identity")]
    assert path.read_bytes() == old_cache
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM packages").fetchall() == [("old",)]
    assert not list(path.parent.glob(".repodata-*.tmp*"))
    assert progress_display.statuses.tasks == []


def test_zstd_http_errors_are_not_treated_as_missing(server_repository):
    repository, bodies, requests, _channel = server_repository
    bodies["/channel/noarch/repodata.json.zst"] = (403, b"denied")
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        repository.get_package_data("name")
    assert exc_info.value.status == 403
    assert requests == [("/channel/noarch/repodata.json.zst", "identity")]
    assert not list(repository.get_cache_dir().glob("*.sqlite"))
