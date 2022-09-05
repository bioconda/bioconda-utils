FAQs
====

.. _speedup:

How do I speed up package installation?
---------------------------------------

Speedup option 1: use ``mamba``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`mamba <https://github.com/mamba-org/mamba>`_ is a drop-in replacement for
conda that uses a faster dependency solving library and parts reimplemented in
C++ for speed. Install it just into the base environment so that it's always
available, like this:

.. code-block:: bash

    conda install mamba -n base -c conda-forge

Then use ``mamba`` instead of ``conda``.

For example, instead of ``conda install``, use ``mamba install``. Instead of
``conda env create`` use ``mamba env create``, and so on. ``mamba`` also uses
the same configuration as ``conda``, so you don't need to reconfigure the
channels.

.. note::

    Installing ``mamba`` into the base environment (``-n base`` in the command
    above) means that it does **not** need to be installed into each subsequent
    environment you create.

Speedup option 2: use environments strategically
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Here are several ways you can use environments to minimize the time spent on
solving dependencies, which typically is what takes the longest amount of time:

1. Keep the ``base`` environment small.

   If you install everything into the same environment (e.g. the ``base``
   environment, which is used any time you don't otherwise specify an
   environment), then whenever you add or update packages to it, the solver has
   to do a lot of work to make sure all of the many packages are mutually
   compatible with each other.

2. Use smaller environments.

   Fewer packages means less work for the solver. Try to use environments only
   containing what you need for a particular project or task.

3. Pin dependencies.

   Sometimes pinning dependencies to a specific version can speed up the
   solving, since it reduces the search space for the solver. In some cases
   this may backfire though. For example, you can't pin an older version of
   R and also use newer R packages that don't support that version of R.

4. Create an environment from a file with all dependencies.

   Creating an environment with all dependencies at once can be faster than
   incrementally adding packages to an existing environment. For example
   ``conda create -n myenv --file requirements.txt``, or ``conda env create
   --file env.yaml``.

5. Use strict channel priority.

   Ensure that you've run ``conda config --set channel_priority strict`` to
   respect the configured channel order. This can also speed up the solving.

What versions are supported?
----------------------------

Operating Systems
~~~~~~~~~~~~~~~~~

Bioconda only supports 64-bit Linux and macOS. ARM is not currently supported.

Python
~~~~~~

Bioconda only supports python 2.7, 3.6, 3.7, 3.8 and 3.9.

The exception to this is Bioconda packages which declare `noarch: python` and
only depend on such packages - those packages can be installed in an
environment with any version of python they say they can support. However many
python packages in Bioconda depend on other Bioconda packages with architecture
specific builds, such as `pysam`, and so do not meet this criteria.


Pinned packages
~~~~~~~~~~~~~~~

Some packages require `ABI
<https://en.wikipedia.org/wiki/Application_binary_interface>`_ compatibility
with underlying libraries. To ensure that packages can work together, there are
some libraries that need to be *pinned*, or fixed to a particular version.
Other packages are then built with that specific version (and therefore that
specific ABI) to ensure they can all work together.

The authoritative source for which packages are pinned and to which versions
can be found in the `bioconda_utils-conda_build_config.yaml
<https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-conda_build_config.yaml>`_
file.

This is *in addition to* the conda-forge specified versions,
`conda_build_config.yaml
<https://github.com/conda-forge/conda-forge-pinning-feedstock/blob/master/recipe/conda_build_config.yaml>`_
which pins versions of base dependencies like boost, zlib, and many others.

Unsupported versions
~~~~~~~~~~~~~~~~~~~~

If there is a version of a dependency you wish to build against that Bioconda
does not currently support, please reach out to the `Bioconda Gitter
<https://gitter.im/bioconda/Lobby>`_ for more information about if supporting
that version is feasible, if work on that is already being done, and how you
can help.

To find out against which version you can pin a package, e.g. x.y.* or x.*
please use `ABI-Laboratory <https://abi-laboratory.pro/tracker/>`_.

How do I keep track of environments?
------------------------------------

You can view your created environments with ``conda env list``.

Note that if keeping track of different environment names
becomes a burden, you can create an environment in the same directory as
a project with the ``-p`` argument, e.g., 

.. code-block:: bash

    conda create -p ./env --file requirements.txt

and then activate the environment with

.. code-block:: bash

    conda activate ./env

This also works quite well in a shared directory so everyone can use (and
maintain) the same environment.

.. _conda-anaconda-minconda:

What's the difference between Anaconda, conda, Miniconda, and mamba?
--------------------------------------------------------------------

- conda is the name of the package manager, which is what runs when you call,
  e.g., ``conda install``.
- mamba is a drop-in replacement for conda (see above for details)
- Anaconda is a large installation including Python, conda, and a large number
  of packages.
- Miniconda just has conda and its dependencies (in contrast to the larger
  Anaconda distribution).

The `Anaconda Python distribution <https://www.continuum.io/downloads>`_
started out as a bundle of scientific Python packages that were otherwise
difficult to install. It was created by `ContinuumIO
<https://www.continuum.io/>`_ and remains the easiest way to install the full
scientific Python stack.

Many packaging problems had to be solved in order to provide all of that
software in Anaconda in a cross-platform bundle, and one of the tools that came
out of that work was the conda package manager. So conda is part of th Anaconda
Python distribution. But conda ended up being very useful on its own and for
things other than Python, so ContinuumIO spun it out into its own separate
`open-source package <https://github.com/conda/conda>`_.

Conda became very useful for setting up lightweight environments for testing
code or running individual steps of a workflow. To avoid needing to install the
*entire* Anaconda distribution each time, the Miniconda installer was created.
This installs only what you need to run conda itself, which can then be used to
create other environments. So the "mini" in Miniconda means that it's
a fraction of the size of the full Anaconda installation.

So: conda is a package manager, Miniconda is the conda installer, and Anaconda
is a scientific Python distribution that also includes conda.

What's the difference between a recipe and a package?
-----------------------------------------------------

A *recipe* is a directory containing small set of files that defines name,
version, dependencies, and URL for source code. A recipe typically contains
a ``meta.yaml`` file that defines these settings and a ``build.sh`` script that
builds the software.

A recipe is converted into a *package* by running `conda-build` on the recipe.
A package is a bgzipped tar file (``.tar.bz2``) that contains the built
software in expected subdirectories, along with a list of what other packages
are dependencies. For example, a conda package built for a Python package would
end up with `.py` files in the `lib/python3.8/site-packages/<pkgname>`
directory inside the tarball, and would specify (at least) Python as
a dependency.

Packages are uploaded to anaconda.org so that users can install them
with ``conda install``.

.. seealso::

    The `conda-build:resources/package-spec` has details on exactly
    what a package contains and how it is installed into an
    environment.

What's the difference between miniconda, miniforge, mambaforge, micromamba?
---------------------------------------------------------------------------

**Miniconda** is the slimmed-down version of the Anaconda distribution;
miniconda only has conda and its dependencies.

**Miniforge** is like miniconda, but with the conda-forge channel preconfigures
and all packages coming from the conda-forge and *not* the ``defaults``
channel.

**Mambaforge** is like miniforge, but has mamba installed into the base environment.

**Micromamba** is not a conda distribution. Rather, it is a minimal binary that
has roughly the same commands as mamba, so that a single executable (rather
than an entire Python installation required for conda itself) can be used to
create environments. Micromamba is currently still experimental.

Why are Bioconductor data packages failing to install?
------------------------------------------------------

When creating an environment containing Bioconductor data packages, you may get
errors like this::

    ValueError: unsupported format character 'T' (0x54) at index 648

The actual error will be somewhere above that, with something like this (here,
it's for the ``bioconductor-org.hs.eg.db=3.14.0=r41hdfd78af_0`` package)::

    message:
    post-link script failed for package bioconda::bioconductor-org.hs.eg.db-3.14.0-r41hdfd78af_0
    location of failed script: /Users/dalerr/env/bin/.bioconductor-org.hs.eg.db-post-link.sh
    ==> script messages <==
    <None>
    ==> script output <==
    stdout: ERROR: post-link.sh was unable to download any of the following URLs with the md5sum ef7fc0096ec579f564a33f0f4869324a:
    https://bioconductor.org/packages/3.14/data/annotation/src/contrib/org.Hs.eg.db_3.14.0.tar.gz
    https://bioarchive.galaxyproject.org/org.Hs.eg.db_3.14.0.tar.gz
    https://depot.galaxyproject.org/software/bioconductor-org.hs.eg.db/bioconductor-org.hs.eg.db_3.14.0_src_all.tar.gz

**To fix it**, you need to adjust the requirements. If you had this as a requirement::

    bioconductor-org.hs.eg.db=3.14.0=r41hdfd78af_0

then increase the build number on the end, here from ``_0`` to ``_1``::

    bioconductor-org.hs.eg.db=3.14.0=r41hdfd78af_1

or, relax the exact build constraint while keeping the package version the same::

    bioconductor-org.hs.eg.db=3.14.0

and then re-build your environment.

**The reason this is happening** is a combination of factors. Early on in
Bioconda's history we made the decision that pure data packages -- like
Bioconductor data packages, which can be multiple GB in size -- would not be
directly converted into conda packages. That way, we could avoid additional
storage load on Anaconda's servers since the data were already available from
Bioconductor, and we could provide a mechanism to use the data packages within
an R environment living in a conda environment. This mechanism is
a `post-link.sh
<https://docs.conda.io/projects/conda-build/en/latest/resources/link-scripts.html>`_
script for the recipe.

When a user installs the package via conda, the GB of data aren't in the
package. Rather, the URL pointing to the tarball is in the post-link script,
and the script uses ``curl`` to download the package from Bioconductor and
install into the conda environment's R library. We also set up separate
infrastructure to archive data packages to other servers, and these archive
URLs were also stored in the post-link scripts as backups.

*The problem is that back then, we assumed that URLs would be stable and we did
not use the* ``-L`` *argument for curl in post-link scripts*.

Recently Bioconductor packages have moved to a different server (XSEDE/ACCESS).
The old URL, the one hard-coded in the post-link scripts, is correctly now
a redirect to the new location. But without ``-L``, the existing recipes and
their post-link scripts cannot follow the redirect! Compounding this, the
archive URLs stopped being generated, so the backup strategy also failed.

The fix was to re-build all Bioconductor data packages and include the ``-L``
argument, allowing them to follow the redirect and correctly install the
package. Conda packages have the idea of a "build number", which allows us to
still provide the same version of the package (3.14.0 in the example above) but
packaged differently (in this case, with a post-link script that works in
Bioconductor's current server environment).

**Reproducibility is hard.** We are trying our best, and conda is an amazing
resource. But the fact that a single entity does not (and should not!) control
all code, data, packages, distribution mechanisms, and installation mechanisms,
means that we will always be at risk of similar situtations in the future.
Hopefully we are guarding better against this particular issue, but see
`Grüning et al 2018 <http://dx.doi.org/10.1016/j.cels.2018.03.014>`_
(especially Fig 1) for advice on more reproducible strategies you can use for
your own work.
