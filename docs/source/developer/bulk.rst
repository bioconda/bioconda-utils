The `bulk` branch
=================

Sometimes we need to do maintenance or make changes to lots of recipes at once.
This happens most often when there is a new Bioconductor release: all
`bioconductor-*` recipes need to be updated, and the corresponding packages
need to be built.

This ends up taking substantial compute time on CI infrastructure. If run on
the same CI infrastructure that processes pull requests, this might consume CI
time needed for the typical functioning of the daily activity in the bioconda
repository. The `bulk` branch is a mechanism for the Bioconda core team to
perform large-scale changes on a different CI system, without tying up the CI
infrastructure used by contributors on individual pull requests.

Only bioconda core team members have the ability to push to the bulk branch.

The bulk branch reads from the `bulk` branch of `bioconda-common`.


Updating pinnings
-----------------

1. Update `bioconda pinnings
   <https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-conda_build_config.yaml>`_.
   This may take a few tries; you may need to match conda-forge's pinnings.
   Merge this into the master branch, and merge in the Release Please PR to
   create a new version.

2. Allow autobump to pick up the new version (usually takes an hour) and then
   merge the corresponding PR in bioconda-recipes.

3. Update common.sh to use the new version. **In general, this should first be
   done only on the bulk branch**. The bioconda-recipes/bulk branch reads from
   the bioconda-common/bulk branch. So it's possible to run a different version
   of bioconda-utils on bulk.

4. Merge master into bulk. In bioconda-recipes, on the command line merge
   master into bulk to start with a reasonably recent version of history. Since
   bulk is infrequently updated, there may be substantial conflicts such that
   the default ``git checkout bulk && git merge master`` may not work cleanly.
   This tends to happen most with build numbers. You can merge master into
   bulk, while preferring master version in any conflicts, with:

   .. code-block:: bash

     git checkout bulk
     git merge master -s recursive -X theirs

   There may be a few remaining conflicts to fix; in all cases you should
   prefer what's on the master branch.

5. Preliminary run to build cache: In :file:`.github/workflows/Bulk.yml`, set
   the number of workers to 1 (so,
   ``jobs:build-linux:strategy:matrix:runner:[0]``) and also set
   ``--n-workers=1`` in the ``bioconda-utils`` call. This will allow building
   the cache which will be used in subsequent (parallel) runs.

6. Let the initial run finish. Fix anything obvious, and incrementally increase
   the workers and the ``--n-workers`` argument to allow parallel jobs.

7. Once things largely settle down, run ``bioconda-utils update-pinnings`` in
   the bulk branch. This will go through all the pinnings, figure out what
   recipes they're used with, and bump the recipes' build numbers
   appropriately. Then push to bulk to rebuild all of those.
