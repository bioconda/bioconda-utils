[![CircleCI](https://circleci.com/gh/bioconda/bioconda-utils/tree/master.svg?style=shield)](https://circleci.com/gh/bioconda/bioconda-utils/tree/master)
[![Gitter](https://badges.gitter.im/bioconda/bioconda-recipes.svg)](https://gitter.im/bioconda/Lobby?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge)

![](https://raw.githubusercontent.com/bioconda/bioconda-recipes/master/logo/bioconda_monochrome_small.png
 "Bioconda")

`bioconda-utils` is a set of utilities for building and managing
[bioconda](https://github.com/bioconda/bioconda-recipes) recipes.

Since `bioconda-utils` is tightly coupled to `bioconda-recipes`, it is
strongly recommended that `bioconda-utils` be set up and used according to the
instructions at https://bioconda.github.io/contributor/index.html. This will
ensure that your local setup matches that used to build recipes in GitHub
Actions as closely as possible.

For standalone use or development, install
[Pixi](https://pixi.prefix.dev/latest/installation/), then run from this checkout:

```bash
pixi run install
pixi shell
bioconda-utils -h
```

Alternatively, you can also globally install `bioconda-utils`, adding it to
your user's `$PATH`, with the `global-install` task:

```bash
pixi run global-install
```

Or use the Just wrappers around the Pixi tasks:

```bash
just global-install
```

Docker builds use the image tag matching the leading `X.Y.Z` of the installed
version, including development versions (for example, `5.1.1.dev189` uses
`quay.io/bioconda/bioconda-utils-build-env-cos7:5.1.1`). A matching local image
is reused; otherwise the published image is pulled. If that image is not yet
published, build it from this checkout:

```bash
just docker-image
```

The recipe tags the image with that same version so `build --docker` and Linux
`handle-merged-pr --fallback build` can use it. It defaults to the Docker
daemon's platform. To build for another supported platform, use
`just docker-image linux/arm64` or `just docker-image linux/amd64`
(cross-platform builds require emulation). Rebuild when you need checkout or
dependency changes reflected inside the container. `--build-image` only
customizes an existing image and does not replace this step. An explicit
`--docker-base-image` or `BUILD_ENV_IMAGE` overrides the default.

To update selected recipes, pass the recipe collection root and select
packages:

```bash
bioconda-utils autobump ./recipes --packages samtools
```

Autobump updates upstream versions and checksums and checks whether pinning
changes require rebuilding. Historical version subdirectories are excluded by
default; pass `--exclude-subrecipes never` to include them, or enable an
individual subrecipe with `extra.autobump.enable: true`.

Downloads and metadata are cached between commands in the platform's user cache
directory. Package metadata is reused for up to eight hours.
`build`, `lint`, `update-pinning`, and `autobump` accept
`--cache DIRECTORY` to choose another location and `--refresh-repodata` to
refresh package metadata needed by the current run. Local `file://` channels
are always read afresh. To clear a cache, stop all commands using it before
removing the directory; it will be recreated as needed.
