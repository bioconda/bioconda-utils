import os
import time
from typing import Optional, Union
import subprocess as sp
import logging
from hashlib import sha256

import ruamel.yaml
from ruamel.yaml import YAML, CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString
import conda.exports
import conda.base.constants
import pandas as pd
import networkx as nx

from bioconda_utils.recipe import Recipe
from bioconda_utils import graph, utils
from bioconda_utils.githandler import GitHandler


logger = logging.getLogger(__name__)


class BuildFailureRecord:
    git_handler = None

    def __init__(self, recipe: Union[str, Recipe], platform: Optional[str]=None):
        if isinstance(recipe, Recipe):
            self.recipe_path = recipe.path
        else:
            self.recipe_path = recipe
        if platform is None:
            platform = conda.exports.subdir
        self.path = os.path.join(self.recipe_path, f"build_failure.{platform}.yaml")
        self.platform = platform

        def load(path):
            with open(path, "r") as f:
                yaml=YAML()
                try:
                    self.inner = dict(yaml.load(f))
                except ruamel.yaml.reader.ReaderError as e:
                    raise IOError(f"Unable to read build failure record {path}: {e}")

        if self.exists():
            load(self.path)
        else:
            self.inner = dict()

    def exists(self):
        return os.path.exists(self.path)

    def set_recipe_sha_to_current_recipe(self):
        self.recipe_sha = self.get_recipe_sha()
    
    def fill(self, log: Optional[str]=None, reason: Optional[str]=None, category: Optional[str]=None, skiplist: bool=False):
        self.set_recipe_sha_to_current_recipe()
        # if recipe is a leaf (i.e. not used by others as dependency)
        # we can automatically blacklist it if desired
        self.skiplist = skiplist
        if log:
            self.log = log
        if reason:
            self.reason = reason
        if category:
            self.category = category

    def get_recipe_sha(self):
        h = sha256()
        with open(os.path.join(self.recipe_path, "meta.yaml"), "rb") as f:
            h.update(f.read())
            return h.hexdigest()

    def skiplists_current_recipe(self):
        if self.skiplist:
            recipe_sha = self.get_recipe_sha()
            if recipe_sha == self.recipe_sha:
                logger.info(f"Skipping {self.recipe_path} because it is skiplisted in {self.path}.")
                return True
            else:
                logger.info(f"Not skipping {self.recipe_path} as requested in {self.path} because it has been changed (recipe_sha {recipe_sha}) since skiplisting (recipe_sha {self.recipe_sha}).")
        return False

    def write(self):
        logger.info(f"Storing build failure record for recipe {self.recipe_path}")
        with open(self.path, "w") as f:
            yaml=YAML()
            commented_map = CommentedMap()
            commented_map.insert(0, "recipe_sha", self.recipe_sha, comment="The commit at which this recipe failed to build.")
            commented_map.insert(1, "skiplist", self.skiplist, comment="Set to true to skiplist this recipe so that it will be ignored as long as its latest commit is the one given above.")
            i = 2
            if self.log:
                commented_map.insert(
                    i,
                    "log", 
                    # remove invalid chars and keep only the last 100 lines
                    LiteralScalarString("\n".join(utils.yaml_remove_invalid_chars(self.log).splitlines()[-100:])),
                    comment="Last 100 lines of the build log."
                )
                i += 1
            if self.reason:
                commented_map.insert(i, "reason", LiteralScalarString(self.reason))
            yaml.dump(commented_map, f)

    def remove(self):
        logger.info(f"Removing build failure record for recipe {self.recipe_path}")
        os.remove(self.path)

    def commit_and_push_changes(self):
        """Commit and push any changes, including removal of the record."""
        utils.run(["git", "add", self.path], mask=False)
        if utils.run(["git", "diff", "--quiet", "--exit-code", "HEAD", "--", self.path], mask=False, check=False, quiet_failure=True).returncode:
            operation = "add" if os.path.exists(self.path) else "remove"
            utils.run(["git", "commit", "-m", f"[ci skip] {operation} build failure record for recipe {self.recipe_path}"], mask=False)
            for _ in range(3):
                try:
                    # Rebase is "safe" here because this is meant to be run only on the bulk branch,
                    # with no other concurrent committers than the bulk CI processes which do indepenendent
                    # commits on different recipes.
                    # We don't want to use merge commits here because they would all trigger subsequent CI runs
                    # since they lack the [ci skip] part. Further, they would pollute the git history.
                    # If the rebase fails, we simply get an error.
                    utils.run(["git", "pull", "--rebase"], mask=False)
                    utils.run(["git", "push"], mask=False)
                    return
                except sp.CalledProcessError:
                    time.sleep(1)
            logger.error(
                f"Failed to push build failure record for recipe {self.recipe_path}. "
                "This might be because of raise conditions if multiple jobs do "
                "this at the same time. Consider trying again later.")
        else:
            logger.info("Nothing changed in build failure record. Keeping the current version.")

    @property
    def reason(self):
        return self.inner.get("reason", "")

    @property
    def category(self):
        return self.inner.get("category", "")

    @property
    def log(self):
        # Remove category and reason in case a new log is recorded.
        # This is necessary to avoid inconsistency with those manual annotations.
        if "category" in self.inner:
            del self.inner["category"]
        if "reason" in self.inner:
            del self.inner["reason"]
        return self.inner.get("log", "")

    @property
    def skiplist(self):
        return self.inner.get("skiplist", False)

    @property
    def recipe_sha(self):
        return self.inner.get("recipe_sha", None)

    @skiplist.setter
    def skiplist(self, value):
        self.inner["skiplist"] = value

    @log.setter
    def log(self, value):
        self.inner["log"] = value
    
    @recipe_sha.setter
    def recipe_sha(self, value):
        self.inner["recipe_sha"] = value

    @reason.setter
    def reason(self, value):
        self.inner["reason"] = value

    @category.setter
    def category(self, value):
        self.inner["category"] = value


def collect_build_failure_dataframe(recipe_folder, config, channel, link_fmt="txt", link_prefix=""):
    def get_build_failure_records(recipe):
        return filter(
            BuildFailureRecord.exists, 
            [BuildFailureRecord(recipe, platform=platform) for platform in conda.base.constants.PLATFORM_DIRECTORIES]
        )

    def has_build_failure(recipe):
        return any(get_build_failure_records(recipe))
    
    recipes = list(utils.get_recipes(recipe_folder))
    dag, _ = graph.build(recipes, config)

    def get_data():
        for recipe in utils.tqdm(recipes, desc="Checking recipes"):
            if not has_build_failure(recipe):
                continue

            components = recipe.split(os.sep)
            is_version_subdir = len(components) == 3

            if is_version_subdir:
                # skip if latest recipe does not have a build failure
                if not has_build_failure(os.path.dirname(recipe)):
                    continue

            package = components[-1] if not is_version_subdir else components[-2]

            descendants = len(nx.descendants(dag, package))

            downloads = utils.get_package_downloads(channel, package)
            recs = list(get_build_failure_records(recipe))

            failures = ", ".join(utils.format_link(rec.path, link_fmt, prefix=link_prefix, label=rec.platform) for rec in recs)
            skiplisted = any(rec.skiplist for rec in recs)
            prs = utils.format_link(f"https://github.com/bioconda/bioconda-recipes/pulls?q=is%3Apr+is%3Aopen+{package}", link_fmt, label="show")
            yield (recipe, downloads, descendants, skiplisted, failures, prs)

    data = pd.DataFrame(get_data(), columns=["recipe", "downloads", "depending", "skiplisted", "build failures", "pull requests"])
    data.sort_values(by=["depending", "downloads"], ascending=False, inplace=True)
    return data
