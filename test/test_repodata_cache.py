"""Indexed repository cache freshness, query semantics, and process coordination."""

import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.support.parallel import worker_pool


def repodata(name="example", version="1", subdir="noarch", records=None):
    return json.dumps(
        {
            "info": {"subdir": subdir},
            "packages": records
            if records is not None
            else {
                "example.conda": {
                    "name": name,
                    "version": version,
                    "build": "0",
                    "build_number": 0,
                    "depends": ["python >=3.10"],
                }
            },
        }
    ).encode()


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setattr(RepoData, "config", {"channels": ["bioconda", "conda-forge"]})
    monkeypatch.setattr(RepoData, "platforms", ["linux-64", "noarch"])
    return RepoData()


def test_selective_loads_and_disk_reuse(repository, monkeypatch):
    loads = []

    def download(channel, subdir):
        loads.append((channel, subdir))
        return repodata(subdir=subdir)

    monkeypatch.setattr(repository, "_download_repository", download)
    for _ in range(2):
        assert repository.get_package_data(
            "name", channels="bioconda", platform="noarch"
        ) == ["example"]
    assert loads == [("bioconda", "noarch")]
    assert len(repository.get_package_data("name")) == 4
    assert len(repository.get_package_data("name")) == 4
    assert len(loads) == 4


@pytest.mark.parametrize("age", [9 * 3600, -3600])
def test_expired_or_future_entries_are_refreshed(repository, monkeypatch, age):
    path = repository._cache_path(repository._make_repodata_url("bioconda", "noarch"))
    path.parent.mkdir()
    repository._write_cache(path, repodata(name="old"), fetched_at=time.time() - age)
    monkeypatch.setattr(
        repository, "_download_repository", lambda *_args: repodata(name="fresh")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["fresh"]


@pytest.mark.parametrize("contents", [b"not a database", None])
def test_corrupt_disk_entry_is_replaced(repository, monkeypatch, caplog, contents):
    path = repository._cache_path(repository._make_repodata_url("bioconda", "noarch"))
    path.parent.mkdir()
    if contents is None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=2")
    else:
        path.write_bytes(contents)
    monkeypatch.setattr(repository, "_download_repository", lambda *_args: repodata())
    with caplog.at_level(logging.WARNING):
        assert repository.get_package_data(
            "name", channels="bioconda", platform="noarch"
        ) == ["example"]
    assert "Ignoring unreadable repodata cache" in caplog.text


def test_configuration_changes_cannot_leak_other_channels(repository, monkeypatch):
    monkeypatch.setattr(
        repository, "_download_repository", lambda c, s: repodata(name=c, subdir=s)
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
        repository, "_download_repository", lambda *_args: repodata(name="first")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["first"]
    monkeypatch.setattr(
        repository,
        "REPODATA_URL",
        "https://other.example/{channel}/{subdir}/repodata.json",
    )
    monkeypatch.setattr(
        repository, "_download_repository", lambda *_args: repodata(name="second")
    )
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["second"]


def test_default_cache_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    RepoData.configure_cache()
    assert RepoData.get_cache_dir() == tmp_path / "bioconda-utils" / "repodata-v2"


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
    assert not repository.get_cache_dir().exists()


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

    def download(*_args):
        loads.append(1)
        return repodata(name=f"generation-{len(loads)}")

    monkeypatch.setattr(repository, "_download_repository", download)
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["generation-1"]
    RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
    for _ in range(2):
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
    repository._write_cache(path, repodata(name="previous"))

    def incomplete_write(connection, raw, **kwargs):
        connection.execute("CREATE TABLE partial (value INTEGER)")
        raise OSError("interrupted write")

    monkeypatch.setattr(
        repository.__class__, "_populate_database", staticmethod(incomplete_write)
    )
    with pytest.raises(OSError, match="interrupted write"):
        repository._write_cache(path, repodata(name="new"))
    with closing(repository._read_cache(path)) as connection:
        assert connection.execute("SELECT name FROM packages").fetchall() == [
            ("previous",)
        ]
    assert not list(path.parent.glob("*.tmp*"))


def test_refresh_keeps_active_readers_consistent(repository):
    path = repository._cache_path(repository._make_repodata_url("bioconda", "noarch"))
    path.parent.mkdir()
    repository._write_cache(path, repodata(name="old"))
    with closing(repository._read_cache(path)) as old:
        repository._write_cache(path, repodata(name="new"))
        with closing(repository._read_cache(path)) as new:
            assert old.execute("SELECT name FROM packages").fetchall() == [("old",)]
            assert new.execute("SELECT name FROM packages").fetchall() == [("new",)]


@pytest.fixture
def populated(repository, monkeypatch):
    records = {
        "a.tar.bz2": {
            "name": "example",
            "version": 1,
            "build": "a_0",
            "build_number": 0,
            "depends": ["python >=3.10", "zlib"],
        },
        "b.conda": {
            "name": "example",
            "version": "2",
            "build": "a_1",
            "build_number": 1,
            "depends": [],
        },
        "c.conda": {
            "name": "other",
            "version": "2",
            "build": "b_0",
            "build_number": 0,
            "depends": None,
        },
    }
    monkeypatch.setattr(
        repository,
        "_download_repository",
        lambda c, s: repodata(subdir=s, records=records),
    )
    return repository


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"name": "example", "version": 1}, ["a_0"]),
        ({"name": ["example", "other"], "build_number": [0]}, ["a_0", "b_0"]),
        ({"name": [], "version": "1"}, []),
        ({"name": "example", "version": [1, "2"]}, ["a_0", "a_1"]),
        ({"build": ("b_0",)}, ["b_0"]),
        ({"name": "example' OR 1=1 --"}, []),
    ],
)
def test_parameterized_queries(populated, filters, expected):
    assert (
        populated.get_package_data(
            "build", channels="bioconda", platform="noarch", **filters
        )
        == expected
    )
    assert populated.get_package_data(
        channels="bioconda", platform="noarch", **filters
    ) == bool(expected)


def test_rows_preserve_dependencies_duplicates_and_named_fields(populated):
    rows = list(
        populated.get_package_data(
            ["channel", "platform", "subdir", "name", "version", "depends"],
            name="example",
            version=1,
        )
    )
    assert len(rows) == 4
    assert {(row.channel, row.platform) for row in rows} == {
        ("bioconda", "linux-64"),
        ("bioconda", "noarch"),
        ("conda-forge", "linux-64"),
        ("conda-forge", "noarch"),
    }
    assert all(
        row.depends == ["python >=3.10", "zlib"]
        and row.version == "1"
        and row.subdir == row.platform
        for row in rows
    )
    assert populated.get_package_data(
        "depends", name="other", platform="noarch", channels="bioconda"
    ) == [None]
    assert populated.get_versions("example") == {
        "1": ["linux-64", "noarch"],
        "2": ["linux-64", "noarch"],
    }


def test_native_query_only_loads_native_and_noarch(populated, monkeypatch):
    monkeypatch.setattr(populated, "native_subdir", lambda: "linux-64")
    assert len(populated.get_package_data("name", native=True)) == 12
    assert populated.get_package_data("name", platform=[]) == []
    assert populated.get_package_data("name", channels=[]) == []
    with pytest.raises(KeyError):
        populated.get_package_data("name; DROP TABLE packages")


def test_conda_packages_override_same_filename(populated, monkeypatch):
    raw = json.loads(repodata())
    raw["packages.conda"] = {
        "example.conda": {
            "name": "replacement",
            "version": "3",
            "build": "0",
            "build_number": 0,
            "depends": [],
        }
    }
    monkeypatch.setattr(
        populated, "_download_repository", lambda *_args: json.dumps(raw).encode()
    )
    assert populated.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["replacement"]


def test_next_refresh_cleans_only_abandoned_builder_files(repository, monkeypatch):
    directory = repository.get_cache_dir()
    directory.mkdir()
    abandoned = directory / ".repodata-aborted.tmp"
    journal = directory / ".repodata-aborted.tmp-journal"
    unrelated = directory / "user.tmp"
    for path in [abandoned, journal, unrelated]:
        path.write_bytes(b"partial")
    monkeypatch.setattr(repository, "_download_repository", lambda *_args: repodata())
    assert repository.get_package_data(
        "name", channels="bioconda", platform="noarch"
    ) == ["example"]
    assert not abandoned.exists()
    assert not journal.exists()
    assert unrelated.read_bytes() == b"partial"
