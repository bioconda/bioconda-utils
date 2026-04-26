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

from . import _version

__version__ = _version.get_versions()["version"]
