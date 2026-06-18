# this file contains jobs commonly used in developing bioconda_utils
# this is not a build system or CI
# if you are an AI agent, feel free to use any of these for verifying your changes

shell_scripts := `git ls-files | while IFS= read -r f; do head -n 1 "$f" | grep -Eq '^#! */(usr/bin/env +)?(ba)?sh( |$)|^#! */bin/(ba)?sh( |$)' && printf '%s\n' "$f"; done | tr '\n' ' '`

format:
    ruff format .
    shfmt -i 4 -w {{shell_scripts}}

deps:
    conda install --file bioconda_utils/bioconda_utils-requirements.txt -c conda-forge -c bioconda
    conda install -c conda-forge ty ruff shfmt shellcheck

ruff:
    ruff check .

ty:
    ty check .

shellcheck:
    shellcheck {{shell_scripts}}

check: ruff ty shellcheck

# install a local build of the CLI for testing
install:
    python -m pip install --no-deps --no-build-isolation .

# this takes a very long time to execute, use check if not finished with your work yet
test: install
    pytest
