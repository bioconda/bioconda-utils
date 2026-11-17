"""Compare cold batched shards with full repodata using a local HTTP server.

Run with ``pixi run python scripts/benchmark-repodata.py``. Both strategies
include downloads, parsing, and cache writes. Adjust repository size and HTTP
latency to explore the crossover; these synthetic timings are not a prediction
for every channel or network connection.
"""

import argparse
import json
import logging
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import msgpack
import zstandard

from bioconda_utils._types import Channel, Config
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.conda.shards import ShardClient
from bioconda_utils.support.caching import configure_cache_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=int, default=2048)
    parser.add_argument("--builds", type=int, default=50)
    parser.add_argument("--latency-ms", type=float, default=25)
    parser.add_argument("--counts", type=int, nargs="+", default=[32, 128, 256])
    args = parser.parse_args()
    if min(args.packages, args.builds, *args.counts) < 1:
        parser.error("package, build, and batch counts must be positive")
    if max(args.counts) > args.packages or args.latency_ms < 0:
        parser.error(
            "batch counts must fit the repository and latency must be nonnegative"
        )

    logging.getLogger("bioconda_utils").setLevel(logging.ERROR)
    bodies = {}
    packages = {}
    hashes = {}
    for i in range(args.packages):
        name = f"package-{i}"
        records = {
            f"{name}-{build}-0.conda": {
                "name": name,
                "version": str(build),
                "build": "0",
                "build_number": 0,
                "depends": ["python >=3.10", "zlib >=1.2", "libgcc >=13"],
            }
            for build in range(args.builds)
        }
        packages.update(records)
        hashes[name] = f"{i:064x}"
        bodies[f"/channel/noarch/shards/{hashes[name]}.msgpack.zst"] = (
            zstandard.ZstdCompressor().compress(
                msgpack.packb({"packages": records, "packages.conda": {}})
            )
        )
    bodies["/channel/noarch/repodata.json"] = json.dumps(
        {"info": {"subdir": "noarch"}, "packages": packages}
    ).encode()
    bodies["/channel/noarch/repodata.json.zst"] = zstandard.ZstdCompressor().compress(
        bodies["/channel/noarch/repodata.json"]
    )
    bodies["/channel/noarch/repodata_shards.msgpack.zst"] = (
        zstandard.ZstdCompressor().compress(
            msgpack.packb(
                {
                    "version": 1,
                    "info": {"shards_base_url": "./shards/"},
                    "shards": hashes,
                }
            )
        )
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def do_GET(self):
            time.sleep(args.latency_ms / 1000)
            body = bodies.get(self.path, b"")
            self.send_response(200 if self.path in bodies else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        RepoData.register_config(
            Config(channels=[Channel(f"http://127.0.0.1:{server.server_port}/channel")])
        )
        RepoData.platforms = ["noarch"]
        size = len(bodies["/channel/noarch/repodata.json"]) / 2**20
        compressed_size = len(bodies["/channel/noarch/repodata.json.zst"]) / 2**20
        print(
            f"{args.packages} packages x {args.builds} builds; "
            f"{size:.1f} MiB JSON / {compressed_size:.2f} MiB zstd"
        )
        print(f"{args.latency_ms:g} ms latency; 4 concurrent connections per host")
        print("missing shards  batched shards (s)  full repodata.zst (s)")
        with tempfile.TemporaryDirectory() as directory:
            for count in args.counts:
                timings = []
                for strategy in ("shards", "full"):
                    configure_cache_root(Path(directory) / f"{count}-{strategy}")
                    RepoData.configure_cache()
                    ShardClient.max_missing_shards = (
                        args.packages + 1 if strategy == "shards" else 1
                    )
                    start = time.perf_counter()
                    RepoData().prefetch(f"package-{i}" for i in range(count))
                    timings.append(time.perf_counter() - start)
                print(f"{count:14d}  {timings[0]:18.3f}  {timings[1]:17.3f}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
