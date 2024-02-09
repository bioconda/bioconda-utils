# bioconda/create-env

The `create-env` container image, available as [`quay.io/bioconda/create-env`](https://quay.io/repository/bioconda/create-env?tab=tags), provides [`conda`](https://github.com/conda/conda/) (and [`mamba`](https://github.com/mamba-org/mamba)) alongside a convenience wrapper `create-env` to create small container images based on Conda packages.


## Options

`create-env` runs `conda create` for a given `PREFIX` plus a set of packages and (optionally) runs post-processing steps on the created environment.

Post-processing steps are triggered by arguments to `create-env`:

- `--env-activate-script=FILE`:

  Create a shell activation script `FILE` (defaults to `PREFIX/env-activate.sh`) which contains the environment activation instructions as executed per `conda activate PREFIX`.

  Example usage: `sh -c '. PREFIX/env-activate.sh && command-to-run-from-PREFIX'`.

- `--env-execute-script=FILE`:

  Create an executable `FILE` (defaults to `PREFIX/env-execute`) which runs a given program in the activated `PREFIX` environment.

  Example usage: `PREFIX/env-execute command-to-run-from-PREFIX`.

- `--remove-paths=GLOB`:

  Remove some paths from `PREFIX` to reduce the target container image size.

- `--strip-files=GLOB`:

  Run [`strip`](https://sourceware.org/binutils/docs/binutils/strip.html) on files in `PREFIX` whose paths match `GLOB` to reduce the target container image size.

- `licenses-path=PATH`:

  Directory in which to copy license files for the installed packages (defaults to `PREFIX/conda-meta`).


## Usage example:
```Dockerfile
FROM quay.io/bioconda/create-env:2.1.0 as build
# Create an environment containing python=3.9 at /usr/local using mamba, strip
# files and remove some less important files:
RUN export CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY=0 \
    && \
    /opt/create-env/env-execute \
      create-env \
        --conda=mamba \
        --strip-files='bin/*' \
        --strip-files='lib/*' \
        --remove-paths='*.a' \
        --remove-paths='share/terminfo/[!x]*' \
        /usr/local \
        python=3.9

# The base image below (quay.io/bioconda/base-glibc-busybox-bash:2.1.0) defines
# /usr/local/env-execute as the ENTRYPOINT so that created containers always
# start in an activated environment.
FROM quay.io/bioconda/base-glibc-busybox-bash:2.1.0 as target
COPY --from=build /usr/local /usr/local

FROM target as test
RUN /usr/local/env-execute python -c 'import sys; print(sys.version)'
RUN /usr/local/env-activate.sh && python -c 'import sys; print(sys.version)'

# Build and test with, e.g.:
# buildah bud --target=target --tag=localhost/python:3.9 .
# podman run --rm localhost/python:3.9 python -c 'import sys; print(sys.version)'
```

## Miscellaneous information:

- Run `podman run --rm quay.io/bioconda/create-env create-env --help` for usage information.

- Run `podman run --rm quay.io/bioconda/create-env conda config --show-sources` to see predefined configuration options.

- The environment in which `create-env` runs has been itself created by `create-env`.
  As such, `/opt/create-env/env-activate.sh` and `/opt/create-env/env-execute` scripts can be used to activate/execute in `create-env`'s environment in a `Dockerfile` context.
  In other contexts when a container is run via the image's entrypoint, the environments is activated automatically.

  The separate `/opt/create-env` path is used to avoid collisions with environments created at, e.g., `/usr/local` or `/opt/conda`.

- By default, package files are copied rather than hard-linked to avoid altering Conda package cachge files when running `strip`.

  If the target image should contain multiple environments, it is advisable to set `CONDA_ALWAYS_COPY=0` to allow hardlinks between the environments (to reduce the overall image size) and run `strip` after the environments have been created.
  This can be done by invoking `create-env` twice whilst omitting the environment creation during the second invocation (using `--conda=:`).

  E.g.:
  ```sh
  . /opt/create-env/env-activate.sh
  export CONDA_ALWAYS_COPY=0
  create-env --conda=mamba /opt/python-3.8 python=3.8
  create-env --conda=mamba /opt/python-3.9 python=3.9
  create-env --conda=: --strip-files=\* /opt/python-3.8
  create-env --conda=: --strip-files=\* /opt/python-3.9
  ```

- Container images created as in the example above are meant to be lightweight and as such do **not** contain `conda`.
  Hence, there is no `conda activate PREFIX` available but only the source-able `PREFIX/env-activate.sh` scripts and the `PREFIX/env-execute` launchers.
  These scripts are generated at build time and assume no previously activated Conda environment.
  Likewise, the environments are not expected to be deactivated, which is why no corresponding deactivate scripts are provided.
