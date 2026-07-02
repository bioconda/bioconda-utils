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
global-install:
    pixi run global-install

# run typechecks and linters, use after a moderate amount of changes
check: shellcheck
    pixi run check

shellcheck:
    pixi run shellcheck {{shell_scripts}}

# this takes a very long time to execute, use check if not finished with your work yet
test:
    pixi run test
