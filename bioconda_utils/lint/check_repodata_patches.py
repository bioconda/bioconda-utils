from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.lint import INFO, LintCheck
from bioconda_utils.recipe import Recipe


class repodata_patches_no_version_bump(LintCheck):
    """The bioconda-repodata-patches recipe was changed but does not contain a version bump.

    Please set the version to the current date in the format ``YYYYMMDD``.
    """

    def check_recipe(self, recipe: Recipe) -> None:
        if recipe.get("package/name") != "bioconda-repodata-patches":
            return
        repodata = RepoData()
        old_versions = repodata.get_versions("bioconda-repodata-patches")
        if recipe.get("package/version") in old_versions:
            self.message()


class repodata_patches_show_diff(LintCheck):
    """The bioconda-repodata-patches recipe was changed.

    Here comes the resulting repodata-diff:
    """

    severity = INFO

    def check_recipe(self, recipe: Recipe) -> None:
        if recipe.get("package/name") != "bioconda-repodata-patches":
            return
        # TODO run diff script and display the diff as a lint message
