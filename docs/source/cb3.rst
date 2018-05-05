Conda build v3
--------------

Conda build version 3 has lots of nice features that will make managing
packages in Bioconda much easier. However there are some changes that you will
need to be aware of, especially if you're used to making recipes using the old
conda-build v2 way.

This page documents each change and is intended to serve as a reference for the
transition.

.. _host-section:

The new ``host`` section
~~~~~~~~~~~~~~~~~~~~~~~~

**Summary:**

- Previously, build-time dependencies were listed in the ``requirements:build:`` section.
- Conda-build 3 now has an additional ``host:`` section. It is required if
  using compilers; otherwise old recipes can remain as-is and can be gradually
  ported over. New recipes should use the new ``host:`` section as described
  below.

Due to the improved way compilers are now being handled (see
:ref:`compiler-tools`), the old build section is now split into two sections,
``build`` and ``host``. This change is largely to support cross-compiling,
which we are not doing yet. However if a recipe uses one of the new ``{{
compiler() }}`` methods described in :ref:`compiler-tools`, the ``host``
section is **required**.

Existing recipes that only have a ``build:`` section and do not use the new
compiler tools (the thousands of existing recipes!) should still work for now;
they just won't work if we ever try to cross-compile them.  However **new**
recipes created by ``conda skeleton`` have the new ``host`` section, and this
seems to be the way conda is going, so we will gradually port over our recipes.

The new ``build`` section should have things like compilers, ``git``,
``automake``, ``make``, ``cmake``, and other build tools. If there are no
compilers or other build tools, there should be no ``build:`` section.

The new ``host`` section should have everything else.

The ``run`` section remains the same.

Before:

.. code-block:: yaml

    package:
      name: example
      version: 0.1
    requirements:
      build:
        - python
      run:
        - python

After:

.. code-block:: yaml

    package:
      name: example
      version: 0.1
    requirements:
      host:
        - python
      run:
        - python

.. seealso::

    See the `requirements section
    <https://conda.io/docs/user-guide/tasks/build-packages/define-metadata.html#requirements-section>`_
    of the conda docs for more info.


.. _compiler-tools:

Compiler tools
~~~~~~~~~~~~~~
**Summary:**

- Previously we used ``- gcc #[linux]`` and ``- llvm # [osx]`` for compilers
- Instead, we should use the syntax ``{{ compiler('c') }}``, ``{{
  compiler('cxx') }}``, and/or ``{{ compiler('fortran') }}``. These should go
  in the ``build`` section, and all other build dependencies should go in the
  ``host`` section.

Anaconda now provides platform-specific compilers that are automatically
determined. The string ``{{ compiler('c') }}`` will resolve to ``gcc`` on
Linux, but ``clang`` on macOS. This should greatly simplify recipes, as we no
longer need to have separate lines for linux and osx.

Note that previously we typically would also add ``- libgcc #[linux]`` as a run
dependency, but this is now taken care of by the compiler tools.

Conda-build 3 also now has the ability to cross-compile, making it now possible
to compile packages for macOS while running on Linux. To support this, recipes
must now make a distinction between dependencies that should be specific to the
building machine and dependencies that should be specific to the running
machine.

Dependencies specific to the building machine go in ``build``;
dependencies specific to the running machine go in ``host`` (see
:ref:`host-section`).


Before:

.. code-block:: yaml

    package:
      name: example
      version: 0.1
    requirements:
      build:
        - python
        - gcc  # [linux]
        - llvm # [osx]
      run:
        - python
        - libgcc  # [linux]

After:

.. code-block:: yaml

    package:
      name: example
      version: 0.1
    requirements:
      build:
        - {{ compiler('c') }}
      host:
        - python
      run:
        - python

.. seealso::

    - The `compiler tools
      <https://conda.io/docs/user-guide/tasks/build-packages/compiler-tools.html>_
      section of the conda docs has much more info.

    - The default compiler options are defined by conda-build in the
      `variants.DEFAULT_COMPILERS
      <https://github.com/conda/conda-build/blob/master/conda_build/variants.py#L42>`_
      variable.

    - More details on "strong" and "weak" exports (using examples of libpng and
      libgcc) can be found in the `export runtime requirements
      <https://conda.io/docs/user-guide/tasks/build-packages/define-metadata.html#export-runtime-requirements>`_
      conda documentation.


.. warning::

    These compilers are only available in the ``anaconda`` channel. Until now
    we have not had this channel as a dependency, so be sure to add the channel
    when setting up bioconda (see :ref:`set-up-channels`).

.. _global-pinning:

Global pinning
~~~~~~~~~~~~~~

**Summary:**

- Previously we pinned packages using the syntax ``- zlib {{ CONDA_ZLIB }}*``
- Instead, we should now pin packages with the syntax ``- zlib {{ zlib }}``.

Global pinning is the idea of making sure all recipes use the same versions of
common libraries. For example, many bioinformatics tools have `zlib` as
a dependency. The version of `zlib` used when building the package should be the
same as the version used when installing the package into a new environment.
Problems arise when the build-time version does not match the install-time
version. Furthermore, all packages installed into the same environment should
have been built using the same zlib so that they can co-exist. This implies
that we need to specify the `zlib` version in one place and have all recipes
use that version.

Previously we maintained a global pinning file (see `scripts/env_matrix.yaml
<https://github.com/bioconda/bioconda-recipes/blob/dd7248c5dcc5ea0237c81bff4d1e6df5a9bdd274/scripts/env_matrix.yml>`_),
and in there was the variable ``CONDA_ZLIB`` that was made available to the
recipes as a jinja2 variable. One problem with this is that we did not often
synchronize our pinned versions with conda-forge's pinned versions, and this
disconnect could cause problems.

Now, conda-build 3 has the concept of "variants", which is a generalized way of
solving this problem. This generally takes the form of a YAML file. We have
adopted the pinned versions used by conda-forge, which they provide in the
``conda-forge-pinning`` conda package. That package unpacks a config YAML into
the conda environment so that we can use that for building all recipes.

.. seealso::

    The `build variants
    <https://conda.io/docs/user-guide/tasks/build-packages/variants.html#>`_
    section of the conda docs has much more information.

    Packages pinned by conda-forge (which we also use) can be found in their
    `conda_build_config.yaml
    <https://github.com/conda-forge/conda-forge-pinning-feedstock/blob/master/recipe/conda_build_config.yaml>`_

    Bio-specific packages additionally pinned by bioconda can be found at
    ``bioconda_utils-conda_build_config.yaml`` in the bioconda-utils source.
