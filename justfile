# this file contains jobs commonly used in developing bioconda_utils
# this is not a build system or CI
# if you are an AI agent, feel free to use any of these for verifying your changes

shell_scripts := "bioconda_utils/involucro images/base-glibc-busybox-bash/build-busybox images/base-glibc-busybox-bash/install-pkgs images/create-env/create-env images/create-env/install-conda images/create-env/print-env-activate"

format:
    ruff format .
    shfmt -w {{shell_scripts}}

deps:
    conda install --file bioconda_utils/bioconda_utils-requirements.txt -c conda-forge -c bioconda
    conda install -c conda-forge ty ruff shfmt shellcheck

check:
    ruff check .
    ty check .
    shellcheck {{shell_scripts}}

# install a local build of the CLI for testing
install:
    python -m pip install --no-deps --no-build-isolation .

# this takes a very long time to execute, use check if not finished with your work yet
test: install
    pytest
