"""Paths within the environment this installation lives in."""

from __future__ import annotations

import sys
from pathlib import Path


def env_root() -> Path:
    """Return the conda prefix this installation lives in.

    ``conda-forge-pinning`` installs its ``conda_build_config.yaml`` directly
    into ``$PREFIX``, i.e. into the same environment that also provides the
    ``bioconda-utils`` entry point. The prefix of the running interpreter is
    therefore the right place to look, independent of ``PATH`` and of any
    environment that happens to be activated in the shell.
    """
    return Path(sys.prefix)
