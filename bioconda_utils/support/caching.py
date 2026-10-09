"""
Shared on-disk cache for long-running lookups.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

import diskcache
import platformdirs

disk_cache = diskcache.Cache(platformdirs.user_cache_dir("bioconda-utils"))


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
