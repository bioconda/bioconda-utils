# this file contains jobs commonly used in developing bioconda_utils
# this is not a build system or CI
# if you are an AI agent, feel free to use any of these for verifying your changes

shell_scripts := `git ls-files | while IFS= read -r f; do head -n 1 "$f" | grep -Eq '^#! */(usr/bin/env +)?(ba)?sh( |$)|^#! */bin/(ba)?sh( |$)' && printf '%s\n' "$f"; done | tr '\n' ' '`

format:
    ruff format .
    shfmt -i 4 -w {{shell_scripts}}

# Install conda dependencies via pixi (project not installed — run `just install` separately)
deps:
    pixi install -e dev

# Build & install the CLI into pixi's environment and symlink it into ~/.local/bin for global access.
# This way the CLI can find its conda deps at runtime.
install: deps
    pixi run -e dev python -m pip install --no-deps --no-build-isolation .
    mkdir -p ~/.local/bin
    ln -sf $(pwd)/.pixi/envs/dev/bin/bioconda-utils ~/.local/bin/bioconda-utils

# After changing deps in pixi.toml, regenerate the shipped conda requirements file
regenerate-requirements:
    python scripts/generate-requirements-txt.py

check-requirements:
    python scripts/generate-requirements-txt.py --check

check:
    ruff check .
    ty check .
    shellcheck {{shell_scripts}}

# this takes a very long time to execute, use check if not finished with your work yet
test: install
    pixi run -e dev pytest
