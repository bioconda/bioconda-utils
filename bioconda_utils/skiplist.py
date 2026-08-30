import os
from pathlib import Path
from typing import Any
from bioconda_utils.recipe import Recipe


class Skiplist:
    def __init__(self, config: dict[str, Any], recipe_folder: Path) -> None:
        self.recipe_folder = recipe_folder.resolve()
        self.global_list: set[Path] = set()

        for p in config.get("blacklists", []):
            blacklist_path: Path = Path(p)

            if not blacklist_path.exists():
                continue

            lines: list[str] = blacklist_path.read_text(encoding="utf8").splitlines()

            self.global_list.update(
                self._get_reldir(line.strip())
                for line in lines
                if line.strip() and not line.startswith("#")
            )

    def _get_reldir(self, recipe_path: str | Path) -> Path:
        return Path(recipe_path).resolve().relative_to(self.recipe_folder)

    def is_skiplisted(self, recipe: str | Path | Recipe) -> bool:
        from bioconda_utils.build_failure import BuildFailureRecord

        if isinstance(recipe, Recipe):
            recipe_reldir = recipe.reldir
        else:
            recipe_reldir = self._get_reldir(recipe)

        if recipe_reldir in self.global_list:
            return True

        build_failure_record = BuildFailureRecord(recipe)
        return build_failure_record.skiplists_current_recipe()
