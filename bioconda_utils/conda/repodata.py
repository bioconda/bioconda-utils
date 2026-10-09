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
from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager
from hashlib import sha256
from itertools import product, zip_longest
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any, ClassVar, cast

import aiofiles
import aiohttp
import requests

from .._types import (
    ALL_PACKAGE_SUBDIRS,
    PackageSubdir,
    Subdir,
    container_platform_to_package_subdir,
    native_container_platform,
)
from ..support import http
from ..support.caching import disk_cache, file_lock, get_cache_root
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
            res = pool.apply(fetch, (urls, descriptions, transform, metadata))
        return res

    # asyncio.run cancels the fetch on SIGINT before raising
    # KeyboardInterrupt, so pending connections close cleanly
    return cast(
        list[Any],
        asyncio.run(async_fetch(urls, descriptions, transform, metadata)),
    )


async def async_fetch(
    urls: Iterable[str] = (),
    descriptions: Iterable[str] = (),
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: Iterable[RepoDataKey] | None = None,
) -> list[Any | bytes]:
    if metadata is None:
        metadata = []
    conn = aiohttp.TCPConnector(limit_per_host=CONNECTIONS_PER_HOST)
    async with http.make_session(connector=conn) as session:
        coros = [
            asyncio.create_task(
                _async_fetch_one(
                    session,
                    url,
                    description or url,
                    transform=transform,
                    metadata=datum,
                )
            )
            for url, description, datum in zip_longest(urls, descriptions, metadata)
            if url is not None
        ]
        try:
            with progress_display.count_task("Downloading", total=len(coros)) as (
                progress,
                task,
            ):
                result = [
                    await coro
                    for coro in progress.track(
                        asyncio.as_completed(coros),
                        total=len(coros),
                        task_id=task,
                    )
                ]
        finally:
            for coro in coros:
                if not coro.done():
                    coro.cancel()
            if any(not coro.done() for coro in coros):
                await asyncio.gather(*coros, return_exceptions=True)
    return result


@http.retry_on_transient
async def _async_fetch_one(
    session: aiohttp.ClientSession,
    url: str,
    description: str,
    transform: Callable[[bytes, RepoDataKey], Any] | None = None,
    metadata: RepoDataKey | None = None,
) -> Any | bytes:
    if url.startswith("file://"):
        local_path = Path(url[7:])
        if local_path.exists():
            async with aiofiles.open(local_path, mode="rb") as f:
                raw = await f.read()
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

    #: Columns available in internal dataframe
    columns = _load_columns + ["channel", "subdir", "platform"]
    #: Conda repodata subdirs loaded by default. The dataframe ``platform``
    #: column stores these subdir strings directly for historical reasons.
    platforms: ClassVar[list[Subdir]] = [*ALL_PACKAGE_SUBDIRS, "noarch"]
    # config object
    config = None

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
        with connection:
            connection.execute("CREATE TABLE metadata (fetched_at REAL, subdir TEXT)")
            connection.execute(
                "INSERT INTO metadata VALUES (?, ?)",
                (fetched_at, data["info"]["subdir"]),
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
                    for record in packages.values()
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
        Package = namedtuple("Package", keys, rename=True)
        rows = []
        for channel, subdir in self._repositories(
            requested_channels, requested_subdirs
        ):
            with self._open_repository(channel, subdir) as connection:
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


@disk_cache.memoize(expire=604800)
def get_package_downloads(channel: str, package: str) -> int:
    """Use anaconda API to obtain download counts."""
    data = requests.get(f"https://api.anaconda.org/package/{channel}/{package}").json()
    if "files" in data:
        return sum(rec["ndownloads"] for rec in data["files"])
    return 0
