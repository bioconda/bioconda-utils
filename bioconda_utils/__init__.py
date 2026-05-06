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

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("bioconda-utils")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0+unknown"
