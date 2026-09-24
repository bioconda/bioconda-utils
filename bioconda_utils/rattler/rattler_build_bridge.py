"""
Bridge to py-rattler-build.
"""

from dataclasses import dataclass
import json
import platformdirs
import os
from pathlib import Path
import shutil
from typing import Any, Iterator

from .._types import OsLabel
from ..conda.conda_build_bridge import subdir_to_oslabel
from ..conda.repodata import RepoData
import rattler_build as rb
import conda_build.config
import conda_build.metadata as metadata


@dataclass(slots=True)
class RattlerDictList:
    recipes: list[dict[str, Any]]
    is_multi: bool

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.recipes)


# TODO (rb): Is it correct to assume the native platform is the target platform?
def _filter_config(config_path: Path) -> str:
    """
    Filters out lines in the conda build config based on the platforms
    specified in their comments. This conda-build convention is not supported
    by rattler-build.
    """

    subdir = RepoData.native_subdir()
    os_label = subdir_to_oslabel(subdir)
    arch: str = subdir.removeprefix(f"{os_label}-")
    config = conda_build.config.Config(platform=os_label, arch=arch)
    # target = RepoData.native_platform().split("-")
    # native_platform = target[0]
    # arch = platform.machine()
    # config = conda_build.config.Config(platform=native_platform, arch=arch)
    namespace = metadata.get_selectors(config)

    with open(config_path, "r") as f:
        raw = f.read()

    filtered: str = metadata.select_lines(
        text=raw, namespace=namespace, variants_in_place=False
    )
    return filtered


def get_rattler_build_global_variants_paths() -> list[Path]:
    bioconda_utils_bin = shutil.which("bioconda-utils")
    if bioconda_utils_bin is None:
        raise FileNotFoundError("Unable to find bioconda-utils on PATH")
    env_root = Path(bioconda_utils_bin).parents[1]
    return [
        Path(env_root) / "bioconda_utils-conda_build_config.yaml",
        Path(__file__).resolve().parent / "bioconda_utils-conda_build_config.yaml",
    ]


def render_rattler_recipe(
    recipe: Path, global_variants: rb.VariantConfig
) -> list[rb.RenderedVariant]:
    """
    Given a package name, find the current recipe.yaml file, render it, and return
    the rendered variants.
    """
    try:
        # Parse YAML into Stage0Recipe
        recipe_file: Path = Path(recipe) / "recipe.yaml"
        local_variants_path: Path = Path(recipe) / "variants.yaml"

        recipe_s0: rb.Stage0Recipe = rb.Stage0Recipe.from_file(recipe_file)

        # merging variants

        variants: rb.VariantConfig = global_variants

        if local_variants_path.exists():
            local_variants = rb.VariantConfig.from_file(local_variants_path)
            variants = global_variants.merge(local_variants)

        # rendering recipe
        rendered_variants: list[rb.RenderedVariant] = recipe_s0.render(variants)

        return rendered_variants
    except Exception:
        raise ValueError("Problem inspecting rattler recipe {0}".format(recipe))


def render_rattler_recipe_to_dicts(
    recipe: Path, global_variants: rb.VariantConfig
) -> RattlerDictList:
    """
    Given a package name, find the current recipe.yaml file, render it, and return
    the rendered variants.
    """
    try:
        # Parse YAML into Stage0Recipe
        recipe_file: Path = Path(recipe) / "recipe.yaml"
        local_variants_path: Path = Path(recipe) / "variants.yaml"

        recipe_s0: rb.Stage0Recipe = rb.Stage0Recipe.from_file(recipe_file)
        is_multi: bool = isinstance(recipe_s0, rb.MultiOutputRecipe)

        # merging variants

        variants: rb.VariantConfig = global_variants

        if local_variants_path.exists():
            local_variants = rb.VariantConfig.from_file(local_variants_path)
            variants = global_variants.merge(local_variants)

        # rendering recipe
        rendered_variants: list[rb.RenderedVariant] = recipe_s0.render(variants)

        return RattlerDictList(
            recipes=[r.recipe.to_dict() for r in rendered_variants], is_multi=is_multi
        )
    except Exception:
        raise ValueError("Problem rendering rattler recipe to dict {0}".format(recipe))


def load_rattler_build_global_variants() -> rb.VariantConfig:
    paths: list[Path] = get_rattler_build_global_variants_paths()

    filtered_yaml: str = ""

    for p in paths:
        if p.exists():
            filtered_yaml = _filter_config(p)
            break

    if not filtered_yaml:
        path_str: str = ", ".join([str(p) for p in paths])
        raise FileNotFoundError(
            f"Failed to load bioconda_utils-variants.yaml from any of these paths: {path_str}"
        )
    else:
        global_variants: rb.VariantConfig = rb.VariantConfig.from_yaml(filtered_yaml)
        return global_variants


def get_default_rattler_cache_dir_path() -> Path:
    bioconda_utils_cache: Path = Path(platformdirs.user_cache_dir("bioconda-utils"))
    return bioconda_utils_cache / "rattler_cache"


CURR_RATTLER_CACHE_DIR_PATH: Path = get_default_rattler_cache_dir_path()


def load_v1_recipe_schema() -> dict[Any, Any]:
    schema_path: Path = Path(__file__).parent / "v1_recipe_schema.json"
    with open(schema_path, "r") as f:
        schema = json.load(f)
    return schema


def set_rattler_cache_to_dir(
    path: Path, curr_path: Path = CURR_RATTLER_CACHE_DIR_PATH
) -> None:
    if not path.exists():
        path.mkdir()
    os.environ["RATTLER_CACHE_DIR"] = str(path)
    # TODO (rb): this way of setting and getting the current cache dir is very ugly and should be improved
    # is there a more elegant way to do this?
    global CURR_RATTLER_CACHE_DIR_PATH
    CURR_RATTLER_CACHE_DIR_PATH = path
