"""Repository cache identity, freshness, and cross-process refreshes."""

import datetime
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from bioconda_utils.conda.repodata import RepoData, _CachedRepoData
from bioconda_utils.support.parallel import worker_pool


def dataframe(channel="bioconda", subdir="noarch", name="example"):
    return pd.DataFrame(
        [
            {
                "channel": channel,
                "subdir": subdir,
                "platform": subdir,
                "name": name,
                "version": "1",
                "build": "0",
                "build_number": 0,
                "depends": [],
            }
        ],
        columns=RepoData.columns,
    )


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda", "conda-forge"]})
    monkeypatch.setattr(RepoData, "platforms", ["linux-64", "noarch"])
    return RepoData()


def test_selective_loads_and_dataframe_view_reuse_same_entries(repository, monkeypatch):
    loads = []

    def load(repositories):
        pairs = tuple(repositories)
        loads.append(pairs)
        return pd.concat([dataframe(channel, subdir) for channel, subdir in pairs])

    monkeypatch.setattr(repository, "_load_channel_dataframe", load)
    repository.get_package_data("name", channels="bioconda", platform="noarch")
    repository.get_package_data("name", channels="bioconda", platform="noarch")
    assert len(repository.df) == 4
    assert len(repository.df) == 4
    assert loads == [
        (("bioconda", "noarch"),),
        (
            ("bioconda", "linux-64"),
            ("conda-forge", "linux-64"),
            ("conda-forge", "noarch"),
        ),
    ]
    assert len(repository._repository_cache) == 4


def test_disk_reuse_does_not_require_inherited_memory(repository, monkeypatch):
    monkeypatch.setattr(
        repository, "_load_channel_dataframe", lambda _repos: dataframe()
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["example"]
    RepoData._repository_cache.clear()
    monkeypatch.setattr(
        repository,
        "_load_channel_dataframe",
        lambda _repos: pytest.fail("unexpected download"),
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["example"]


@pytest.mark.parametrize("location", ["memory", "disk", "both"])
def test_expired_entries_are_refreshed(repository, monkeypatch, location):
    url = repository._make_repodata_url("bioconda", "noarch")
    entry = _CachedRepoData(
        dataframe(name="old"),
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=9),
    )
    if location in ("memory", "both"):
        RepoData._repository_cache[url] = entry
    if location in ("disk", "both"):
        repository.get_cache_dir().mkdir()
        repository._write_cache(repository._cache_path(url), entry)
    monkeypatch.setattr(
        repository, "_load_channel_dataframe", lambda _repos: dataframe(name="fresh")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["fresh"]
    fresh = repository._read_cache(repository._cache_path(url))
    assert fresh is not None
    assert list(fresh.dataframe.name) == ["fresh"]


def test_corrupt_disk_entry_is_replaced(repository, monkeypatch, caplog):
    path = repository._cache_path(repository._make_repodata_url("bioconda", "noarch"))
    path.parent.mkdir()
    path.write_bytes(b"not a pickle")
    monkeypatch.setattr(
        repository, "_load_channel_dataframe", lambda _repos: dataframe()
    )
    with caplog.at_level(logging.WARNING):
        assert repository.get_package_data(
            "name", channels="bioconda", platform="noarch"
        ) == ["example"]
    assert "Ignoring unreadable repodata cache" in caplog.text
    assert repository._read_cache(path) is not None


def test_configuration_changes_cannot_leak_other_channels(repository, monkeypatch):
    monkeypatch.setattr(
        repository,
        "_load_channel_dataframe",
        lambda repos: pd.concat([dataframe(c, s, c) for c, s in repos]),
    )
    assert repository.get_package_data("name", platform="noarch") == [
        "bioconda",
        "conda-forge",
    ]
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda"]})
    assert repository.get_package_data("name", platform="noarch") == ["bioconda"]
    assert not repository.get_package_data(channels="conda-forge", platform="noarch")


def test_cache_identity_includes_repository_url(repository, monkeypatch):
    monkeypatch.setattr(
        repository, "_load_channel_dataframe", lambda _repos: dataframe(name="first")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["first"]
    monkeypatch.setattr(
        repository,
        "REPODATA_URL",
        "https://another.example/{channel}/{subdir}/repodata.json",
    )
    monkeypatch.setattr(
        repository, "_load_channel_dataframe", lambda _repos: dataframe(name="second")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["second"]


def test_default_cache_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    RepoData.configure_cache()
    assert RepoData.get_cache_dir() == tmp_path / "bioconda-utils" / "repodata-v1"


def test_local_channels_are_always_read_fresh(repository, monkeypatch, tmp_path):
    channel = (tmp_path / "channel").as_uri()
    path = tmp_path / "channel" / "noarch" / "repodata.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(RepoData, "config", {"channels": [channel]})

    def write(name):
        path.write_text(
            json.dumps(
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
            )
        )

    write("first")
    assert repository.get_package_data("name", platform="noarch") == ["first"]
    write("second")
    assert repository.get_package_data("name", platform="noarch") == ["second"]
    assert not repository._repository_cache


def query_repository(base_url):
    """Spawn-picklable worker using the real HTTP downloader and disk cache."""
    RepoData.REPODATA_URL = base_url + "/{channel}/{subdir}/repodata.json"
    return RepoData().get_package_data("name", channels="bioconda", platform="noarch")


def test_concurrent_spawned_workers_download_repository_once(repository):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            time.sleep(0.2)
            body = json.dumps(
                {
                    "info": {"subdir": "noarch"},
                    "packages": {
                        "example.conda": {
                            "name": "example",
                            "version": "1",
                            "build": "0",
                            "build_number": 0,
                            "depends": [],
                        }
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        with worker_pool(2) as pool:
            futures = [pool.submit(query_repository, url) for _ in range(2)]
            assert [future.result(timeout=30) for future in futures] == [
                ["example"],
                ["example"],
            ]
        # An independent process pool reuses the persisted entry, too.
        with worker_pool(1) as pool:
            assert pool.submit(query_repository, url).result(timeout=30) == ["example"]
        assert requests == ["/bioconda/noarch/repodata.json"]
        # A refresh cutoff is shared with workers, and only one worker refreshes
        # the still-fresh disk entry for this run.
        RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
        with worker_pool(2) as pool:
            futures = [pool.submit(query_repository, url) for _ in range(2)]
            assert [future.result(timeout=30) for future in futures] == [
                ["example"],
                ["example"],
            ]
        assert requests == ["/bioconda/noarch/repodata.json"] * 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_refresh_replaces_fresh_entry_once(repository, monkeypatch):
    loads = []

    def load(_repos):
        loads.append(1)
        return dataframe(name=f"generation-{len(loads)}")

    monkeypatch.setattr(repository, "_load_channel_dataframe", load)
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["generation-1"]
    RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["generation-2"]
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["generation-2"]
    assert len(loads) == 2


def lock_and_exit(path):
    import os

    from bioconda_utils.support.caching import file_lock

    with file_lock(path):
        os._exit(23)


def test_cache_lock_is_released_when_worker_dies(tmp_path):
    from concurrent.futures.process import BrokenProcessPool

    from bioconda_utils.support.caching import file_lock

    path = tmp_path / "repository.lock"
    with worker_pool(1) as pool, pytest.raises(BrokenProcessPool):
        pool.submit(lock_and_exit, path).result(timeout=20)
    with file_lock(path, timeout=0):
        pass


def test_failed_refresh_preserves_previous_cache(repository, monkeypatch):
    path = repository._cache_path(repository._make_repodata_url("bioconda", "noarch"))
    path.parent.mkdir()
    original = _CachedRepoData(
        dataframe(name="previous"), datetime.datetime.now(datetime.UTC)
    )
    repository._write_cache(path, original)

    def incomplete_write(entry, target):
        target.write_bytes(b"partial")
        raise OSError("interrupted write")

    monkeypatch.setattr(pd, "to_pickle", incomplete_write)
    with pytest.raises(OSError, match="interrupted write"):
        repository._write_cache(path, original)
    preserved = repository._read_cache(path)
    assert preserved is not None
    assert list(preserved.dataframe.name) == ["previous"]
    assert not list(path.parent.glob("*.tmp"))
