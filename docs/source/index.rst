.. image:: images/bioconda.png

**Bioconda** lets you install thousands of software packages related to
biomedical research using the `conda <https://conda.io>`_ package manager.

Usage
=====

`Install conda`_, then perform a one-time set up of Bioconda with the following
commands::

    conda config --add channels defaults
    conda config --add channels bioconda
    conda config --add channels conda-forge

.. _`Install conda`: https://docs.conda.io/projects/conda/en/latest/user-guide/install/index.html


.. details:: What did these commands do?

    Running `conda config` modifies your condarc file which is
    `~/.condarc` by default.

    The order of these commands is
    important: *conda-forge* gets the highest priority, followed by
    *bioconda*, and *defaults* gets the lowest priority.

Now you can use `conda install` to install and use any of the `available
packages <conda-package_index.html>`_.

Installation too slow? See :ref:`recommendations` for tips on speeding up
package installation and dependency resolution.

In addition, every conda package in Bioconda has a corresponding Docker
`BioContainer`_ automatically created and uploaded to `Quay.io`_. A list of
these and other containers can be found at the `BioContainers Registry
<https://biocontainers.pro/#/registry>`_.

Overview
========

.. role:: circlednumber

Bioconda consists of the components illustrated in the diagram below.

.. image:: images/bioconda-dag.png
    :width: 80%

**Legend** (starting from the bottom):

:circlednumber:`①` Over 1400 contributors who add,
modify, update, and maintain recipes and packages

.. details:: Details

    `Contributors
    <https://github.com/bioconda/bioconda-recipes/graphs/contributors>`_ to
    Bioconda add new recipes or update existing recipes by opening pull
    requests to GitHub. Contributors might include:

    - authors of the software
    - Bioconda core team
    - anyone interested creating a conda package

    There's even a Bioconda bot that watches common places
    for updates (like CRAN, PyPI, GitHub releases) and submits a pull
    request with the update for other contributors to review.


:circlednumber:`②` A repository of recipes hosted on GitHub

.. details:: Details

    The full history of all recipes is stored in the `repository of recipes`_
    on GitHub. Contributors and Bioconda core team coordinate, collaborate, and
    help each other out via comments on GitHub in issues and pull requests
    which are tested (see :circlednumber:`③`). The goal is to get a working
    package that satisfies the Bioconda policies.

:circlednumber:`③` A build system that turns each recipes into a conda
package and a Docker container

.. details:: Details

    On every push to GitHub, the changes in the entire pull request (e.g., new
    recipe or updates to recipe) are automatically built and tested. The
    results are reported back to the PR. Contributors work together to fix any
    issues (which are tested again) and the process repeats until all tests
    pass.

    Our `build system`_, `bioconda-utils`, runs the various building and
    testing steps on Azure Pipelines. The output consists of both a `conda
    package`_ and a `Biocontainer`_ that can be inspected before merging the
    pull request.


:circlednumber:`④` A repository of packages and a registry of containers
containing over 8000 bioinformatics packages

.. details:: Details

    When all tests pass and the pull request is merged into the main branch,
    two things happen. First, a final conda package is built and uploaded to
    the `Bioconda channel on anaconda.org <https://anaconda.org/bioconda>`_ on
    anaconda.org. And second, that conda package is installed into a Docker
    container which is then uploaded to the `Biocontainers registry
    <https://quay.io/biocontainers>`_.


:circlednumber:`⑤` Users can then use the package with `conda install` or `docker pull`

.. details:: Details

    See above for how to configure the conda channels; using docker containers
    just needs to have docker installed.



Package downloads
-----------------

**Browse packages in the Bioconda channel:** `Package Index <conda-package_index.html>`_

.. raw:: html
    :file: templates/dashboard.html



Acknowledgments
===============

Bioconda is a derivative mark of Anaconda :sup:`®`, a trademark of Anaconda,
Inc registered in the U.S. and other countries.  Anaconda, Inc.
grants permission of the derivative use but is not associated with Bioconda.

The Bioconda channel is sponsored by `Anaconda, Inc <https://www.anaconda.com/>`_
in the form of providing unlimited (in time and space) storage.
Bioconda is supported by `Circle CI <https://circleci.com/>`_ via an open
source plan including free Linux and MacOS builds.

Citing Bioconda
---------------

When using Bioconda please **cite our article**:

  Grüning, Björn, Ryan Dale, Andreas Sjödin, Brad A. Chapman, Jillian
  Rowe, Christopher H. Tomkins-Tinch, Renan Valieris, the Bioconda
  Team, and Johannes Köster. 2018. "Bioconda: Sustainable and
  Comprehensive Software Distribution for the Life Sciences". Nature
  Methods, 2018 doi::doi:`10.1038/s41592-018-0046-7`.

Contributors
------------

Core
~~~~

* `Johannes Köster <https://github.com/johanneskoester>`_
* `Ryan Dale <https://github.com/daler>`_
* `Brad Chapman <https://github.com/chapmanb>`_
* `Chris Tomkins-Tinch <https://github.com/tomkinsc>`_
* `Björn Grüning <https://github.com/bgruening>`_
* `Andreas Sjödin <https://github.com/druvus>`_
* `Jillian Rowe <https://github.com/jerowe>`_
* `Renan Valieris <https://github.com/rvalieris>`_
* `Marcel Bargull <https://github.com/mbargull>`_
* `Devon Ryan <https://github.com/dpryan79>`_
* `Elmar Pruesse <https://github.com/epruesse>`_

Team
~~~~

Bioconda would not exist without the continuous hard work and support of the
wonderful community which includes over 1400 (as of 2022) `contributors
<https://github.com/bioconda/bioconda-recipes/graphs/contributors>`_.


Table of contents
=================

.. toctree::
   :includehidden:/p

   faqs
   contributor/index
   developer/index
   tutorials/index


.. _conda: https://conda.io/en/latest/index.html
.. _`repository of recipes`: https://github.com/bioconda/bioconda-recipes
.. _`build system`: https://github.com/bioconda/bioconda-utils
.. _`repository of packages`: https://anaconda.org/bioconda/
.. _`conda package`: https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/packages.html
.. _`BioContainer`: https://biocontainers.pro
.. _`Quay.io`: https://quay.io/organization/biocontainers
