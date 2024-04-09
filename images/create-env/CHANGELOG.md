# Changelog


## bioconda/create-env 3.0 (2023-10-17)

### Changed

- Add linux-aarch64 image; bioconda/create-env is now a multiplatform manifest.

- Change to a simple "major.minor" version scheme and offer mutable "major" tag.

- Drop defaults channel from included config.

- Use Miniforge installer to build this image.

- Rebuilt on the latest base image with Debian 12.2 / BusyBox 1.36.1.

- Do not install findutils, sed if provided by the base image (as is currently).


## bioconda/create-env 2.2.1 (2022-10-14)

### Changed

- Limit open fd (ulimit -n) for strip (small number chosen arbitrarily).

  The container image itself had unstripped binaries in 2.2.0.


## bioconda/create-env 2.2.0 (2022-10-14)

### Changed

- Use the exact conda, mamba versions as used in bioconda-recipes' builds.


## bioconda/create-env 2.1.0 (2021-04-14)

### Changed

- Copy instead of hardlink licenses, exit on error

  Hardlink fails if copying spans cross devices (e.g., via bound volumes).


## bioconda/create-env 2.0.0 (2021-04-13)

### Changed

- Rename `--remove-files` to `--remove-paths`

- Replace `--strip` by `--strip-files=GLOB`

- Replace `CONDA_ALWAYS_COPY=1` usage by config option

- Use `/bin/bash` for entrypoints

  `/bin/sh` fails on some Conda packages' activations scripts' Bashisms.


## bioconda/create-env 1.2.1 (2021-04-09)

### Fixed

- Fail `--strip` if `strip` is not available

### Changed

- Delete links/dirs for `--remove-files`


## bioconda/create-env 1.2.0 (2021-03-30)

### Added

- Add license copying

- Add status messages

- Add help texts

### Changed

- Suppress `bash -i` ioctl warning


## bioconda/create-env 1.1.1 (2021-03-27)

### Changed

- Use `CONDA_ALWAYS_COPY=1`


## bioconda/create-env 1.1.0 (2021-03-27)

### Added

- Add option to change `create --copy`

### Changed

- Rebuild with `python` pinned to `3.8`

  To avoid hitting
    - https://github.com/conda/conda/issues/10490
    - https://bugs.python.org/issue43517


## bioconda/create-env 1.0.2 (2021-03-22)

### Changed

- Rebuild on new Debian 10 base images


## bioconda/create-env 1.0.1 (2021-03-22)

### Fixed

- Use entrypoint from `/opt/create-env/`

  `/usr/local` gets "overwritten" (=bind-mounted) when building via mulled.


## bioconda/create-env 1.0.0 (2021-03-21)

### Added

- Initial release


<!--

## bioconda/create-env X.Y.Z (YYYY-MM-DD)

### Added

- item

### Fixed

- item

### Changed

- item

### Removed

- item

-->
