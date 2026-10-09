"""
Access to the conda package directory (repodata) of anaconda.org channels.

:class:`RepoData` that loads channel/subdir repodata,
caches it in memory and on disk, and answers package queries.
:func:`fetch` is the parallel HTTP downloader it relies on.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import pickle
import platform
import sys
import tempfile
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from itertools import product, zip_longest
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import ClassVar, cast

import aiofiles
import aiohttp
import pandas as pd
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


@dataclass
class _CachedRepoData:
    dataframe: pd.DataFrame
    fetched_at: datetime.datetime


#: Max connections to each server
CONNECTIONS_PER_HOST = 4


def fetch(
    urls: Iterable[str],
    descriptions: Iterable[str],
    transform: Callable[[bytes, RepoDataKey], pd.DataFrame],
    metadata: Iterable[RepoDataKey],
) -> list[pd.DataFrame]:
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
    # transform is required here, so every item went through it --
    # the cast spells out what the checker cannot infer.
    return cast(
        list[pd.DataFrame],
        asyncio.run(async_fetch(urls, descriptions, transform, metadata)),
    )


async def async_fetch(
    urls: Iterable[str] = (),
    descriptions: Iterable[str] = (),
    transform: Callable[[bytes, RepoDataKey], pd.DataFrame] | None = None,
    metadata: Iterable[RepoDataKey] | None = None,
) -> list[pd.DataFrame | bytes]:
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
    transform: Callable[[bytes, RepoDataKey], pd.DataFrame] | None = None,
    metadata: RepoDataKey | None = None,
) -> pd.DataFrame | bytes:
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

    Each repository is cached for eight hours in memory and in the user's
    cache directory. Processes coordinate refreshes with OS advisory locks.
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

    depends: Runtime requirements for package as list of strings. We
      do not currently load this.

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
    _repository_cache: ClassVar[dict[str, _CachedRepoData]] = {}

    #: The same lifetime applies to memory and disk entries.
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
        cls._repository_cache.clear()

    @classmethod
    def get_cache_dir(cls) -> Path:
        return cls.cache_dir or get_cache_root() / "repodata-v1"

    @property
    def channels(self):
        assert self.config is not None, "Load configuration before querying repodata"
        return self.config["channels"]

    @property
    def df(self) -> pd.DataFrame:
        """Assemble a view of configured repositories without a second cache."""
        frames = self._get_repository_dataframes(self.channels, self.platforms)
        return (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=self.columns)
        )

    def _cache_path(self, url: str) -> Path:
        return self.get_cache_dir() / (sha256(url.encode()).hexdigest() + ".pkl")

    def _is_fresh(self, entry: _CachedRepoData) -> bool:
        age = (datetime.datetime.now(datetime.UTC) - entry.fetched_at).total_seconds()
        return 0 <= age < self.cache_timeout and (
            self.refresh_after is None or entry.fetched_at >= self.refresh_after
        )

    def _read_cache(self, path: Path) -> _CachedRepoData | None:
        try:
            entry = pd.read_pickle(path)
            if not isinstance(entry, _CachedRepoData) or not set(self.columns).issubset(
                entry.dataframe.columns
            ):
                raise ValueError("incompatible repodata cache")
            return entry if self._is_fresh(entry) else None
        except FileNotFoundError:
            return None
        except (
            pickle.UnpicklingError,
            EOFError,
            ValueError,
            AttributeError,
            ImportError,
            TypeError,
        ):
            logger.warning("Ignoring unreadable repodata cache %s", path)
            return None

    @staticmethod
    def _write_cache(path: Path, entry: _CachedRepoData) -> None:
        # Readers never see a partial pickle. The lock serializes refreshes;
        # atomic replacement also protects a reader if the writer is killed.
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".tmp", delete=False
        ) as tmp:
            temporary = Path(tmp.name)
        try:
            pd.to_pickle(entry, temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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

    def _load_channel_dataframe(
        self, repositories: Iterable[tuple[str, Subdir]] | None = None
    ):
        repos = list(
            product(self.channels, self.platforms)
            if repositories is None
            else repositories
        )
        urls = [self._make_repodata_url(c, p) for c, p in repos]
        descriptions = [f"{c}/{p}" for c, p in repos]

        def to_dataframe(json_data: bytes, meta_data: RepoDataKey) -> pd.DataFrame:
            channel, platform = meta_data
            raw = json.loads(json_data)
            subdir = raw["info"]["subdir"]
            packages = raw["packages"]
            packages.update(raw.get("packages.conda", {}))

            df = pd.DataFrame.from_dict(packages, "index", columns=self._load_columns)
            # Ensure that version is always a string.
            df["version"] = df["version"].astype(str)
            df["channel"] = channel
            df["platform"] = platform
            df["subdir"] = subdir
            return df

        if urls:
            dfs = fetch(urls, descriptions, to_dataframe, repos)
            res = pd.concat(dfs)
        else:
            res = pd.DataFrame(columns=self.columns)

        for col in (
            "channel",
            "platform",
            "subdir",
            "name",
            "version",
            "build",
        ):
            res[col] = res[col].astype("category")
        res = res.reset_index(drop=True)

        return res

    def _get_repository_dataframes(
        self, channels: Iterable[str], subdirs: Iterable[Subdir]
    ) -> list[pd.DataFrame]:
        """Reuse repository entries, fetching each missing URL once across processes."""
        configured = set(self.channels)
        repositories = {
            self._make_repodata_url(channel, subdir): (channel, subdir)
            for channel, subdir in product(
                dict.fromkeys(channels), dict.fromkeys(subdirs)
            )
            if channel in configured
        }
        frames = {}
        missing = {}
        for url, repository in repositories.items():
            if url.startswith("file://"):
                frames[url] = self._load_channel_dataframe([repository])
            elif (
                entry := self._repository_cache.get(url)
            ) is not None and self._is_fresh(entry):
                frames[url] = entry.dataframe
            else:
                missing[url] = repository
        if missing:
            self.get_cache_dir().mkdir(parents=True, exist_ok=True)
            with ExitStack() as locks:
                # A fixed order prevents deadlocks between overlapping requests.
                for url in sorted(missing):
                    locks.enter_context(
                        file_lock(self._cache_path(url).with_suffix(".lock"))
                    )
                to_load = {}
                for url, repository in missing.items():
                    entry = self._read_cache(self._cache_path(url))
                    if entry is None:
                        to_load[url] = repository
                    else:
                        self._repository_cache[url] = entry
                        frames[url] = entry.dataframe
                if to_load:
                    loaded = self._load_channel_dataframe(to_load.values())
                    fetched_at = datetime.datetime.now(datetime.UTC)
                    for url, (channel, subdir) in to_load.items():
                        entry = _CachedRepoData(
                            loaded[
                                (loaded["channel"] == channel)
                                & (loaded["platform"] == subdir)
                            ].reset_index(drop=True),
                            fetched_at,
                        )
                        self._write_cache(self._cache_path(url), entry)
                        self._repository_cache[url] = entry
                        frames[url] = entry.dataframe
        return [frames[url] for url in repositories]

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
        # called from doc generator
        packages = pd.DataFrame(
            self.get_package_data(["version", "platform"], name=name),
            columns=["version", "platform"],
        )
        versions = packages.groupby("version", observed=True).agg(
            lambda x: list(set(x))
        )
        return versions["platform"].to_dict()

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
            version = str(version)

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

        frames = self._get_repository_dataframes(requested_channels, requested_subdirs)
        channel_filter = requested_channels if channels is not None else None
        platform_filter = requested_subdirs if platform is not None else None
        # We iteratively drill down here, starting with the (probably)
        # most specific columns. Filtering this way on a large data frame
        # is much faster than executing the comparisons for all values
        # every time, in particular if we are looking at a specific package.
        # NB: cheap, high-selectivity filters come first so that later,
        #     expensive filters (e.g. high-cardinality categoricals such as
        #     "build", or int comparisons that box every value) only run on
        #     an already tiny frame. The result is identical either way.
        filters = (
            ("name", name),
            ("version", version),
            ("channel", channel_filter),
            ("platform", platform_filter),
            ("build_number", build_number),
            ("build", build),
        )
        selected = []
        for df in frames:
            for col, val in filters:
                if val is None:
                    continue
                df = (
                    df[df[col].isin(val)]
                    if isinstance(val, (list, tuple))
                    else df[df[col] == val]
                )
            if not df.empty:
                selected.append(df)
        df = (
            pd.concat(selected, ignore_index=True)
            if selected
            else pd.DataFrame(columns=self.columns)
        )

        if key is None:
            return not df.empty
        if isinstance(key, str):
            return list(df[key])
        return df[key].itertuples(index=False)


@disk_cache.memoize(expire=604800)
def get_package_downloads(channel: str, package: str) -> int:
    """Use anaconda API to obtain download counts."""
    data = requests.get(f"https://api.anaconda.org/package/{channel}/{package}").json()
    if "files" in data:
        return sum(rec["ndownloads"] for rec in data["files"])
    return 0
