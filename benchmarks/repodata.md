# Repodata memory and storage benchmark

Measured on 2026-10-09 on the shared Linux development host. The baseline is
commit `2c343d6`, before the SQLite change. Raw results are in
[repodata.json](repodata.json); [repodata.py](repodata.py) runs the comparisons.

## Results

Each range below represents two fresh-process samples. Timing includes cache
opening/deserialization; it excludes interpreter startup and output verification.
Warm describes an existing application cache, not an already loaded pandas table.

| Workload | Previous pandas/pickle | Indexed SQLite |
| --- | --- | --- |
| Cold JSON-to-cache, one large repository | 18.0–18.5 s | 11.2–11.8 s |
| 30 targeted queries, all repositories | 14.3–14.6 s | 0.23–0.24 s |
| All conda-forge names, 2.56 million rows | 6.35–7.23 s | 2.01–2.06 s |
| All conda-forge name/version/build rows | 13.78–13.84 s | 5.68–5.94 s |
| Targeted-query process PSS | 2.44 GiB | 58 MiB |

Sixteen concurrently spawned SQLite query workers used about **0.9 GiB combined
PSS**, including imports and retained query results. Each completed its targeted
workload in 0.24–0.27 seconds. This is a query benchmark, not a claim that sixteen
conda-build rendering workers need only that much memory.

The cold comparison used the same downloaded **454,390,641-byte**
conda-forge/linux-64 JSON, containing 791,405 package records. It includes reading
JSON, parsing it, constructing storage, indexing for SQLite, and publishing the
cache atomically. Network download time is excluded. SQLite's file was larger:
248,643,584 bytes versus 184,411,838 bytes for the baseline pickle. Cold peak RSS
was about 2.43 GiB versus 2.58 GiB. Parsing an individual large JSON file still
requires substantial memory; cross-process refresh serialization prevents that
peak from multiplying across workers.

The warm corpus contains **2,781,467 records** across conda-forge and bioconda,
with six subdirectories each. SQLite was seeded from the identical earlier
snapshot for this comparison; this is benchmark preparation, not production
pickle migration. Every paired workload produced the same row count and SHA256
digest, including duplicate records. The 30 targeted queries cover Python, R,
compilers, common libraries, samtools, and a missing package, selecting versions,
build strings, and dependency lists.

Broad reports still allocate their output rows. The full three-column report's
post-verification PSS was about 698 MiB with SQLite versus 2.52 GiB with pandas.
The recorded RSS peaks also include sorting/serializing millions of rows to
verify equality; these allocations are outside the timed query section.

## Live pinning verification and its limit

An isolated recipe collection was extracted from the testing checkout's Git
HEAD. Autobump used `--threads 16`, default graph traversal and pinning checks,
and `--no-check-version-update` to isolate pinning from upstream HTTP latency.
The initial selection included 192 top-level recipes plus historical directories.

The initial run stalled inside conda-build rendering `arb-bio` and was terminated
at a 240-second test deadline. Its worker used roughly 2.4 GiB, independently of
our repodata cache. A separate reproduction reached conda-build's
`finalize_outputs_pass` → `get_rendered_output` → `pin_compatible` →
`get_env_dependencies` → conda/libmamba solving despite `bypass_env_check=True`.
This is a remaining rendering/solver issue, not fixed by changing our storage.
No production exclusion was added for this recipe.

The repeat explicitly passed `--exclude arb-bio`. It completed successfully in
**13.51 seconds**, reported **195 recipe outcomes**, exercised **16 workers**, and
peaked at **3.21 GiB combined PSS**, including the parent, workers, and subprocesses.
A second run in the Herdr biotest pane also completed: 13.06 seconds,
16 workers, 195 outcomes, and 3.23 GiB peak PSS.

This verifies the ordinary concurrent pinning path; it does not establish that
all recipes in a full-collection run have bounded solver memory or runtime.

Regression tests separately terminate parents with both SIGTERM and SIGKILL and
verify their running workers exit. Worker exit relies on Python's parent process
sentinel, without platform-specific parent-death syscalls.

## Reproduction

Use a separate directory on a disk-backed filesystem. On this machine `/tmp` is
tmpfs; the benchmark artifacts were moved to
`~/.cache/bioconda-utils-benchmarks/bioconda-repodata-benchmark` before the final
measurements. `/tmp/bioconda-repodata-benchmark` links there for existing probes.
The initial exploratory numbers reported before implementation are superseded by
these production-code measurements.

```sh
git show 2c343d6:bioconda_utils/conda/repodata.py > /path/to/legacy-repodata.py

# Download repodata.json once, then benchmark the exact same bytes.
pixi run python benchmarks/repodata.py cold \
  --cache /path/to/benchmark-output --input /path/to/repodata.json
pixi run python benchmarks/repodata.py cold \
  --cache /path/to/benchmark-output --input /path/to/repodata.json \
  --legacy /path/to/legacy-repodata.py

# Use populated caches from the same repository snapshot.
pixi run python benchmarks/repodata.py targeted --cache /path/to/sqlite-cache
pixi run python benchmarks/repodata.py targeted --cache /path/to/pickle-cache \
  --legacy /path/to/legacy-repodata.py
pixi run python benchmarks/repodata.py targeted --cache /path/to/sqlite-cache --workers 16
# Repeat the single-worker comparisons with modes names and specs.
```

Local live-test commands, detailed memory samples, and logs are retained beneath
that benchmark directory in `live/` and `live-no-arb/`. The local invocation script
is `/tmp/bioconda-pinning-memory-live.py`.

These are small samples on a shared host with warm OS file caches and other
running jobs. They support the storage decision and identify the memory scaling
failure; they are not universal latency guarantees or a completed full-collection
stress test.
