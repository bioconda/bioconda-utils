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

However, if you would like to test in a standalone manner or help develop
bioconda-utils, you can use the respective Pixi tasks defined in
[`pixi.toml`](pixi.toml). You will need
[an installation of `pixi`](https://pixi.prefix.dev/latest/installation/).
Then, you can install the current `bioconda-utils` version in your local folder
into the `dev` environment, by
[running](https://pixi.prefix.dev/latest/reference/cli/pixi/run/) the `install`
task:

```bash
pixi run install
```

To then run `bioconda-utils` from anywhere, start a
[`pixi` shell](https://pixi.prefix.dev/latest/reference/cli/pixi/shell/) with
that environment activated:

```bash
pixi shell -e dev
```

See the help for the `bioconda-utils` command-line interface for details:

```bash
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

To update selected recipes, pass the recipe collection root and select
packages:

```bash
bioconda-utils autobump ./recipes --packages samtools
```

Autobump updates upstream versions and checksums and checks whether pinning
changes require rebuilding. Historical version subdirectories are excluded by
default; pass `--exclude-subrecipes never` to include them, or enable an
individual subrecipe with `extra.autobump.enable: true`.

Downloads and metadata are cached under the platform's user cache directory
(`$XDG_CACHE_HOME/bioconda-utils`, falling back to
`~/.cache/bioconda-utils` on Linux, `~/Library/Caches/bioconda-utils` on
macOS). Repodata is refreshed after eight hours; upstream HTTP responses are
revalidated against the server and pruned after seven days. Caches are shared
between concurrent commands, are safe to delete, and local `file://` channels
are always read afresh. `build`, `lint`, `update-pinning`, and `autobump`
accept `--repodata-cache DIRECTORY` to choose another cache directory and
`--refresh-repodata` to refresh entries needed by the current run. Older
scripts can still pass `--cache PREFIX`, which stores caches under `PREFIX.d`;
the two options cannot be combined.
