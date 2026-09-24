"""
Bioconda Utilities Package

.. rubric:: Subpackages

.. autosummary::
   :toctree:

   bioconda_utils.conda
   bioconda_utils.containers
   bioconda_utils.lint
   bioconda_utils.support

.. rubric:: Submodules

.. autosummary::
   :toctree:

   aiopipe
   autobump
   bioconductor_skeleton
   build
   build_failure
   bulk
   cli
   config
   cran_skeleton
   githandler
   githubhandler
   graph
   hosters
   recipe
   skiplist
   update_pinnings
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bioconda-utils")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0+unknown"
