The `bulk` branch
=================

Sometimes we need to do maintenance or make changes to lots of recipes at once.
This happens most often when there is a new Bioconductor release: all
`bioconductor-*` recipes need to be updated, and the corresponding packages
need to be built.

This ends up taking substantial compute time on CI infrastructure. If this were
run on the same CI infrastructure that processes pull requests, this might
consume CI time needed for the typical functioning of the daily activity in the
bioconda repository. The `bulk` branch is a mechanism for the Bioconda core
team to perform large-scale changes on a different CI system, without tying up
the CI infrastructure used by contributors on individual pull requests.

The bulk branch reads from the `bulk` branch of `bioconda-common`.

**The bulk branch immediately uploads successfully built packages to
Anaconda.** As such, only the bioconda core team has the ability to push to
this branch.

Updating pinnings
-----------------

Pinnings are updated for example when we are supporting a new version of
Python. These are versions of base packages that are supported, and form the
basis of the build string hashes at the end of conda package names. A recent
example is updating pinnings to support Python 3.10.

1. Update `bioconda pinnings
   <https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-conda_build_config.yaml>`_.
   This may take a few tries; you may need to make changes to match
   conda-forge's pinnings. Merge these changes into the master branch (which
   will create or update a Release Please PR) and merge in the Release Please
   PR to create a new version of bioconda-utils.

2. Allow autobump to pick up the new version. This usually takes an hour. Then
   merge the corresponding PR in bioconda-recipes. You now have a new
   bioconda-utils package to use which contains those pinnings.

3. Update ``common.sh`` (see `here
   <https://github.com/bioconda/bioconda-common/blob/master/common.sh>`_) to
   use the new version. **In general, this should be done only on the bulk
   branch to start**, since changing the pinnings will likely trigger many
   recipes to require rebuilding. Since the bioconda-recipes/bulk branch reads
   from the bioconda-common/bulk branch, this allows bulk to run a different
   version of bioconda-utils. Once a bulk migration is complete, 

4. In bioconda-recipes, merge master into bulk to start with a clean slate.
   Since bulk is infrequently updated, there may be substantial conflicts
   caused by running the default ``git checkout bulk && git merge master``.
   This tends to happen most with build numbers. But since we want to prefer
   using whatever is in the master branch, we can merge master into bulk, while
   preferring master version in any conflicts, with:

   .. code-block:: bash

     git checkout bulk
     git merge master -s recursive -X theirs

   There may be a few remaining conflicts to fix; in all cases you should
   prefer what's on the master branch.

5. Start a preliminary bulk run to build the cache. In :file:`.github/workflows/Bulk.yml`, set
   the number of workers to 1 (so,
   ``jobs:build-linux:strategy:matrix:runner:[0]``) and also set
   ``--n-workers=1`` in the ``bioconda-utils`` call. This will allow building
   the cache which will be used in subsequent (parallel) runs. Make sure you do
   this for both the Linux and MacOS sections.

6. Let the initial run finish. Fix anything obvious, and now that the cache is
   built you can incrementally increase the workers and the ``--n-workers``
   argument to allow parallel jobs.

7. Once things largely settle down, run ``bioconda-utils update-pinnings`` in
   the bulk branch. This will go through all the pinnings, figure out what
   recipes they're used with, and bump the recipes' build numbers
   appropriately. Then push to bulk to rebuild all of those.

Merging back to master
----------------------

The goal on the bulk branch is to get all workers successfully passing, such
that there is nothing to do in the PR where bulk is merged into master. This
may require adding recipes to the ``build-fail-blacklist`` to skip building
them.

Notes on working with bulk branch
---------------------------------

Some unordered notes on working with the bulk branch:

- Remember that successfully-built packages are immediately pushed to Anaconda.

- You may want to coordinate the timing of fixes and pushes (say, via gitter).
  This is because the bulk branch has ``fail-fast: false`` set to allow
  parallel jobs to progress as much as possible. Multiple people pushing to
  bulk means that there is a risk of trying to build the same recipes multiple
  times. In such a case, only the first package will be actually uploaded and
  subsequent packages will a failure on the upload step. So there is no danger
  to the channel, it's just poor use of CI resources.

- The logs are awkward to read and hard to find exactly where failures occur.
  One way to do this is to go to the bottom where there is a report of which
  packages failed. This report is shown when a bulk job goes to completion
  (rather than timing out). Then search for that package backwards through the
  log. You can also look for the broad structure of the log: recipes with
  nothing to do will be reported in a short stanza, so you can use those as
  structural markers to indicate where there's no useful log info.

- Instead of using the search functionality in the CI logs, download the raw
  log (from gear menu at top right) to use your browser search functionality,
  which is often much easier to use (for example, Chrome shows occurrences of
  search term throughout the document in the scrollbar, which makes digging for
  the actual error a lot easier).

- You may see a lot of output for Python packages in particular. This is because for
  bioconda-utils to figure out whether it needs to build the package, it needs
  to know what the hash is for the package. This in turn requires figuring out
  all the dependencies to see which of them are pinned and then using those to
  calculate a hash. So it may appear that it's doing a lot of work for packages
  that don't need to be rebuilt, but that work needs to be done simply to
  figure out if a rebuild is needed, and so this is expected.

- The bulk runs take place on GitHub Actions, and the configuration is in
  :file:`.github/workflows/Bulk.yml`.
