import os
from typing import Optional, Union
from bioconda_utils import utils
from bioconda_utils.githandler import GitHandler
import subprocess as sp
import logging

import ruamel.yaml
from ruamel.yaml import YAML, CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString
import conda.exports

from bioconda_utils.recipe import Recipe


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

        def load(path):
            with open(path, "r") as f:
                yaml=YAML()
                try:
                    self.inner = dict(yaml.load(f))
                except ruamel.yaml.reader.ReaderError as e:
                    raise IOError(f"Unable to read build failure record {path}: {e}")

        if os.path.exists(self.path):
            load(self.path)
            self.exists = True
        else:
            self.inner = dict()
            self.exists = False

    def set_commit_sha_to_current_recipe(self):
        self.commit_sha = self.get_recipe_commit_sha()

    def get_recipe_commit_sha(self):
        if self.git_handler is None:
            self.git_handler = GitHandler()
        # Get last commit sha of recipe
        filepath = os.path.join(self.recipe_path, "meta.yaml")
        commit_sha = sp.run(["git", "rev-list", "-1", "HEAD", filepath], check=True, capture_output=True).stdout.decode().strip()
        return commit_sha

    def skiplists_current_recipe(self):
        if self.skiplist:
            commit_sha = self.get_recipe_commit_sha()
            if commit_sha == self.commit_sha:
                logger.info(f"Skipping {self.recipe_path} because it is skiplisted in {self.path}.")
                return True
            else:
                logger.info(f"Not skipping {self.recipe_path} as requested in {self.path} because it has been changed (commit {commit_sha}) since skiplisting (commit {self.commit_sha}).")
        return False

    def write(self):
        with open(self.path, "w") as f:
            yaml=YAML()
            commented_map = CommentedMap()
            commented_map.insert(0, "commit_sha", self.commit_sha, comment="The commit at which this recipe failed to build.")
            commented_map.insert(1, "skiplist", self.skiplist, comment="Set to true to skiplist this recipe so that it will be ignored as long as its latest commit is the one given above.")
            i = 2
            if self.log:
                commented_map.insert(
                    i,
                    "log", 
                    # remove invalid chars and keep only the last 100 lines
                    LiteralScalarString(utils.yaml_remove_invalid_chars(self.log).splitlines()[-100:]),
                    comment="Last 100 lines of the build log."
                )
                i += 1
            if self.reason:
                commented_map.insert(i, "reason", LiteralScalarString(self.reason))
            yaml.dump(commented_map, f)
        self.exists = True

    def delete(self):
        if self.exists:
            os.remove(self.path)
            self.exists = False

    @property
    def reason(self):
        return self.inner.get("reason", "")

    @property
    def log(self):
        return self.inner.get("log", "")

    @property
    def skiplist(self):
        return self.inner.get("skiplist", False)

    @property
    def commit_sha(self):
        return self.inner.get("commit_sha", None)

    @skiplist.setter
    def skiplist(self, value):
        self.inner["skiplist"] = value

    @log.setter
    def log(self, value):
        self.inner["log"] = value
    
    @commit_sha.setter
    def commit_sha(self, value):
        self.inner["commit_sha"] = value

    @reason.setter
    def reason(self, value):
        self.inner["reason"] = value