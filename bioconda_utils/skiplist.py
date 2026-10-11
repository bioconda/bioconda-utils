import os
from pathlib import Path
from typing import Any

from bioconda_utils.recipe import Recipe


class Skiplist:
    def __init__(self, config: dict[str, Any], recipe_folder: Path) -> None:
        self.recipe_folder = recipe_folder.resolve()
        self.global_list: set[Path] = set()

        for p in config.get("blacklists", []):
            lines = p.read_text(encoding="utf8").splitlines()
            self.global_list.update(
                [
                    self._get_reldir(Path(i.strip()))
                    for i in lines
                    if not i.startswith("#") and i.strip()
                ]
            )

    def _get_reldir(self, recipe_path: Path) -> Path:
        # os.path.relpath, not relative_to: recipe_folder may be relative (e.g.
        # "recipes/foo" from a blacklist line) while recipe_path is absolute,
        # and paths outside recipe_folder must yield a ../.. path rather than
        # raising ValueError.
        return Path(os.path.relpath(recipe_path, self.recipe_folder))

    def is_skiplisted(self, recipe: Path | Recipe) -> bool:
        from bioconda_utils.build_failure import BuildFailureRecord

        if isinstance(recipe, Recipe):
            recipe_reldir = recipe.reldir
        else:
            recipe_reldir = self._get_reldir(recipe)

        if recipe_reldir in self.global_list:
            return True

        build_failure_record = BuildFailureRecord(recipe)
        return build_failure_record.skiplists_current_recipe()
