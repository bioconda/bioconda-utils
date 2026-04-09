"""
Bioconda Utilities Package

.. rubric:: Subpackages

.. autosummary::
   :toctree:

   bioconda_utils.lint

.. rubric:: Submodules

.. autosummary::
   :toctree:

   aiopipe
   bioconductor_skeleton
   build
   circleci
   cli
   cran_skeleton
   docker_utils
   githandler
   githubhandler
   gitter
   graph
   hosters
   pkg_test
   recipe
   autobump
   update_pinnings
   upload
   utils
"""

import re

import conda_build.metadata

from ._version import get_versions

# Monkeypatch conda_build.metadata.find_used_variables_in_text to include __glibc
# in the hash when stdlib('c') is used on Linux. This ensures that hashes
# calculated with bypass_env_check=True (as done in built_package_paths)
# match those from real builds.
_old_find_used_variables_in_text = conda_build.metadata.find_used_variables_in_text


def _patched_find_used_variables_in_text(variant, recipe_text, selectors_only=False):
    used = _old_find_used_variables_in_text(variant, recipe_text, selectors_only)
    if not selectors_only and "__glibc" in variant:
        # Match stdlib('c')
        if re.search(r"\{\{\s*stdlib\(\s*[\'\"]c[\'\"]\s*\)", recipe_text):
            used.add("__glibc")
    return used


conda_build.metadata.find_used_variables_in_text = _patched_find_used_variables_in_text

__version__ = get_versions()["version"]
del get_versions
