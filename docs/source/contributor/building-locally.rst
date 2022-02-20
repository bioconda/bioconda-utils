Testing Recipes Locally
=======================

Queue times on Azure DevOps may sometimes make it more convenient and
faster to work on complex packages locally. There are several ways to
do so, each with their own caveats.

.. contents::
   :local:


.. _bioconda_utils:

Using bioconda-utils
~~~~~~~~~~~~~~~~~~~~

Whether on a CI node or locally, Bioconda packages are built and tested using ``bioconda-utils``.
You can install ``bioconda-utils`` locally by creating a new conda environment:

.. code-block::

    # You can use "conda create" here instead, if you don't have mamba installed
    mamba create -n bioconda -c conda-forge -c bioconda bioconda-utils

    conda activate bioconda

    # optional linting
    bioconda-utils lint --git-range master

    # build and test
    bioconda-utils build --docker --mulled-test --git-range master

The above commands do the following:

- Creates a new environment with bioconda-utils.
- Activates the new environment. You can later just start at ``conda activate bioconda``
- Run ``bioconda-utils`` in the new environment:
   - The ``lint`` command will run the lint checks on your recipes
   - The ``build`` command will run the build pipeline

.. note::

   - You can select recipes to lint/build using ``--git-range master``,
     which which will select those recipes that have been changed
     between your master and your branch. Or you can specify recipes
     directly using ``--packages mypackage1 mypackage2``.
   - The ``--docker`` flag instructs ``bioconda-utils`` to execute the
     build within a docker container. On MacOS, this will do a Linux
     build in addition to the local MacOS build.
   - The ``--mulled-test`` flag instructs ``bioconda-utils`` to repeat
     the recipes test in a clean, freshly created docker container to
     ensure that the package does not depend on anything that happens
     to be included in the build container.

If you do not have access to Docker, you can still run the basic test by
omitting the ``--docker`` and ``--mulled-test`` options.

Using the "Debug" Method
~~~~~~~~~~~~~~~~~~~~~~~~

.. todo::

   - Explain how to use ``conda debug`` for difficult recipes.
   - Explain how to create patch series.
