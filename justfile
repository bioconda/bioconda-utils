# This file contains jobs commonly used in developing bioconda_utils
# this is not a build system or CI
# if you are an AI agent, feel free to use any of these for verifying your changes

shell_scripts := `git ls-files | while IFS= read -r f; do [ -f "$f" ] && head -n 1 "$f" | grep -Eq '^#! */(usr/bin/env +)?(ba)?sh( |$)|^#! */bin/(ba)?sh( |$)' && printf '%s\n' "$f"; done | tr '\n' ' '`

# frequently run to autoformat all code
format:
    pixi run format
    pixi run shfmt -i 4 -w {{shell_scripts}}


# Symlink the CLI into ~/.local/bin for global access.
# This way the CLI can find its conda deps at runtime.
install:
    pixi run global-install

# Use when testing bioconda-utils against an unpublished Galaxy change, such
# as mulled-build --target-platform support from a sibling Galaxy checkout.
# This replaces the conda galaxy-tool-util dependency with editable PyPI
# packages from Galaxy and mutates pixi.toml/pixi.lock for local development.
# Run `just restore-galaxy-dev` before committing normal dependency metadata.
install-galaxy-dev galaxy_dir="../galaxy":
    #!/usr/bin/env bash
    set -euo pipefail
    galaxy_dir="$(realpath "{{galaxy_dir}}")"
    test -f "${galaxy_dir}/packages/tool_util/pyproject.toml"
    test -f "${galaxy_dir}/packages/util/pyproject.toml"
    test -f "${galaxy_dir}/packages/tool_util_models/pyproject.toml"
    pixi remove --pypi galaxy-tool-util galaxy-util galaxy-tool-util-models --no-install || true
    pixi remove galaxy-tool-util --no-install || true
    pixi add --pypi --editable "galaxy-tool-util-models @ file://${galaxy_dir}/packages/tool_util_models" --no-install
    pixi add --pypi --editable "galaxy-util @ file://${galaxy_dir}/packages/util" --no-install
    pixi add --pypi --editable "galaxy-tool-util @ file://${galaxy_dir}/packages/tool_util" --no-install
    pixi install
    pixi run mulled-build --help | grep -q -- "--target-platform"
    pixi run involucro --help | grep -q -- "-platform"
    pixi run python -c 'import importlib.metadata as md; print("galaxy-tool-util", md.version("galaxy-tool-util"))'

# Use after `just install-galaxy-dev` when you are done testing unpublished
# Galaxy changes. This removes editable Galaxy PyPI overrides and restores the
# normal conda-provided galaxy-tool-util dependency.
restore-galaxy-dev:
    #!/usr/bin/env bash
    set -euo pipefail
    pixi remove --pypi galaxy-tool-util galaxy-util galaxy-tool-util-models --no-install || true
    pixi add "galaxy-tool-util=25.*" --no-install
    pixi install

# run typechecks and linters, use after a moderate amount of changes
check: shellcheck
    pixi run check

shellcheck:
    pixi run shellcheck {{shell_scripts}}

# this takes a very long time to execute, use check if not finished with your work yet
test:
    pixi run test
