import os
from typing import Any, Dict, Union
from bioconda_utils.recipe import Recipe


class Skiplist:
    def __init__(self, config: Dict[str, Any], recipe_folder: str):
        self.recipe_folder = recipe_folder
        self.global_list = set()
        for p in config.get('blacklists', []):
            self.global_list.update(
                [
                    self._get_reldir(i.strip())
                    for i in open(p, encoding='utf8')
                    if not i.startswith('#') and i.strip()
                ]
            )

    def _get_reldir(self, recipe_path: str):
        return os.path.relpath(recipe_path, self.recipe_folder)

    def is_skiplisted(self, recipe: Union[str, Recipe]) -> bool:
        from bioconda_utils.build_failure import BuildFailureRecord
        
        if isinstance(recipe, Recipe):
            recipe_reldir = recipe.reldir
        else:
            recipe_reldir = self._get_reldir(recipe)

        if recipe_reldir in self.global_list:
            return True

        build_failure_record = BuildFailureRecord(recipe)
        return build_failure_record.skiplists_current_recipe()