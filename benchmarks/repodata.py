"""Reproduce repodata storage benchmarks; run with pixi run python.

The legacy module is an exported copy of repodata.py from commit 2c343d6.
Cold runs use one downloaded JSON file for both implementations, excluding
network time. Warm runs require populated legacy and SQLite cache directories.
Use fresh processes to isolate memory accounting. See repodata.md for commands.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import importlib.util
import json
import multiprocessing
import resource
import sys
import time
from pathlib import Path

from bioconda_utils.conda.repodata import RepoData

NAMES = [
    "samtools",
    "python",
    "zlib",
    "numpy",
    "r-base",
    "htslib",
    "openssl",
    "perl",
    "gcc_impl_linux-64",
    "nonexistent-package-probe",
]


def repository(legacy, cache):
    if legacy:
        name = "bioconda_utils.conda.benchmark_legacy"
        spec = importlib.util.spec_from_file_location(name, legacy)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        # Original pickles refer to this class in the production module.
        production = sys.modules["bioconda_utils.conda.repodata"]
        production.__dict__["_CachedRepoData"] = module._CachedRepoData
        cls = module.RepoData
    else:
        cls = RepoData
    cls.register_config({"channels": ["conda-forge", "bioconda"]})
    cls.configure_cache(Path(cache))
    return cls()


def memory():
    path = Path("/proc/self/smaps_rollup")
    return {
        key: int(value.split()[0])
        for key, _, value in (
            line.partition(":") for line in path.read_text().splitlines()
        )
        if key in ("Rss", "Pss", "Private_Dirty")
    }


def warm(arguments):
    legacy, cache, barrier, workload = arguments
    repo = repository(legacy, cache)
    while time.monotonic() < barrier:
        time.sleep(0.01)
    started = time.monotonic()
    if workload == "targeted":
        rows = [
            row
            for _ in range(3)
            for name in NAMES
            for row in repo.get_package_data(["version", "build", "depends"], name=name)
        ]
    else:
        keys = "name" if workload == "names" else ["name", "version", "build"]
        rows = list(repo.get_package_data(keys, channels="conda-forge"))
    elapsed = time.monotonic() - started
    # Outside the timing: verify content and duplicate counts, not just speed.
    digest = hashlib.sha256()
    for row in sorted(json.dumps(row, separators=(",", ":")) for row in rows):
        digest.update(row.encode())
        digest.update(b"\n")
    return {
        "pid": __import__("os").getpid(),
        "seconds": elapsed,
        "rows": len(rows),
        "digest": digest.hexdigest(),
        "memory_kib": memory(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cold", "targeted", "names", "specs"])
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.mode == "cold":
        assert args.input is not None
        repo = repository(args.legacy, args.cache)
        started = time.monotonic()
        path = args.cache / ("cold.pkl" if args.legacy else "cold.sqlite")
        if args.legacy:
            module = sys.modules[type(repo).__module__]
            raw = args.input.read_bytes()
            module.__dict__["fetch"] = lambda urls, descriptions, transform, metadata: [
                transform(raw, pair) for pair in metadata
            ]
            frame = repo._load_channel_dataframe([("conda-forge", "linux-64")])
            repo._write_cache(
                path,
                module._CachedRepoData(frame, datetime.datetime.now(datetime.UTC)),
            )
        else:
            repo._write_cache(path, args.input.read_bytes())
        print(
            json.dumps(
                {
                    "seconds": time.monotonic() - started,
                    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "cache_bytes": path.stat().st_size,
                }
            ),
            flush=True,
        )
    else:
        with concurrent.futures.ProcessPoolExecutor(
            args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            barrier = time.monotonic() + 3
            arguments = (args.legacy, args.cache, barrier, args.mode)
            results = list(pool.map(warm, [arguments] * args.workers))
        print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
