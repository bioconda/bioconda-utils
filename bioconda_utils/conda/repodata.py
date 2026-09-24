"""
Access to the conda package directory (repodata) of anaconda.org channels.

:class:`RepoData` is a singleton that loads channel/subdir repodata,
caches it in memory and on disk, and answers package queries.
:class:`AsyncRequests` is the parallel HTTP downloader it relies on.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import platform
import sys
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product, zip_longest
from multiprocessing.pool import ThreadPool
from typing import ClassVar, TypeAlias, cast

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
from ..support.caching import disk_cache
from ..support.logsetup import tqdm

logger = logging.getLogger(__name__)


class BiocondaUtilsWarning(UserWarning):
    pass


RepoDataKey: TypeAlias = tuple[str, Subdir]


@dataclass
class _CachedRepoData:
    dataframe: pd.DataFrame
    fetched_at: datetime.datetime


class AsyncRequests:
    """Download a bunch of files in parallel

    This is not really a class, more a name space encapsulating a bunch of calls.
    """

    #: Identify ourselves
    USER_AGENT = http.USER_AGENT
    #: Max connections to each server
    CONNECTIONS_PER_HOST = 4

    @classmethod
    def fetch(cls, urls, descs, cb, datas):
        """Fetch data from URLs.

        This will use asyncio to manage a pool of connections at once, speeding
        up download as compared to iterative use of ``requests`` significantly.
        It will also retry on non-permanent HTTP error codes (i.e. 429, 502,
        503 and 504).

        Args:
          urls: List of URLS
          descs: Matching list of descriptions (for progress display)
          cb: As each download is completed, data is passed through this function.
              Use to e.g. offload json parsing into download loop.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            logger.warning("Running AsyncRequests.fetch from within running loop")
            # Workaround the fact that asyncio's loop is marked as not-reentrant
            # (it is apparently easy to patch, but not desired by the devs,
            with ThreadPool(1) as pool:
                res = pool.apply(cls.fetch, (urls, descs, cb, datas))
            return res

        task = asyncio.ensure_future(cls.async_fetch(urls, descs, cb, datas))

        try:
            loop.run_until_complete(task)
        except KeyboardInterrupt:
            task.cancel()
            loop.run_forever()
            task.exception()

        return task.result()

    @classmethod
    async def async_fetch(cls, urls, descs=None, cb=None, datas=None, fds=None):
        if descs is None:
            descs = []
        if datas is None:
            datas = []
        if fds is None:
            fds = []
        conn = aiohttp.TCPConnector(limit_per_host=cls.CONNECTIONS_PER_HOST)
        async with http.make_session(
            user_agent=cls.USER_AGENT,
            connector=conn,
        ) as session:
            coros = [
                asyncio.ensure_future(
                    cls._async_fetch_one(session, url, desc, cb, data, fd)
                )
                for url, desc, data, fd in zip_longest(urls, descs, datas, fds)
            ]
            with tqdm(
                asyncio.as_completed(coros),
                total=len(coros),
                desc="Downloading",
                unit="files",
            ) as t:
                result = [await coro for coro in t]
        return result

    @staticmethod
    @http.retry_on_transient
    async def _async_fetch_one(session, url, desc, cb=None, data=None, fd=None):
        result = []
        if url.startswith("file://"):
            if os.path.exists(url[7:]):
                async with aiofiles.open(url[7:], mode="rb") as f:
                    result.append(await f.read())
            else:
                subdir = url.split("/")[-2]
                d = {
                    "info": {"subdir": subdir},
                    "packages": {},
                    "packages.conda": {},
                    "removed": [],
                    "repodata_version": 1,
                }
                result.append(json.dumps(d).encode("UTF-8"))
        else:
            async with session.get(url, timeout=None) as resp:
                resp.raise_for_status()
                async for block in http.stream_download(
                    resp,
                    desc,
                    block_size=1024 * 16,
                    disable=logger.getEffectiveLevel() > logging.INFO,
                ):
                    if fd:
                        fd.write(block)
                    else:
                        result.append(block)
        if cb:
            return cb(b"".join(result), data)
        else:
            return b"".join(result)


class RepoData:
    """Singleton providing access to package directory on anaconda cloud

    If the first call provides a filename as **cache** argument, the
    file is used to cache the directory in CSV format.

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

    cache_file = None
    _df = None
    _df_ts = None
    _repository_cache: ClassVar[dict[RepoDataKey, _CachedRepoData]] = {}

    #: default lifetime for repodata cache
    cache_timeout = 60 * 60 * 8

    @classmethod
    def register_config(cls, config):
        previous_channels = tuple((cls.config or {}).get("channels", ()))
        current_channels = tuple(config.get("channels", ()))
        if previous_channels != current_channels:
            cls._df = None
            cls._df_ts = None
        cls.config = config

    __instance = None

    def __new__(cls):
        """Makes RepoData a singleton"""
        if RepoData.__instance is None:
            assert RepoData.config is not None, (
                "bug: ensure to load config before instantiating RepoData."
            )
            RepoData.__instance = object.__new__(cls)
        return RepoData.__instance

    def set_cache(self, cache):
        if self._df is not None:
            warnings.warn("RepoData cache set after first use", BiocondaUtilsWarning)
        else:
            self.cache_file = cache

    @property
    def channels(self):
        """Return channels to load."""
        assert self.config is not None
        return self.config["channels"]

    @property
    def df(self):
        """Internal Pandas DataFrame object

        Try not to use this ... the point of this class is to be able to
        change the structure in which the data is held.
        """
        if self._df_ts is not None:
            seconds = (
                datetime.datetime.now(datetime.UTC) - self._df_ts
            ).total_seconds()
        else:
            seconds = 0

        if self._df is None or seconds > self.cache_timeout:
            self._df = None
            self._df_ts = None
            self._df = self._load_channel_dataframe_cached()
            self._df_ts = datetime.datetime.now(datetime.UTC)
            self._repository_cache.clear()
        return self._df

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

    def _load_channel_dataframe_cached(self):
        if self.cache_file is not None and os.path.exists(self.cache_file):
            ts = datetime.datetime.fromtimestamp(
                os.path.getmtime(self.cache_file), datetime.UTC
            )
            seconds = (datetime.datetime.now(datetime.UTC) - ts).total_seconds()
            if seconds <= self.cache_timeout:
                logger.info("Loading repodata from cache %s", self.cache_file)
                return pd.read_pickle(self.cache_file)
            else:
                logger.info("Repodata cache file too old. Reloading")

        if self.cache_file is None:
            res = self._get_repository_dataframe(self.channels, self.platforms)
        else:
            res = self._load_channel_dataframe()

        if self.cache_file is not None:
            res.to_pickle(self.cache_file)
        return res

    def _load_channel_dataframe(
        self, repositories: Iterable[tuple[str, Subdir]] | None = None
    ):
        repos = list(
            product(self.channels, self.platforms)
            if repositories is None
            else repositories
        )
        urls = [self._make_repodata_url(c, p) for c, p in repos]
        descs = [f"{c}/{p}" for c, p in repos]

        def to_dataframe(json_data, meta_data):
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
            dfs = AsyncRequests.fetch(urls, descs, to_dataframe, repos)
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

    def _get_repository_dataframe(
        self, channels: Iterable[str], subdirs: Iterable[Subdir]
    ) -> pd.DataFrame:
        """Load and cache only the requested channel/subdirectory pairs."""
        if self._df is not None or self.cache_file is not None:
            return self.df

        configured_channels = set(self.channels)
        requested_channels = tuple(
            channel
            for channel in dict.fromkeys(channels)
            if channel in configured_channels
        )
        requested_subdirs = tuple(dict.fromkeys(subdirs))
        repositories = tuple(product(requested_channels, requested_subdirs))
        now = datetime.datetime.now(datetime.UTC)
        missing = [
            repository
            for repository in repositories
            if repository not in self._repository_cache
            or (now - self._repository_cache[repository].fetched_at).total_seconds()
            > self.cache_timeout
        ]
        if missing:
            loaded = self._load_channel_dataframe(missing)
            for channel, subdir in missing:
                repository = (channel, subdir)
                self._repository_cache[repository] = _CachedRepoData(
                    dataframe=loaded[
                        (loaded["channel"] == channel) & (loaded["subdir"] == subdir)
                    ],
                    fetched_at=now,
                )

        frames = [
            self._repository_cache[repository].dataframe for repository in repositories
        ]
        return pd.concat(frames) if frames else pd.DataFrame(columns=self.columns)

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
        packages = self.df[self.df.name == name][["version", "platform"]]
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

        df = self._get_repository_dataframe(requested_channels, requested_subdirs)
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
        for col, val in (
            ("name", name),  # thousands of different values
            ("version", version),  # still pretty good variety
            ("channel", channel_filter),  # 3 values
            ("platform", platform_filter),  # 3 values
            ("build_number", build_number),  # most values 0
            ("build", build),  # build string should vary a lot
        ):
            if val is None:
                continue
            if isinstance(val, (list, tuple)):
                df = df[df[col].isin(val)]
            else:
                df = df[df[col] == val]

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
