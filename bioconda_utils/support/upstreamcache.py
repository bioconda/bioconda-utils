"""Bounded HTTP freshness and representation validation for upstream scans."""

import asyncio
import math
import re
import time
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlparse

import aiohttp
from yarl import URL

from . import http
from .caching import async_file_lock, file_lock, get_cache_root, read_json, write_json


def prune(directory):
    """Bound disk use, retaining stale validators for at most seven days."""
    try:
        with file_lock(directory / ".maintenance.lock", timeout=0):
            stamp = directory / ".maintenance"
            if stamp.exists() and time.time() - stamp.stat().st_mtime < 600:
                return
            files = []
            for path in directory.glob("*.json"):
                try:
                    stat = path.stat()
                    if time.time() - stat.st_mtime > 7 * 86400:
                        path.unlink(missing_ok=True)
                    else:
                        files.append((stat.st_mtime, path, max(4096, stat.st_size)))
                except FileNotFoundError:
                    pass
            total = sum(size for _, _, size in files)
            for _, path, size in sorted(files):
                if total <= 256 * 1024 * 1024:
                    break
                path.unlink(missing_ok=True)
                total -= size
            stamp.touch()
    except TimeoutError:
        pass  # Another process is already maintaining this cache.


def _directives(value: str) -> dict[str, str]:
    return {
        part.strip().lower(): argument.strip().strip('"')
        for item in value.split(",")
        for part, _, argument in [item.partition("=")]
        if part.strip()
    }


def _date(value: str | None, default: float) -> float:
    try:
        return parsedate_to_datetime(value).timestamp() if value else default
    except (ValueError, TypeError, OverflowError):
        return default


def _lifetime(headers, directives, kind: str) -> float:
    now = time.time()
    if "no-cache" in directives:
        return 0
    limit = 600 if kind == "text" else 3600
    try:
        if "max-age" in directives:
            lifetime = float(directives["max-age"])
        elif "Expires" in headers:
            lifetime = _date(headers["Expires"], now) - _date(headers.get("Date"), now)
        else:
            lifetime = 600 if kind == "text" else 0
        age = max(
            float(headers.get("Age", 0)), now - _date(headers.get("Date"), now), 0
        )
        return (
            max(0, min(limit, lifetime - age))
            if math.isfinite(lifetime) and math.isfinite(age)
            else 0
        )
    except (ValueError, TypeError):
        return 0


async def fetch_cached(
    session: aiohttp.ClientSession, url: str, kind: str, desc: str = ""
) -> str:
    directory = get_cache_root() / "upstream-v1"
    directory.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(prune, directory)
    key = sha256(f"{kind}\n{http.USER_AGENT}\n{url}".encode()).hexdigest()
    path = directory / (key + ".json")
    async with async_file_lock(directory / (key[:2] + ".lock")):
        entry = read_json(path)
        authenticated = (
            "Authorization" in session.headers
            or "Cookie" in session.headers
            or session.auth is not None
            or bool(session.cookie_jar.filter_cookies(URL(url)))
            or urlparse(url).username is not None
        )
        if authenticated and kind != "checksum":
            entry = None
        valid = (
            isinstance(entry, dict)
            and entry.get("url") == url
            and entry.get("kind") == kind
            and isinstance(entry.get("value"), str)
            and isinstance(entry.get("expires"), (int, float))
            and isinstance(entry.get("stored"), (int, float))
            and isinstance(entry.get("headers"), dict)
            and isinstance(entry.get("vary"), dict)
            and all(
                isinstance(k, str)
                and (v is None or isinstance(v, str))
                and session.headers.get(k) == v
                for k, v in entry["vary"].items()
            )
            and all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in entry["headers"].items()
            )
        )
        if valid and kind == "checksum":
            valid = re.fullmatch(r"[0-9a-f]{64}", entry["value"]) is not None
        if not valid:
            entry = None
        if entry is not None and not 0 <= time.time() - entry["stored"] < 7 * 86400:
            entry = None
        if (
            entry is not None
            and not authenticated
            and not entry.get("revalidate", False)
            and time.time() < entry["expires"]
        ):
            return entry["value"]
        conditional = {}
        if entry is not None:
            etag = entry["headers"].get("ETag")
            # A weak ETag only promises semantic equality, not archive bytes.
            if etag and (kind == "text" or not etag.startswith("W/")):
                conditional["If-None-Match"] = etag
            elif modified := entry["headers"].get("Last-Modified"):
                conditional["If-Modified-Since"] = modified
        async with session.get(url, headers=conditional) as response:
            response.raise_for_status()
            if response.status == 304:
                if entry is None or not conditional:
                    raise ValueError(
                        f"Unexpected HTTP 304 without cached representation: {url}"
                    )
                value = entry["value"]
                headers = {**entry["headers"], **dict(response.headers)}
            else:
                value = (
                    await response.text()
                    if kind == "text"
                    else await http.download_to_checksum(response, desc)
                )
                headers = dict(response.headers)
            # Normalize header names: aiohttp headers are case-insensitive, JSON isn't.
            headers = {name.lower(): value for name, value in headers.items()}
            canonical = {
                name: headers[name.lower()]
                for name in (
                    "ETag",
                    "Last-Modified",
                    "Cache-Control",
                    "Vary",
                    "Expires",
                )
                if name.lower() in headers
            }
            directives = _directives(canonical.get("Cache-Control", ""))
            vary = {
                v.strip().lower()
                for v in canonical.get("Vary", "").split(",")
                if v.strip()
            }
            request_headers = response.request_info.headers
            cacheable = (
                (kind == "checksum" or not authenticated)
                and "no-store" not in directives
                and not {"*", "cookie", "authorization"} & vary
                and "Authorization" not in request_headers
                and (kind == "checksum" or "Cookie" not in request_headers)
                and not any(
                    "Authorization" in hop.request_info.headers
                    for hop in (*response.history, response)
                )
                and urlparse(url).username is None
                and response.status in (200, 304)
            )
            if cacheable:
                # Date/Age must describe this response, not the previous retrieval.
                lifetime_headers = {
                    **canonical,
                    **{
                        name: response.headers[name]
                        for name in ("Date", "Age")
                        if name in response.headers
                    },
                }
                now = time.time()
                write_json(
                    path,
                    {
                        "url": url,
                        "kind": kind,
                        "value": value,
                        # Cookie-bearing archive responses may only reuse a digest
                        # after the server validates that representation again.
                        "revalidate": authenticated
                        or any(
                            "Cookie" in hop.request_info.headers
                            for hop in (*response.history, response)
                        ),
                        "stored": now,
                        "expires": now + _lifetime(lifetime_headers, directives, kind),
                        "headers": canonical,
                        "vary": {name: request_headers.get(name) for name in vary},
                    },
                )
            else:
                path.unlink(missing_ok=True)
            return value
