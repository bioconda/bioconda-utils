"""Sharded repodata: per-package records from content-addressed shards.

Repositories may publish sharded repodata next to ``repodata.json``: an
index (``repodata_shards.msgpack.zst``) maps package names to hashes of
individual ``<hash>.msgpack.zst`` shard files. :class:`ShardClient` resolves
package records from shards, caching indexes and shard records in memory,
and backing off from shards or indexes that failed to download.
"""

from __future__ import annotations

import datetime
import logging
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urljoin

import aiohttp
import msgpack
import zstandard

from .._types import Channel, Subdir
from .fetch import FetchRequest, fetch

logger = logging.getLogger(__name__)


#: Name of the sharded-repodata index, living in the same directory as repodata.json
SHARDS_INDEX_FILENAME = "repodata_shards.msgpack.zst"


def _unpack_msgpack_zst(data: bytes) -> Any:
    """Decompress zstandard-compressed msgpack data."""
    decompressed = zstandard.ZstdDecompressor().stream_reader(data).read()
    return msgpack.unpackb(decompressed)


@dataclass
class _CachedShardIndex:
    """Parsed CEP 16 shard index.

    ``shards`` maps package names to hex-encoded shard hashes, and
    ``shards_base_url`` is the base URL of the shard files, resolved
    against the index URL at parse time.
    """

    shards: dict[str, str]
    shards_base_url: str
    fetched_at: datetime.datetime


@dataclass
class _CachedPackageShard:
    records: list[dict[str, Any]]


class ShardClient:
    """Resolves package records from sharded repodata.

    Shard indexes are cached briefly, as they may change at any time, while
    content-addressed package shards are immutable. Failed downloads mark
    the index or shard unavailable, skipping retries until the matching
    timeout expires.
    """

    _shard_index_cache: ClassVar[dict[str, _CachedShardIndex]] = {}
    _package_shard_cache: ClassVar[OrderedDict[str, _CachedPackageShard]] = (
        OrderedDict()
    )
    _package_shards_unavailable: ClassVar[dict[str, datetime.datetime]] = {}
    _shards_unavailable: ClassVar[dict[str, datetime.datetime]] = {}

    #: Retry unavailable shard indexes sooner than successful cache entries.
    shards_unavailable_timeout = 60 * 5
    package_shards_unavailable_timeout = 60 * 5

    #: Shard indexes are mutable; content-addressed package shards are not.
    shard_index_cache_timeout = 60 * 15
    package_shard_cache_max_entries = 4096

    @classmethod
    def clear_caches(cls) -> None:
        cls._shard_index_cache.clear()
        cls._package_shard_cache.clear()
        cls._package_shards_unavailable.clear()
        cls._shards_unavailable.clear()

    @staticmethod
    def shards_index_url(repodata_url: str) -> str:
        return repodata_url.rsplit("/", 1)[0] + "/" + SHARDS_INDEX_FILENAME

    @staticmethod
    def shard_url(shard_index: _CachedShardIndex, hex_hash: str) -> str:
        return urljoin(shard_index.shards_base_url, f"{hex_hash}.msgpack.zst")

    @staticmethod
    def _parse_shard_index(
        data: bytes | None, index_url: str
    ) -> _CachedShardIndex | None:
        if not data:
            return None
        try:
            raw = _unpack_msgpack_zst(data)

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

            shards_base_url = info.get("shards_base_url", "")
            if not isinstance(shards_base_url, str):
                raise TypeError("invalid shard index base URL")
            # Shard files live next to the index unless it says otherwise.
            shards_base_url = urljoin(index_url, shards_base_url or "./")
            if not shards_base_url.endswith("/"):
                shards_base_url += "/"
        except (
            zstandard.ZstdError,
            msgpack.UnpackException,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ) as exc:
            logger.debug(
                "Failed to decompress/unpack shards index at %s: %s",
                index_url,
                exc,
            )
            return None

        return _CachedShardIndex(
            shards=shards_map,
            shards_base_url=shards_base_url,
            fetched_at=datetime.datetime.now(datetime.UTC),
        )

    @staticmethod
    def _parse_package_shard(
        data: bytes | None,
        meta_data: tuple[Channel, Subdir, str],
    ) -> list[dict[str, Any]] | None:
        if not data:
            return None
        channel, subdir, package_name = meta_data
        try:
            shard = _unpack_msgpack_zst(data)

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

    @classmethod
    def _get_shard_index(
        cls, channel: Channel, subdir: Subdir, repodata_url: str
    ) -> _CachedShardIndex | None:
        """Return a fresh shard index, or None when shards are unavailable."""
        index_url = cls.shards_index_url(repodata_url)
        now = datetime.datetime.now(datetime.UTC)
        unavailable_at = cls._shards_unavailable.get(index_url)
        if (
            unavailable_at is not None
            and 0
            <= (now - unavailable_at).total_seconds()
            <= cls.shards_unavailable_timeout
        ):
            return None
        cls._shards_unavailable.pop(index_url, None)
        cached = cls._shard_index_cache.get(index_url)
        if (
            cached is not None
            and 0
            <= (now - cached.fetched_at).total_seconds()
            <= cls.shard_index_cache_timeout
        ):
            return cached
        cls._shard_index_cache.pop(index_url, None)
        try:
            [raw] = fetch(
                [FetchRequest(index_url, f"{channel}/{subdir} shards index")],
                allow_404=True,
                return_exceptions=True,
            )
        except (aiohttp.ClientError, OSError, TimeoutError, RuntimeError) as exc:
            logger.debug(
                "Error fetching shard index for %s/%s: %s", channel, subdir, exc
            )
            raw = exc
        index = (
            None
            if isinstance(raw, Exception)
            else cls._parse_shard_index(raw, index_url)
        )
        if index is None:
            cls._shards_unavailable[index_url] = now
            return None
        cls._shard_index_cache[index_url] = index
        return index

    @classmethod
    def _cached_shards(
        cls,
        shard_name_by_url: dict[str, str],
        records_by_url: dict[str, list[dict[str, Any]]],
    ) -> dict[str, str] | None:
        """Fill **records_by_url** from cached shard records.

        Returns the url->name mapping of shards still to download, or None
        when a requested shard is unavailable within its retry timeout.
        """
        missing: dict[str, str] = {}
        now = datetime.datetime.now(datetime.UTC)
        for url, name in shard_name_by_url.items():
            cached = cls._package_shard_cache.get(url)
            if cached is not None:
                cls._package_shard_cache.move_to_end(url)
                records_by_url[url] = cached.records
                continue
            unavailable_at = cls._package_shards_unavailable.get(url)
            if (
                unavailable_at is not None
                and 0
                <= (now - unavailable_at).total_seconds()
                < cls.package_shards_unavailable_timeout
            ):
                return None
            missing[url] = name
        return missing

    @classmethod
    def _download_shards(
        cls,
        channel: Channel,
        subdir: Subdir,
        missing: dict[str, str],
        records_by_url: dict[str, list[dict[str, Any]]],
    ) -> bool:
        """Download and parse shards into **records_by_url**.

        Returns False when any shard failed, requesting fallback to full
        repodata for the whole repository.
        """
        results = fetch(
            [
                FetchRequest(url, f"{channel}/{subdir}/{name} shard")
                for url, name in missing.items()
            ],
            allow_404=True,
            return_exceptions=True,
        )
        failed = False
        now = datetime.datetime.now(datetime.UTC)
        for (url, name), raw in zip(missing.items(), results):
            records = (
                None
                if isinstance(raw, Exception)
                else cls._parse_package_shard(raw, (channel, subdir, name))
            )
            if records is None:
                cls._package_shards_unavailable[url] = now
                failed = True
                continue
            cls._package_shards_unavailable.pop(url, None)
            cls._package_shard_cache[url] = _CachedPackageShard(records)
            while len(cls._package_shard_cache) > cls.package_shard_cache_max_entries:
                cls._package_shard_cache.popitem(last=False)
            records_by_url[url] = records
        return not failed

    @classmethod
    def get_repository_shards(
        cls, channel: Channel, subdir: Subdir, names: list[str], repodata_url: str
    ) -> list[dict[str, Any]] | None:
        """Return targeted records, or None when full repodata is needed.

        A missing name in a valid index is authoritative. Any failed package
        download falls back for the whole repository, avoiding duplicate rows.
        """
        if not names:
            return []
        if channel.startswith("file://"):
            return None
        index = cls._get_shard_index(channel, subdir, repodata_url)
        if index is None:
            return None
        shard_name_by_url: dict[str, str] = {}
        for name in dict.fromkeys(names):
            shard_hash = index.shards.get(name)
            if shard_hash is None:
                # A missing name in a valid index is authoritative.
                continue
            shard_name_by_url.setdefault(cls.shard_url(index, shard_hash), name)
        records_by_url: dict[str, list[dict[str, Any]]] = {}
        missing = cls._cached_shards(shard_name_by_url, records_by_url)
        if missing is None:
            return None
        if missing and not cls._download_shards(
            channel, subdir, missing, records_by_url
        ):
            return None
        wanted = set(names)
        return [
            record
            for records in records_by_url.values()
            for record in records
            if record["name"] in wanted
        ]
