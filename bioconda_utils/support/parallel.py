"""
Helpers for parallel iteration over recipes and other work items.
"""

from __future__ import annotations

import os
from functools import partial
from multiprocessing import Pool

from .logsetup import tqdm

_max_threads = 1


def set_max_threads(n):
    global _max_threads
    _max_threads = n


def threads_to_use():
    """Returns the number of cores we are allowed to run on"""
    if hasattr(os, "sched_getaffinity"):
        cores = len(os.sched_getaffinity(0))
    else:
        cores = os.cpu_count()
    return min(_max_threads, cores)


def parallel_iter(func, items, desc, *args, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    with Pool(threads_to_use()) as pool:
        yield from tqdm(pool.imap_unordered(pfunc, items), desc=desc, total=len(items))
