import os
from bioconda_utils.githandler import GitHandler

from ruamel.yaml import YAML, CommentedMap
import conda.exports

from bioconda_utils.recipe import Recipe


class BuildFailureRecord:
    git_handler = None

    def __init__(self, recipe: str | Recipe):
        if isinstance(recipe, Recipe):
            self.recipe_path = recipe.path
        else:
            self.recipe_path = recipe
        self.path = os.path.join(self.recipe_path, f"build_failure.{conda.exports.subdir}.yaml")

        self.exists = False

        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                yaml=YAML()
                self.inner = yaml.load(f)
        else:
            self.inner = dict()

    def set_commit_sha_to_current_recipe(self):
        self.commit_sha = self.get_recipe_commit_sha()

    def get_recipe_commit_sha(self):
        if self.git_handler is None:
            self.git_handler = GitHandler()
        # Get last commit sha of recipe
        commit = self.git_handler.repo.head.commit
        tree = commit.tree
        file_blob = tree[os.path.join(self.recipe_path, "meta.yaml")]
        commit_sha = file_blob.binsha.hex()
        return commit_sha

    def blacklists_current_recipe(self):
        if self.blacklist:
            commit_sha = self.get_recipe_commit_sha()
            if commit_sha == self.commit_sha:
                return True
        return False

    def write(self):
        with open(self.path, "w") as f:
            yaml=YAML()
            commented_map = CommentedMap()
            commented_map.insert(0, "commit_sha", self.commit_sha, comment="The commit at which this recipe failed to build.")
            commented_map.insert(1, "blacklist", self.blacklist, comment="Set to true to blacklist this recipe so that it will be ignored as long as its latest commit is the one given above.")
            commented_map.insert(2, "log", self.log)
            yaml.dump(self.inner, f)
        self.exists = True

    def delete(self):
        if self.exists:
            os.remove(self.path)
            self.exists = False

    @property
    def log(self):
        return self.inner.get("log", "")

    @property
    def blacklist(self):
        return self.inner.get("blacklist", False)

    @property
    def commit_sha(self):
        return self.inner.get("commit_sha", None)

    @blacklist.setter
    def blacklist(self, value):
        self.inner["blacklist"] = value

    @log.setter
    def log(self, value):
        self.inner["log"] = value
    
    @commit_sha.setter
    def commit_sha(self, value):
        self.inner["commit_sha"] = value