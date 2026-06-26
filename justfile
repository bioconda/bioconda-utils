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
    has_toml_key() {
        pixi run python - "$1" "$2" <<'PY'
    import pathlib
    import sys
    import tomllib

    section, key = sys.argv[1:]
    data = tomllib.loads(pathlib.Path("pixi.toml").read_text())
    raise SystemExit(0 if key in data.get(section, {}) else 1)
    PY
    }
    remove_pypi_dep_if_present() {
        if has_toml_key pypi-dependencies "$1"; then
            pixi remove --pypi "$1" --no-install
        fi
    }
    remove_conda_dep_if_present() {
        if has_toml_key dependencies "$1"; then
            pixi remove "$1" --no-install
        fi
    }
    galaxy_dir="$(realpath "{{galaxy_dir}}")"
    test -f "${galaxy_dir}/packages/tool_util/pyproject.toml"
    test -f "${galaxy_dir}/packages/util/pyproject.toml"
    test -f "${galaxy_dir}/packages/tool_util_models/pyproject.toml"
    remove_pypi_dep_if_present galaxy-tool-util
    remove_pypi_dep_if_present galaxy-util
    remove_pypi_dep_if_present galaxy-tool-util-models
    remove_conda_dep_if_present galaxy-tool-util
    pixi add --pypi --editable "galaxy-tool-util-models @ file://${galaxy_dir}/packages/tool_util_models" --no-install
    pixi add --pypi --editable "galaxy-util @ file://${galaxy_dir}/packages/util" --no-install
    pixi add --pypi --editable "galaxy-tool-util @ file://${galaxy_dir}/packages/tool_util" --no-install
    pixi install
    mulled_help="$(pixi run mulled-build --help 2>&1)"
    grep -q -- "--target-platform" <<<"$mulled_help"
    involucro_help="$(pixi run involucro --help 2>&1 || true)"
    grep -q -- "-platform" <<<"$involucro_help"
    pixi run python -c 'import importlib.metadata as md; print("galaxy-tool-util", md.version("galaxy-tool-util"))'

# Use after `just install-galaxy-dev` when you are done testing unpublished
# Galaxy changes. This removes editable Galaxy PyPI overrides and restores the
# normal conda-provided galaxy-tool-util dependency.
restore-galaxy-dev:
    #!/usr/bin/env bash
    set -euo pipefail
    has_toml_key() {
        pixi run python - "$1" "$2" <<'PY'
    import pathlib
    import sys
    import tomllib

    section, key = sys.argv[1:]
    data = tomllib.loads(pathlib.Path("pixi.toml").read_text())
    raise SystemExit(0 if key in data.get(section, {}) else 1)
    PY
    }
    remove_pypi_dep_if_present() {
        if has_toml_key pypi-dependencies "$1"; then
            pixi remove --pypi "$1" --no-install
        fi
    }
    restore_conda_galaxy_tool_util() {
        pixi run python - <<'PY'
    import pathlib
    import re

    path = pathlib.Path("pixi.toml")
    lines = path.read_text().splitlines()
    dependency_header = lines.index("[dependencies]")
    next_header = next(
        (
            index
            for index in range(dependency_header + 1, len(lines))
            if lines[index].startswith("[")
        ),
        len(lines),
    )
    dependency_lines = lines[dependency_header + 1 : next_header]
    dependency_lines = [
        line
        for line in dependency_lines
        if line != "# mulled test and container build"
        and not re.match(r'^galaxy-tool-util\s*=', line)
    ]
    insert_index = next(
        (
            index
            for index, line in enumerate(dependency_lines)
            if re.match(r'^involucro\s*=', line)
        ),
        None,
    )
    if insert_index is None:
        raise RuntimeError("Could not find involucro dependency in pixi.toml")
    dependency_lines[insert_index:insert_index] = [
        "# mulled test and container build",
        'galaxy-tool-util = "25.*"',
    ]
    lines[dependency_header + 1 : next_header] = dependency_lines
    path.write_text("\n".join(lines) + "\n")
    PY
    }
    remove_pypi_dep_if_present galaxy-tool-util
    remove_pypi_dep_if_present galaxy-util
    remove_pypi_dep_if_present galaxy-tool-util-models
    restore_conda_galaxy_tool_util
    pixi install

# run typechecks and linters, use after a moderate amount of changes
check: shellcheck
    pixi run check

shellcheck:
    pixi run shellcheck {{shell_scripts}}

# this takes a very long time to execute, use check if not finished with your work yet
test:
    pixi run test
