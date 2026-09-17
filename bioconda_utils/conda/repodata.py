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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain, product, zip_longest
from multiprocessing.pool import ThreadPool
from typing import Any, ClassVar, TypeAlias, cast
from urllib.parse import urljoin

import aiofiles
import aiohttp
import msgpack
import pandas as pd
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
from ..support.caching import disk_cache
from ..support.logsetup import tqdm

logger = logging.getLogger(__name__)


class BiocondaUtilsWarning(UserWarning):
    pass


RepoDataKey: TypeAlias = tuple[Channel, Subdir]


@dataclass
class _CachedRepoData:
    dataframe: pd.DataFrame
    fetched_at: datetime.datetime


@dataclass
class _CachedShardIndex:
    shards: dict[str, str]
    base_url: str
    shards_base_url: str
    fetched_at: datetime.datetime


@dataclass
class _CachedPackageShard:
    records: list[dict[str, Any]]
    shard_hash: str
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
    def fetch(cls, urls, descs, cb, datas, allow_404=False):
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
          allow_404: If true, missing resources produce ``None`` instead of an error.
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
                res = pool.apply(cls.fetch, (urls, descs, cb, datas, allow_404))
            return res

        task = asyncio.ensure_future(
            cls.async_fetch(urls, descs, cb, datas, allow_404=allow_404)
        )

        try:
            loop.run_until_complete(task)
        except KeyboardInterrupt:
            task.cancel()
            loop.run_forever()
            task.exception()

        return task.result()

    @classmethod
    async def async_fetch(
        cls, urls, descs=None, cb=None, datas=None, fds=None, allow_404=False
    ):
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

            async def fetch_indexed(index, url, desc, data, fd):
                return index, await cls._async_fetch_one(
                    session, url, desc, cb, data, fd, allow_404=allow_404
                )

            coros = [
                asyncio.ensure_future(fetch_indexed(index, url, desc, data, fd))
                for index, (url, desc, data, fd) in enumerate(
                    zip_longest(urls, descs, datas, fds)
                )
            ]
            result = [None] * len(coros)
            with tqdm(
                asyncio.as_completed(coros),
                total=len(coros),
                desc="Downloading",
                unit="files",
            ) as t:
                for coro in t:
                    index, value = await coro
                    result[index] = value
        return result

    @staticmethod
    @http.retry_on_transient
    async def _async_fetch_one(
        session, url, desc, cb=None, data=None, fd=None, allow_404=False
    ):
        result = []
        if url.startswith("file://"):
            if os.path.exists(url[7:]):
                async with aiofiles.open(url[7:], mode="rb") as f:
                    result.append(await f.read())
            elif allow_404:
                return cb(None, data) if cb else None
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
                if allow_404 and resp.status == 404:
                    return cb(None, data) if cb else None
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

    _load_columns: ClassVar = ["build", "build_number", "name", "version", "depends"]

    #: Columns available in internal dataframe
    columns = _load_columns + ["channel", "subdir", "platform"]
    #: Conda repodata subdirs loaded by default. The dataframe ``platform``
    #: column stores these subdir strings directly for historical reasons.
    platforms: ClassVar[list[Subdir]] = [*ALL_PACKAGE_SUBDIRS, "noarch"]
    # config object
    config = None

    cache_file: str | os.PathLike[str] | None = None
    _df = None
    _df_ts = None
    _repository_cache: ClassVar[dict[RepoDataKey, _CachedRepoData]] = {}

    _shard_index_cache: ClassVar[dict[RepoDataKey, _CachedShardIndex]] = {}
    _package_shard_cache: ClassVar[
        dict[tuple[Channel, Subdir, str], _CachedPackageShard]
    ] = {}
    _shards_unavailable: ClassVar[dict[RepoDataKey, datetime.datetime]] = {}

    #: Retry unavailable shard indexes sooner than successful cache entries.
    shards_unavailable_timeout = 60 * 5

    #: default lifetime for repodata cache
    cache_timeout = 60 * 60 * 8

    @classmethod
    def register_config(cls, config):
        previous_channels = tuple((cls.config or {}).get("channels", ()))
        current_channels = tuple(config.get("channels", ()))
        if previous_channels != current_channels:
            cls._df = None
            cls._df_ts = None
            cls._repository_cache.clear()
            cls._shard_index_cache.clear()
            cls._package_shard_cache.clear()
            cls._shards_unavailable.clear()
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

    def set_cache(self, cache: str | os.PathLike[str] | None) -> None:
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

    def _make_repodata_dir_url(self, channel: Channel, subdir: Subdir) -> str:
        """Return the base URL of a channel subdirectory.

        Single place that knows that ``defaults`` is an alias for
        ``pkgs/main`` on a different host (caveat: only main, not free/r/pro)
        and that ``file://`` channels are local paths.
        """
        if channel == "defaults":
            return f"https://repo.anaconda.com/pkgs/main/{subdir}/"
        elif channel.startswith("file://"):
            return f"{channel}/{subdir}/"
        else:
            return f"https://conda.anaconda.org/{channel}/{subdir}/"

    def _make_repodata_url(self, channel: Channel, subdir: Subdir) -> str:
        return f"{self._make_repodata_dir_url(channel, subdir)}repodata.json"

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
        self, repositories: Iterable[tuple[Channel, Subdir]] | None = None
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

    def _is_loader_mocked(self) -> bool:
        loader = getattr(self, "_load_channel_dataframe", None)
        if loader is None:
            return False
        return getattr(loader, "__func__", loader) != RepoData._load_channel_dataframe

    def _make_shards_index_url(self, channel: Channel, subdir: Subdir) -> str:
        return (
            f"{self._make_repodata_dir_url(channel, subdir)}repodata_shards.msgpack.zst"
        )

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
            unavailable_at = self._shards_unavailable.get(repo)
            if unavailable_at is not None:
                if (
                    now - unavailable_at
                ).total_seconds() <= self.shards_unavailable_timeout:
                    continue
                del self._shards_unavailable[repo]
            if repo in self._shard_index_cache:
                cached = self._shard_index_cache[repo]
                if (now - cached.fetched_at).total_seconds() <= self.cache_timeout:
                    result[repo] = cached
                    continue
                del self._shard_index_cache[repo]
            missing.append(repo)

        if missing:
            urls = [self._make_shards_index_url(c, s) for c, s in missing]
            descs = [f"{c}/{s} shards index" for c, s in missing]
            try:
                indexes = AsyncRequests.fetch(
                    urls,
                    descs,
                    cb=self._parse_shard_index,
                    datas=missing,
                    allow_404=True,
                )
                for repo, index in zip(missing, indexes):
                    if index is not None:
                        self._shards_unavailable.pop(repo, None)
                        self._shard_index_cache[repo] = index
                        result[repo] = index
                    else:
                        self._shards_unavailable[repo] = now
            except (aiohttp.ClientError, OSError, TimeoutError, RuntimeError) as exc:
                logger.debug("Error batch fetching shard indexes: %s", exc)
                for repo in missing:
                    self._shards_unavailable[repo] = now

        return result

    def _get_sharded_package_data(
        self,
        requested_channels: Sequence[Channel],
        requested_subdirs: Sequence[Subdir],
        package_names: Collection[str],
    ) -> list[dict[str, Any]]:
        repositories = tuple(product(requested_channels, requested_subdirs))
        shard_indexes = self._get_shard_indexes(repositories)
        now = datetime.datetime.now(datetime.UTC)

        records_by_repo: dict[RepoDataKey, list[dict[str, Any]]] = {
            repo: [] for repo in repositories
        }
        to_fetch_urls: list[str] = []
        to_fetch_descs: list[str] = []
        to_fetch_metadata: list[tuple[Channel, Subdir, str]] = []

        fallback_repos: set[RepoDataKey] = set()

        for repo in repositories:
            channel, subdir = repo
            shard_index = shard_indexes.get(repo)
            if shard_index is None:
                fallback_repos.add(repo)
                continue

            for pkg_name in package_names:
                if pkg_name not in shard_index.shards:
                    continue
                hex_hash = shard_index.shards[pkg_name]
                cache_key = (channel, subdir, pkg_name)
                if cache_key in self._package_shard_cache:
                    cached = self._package_shard_cache[cache_key]
                    if (
                        cached.shard_hash == hex_hash
                        and (now - cached.fetched_at).total_seconds()
                        <= self.cache_timeout
                    ):
                        records_by_repo[repo].extend(cached.records)
                        continue
                    del self._package_shard_cache[cache_key]

                url = self._make_shard_url(channel, subdir, shard_index, hex_hash)
                to_fetch_urls.append(url)
                to_fetch_descs.append(f"{channel}/{subdir}/{pkg_name} shard")
                to_fetch_metadata.append((channel, subdir, pkg_name))

        if to_fetch_urls:
            try:
                fetched_shards = AsyncRequests.fetch(
                    to_fetch_urls,
                    to_fetch_descs,
                    cb=self._parse_package_shard,
                    datas=to_fetch_metadata,
                    allow_404=True,
                )
                for meta, records in zip(to_fetch_metadata, fetched_shards):
                    channel, subdir, pkg_name = meta
                    repo = (channel, subdir)
                    if records is None:
                        fallback_repos.add(repo)
                        continue
                    recs = records
                    self._package_shard_cache[(channel, subdir, pkg_name)] = (
                        _CachedPackageShard(
                            records=recs,
                            shard_hash=shard_indexes[repo].shards[pkg_name],
                            fetched_at=now,
                        )
                    )
                    records_by_repo[repo].extend(recs)
            except (aiohttp.ClientError, OSError, TimeoutError, RuntimeError) as exc:
                logger.debug("Error fetching package shards: %s", exc)
                fallback_repos.update(
                    (channel, subdir) for channel, subdir, _ in to_fetch_metadata
                )

        if fallback_repos:
            ordered_fallback_repos = tuple(
                repo for repo in repositories if repo in fallback_repos
            )
            df = self._get_repository_pairs_dataframe(ordered_fallback_repos)
            df_filtered = df[df["name"].isin(package_names)]
            for channel, subdir in ordered_fallback_repos:
                records_by_repo[(channel, subdir)] = df_filtered[
                    (df_filtered["channel"] == channel)
                    & (df_filtered["subdir"] == subdir)
                ].to_dict("records")

        return list(chain.from_iterable(records_by_repo[repo] for repo in repositories))

    def _filter_channels(self, channels: Iterable[Channel]) -> tuple[Channel, ...]:
        """Keep only configured channels, warning about dropped names."""
        configured_channels = set(self.channels)
        requested = tuple(dict.fromkeys(channels))
        dropped = [c for c in requested if c not in configured_channels]
        if dropped:
            logger.warning(
                "Ignoring unknown channels %s (configured: %s)",
                dropped,
                sorted(configured_channels),
            )
        return tuple(c for c in requested if c in configured_channels)

    def _get_repository_dataframe(
        self, channels: Iterable[Channel], subdirs: Iterable[Subdir]
    ) -> pd.DataFrame:
        """Load and cache only the requested channel/subdirectory pairs."""
        requested_channels = self._filter_channels(channels)
        requested_subdirs = tuple(dict.fromkeys(subdirs))
        return self._get_repository_pairs_dataframe(
            product(requested_channels, requested_subdirs)
        )

    def _get_repository_pairs_dataframe(
        self, repositories: Iterable[RepoDataKey]
    ) -> pd.DataFrame:
        """Load and cache the exact requested channel/subdirectory pairs."""
        if self._df is not None or self.cache_file is not None:
            return self.df

        repositories = tuple(dict.fromkeys(repositories))
        requested_channels = self._filter_channels(repo[0] for repo in repositories)
        allowed = set(requested_channels)
        repositories = tuple(repo for repo in repositories if repo[0] in allowed)
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
        can_use_shards = (
            not self._is_loader_mocked()
            and self._df is None
            and self.cache_file is None
        )
        if can_use_shards:
            records = self._get_sharded_package_data(
                self.channels, self.platforms, [name]
            )
            df = (
                pd.DataFrame(records, columns=self.columns)
                if records
                else pd.DataFrame(columns=self.columns)
            )
            for col in (
                "channel",
                "platform",
                "subdir",
                "name",
                "version",
                "build",
            ):
                df[col] = df[col].astype("category")
            df = df.reset_index(drop=True)
            packages = df[df.name == name][["version", "platform"]].copy()
        else:
            packages = self.df[self.df.name == name][["version", "platform"]].copy()

        packages["platform"] = packages["platform"].astype(str)
        packages["version"] = packages["version"].astype(str)
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

        requested_channels = self._filter_channels(requested_channels)
        if not requested_channels or not requested_subdirs:
            if key is None:
                return False
            if isinstance(key, str):
                return []
            return iter([])

        can_use_shards = (
            not self._is_loader_mocked()
            and self._df is None
            and self.cache_file is None
        )

        if can_use_shards and name is not None:
            target_names = [name] if isinstance(name, str) else list(name)
            records = self._get_sharded_package_data(
                requested_channels, requested_subdirs, target_names
            )
            df = (
                pd.DataFrame(records, columns=self.columns)
                if records
                else pd.DataFrame(columns=self.columns)
            )
            for col in (
                "channel",
                "platform",
                "subdir",
                "name",
                "version",
                "build",
            ):
                df[col] = df[col].astype("category")
            df = df.reset_index(drop=True)
        else:
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
def get_package_downloads(channel: Channel, package: str) -> int:
    """Use anaconda API to obtain download counts."""
    data = requests.get(f"https://api.anaconda.org/package/{channel}/{package}").json()
    if "files" in data:
        return sum(rec["ndownloads"] for rec in data["files"])
    return 0
