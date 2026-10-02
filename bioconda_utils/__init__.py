"""
Bioconda Utilities Package

Sets single-threaded BLAS thread pools on import; see the comment below.


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

import os
from importlib.metadata import PackageNotFoundError, version

# Importing numpy -- which pandas and conda-build pull in -- makes OpenBLAS
# start one thread per core, and those threads busy-wait. On a 32-core host that
# burned roughly 4s of CPU on *every* invocation while making no difference to
# wall-clock time: bioconda-utils does no linear algebra, and its parallelism
# comes from multiprocessing (see support/parallel.py). Cap the thread pools
# here so it takes effect before any command body imports numpy. ``setdefault``
# leaves an explicit user setting alone.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

try:
    __version__ = version("bioconda-utils")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0+unknown"
