# Autobump cache measurements

Measured on 2026-10-09 against this machine's complete bioconda-recipes test
checkout. Raw measurements are in [autobump-caches.json](autobump-caches.json).

| Operation | Previous persistent snapshot | No persistent cache | New validated cache |
| --- | --- | --- | --- |
| Full dependency graph, repeated run | 10.15–10.35 s | 25.47–25.79 s | 4.82–5.31 s |
| Samtools release listing and archive checksum, repeated scan | 0.0028–0.0036 s | 3.51–3.57 s | 0.209–0.220 s |

The old graph contains 11,437 nodes and 39,916 edges. Implementing the cache
uncovered an existing skiplist bug: `BuildFailureRecord(Recipe)` treated the
metadata filename as a recipe directory. Correcting it excludes 173 currently
skiplisted recipes, yielding 11,264 nodes and 39,319 edges. The new cached graph
and a fresh parse with the same corrected skiplist logic were compared by their
complete sets of relative recipe paths and dependency edges: both match exactly.
Corrected fresh parsing took 26.0–36.0 seconds in separate verification runs.
The new metadata index is approximately 15 MiB, versus 118 MiB for the old pickle.

The first real upstream retrieval with the new cache took 4.64 seconds. Repeated
scans still made two top-level HTTP requests: release pages and archives were
revalidated instead of blindly reused. The old snapshot made no requests and
had no freshness policy. Every scan returned the same archive SHA-256:
`89b2a440123eeaa400392ce1736e7d60ce9041843027d76819753c5a8246bfdd`.

## Method and limits

Baseline code is commit `8361ad4`, immediately before removal of persistent
caches. Both implementations used the same Pixi Python 3.13 environment and
seven graph workers. The recipes checkout was at
`b66803ba0bd529c4edea239d74223954b60f17cb` plus existing test edits. No recipe files
were changed by the graph or HTTP probes.

Graph probes timed `RecipeGraphSource.load_graph`, excluding imports, package
selection, repodata lookup, and recipe updates. Reported old/new uncached results
were alternating fresh Python processes without concurrent graph probes.
Exploratory overlapping runs were discarded. New-cache warm runs were also fresh
Python processes. This is a shared machine, load was not controlled, and each
reported warm mode has only two samples. The node-count difference above means
the old/new comparison is not an identical-workload microbenchmark.

HTTP probes timed fresh `AsyncRequests` instances and sessions per scan,
including disk cache I/O, HTTP requests, and archive hashing. Targets were:

- `https://github.com/samtools/samtools/releases`
- `https://github.com/samtools/samtools/releases/download/1.24/samtools-1.24.tar.bz2`

Request counts exclude redirect hops. There are two repeated scans after the
initial retrieval. Network timings illustrate the improvement for this target;
they are not a forecast for every hoster or a full-repository scan.

Reading and SHA-256 hashing all recipe metadata (17.2 MB across 11,437 recipes)
took 0.99–1.00 seconds. The graph cache checks those contents on every invocation,
reparses changed recipes, and rebuilds edges from current metadata. It never
loads an old graph blindly.

The original runnable probes and logs remain at
`/tmp/bioconda-cache-benchmark/` on the test machine: `graph.py`, `sequential.py`,
`upstream.py`, and `equivalence.py`. The baseline was extracted with
`git archive 8361ad4` into that directory's `baseline/`; `PYTHONPATH` selected
baseline or current sources without installing another environment.

A live-terminal CLI verification also passed with the actual samtools recipes
copied from Git HEAD: only the main recipe was processed, all eight historical
recipes remained unchanged, and there were no repodata downloads.
