Developer Docs
--------------

Updating ``bioconda-utils``
~~~~~~~~~~~~~~~~~~~~~~~~~~~
This section documents the steps required to update bioconda-utils and have it
working on Azure DevOps and building packages.

``bioconda-utils`` is currently using `Release Please
<https://github.com/googleapis/release-please>`_ to manage updates, changelogs,
and versioning. This works in a specific way, so the steps below walk through
the process if you're not already familiar.

Prefix the PR title, as well as at least one commit in the PR, with one of the
following change types. Some special types will change the bioconda-utils
version number, as noted below.

- `<type>!` (that is, any of the types below ending an exclamation point) indicates
  a breaking change. *PRs with this title will result in a new* **MAJOR VERSION**
- `feat:` a new feature. *PRs with this title will result in a new* **MINOR VERSION**
- `fix:` fixes a bug. *PRs with this title will result in a new* **PATCH VERSION**
- `test:` changes related to tests
- `chore:` for maintenance changes. E.g. dependency version changes (should use
  a `!` to indicate breaking change in this case)
- `ci:` for changes related to CI *of bioconda-utils*
- `docs:` a change that only affects documentation (ReST, comments, docstrings)
- `refactor:` a change in code that neither fixes a bug nor adds a feature
- `style:` whitespace, formatting, etc


The general workflow is:

1. Open a pull request to `bioconda-utils repo <https://github.com/bioconda/bioconda-utils>`_,
   being sure to use
  `conventional commit messages
  <https://www.conventionalcommits.org/en/v1.0.0/>`_ in your title.

    .. details:: Why do I need to pay attention to the PR title?

        This is part of Release Please, which uses the PR titles (as well as
        individual commits within the PR) to decide on semantic version bumps.
        The bioconda-utils repo has a GitHub Action that will check for
        conventional commit message in the PR title, and the PR will fail
        without a properly-formatted title.

        Note that if you update the PR title to address a failing check, you
        may need to push an additional commit to trigger the check again (or
        possibly close and then reopen the PR).

2. Merge to master branch

    .. details:: Won't the changes be immediately used?

        Our infrastructure no longer points directly to the master branch of
        bioconda-utils. Instead, the infrastructure points to specific
        releases. Merging to master branch does not create a release -- see
        below for how releases are created.

        Release Please monitors the master branch to determine what to add to
        the special release PR.

3. Allow Release Please to automatically create a release PR

    .. details:: What's a release PR?

        A release PR is a special PR automatically create by the Release Please
        GitHub Action running in the bioconda-utils repo. The release PR will
        keep track of accumulated changes since the last release. The version
        in the title of the PR will reflect semantic versioning to use based on
        the accumulated changes. `Here's an example
        <https://github.com/bioconda/bioconda-utils/pull/765>`_. **Merging the
        special release PR will create a release**.

4. Merge the release PR to automatically create a new GitHub release of
   bioconda-utils

    .. details:: I'm done, right?

        Not done yet...our infrastructure uses the *conda package* of
        bioconda-utils, which is in turn hosted on bioconda-recipes. So simply
        creating a new GitHub release of bioconda-utils is insufficient to use
        it on our infrastructure. We still need to build the conda package,
        which happens over on bioconda-recipes.

5. Allow the autobump bot to detect the new release and create a new PR over on
   bioconda-recipes to create an updated conda package.

    .. details:: How are dependencies kept consistent?

        bioconda-utils keeps a `requirements.txt
        <https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-requirements.txt>`_
        file for its own tests. But this needs to match the conda recipe. To
        double-check this, the recipe over in bioconda-recipes has a test that
        installs the ``bioconda_utils-requirements.txt`` file into the recipe's
        test environment, and the test ensures that doing so does not result in
        any changes to the environment -- confirming that the requirements file
        in the bioconda-utile repo and its meta.yaml in the bioconda-recipes
        repo match.

6. Once tests pass, treat it as a typical package: get approval, and then
   merge.

    .. details:: What version is used to build the package?

      That new conda package *is built using the previous version of
      bioconda-utils* since that's what's running on our infrastructure. Merge into
      bioconda-recipes when tests pass.

7. Once the conda package is available (check by trying to install locally),
   update `bioconda-common/common.sh
   <https://github.com/bioconda/bioconda-common/blob/master/common.sh>`_ to
   point to the new version

    .. details:: Where is that common.sh file used?

        The common.sh file is used in various workflows (like GitHub Actions and
        Azure DevOps) as a means of having a single central authority on what
        versions are being used.

At this point, the next time the various workflows run they will get the new
version of `common.sh`, which will cause a cache miss and trigger the
installation of the version of bioconda-utils specified in that file.
**bioconda-recipes is now using the updated version.**.

.. details:: How do I check?

    You can keep an eye on new bioconda-recipe PRs, or maybe close and then
    reopen an existing one. Look for Azure DevOps log under the "Restore cache"
    step (it should say cache miss on the first time it runs) and then check
    "Install bioconda-utils" step to ensure it installed the version you
    expect.

Bulk branch
~~~~~~~~~~~

.. toctree::

    bulk

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

