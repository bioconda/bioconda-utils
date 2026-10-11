"""
Recipe discovery and build planning.

Finding recipe directories, querying their dependencies, and deciding
which package outputs a build would produce -- and whether it can be
skipped because they already exist in the target channels.

Redirects to the backend-specific methods from the conda and rattler modules.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import rattler_build as rb

from bioconda_utils.conda.conda_build_bridge import load_meta_fast

from ._types import (
    CONDA,
    RATTLER,
    BuildSystem,
    ContainerPlatform,
    PackageSubdir,
    QueryableRecipe,
    QueryableV0Recipe,
    QueryableV1Recipe,
    RecipePath,
)
from .conda.recipes import get_package_paths as conda_get_package_paths
from .rattler.rattler_build_bridge import (
    load_rattler_build_global_variants,
    render_rattler_recipe_to_dicts,
)
from .rattler.recipes import get_package_paths as rattler_get_package_paths

logger = logging.getLogger(__name__)


def get_package_paths(
    recipe: RecipePath,
    check_channels: list[str],
    force: bool = False,
    finalize: bool = True,
    rattler_output_dir: Path | None = None,
    global_variants: rb.VariantConfig | None = None,
    target_platform: ContainerPlatform | None = None,
) -> list[Path]:
    """
    Predict the output package file paths for a rendered recipe.

    The paths are resolved according to the recipe build system: rattler recipes
    use the rendered Rattler output paths, while conda recipes use conda-build
    metadata and the provided channels.

    Args:
        recipe: Recipe to inspect.
        check_channels: Channels used when resolving conda package paths.
        force: Whether to force conda package path resolution.
        finalize: Whether to finalize conda metadata before path resolution.
        rattler_output_dir: Rattler output directory; required for rattler recipes.
        global_variants: Rattler variant configuration; required for rattler recipes.
        target_platform: Optional target platform used for conda path resolution.

    Returns:
        A list of expected package output paths.
    """
    match recipe.build_system:
        case BuildSystem.RATTLER:
            if rattler_output_dir is None or global_variants is None:
                raise ValueError(
                    f"Both rattler_output_dir and global_variants must be set when calling get_package_paths on a rattler-recipe: {recipe}"
                )
            return rattler_get_package_paths(
                recipe, rattler_output_dir, global_variants
            )
        case BuildSystem.CONDA:
            return conda_get_package_paths(
                recipe.path, check_channels, force, finalize, target_platform
            )


def load_meta_and_recipe_fast(
    recipe: RecipePath, env=None, platform: PackageSubdir | None = None
) -> QueryableRecipe:
    """
    Load recipe metadata quickly for either conda or rattler recipes.

    For conda recipes, the metadata is loaded from ``meta.yaml``. For rattler
    recipes, the recipe is rendered using Rattler build global variants.

    Args:
        recipe: Recipe to load.
        env: Optional environment variables used when loading conda metadata.
        platform: Optional target platform used when rendering rattler recipes.

    Returns:
        A QueryableRecipe containing the original recipe path and either the
        loaded conda metadata or the rendered rattler recipe data. The unused
        field is set to ``None``.
    """
    # TODO this always assumes the native platform (for both V1 and V2 recipes)
    # One should perhaps consider the target platforms here as well, e.g. returning a union
    # of all dependencies across target platforms. In most of the cases, this should
    # not make a difference though.
    match recipe.build_system:
        case BuildSystem.CONDA:
            meta, _ = load_meta_fast(recipe.path, env)
            return QueryableV0Recipe(path=recipe, meta=meta)
        case BuildSystem.RATTLER:
            # TODO (rb): is it possible to pass the global variants to the function
            # so we don't have to reload it constantly?
            # as far as I know we have to reload it, otherwise the parallelisation calls pickle on it
            global_variants: rb.VariantConfig = load_rattler_build_global_variants(
                platform
            )
            rattler_dicts = render_rattler_recipe_to_dicts(recipe.path, global_variants)
            return QueryableV1Recipe(path=recipe, recipe=rattler_dicts)


def get_recipes(
    recipe_folder: Path,
    package_patterns: Sequence[str] = ("*",),
    exclude_patterns: Sequence[str] = (),
) -> Iterator[RecipePath]:
    """
    Generator of recipes.

    Finds (possibly nested) directories containing a ``meta.yaml`` or ``recipe.yaml`` file.

    Parameters
    ----------
    recipe_folder : Path
        Top-level dir of the recipes

    package_patterns : sequence of str
        Pattern or patterns to restrict the results.

    exclude_patterns : sequence of str
        Patterns to exclude from the results.
    """
    # recipe_folder_text = os.fspath(recipe_folder)
    for pattern in package_patterns:
        logger.debug(
            "get_recipes(%s, package_patterns=%s): %s",
            recipe_folder.as_posix(),
            package_patterns,
            pattern,
        )
        for new_dir in recipe_folder.glob(pattern):
            meta_yaml_found_or_excluded: bool = False
            recipe_yaml_found_or_excluded: bool = False

            for dir_path, _, file_names in new_dir.walk():
                # prepend `/` to replicate behaviour of legacy code
                relative: str = "/" + dir_path.relative_to(recipe_folder).as_posix()
                if any(fnmatch.fnmatch(relative, pat) for pat in exclude_patterns):
                    meta_yaml_found_or_excluded = True
                    continue
                if "meta.yaml" in file_names:
                    meta_yaml_found_or_excluded = True
                    yield RecipePath(path=dir_path, build_system=CONDA)
                elif "recipe.yaml" in file_names:
                    recipe_yaml_found_or_excluded = True
                    yield RecipePath(path=dir_path, build_system=RATTLER)
            if (
                not meta_yaml_found_or_excluded
                and not recipe_yaml_found_or_excluded
                and new_dir.is_dir()
            ):
                logger.warning(
                    "No meta.yaml or recipe.yaml found in %s."
                    " If you want to ignore this directory, add it to the blacklist.",
                    new_dir,
                )
