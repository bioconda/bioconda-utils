"""
Access to the conda package directory (repodata) of anaconda.org channels.

:class:`RepoData` that loads channel/subdir repodata,
caches indexed records on disk, and answers package queries.
:func:`fetch` is the parallel HTTP downloader it relies on.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import platform
import sqlite3
import sys
import tempfile
from collections import namedtuple
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from itertools import product, zip_longest
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urljoin

import aiofiles
import aiohttp
import msgpack
import requests
import zstandard

from .._types import (
    ALL_PACKAGE_SUBDIRS,
    Channel,
    PackageSubdir,
    Subdir,
    container_platform_to_package_subdir,
    native_container_platform,
)
from ..support import http
from ..support.caching import file_lock, get_cache_root, memoize
from ..support.logsetup import progress_display

logger = logging.getLogger(__name__)


type RepoDataKey = tuple[str, Subdir]


#: Max connections to each server
CONNECTIONS_PER_HOST = 4


def fetch(
    urls: Iterable[str],
    descriptions: Iterable[str],
    transform: Callable[[bytes, RepoDataKey], Any] | None,
    metadata: Iterable[RepoDataKey],
    *,
    allow_404: bool = False,
    return_exceptions: bool = False,
) -> list[Any]:
    """Fetch data from URLs.

    This will use asyncio to manage a pool of connections at once, speeding
    up download as compared to iterative use of ``requests`` significantly.
    It will also retry on non-permanent HTTP error codes (i.e. 429, 502,
    503 and 504).

    Args:
      urls: List of URLS
      descriptions: Matching list of descriptions (for progress display)
      transform: As each download completes, the raw bytes and the matching
          entry from **metadata** are passed through this function, e.g. to
          offload json parsing into the download loop.
      metadata: Per-URL context handed to **transform** alongside the bytes.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning("Running fetch from within running loop")
        # Workaround the fact that asyncio's loop is marked as not-reentrant
        # (it is apparently easy to patch, but not desired by the devs,
        with ThreadPool(1) as pool:
            res = pool.apply(
                lambda: fetch(
                    urls,
                    descriptions,
                    transform,
                    metadata,
                    allow_404=allow_404,
                    return_exceptions=return_exceptions,
                )
            )
        return res

    # asyncio.run cancels the fetch on SIGINT before raising
    # KeyboardInterrupt, so pending connections close cleanly
    return cast(
        list[Any],
        asyncio.run(
            async_fetch(
                urls,
                descriptions,
                transform,
                metadata,
                allow_404=allow_404,
                return_exceptions=return_exceptions,
            )
        ),
    )


async def async_fetch(
    urls: Iterable[str] = (),
    descriptions: Iterable[str] = (),
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: Iterable[RepoDataKey] | None = None,
    *,
    allow_404: bool = False,
    return_exceptions: bool = False,
) -> list[Any | bytes]:
    if metadata is None:
        metadata = []
    conn = aiohttp.TCPConnector(limit_per_host=CONNECTIONS_PER_HOST)
    async with http.make_session(connector=conn) as session:

        async def fetch_indexed(index, url, description, datum):
            try:
                value = await _async_fetch_one(
                    session,
                    url,
                    description or url,
                    transform=transform,
                    metadata=datum,
                    allow_404=allow_404,
                )
            except Exception as exc:
                if not return_exceptions:
                    raise
                value = exc
            return index, value

        coros = [
            asyncio.create_task(fetch_indexed(index, url, description, datum))
            for index, (url, description, datum) in enumerate(
                zip_longest(urls, descriptions, metadata)
            )
            if url is not None
        ]
        try:
            with progress_display.count_task("Downloading", total=len(coros)) as (
                progress,
                task,
            ):
                result = [None] * len(coros)
                for coro in progress.track(
                    asyncio.as_completed(coros), total=len(coros), task_id=task
                ):
                    index, value = await coro
                    result[index] = value
        finally:
            for coro in coros:
                if not coro.done():
                    coro.cancel()
            await asyncio.gather(*coros, return_exceptions=True)
    return result


@http.retry_on_transient
async def _async_fetch_one(
    session: aiohttp.ClientSession,
    url: str,
    description: str,
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: RepoDataKey | None = None,
    *,
    allow_404: bool = False,
) -> Any | bytes:
    if url.startswith("file://"):
        local_path = Path(url[7:])
        if local_path.exists():
            async with aiofiles.open(local_path, mode="rb") as f:
                raw = await f.read()
        elif allow_404:
            return None
        else:
            subdir = url.split("/")[-2]
            d = {
                "info": {"subdir": subdir},
                "packages": {},
                "packages.conda": {},
                "removed": [],
                "repodata_version": 1,
            }
            raw = json.dumps(d).encode("UTF-8")
    else:
        async with session.get(url) as resp:
            if allow_404 and resp.status == 404:
                return None
            resp.raise_for_status()
            raw = await http.download_to_bytes(
                resp,
                description,
                block_size=1024 * 16,
            )
    if transform is None:
        return raw
    assert metadata is not None
    return transform(raw, metadata)


@dataclass
class _CachedShardIndex:
    shards: dict[str, str]
    base_url: str
    shards_base_url: str
    fetched_at: datetime.datetime


@dataclass
class _CachedPackageShard:
    records: list[dict[str, Any]]


class RepoData:
    """Access to the package directory on anaconda cloud

    Each repository is an indexed SQLite database in the user's cache directory,
    refreshed after eight hours. Processes share disk pages rather than Python
    tables, and coordinate refreshes with OS advisory locks.
    Local file channels are read afresh on every query.

    Data structure:

    Each **channel** hosted at anaconda cloud comprises a number of
    **subdirs** in which the individual package files reside. The
    **subdirs** can be one of **noarch**, **osx-64** and **linux-64**
    for Bioconda. (Technically ``(noarch|(linux|osx|win)-(64|32))``
    appears to be the schema).

    For **channel/subdir** (aka **channel/platform**) combination, a
    **repodata.json** contains a **package** key describing each
    package file with at least the following information:

    name: Package name (lowercase, alphanumeric + dash)

    version: Version (no dash, PEP440)

    build_number: Non negative integer indicating packaging revisions

    build: String comprising hash of pinned dependencies and build
      number. Used to distinguish different builds of the same
      package/version combination.

    depends: Runtime requirements for package as list of strings.

    arch: Architecture key (x86_64). Not used by conda and not loaded
      here.

    platform: Platform of package (osx, linux, noarch). Optional
      upstream, not used by conda. We generate this from the subdir
      information to have it available.

    Repodata versions:

    The version is indicated by the key **repodata_version**, with
    absence of that key indication version 0.

    In version 0, the **info** key contains the **subdir**,
    **platform**, **arch**, **default_python_version** and
    **default_numpy_version** keys. In version 1 it only contains the
    **subdir** key.

    In version 1, a key **removed** was added, listing packages
    removed from the repository.

    """

    REPODATA_URL = "https://conda.anaconda.org/{channel}/{subdir}/repodata.json"
    REPODATA_DEFAULTS_URL = "https://repo.anaconda.com/pkgs/main/{subdir}/repodata.json"
    LOCAL_REPODATA = "{channel}/{subdir}/repodata.json"

    _load_columns: ClassVar = ["build", "build_number", "name", "version", "depends"]

    #: Columns available to package queries
    columns = _load_columns + ["channel", "subdir", "platform"]
    #: Conda repodata subdirs loaded by default; ``platform`` returns subdir strings.
    platforms: ClassVar[list[Subdir]] = [*ALL_PACKAGE_SUBDIRS, "noarch"]
    # config object
    config = None

    _shard_index_cache: ClassVar[dict[str, _CachedShardIndex]] = {}
    _package_shard_cache: ClassVar[dict[str, _CachedPackageShard]] = {}
    _package_shards_unavailable: ClassVar[dict[str, datetime.datetime]] = {}
    _shards_unavailable: ClassVar[dict[str, datetime.datetime]] = {}

    #: Retry unavailable shard indexes sooner than successful cache entries.
    shards_unavailable_timeout = 60 * 5
    package_shards_unavailable_timeout = 60 * 5

    #: Shard indexes are mutable; content-addressed package shards are not.
    shard_index_cache_timeout = 60 * 15
    package_shard_cache_max_entries = 4096

    cache_dir: ClassVar[Path | None] = None
    refresh_after: ClassVar[datetime.datetime | None] = None

    #: Repository lifetime; no full-table in-memory cache is kept.
    cache_timeout: ClassVar[float] = 60 * 60 * 8

    @classmethod
    def register_config(cls, config):
        cls.config = config

    @classmethod
    def configure_cache(
        cls, directory: Path | None = None, *, refresh: bool = False
    ) -> None:
        """Choose a cache directory; None restores the platform/XDG default."""
        cls._shard_index_cache.clear()
        cls._package_shard_cache.clear()
        cls._shards_unavailable.clear()
        cls._package_shards_unavailable.clear()
        cls.cache_dir = directory.resolve() if directory is not None else None
        cls.refresh_after = datetime.datetime.now(datetime.UTC) if refresh else None

    @classmethod
    def get_cache_dir(cls) -> Path:
        return cls.cache_dir or get_cache_root() / "repodata-v2"

    @property
    def channels(self):
        assert self.config is not None, "Load configuration before querying repodata"
        return self.config["channels"]

    def _cache_path(self, url: str) -> Path:
        return self.get_cache_dir() / (sha256(url.encode()).hexdigest() + ".sqlite")

    def _is_fresh(self, fetched_at: float) -> bool:
        now = datetime.datetime.now(datetime.UTC).timestamp()
        return 0 <= now - fetched_at < self.cache_timeout and (
            self.refresh_after is None or fetched_at >= self.refresh_after.timestamp()
        )

    def _read_cache(self, path: Path) -> sqlite3.Connection | None:
        """Open a fresh database without deserializing its package records."""
        if not path.exists():
            return None
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            # Bound private page caches. The OS shares file pages across workers.
            connection.execute("PRAGMA cache_size=-512")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise sqlite3.DatabaseError("incompatible repodata cache")
            fetched_at = connection.execute(
                "SELECT fetched_at FROM metadata"
            ).fetchone()[0]
            connection.execute(
                "SELECT name, version, build, build_number, depends FROM packages LIMIT 0"
            )
            if self._is_fresh(fetched_at):
                return connection
        except (sqlite3.DatabaseError, TypeError, IndexError):
            logger.warning("Ignoring unreadable repodata cache %s", path)
        if connection is not None:
            connection.close()
        return None

    @staticmethod
    def _populate_database(
        connection: sqlite3.Connection, raw: bytes, *, fetched_at: float
    ) -> None:
        data = json.loads(raw)
        packages = data["packages"]
        packages.update(data.get("packages.conda", {}))
        RepoData._populate_records(
            connection,
            packages.values(),
            subdir=data["info"]["subdir"],
            fetched_at=fetched_at,
        )

    @staticmethod
    def _populate_records(
        connection: sqlite3.Connection,
        records: Iterable[dict[str, Any]],
        *,
        subdir: Subdir,
        fetched_at: float,
    ) -> None:
        with connection:
            connection.execute("CREATE TABLE metadata (fetched_at REAL, subdir TEXT)")
            connection.execute(
                "INSERT INTO metadata VALUES (?, ?)",
                (fetched_at, subdir),
            )
            connection.execute(
                "CREATE TABLE packages (name TEXT, version TEXT, build TEXT, build_number INTEGER, depends TEXT)"
            )
            connection.executemany(
                "INSERT INTO packages VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        record.get("name"),
                        str(record.get("version")),
                        record.get("build"),
                        record.get("build_number"),
                        json.dumps(record.get("depends")),
                    )
                    for record in records
                ),
            )
            connection.execute(
                "CREATE INDEX package_lookup ON packages (name, version, build_number, build)"
            )
            connection.execute("PRAGMA user_version=2")

    @classmethod
    def _write_cache(
        cls, path: Path, raw: bytes, *, fetched_at: float | None = None
    ) -> None:
        """Build a complete replacement; readers retain a consistent old inode."""
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".repodata-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with closing(sqlite3.connect(temporary)) as connection:
                cls._populate_database(
                    connection,
                    raw,
                    fetched_at=datetime.datetime.now(datetime.UTC).timestamp()
                    if fetched_at is None
                    else fetched_at,
                )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
            temporary.with_name(temporary.name + "-journal").unlink(missing_ok=True)

    def _download_repository(self, channel: str, subdir: Subdir) -> bytes:
        url = self._make_repodata_url(channel, subdir)
        return fetch([url], [f"{channel}/{subdir}"], None, [(channel, subdir)])[0]

    def _repositories(self, channels: Iterable[str], subdirs: Iterable[Subdir]):
        return (
            (channel, subdir)
            for channel, subdir in product(
                dict.fromkeys(channels), dict.fromkeys(subdirs)
            )
            if channel in self.channels
        )

    @contextmanager
    def _open_repository(self, channel: str, subdir: Subdir):
        url = self._make_repodata_url(channel, subdir)
        if url.startswith("file://"):
            with closing(sqlite3.connect(":memory:")) as connection:
                self._populate_database(
                    connection,
                    self._download_repository(channel, subdir),
                    fetched_at=datetime.datetime.now(datetime.UTC).timestamp(),
                )
                yield connection
            return
        path = self._cache_path(url)
        connection = self._read_cache(path)
        if connection is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Hold only one repository lock at a time. A refresh builds one
            # repository at a time, bounding peak JSON/table memory as well.
            with (
                file_lock(path.with_suffix(".lock")),
                file_lock(path.parent / "refresh.lock"),
            ):
                connection = self._read_cache(path)
                if connection is None:
                    # The refresh lock excludes every active builder. Recover
                    # temporary files left by SIGKILL without touching other files.
                    for abandoned in path.parent.glob(".repodata-*.tmp*"):
                        abandoned.unlink(missing_ok=True)
                    self._write_cache(path, self._download_repository(channel, subdir))
                    connection = self._read_cache(path)
                    if connection is None:
                        raise RuntimeError(f"New repodata cache is invalid: {path}")
        with closing(connection):
            yield connection

    def _make_shards_index_url(self, channel: Channel, subdir: Subdir) -> str:
        return f"{self._make_repodata_url(channel, subdir).rsplit('/', 1)[0] + '/'}repodata_shards.msgpack.zst"

    def _make_shard_url(
        self,
        channel: Channel,
        subdir: Subdir,
        shard_index: _CachedShardIndex,
        hex_hash: str,
    ) -> str:
        index_url = self._make_shards_index_url(channel, subdir)
        shards_base = shard_index.shards_base_url or "./"
        if not shards_base.endswith("/"):
            shards_base += "/"
        shards_url = urljoin(index_url, shards_base)
        return urljoin(shards_url, f"{hex_hash}.msgpack.zst")

    @staticmethod
    def _parse_shard_index(
        data: bytes | None, repo_meta: RepoDataKey
    ) -> _CachedShardIndex | None:
        if not data:
            return None
        channel, subdir = repo_meta
        dctx = zstandard.ZstdDecompressor()
        try:
            decompressed = dctx.stream_reader(data).read()
            raw = msgpack.unpackb(decompressed)

            if not isinstance(raw, Mapping) or raw.get("version") != 1:
                raise ValueError("invalid or unsupported shard index")
            raw_shards = raw.get("shards")
            info = raw.get("info")
            if not isinstance(raw_shards, Mapping) or not isinstance(info, Mapping):
                raise TypeError("invalid shard index mappings")

            shards_map: dict[str, str] = {}
            for pkg_name, hash_val in raw_shards.items():
                if not isinstance(pkg_name, str) or not isinstance(
                    hash_val, (bytes, str)
                ):
                    raise TypeError("invalid shard index entry")
                shards_map[pkg_name] = (
                    hash_val.hex() if isinstance(hash_val, bytes) else hash_val
                )

            base_url = info.get("base_url", "")
            shards_base_url = info.get("shards_base_url", "")
            if not isinstance(base_url, str) or not isinstance(shards_base_url, str):
                raise TypeError("invalid shard index URLs")
        except (
            zstandard.ZstdError,
            msgpack.UnpackException,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ) as exc:
            logger.debug(
                "Failed to decompress/unpack shards index for %s/%s: %s",
                channel,
                subdir,
                exc,
            )
            return None

        return _CachedShardIndex(
            shards=shards_map,
            base_url=base_url,
            shards_base_url=shards_base_url,
            fetched_at=datetime.datetime.now(datetime.UTC),
        )

    @classmethod
    def _parse_package_shard(
        cls,
        data: bytes | None,
        meta_data: tuple[Channel, Subdir, str],
    ) -> list[dict[str, Any]] | None:
        if not data:
            return None
        channel, subdir, package_name = meta_data
        dctx = zstandard.ZstdDecompressor()
        try:
            decompressed = dctx.stream_reader(data).read()
            shard = msgpack.unpackb(decompressed)

            if not isinstance(shard, Mapping):
                raise TypeError("invalid package shard")
            raw_packages = shard.get("packages")
            raw_conda_packages = shard.get("packages.conda")
            if not isinstance(raw_packages, Mapping) or not isinstance(
                raw_conda_packages, Mapping
            ):
                raise TypeError("invalid package shard mappings")

            packages = dict(raw_packages)
            packages.update(raw_conda_packages)

            records: list[dict[str, Any]] = []
            for raw_record in packages.values():
                if not isinstance(raw_record, Mapping):
                    raise TypeError("invalid package record")
                rec = {
                    "name": raw_record.get("name", package_name),
                    "version": str(raw_record.get("version", "")),
                    "build": raw_record.get("build", ""),
                    "build_number": int(raw_record.get("build_number", 0)),
                    "depends": list(raw_record.get("depends", [])),
                    "channel": channel,
                    "platform": subdir,
                    "subdir": raw_record.get("subdir", subdir),
                }
                records.append(rec)
        except (
            zstandard.ZstdError,
            msgpack.UnpackException,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ) as exc:
            logger.debug(
                "Failed to decompress/unpack shard for %s/%s/%s: %s",
                channel,
                subdir,
                package_name,
                exc,
            )
            return None
        return records

    def _get_shard_indexes(
        self, repositories: Iterable[RepoDataKey]
    ) -> dict[RepoDataKey, _CachedShardIndex]:
        now = datetime.datetime.now(datetime.UTC)
        result: dict[RepoDataKey, _CachedShardIndex] = {}
        missing: list[RepoDataKey] = []

        for repo in repositories:
            cache_key = self._make_shards_index_url(*repo)
            unavailable_at = self._shards_unavailable.get(cache_key)
            if unavailable_at is not None:
                if (
                    0
                    <= (now - unavailable_at).total_seconds()
                    <= self.shards_unavailable_timeout
                ):
                    continue
                self._shards_unavailable.pop(cache_key, None)
            if cache_key in self._shard_index_cache:
                cached = self._shard_index_cache[cache_key]
                if (
                    0
                    <= (now - cached.fetched_at).total_seconds()
                    <= self.shard_index_cache_timeout
                ):
                    result[repo] = cached
                    continue
                self._shard_index_cache.pop(cache_key, None)
            missing.append(repo)

        if missing:
            urls = [self._make_shards_index_url(c, s) for c, s in missing]
            descs = [f"{c}/{s} shards index" for c, s in missing]
            try:
                indexes = fetch(
                    urls,
                    descs,
                    transform=self._parse_shard_index,
                    metadata=missing,
                    allow_404=True,
                    return_exceptions=True,
                )
                for repo, index in zip(missing, indexes):
                    cache_key = self._make_shards_index_url(*repo)
                    if isinstance(index, Exception):
                        logger.debug(
                            "Error fetching shard index for %s/%s: %s", *repo, index
                        )
                        self._shards_unavailable[cache_key] = now
                    elif index is not None:
                        self._shards_unavailable.pop(cache_key, None)
                        self._shard_index_cache[cache_key] = index
                        result[repo] = index
                    else:
                        self._shards_unavailable[cache_key] = now
            except (aiohttp.ClientError, OSError, TimeoutError, RuntimeError) as exc:
                logger.debug("Error batch fetching shard indexes: %s", exc)
                for repo in missing:
                    cache_key = self._make_shards_index_url(*repo)
                    self._shards_unavailable[cache_key] = now

        return result

    def _get_repository_shards(
        self, channel: Channel, subdir: Subdir, names: list[str]
    ) -> list[dict[str, Any]] | None:
        """Return targeted records, or None when full repodata is needed.

        A missing name in a valid index is authoritative. Any failed package
        download falls back for the whole repository, avoiding duplicate rows.
        """
        if not names:
            return []
        if channel.startswith("file://"):
            return None
        index = self._get_shard_indexes([(channel, subdir)]).get((channel, subdir))
        if index is None:
            return None
        records_by_url: dict[str, list[dict[str, Any]]] = {}
        missing: dict[str, str] = {}
        now = datetime.datetime.now(datetime.UTC)
        for name in dict.fromkeys(names):
            shard_hash = index.shards.get(name)
            if shard_hash is None:
                continue
            url = self._make_shard_url(channel, subdir, index, shard_hash)
            cached = self._package_shard_cache.get(url)
            if cached is not None:
                records_by_url[url] = cached.records
                continue
            unavailable_at = self._package_shards_unavailable.get(url)
            if (
                unavailable_at is not None
                and 0
                <= (now - unavailable_at).total_seconds()
                < self.package_shards_unavailable_timeout
            ):
                return None
            records_by_url.setdefault(url, [])
            missing.setdefault(url, name)

        failed = False
        if missing:
            results = fetch(
                list(missing),
                [f"{channel}/{subdir}/{name} shard" for name in missing.values()],
                None,
                [(channel, subdir)] * len(missing),
                allow_404=True,
                return_exceptions=True,
            )
            for (url, name), raw in zip(missing.items(), results):
                records = (
                    None
                    if isinstance(raw, Exception)
                    else self._parse_package_shard(raw, (channel, subdir, name))
                )
                if records is None:
                    self._package_shards_unavailable[url] = now
                    failed = True
                    continue
                self._package_shards_unavailable.pop(url, None)
                if (
                    len(self._package_shard_cache)
                    >= self.package_shard_cache_max_entries
                ):
                    self._package_shard_cache.pop(next(iter(self._package_shard_cache)))
                self._package_shard_cache[url] = _CachedPackageShard(records)
                records_by_url[url] = records
        if failed:
            return None
        wanted = set(names)
        return [
            record
            for records in records_by_url.values()
            for record in records
            if record["name"] in wanted
        ]

    @contextmanager
    def _open_query_repository(self, channel: Channel, subdir: Subdir, names):
        records = (
            None
            if names is None
            else self._get_repository_shards(channel, subdir, names)
        )
        if records is None:
            with self._open_repository(channel, subdir) as connection:
                yield connection
            return
        # Share SQL filtering and projection with full repodata. Only requested
        # shards are materialized; no full repository Python table is retained.
        with closing(sqlite3.connect(":memory:")) as connection:
            self._populate_records(
                connection,
                records,
                subdir=subdir,
                fetched_at=datetime.datetime.now(datetime.UTC).timestamp(),
            )
            yield connection

    def _make_repodata_url(self, channel, subdir: Subdir):
        if channel == "defaults":
            # caveat: this only gets defaults main, not 'free', 'r' or 'pro'
            url_template = self.REPODATA_DEFAULTS_URL
        else:
            url_template = self.REPODATA_URL
        local_url_template = self.LOCAL_REPODATA

        if channel.startswith("file://"):  # Allow local channels
            url = local_url_template.format(channel=channel, subdir=subdir)
        else:
            url = url_template.format(channel=channel, subdir=subdir)
        return url

    @staticmethod
    def native_subdir() -> PackageSubdir:
        """Return the conda package subdir notation for this host."""
        if sys.platform.startswith("linux"):
            return container_platform_to_package_subdir(native_container_platform())
        if sys.platform.startswith("darwin"):
            arch = platform.machine().lower()
            return "osx-arm64" if arch == "arm64" else "osx-64"
        raise ValueError("Running on unsupported platform")

    def get_versions(self, name):
        """Get versions available for package

        Args:
          name: package name

        Returns:
          Dictionary mapping version numbers to list of subdirs
          e.g. {'0.1': ['linux-64'], '0.2': ['linux-64', 'osx-64'], '0.3': ['noarch']}
        """
        versions: dict[str, set[str]] = {}
        for version, subdir in self.get_package_data(
            ["version", "platform"], name=name
        ):
            versions.setdefault(version, set()).add(subdir)
        return {version: sorted(versions[version]) for version in sorted(versions)}

    def get_package_data(
        self,
        key=None,
        channels=None,
        name=None,
        version=None,
        build_number=None,
        platform=None,
        build=None,
        native=False,
    ):
        """Get **key** for each package in **channels**

        If **key** is not give, returns bool whether there are matches.
        If **key** is a string, returns list of strings.
        If **key** is a list of string, returns tuple iterator.
        """
        if native:
            platform = ["noarch", self.native_subdir()]

        if version is not None:
            version = (
                [str(value) for value in version]
                if isinstance(version, (list, tuple))
                else str(version)
            )

        if isinstance(channels, str):
            requested_channels = [channels]
        elif channels is None:
            requested_channels = self.channels
        else:
            requested_channels = channels

        if isinstance(platform, str):
            requested_subdirs = [cast(Subdir, platform)]
        elif platform is None:
            requested_subdirs = self.platforms
        else:
            requested_subdirs = [cast(Subdir, subdir) for subdir in platform]

        keys = [] if key is None else [key] if isinstance(key, str) else list(key)
        if not keys and key is not None:
            return iter(())
        for column in keys:
            if column not in self.columns:
                raise KeyError(column)
        clauses, parameters = [], []
        for column, value in (
            ("name", name),
            ("version", version),
            ("build_number", build_number),
            ("build", build),
        ):
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                clauses.append(f"{column} IN ({','.join('?' for _ in value)})")
                parameters.extend(value)
            else:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        package_names = None
        if name is not None:
            package_names = list(name) if isinstance(name, (list, tuple)) else [name]
        Package = namedtuple("Package", keys, rename=True)
        rows = []
        for channel, subdir in self._repositories(
            requested_channels, requested_subdirs
        ):
            with self._open_query_repository(
                channel,
                subdir,
                package_names,
            ) as connection:
                constants = {"channel": channel, "platform": subdir}
                if "subdir" in keys:
                    constants["subdir"] = connection.execute(
                        "SELECT subdir FROM metadata"
                    ).fetchone()[0]
                projection = (
                    ", ".join("?" if column in constants else column for column in keys)
                    if keys
                    else "1"
                )
                values = [
                    constants[column] for column in keys if column in constants
                ] + parameters
                query = "SELECT " + projection + " FROM packages" + where
                if key is None:
                    if (
                        connection.execute(query + " LIMIT 1", values).fetchone()
                        is not None
                    ):
                        return True
                else:
                    for row in connection.execute(query + " ORDER BY rowid", values):
                        decoded = [
                            json.loads(value) if column == "depends" else value
                            for column, value in zip(keys, row)
                        ]
                        rows.append(
                            decoded[0] if isinstance(key, str) else Package(*decoded)
                        )
        if key is None:
            return False
        if isinstance(key, str):
            return rows
        return iter(rows)


@memoize(expire=604800)
def get_package_downloads(channel: str, package: str) -> int:
    """Use anaconda API to obtain download counts."""
    data = requests.get(f"https://api.anaconda.org/package/{channel}/{package}").json()
    if "files" in data:
        return sum(rec["ndownloads"] for rec in data["files"])
    return 0
