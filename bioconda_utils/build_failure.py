import os

from ruamel_yaml import YAML
import conda.exports

from bioconda_utils.recipe import Recipe


class BuildFailureRecord:
    def __init__(self, recipe: str | Recipe):
        if isinstance(recipe, Recipe):
            recipe_path = recipe.path
        else:
            recipe_path = recipe
        self.path = os.path.join(recipe_path, f"build_failure.{conda.exports.subdir}.yaml")
        
        self.exists = False

        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                yaml=YAML()
                self.inner = yaml.load(f)
        else:
            self.inner = dict()

    def write(self):
        with open(self.path, "w") as f:
            yaml=YAML()
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
        return self.inner.get("commit_sha", "")

    @blacklist.setter
    def blacklist(self, value):
        self.inner["blacklist"] = value

    @log.setter
    def log(self, value):
        self.inner["log"] = value
    
    @commit_sha.setter
    def commit_sha(self, value):
        self.inner["commit_sha"] = value