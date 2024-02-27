The intended use is to run `build.sh`, providing it an image directory.

Each image directory contains a `prepare.sh` script, a `Dockerfile` for
building, and a `Dockerfile.test` for testing.

Each `prepare.sh` should at least in turn source `versions.sh`.

`build.sh` sources `prepare.sh` to populate the env vars needed for that
particular image or do any other needed work in preparation for building.

To avoid inter-image dependencies, we prepare the C.utf8 locale ahead of time
in `locale.sh`. This can be copied over to image dirs if/when needed by
`prepare.sh`.

`build.sh` will write to a `metadata.json` file in the image dir with the name
of the manifest created, so that subsequent jobs can use it.
