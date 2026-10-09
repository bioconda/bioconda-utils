"""
Shared on-disk cache for long-running lookups.
"""

import asyncio
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from time import monotonic, sleep

import diskcache
import platformdirs

disk_cache = diskcache.Cache(platformdirs.user_cache_dir("bioconda-utils"))
_cache_root: Path | None = None


def configure_cache_root(directory: Path | None) -> None:
    global _cache_root
    _cache_root = directory.resolve() if directory is not None else None


def get_cache_root() -> Path:
    return _cache_root or platformdirs.user_cache_path("bioconda-utils")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError, UnicodeError):
        return None


def write_json(path: Path, value) -> None:
    """Publish a complete entry; a failed writer leaves the previous one intact."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(json.dumps(value, separators=(",", ":")))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@asynccontextmanager
async def async_file_lock(path: Path, *, timeout: float = 600):
    """Wait without blocking the event loop; cancellation always closes the lock."""
    with path.open("a") as handle:
        deadline = monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for cache lock {path}")
                await asyncio.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path, *, timeout: float = 600) -> Iterator[None]:
    """Serialize cache refreshes; the OS releases this lock on process exit."""
    with path.open("a") as handle:
        deadline = monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for cache lock {path}"
                    ) from None
                sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
