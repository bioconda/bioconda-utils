FAQs
====

.. _speedup:

How do I speed up package installation?
---------------------------------------

Use ``mamba``
~~~~~~~~~~~~~
 `mamba <https://github.com/mamba-org/mamba>`_ is a drop-in replacement for
 conda that uses a faster dependency solving library and parts reimplemented in
 C++ for speed. Install it just into the base environment so that it's always
 available, like this:

.. code-block:: bash

    conda install mamba -n base -c conda-forge

Then use `mamba install` instead of `conda install`; use `mamba create`
instead of `conda create`, and so on.

Use environments strategically
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Keep the ``base`` environment small. If you install everything into the same
   environment (e.g. the `base` environment), then whenever you add or update
   packages to it, the solver has to do a lot of work to make sure all of the
   many packages are mutually compatible with each other.

2. Try to use smaller environments only containing what you need for
   a particular project.

3. Sometimes pinning dependencies to a specific version can speed up the solving.

4. Creating an environment from a file with all dependencies can be faster than
   incrementally adding packages to an existing environment.

5. Ensure that you've run ``conda config --set channel_priority strict`` to
   respect the configured channel order.

Naming environments
-------------------

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

What versions are supported?
----------------------------

Operating Systems
~~~~~~~~~~~~~~~~~

Bioconda only supports 64-bit Linux and Mac OS

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
