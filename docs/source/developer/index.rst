Developer Docs
--------------

Updating ``bioconda-utils``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The general workflow is:

- Pull request to `bioconda-utils repo <https://github.com/bioconda/bioconda-utils>`_

.. details:: Why do I need to pay attention to the PR title?

    Be sure to use `conventional commit messages
    <https://www.conventionalcommits.org/en/v1.0.0/>`_ in your commits and in
    titling the PR. This is part of Release Please. The repo has a GitHub
    Action that will check for this. Note that if the check fails and you
    update the PR title, you may need to push an additional commit to trigger
    the check again.

- Merge to master branch

.. details:: Won't the changes be immediately used?

    Our infrastructure no longer points to the master branch of bioconda-utils.
    Instead, the infrastructure points to specific releases. Merging to master
    branch does not create a release -- see below for how releases are created.

- Release Please (see below) automatically creates a release PR
- Merging that release PR creates a release of bioconda-utils

.. details:: Release Please?

    ``bioconda-utils`` is currently using `Release Please
    <https://github.com/googleapis/release-please>`_ to manage updates,
    changelogs, and versioning. The Release Please GitHub Action running in the
    bioconda-utils repo will keep a special "release PR" with accumulated
    changes since the last release. `Here's an example
    <https://github.com/bioconda/bioconda-utils/pull/765>`_. **Merging the
    special release PR will create a release**.

- Autobump bot detects new release and creates a new PR to create a conda
  package
- That new conda package *is built using the previous version of
  bioconda-utils* since that's what's running on our infrastructure. Merge into bioconda-recipes when tests pass.

.. details:: How are dependencies kept consistent?

    bioconda-utils keeps a `requirements.txt
    <https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-requirements.txt>`_
    file for its own tests. But this needs to match the conda recipe. To
    double-check this, the recipe over in bioconda-recipes has a test that
    installs the ``bioconda_utils-requirements.txt`` file into the recipe's
    test environment, and the test ensures that doing so does not result in any
    changes to the environment -- confirming that the requirements file in the
    bioconda-utile repo and its meta.yaml in the bioconda-recipes repo match.

- Once the conda package is available (check by trying to install locally),
  then update `bioconda-common/common.sh
  <https://github.com/bioconda/bioconda-common/blob/master/common.sh>`_ to
  point to the new version

.. details:: Where is that common.sh file used?

    The common.sh file is used in various workflows (like GitHub Actions and
    Azure DevOps) as a means of having a single central authority on what
    versions are being used.

API docs
~~~~~~~~

This section contains the ``docstring`` generated API documentation
for the modules and subpackages comprising the
:py:mod:`bioconda_utils` Python package. This package implements all
infrastructure and build system components used by the Bioconda
project. Please be aware that the API documented here is not
considered stable.

.. autosummary::
   :toctree: _autosummary

   bioconda_utils
