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
import os
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

import rattler_build as rb

from bioconda_utils.conda.conda_build_bridge import (
    load_all_meta,
    load_conda_build_config,
    load_meta_fast,
)
from bioconda_utils.conda.repodata import RepoData

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
    container_platform_to_package_subdir,
)
from .conda.recipes import get_package_paths as conda_get_package_paths
from .rattler.rattler_build_bridge import (
    load_rattler_build_global_variants,
    render_rattler_recipe_to_dicts,
)
from .rattler.recipes import get_package_paths as rattler_get_package_paths

logger = logging.getLogger(__name__)


def _load_platform_metas(
    recipe: Path,
    finalize: bool = True,
    target_platform: ContainerPlatform | None = None,
):
    """
    Load conda recipe metas for a single target platform.

    If ``target_platform`` is provided, the corresponding package subdir is used.
    Otherwise, the native platform subdir is used.

    Args:
        recipe: Path to the recipe directory to load.
        finalize: Whether to finalize the loaded metas.
        target_platform: Optional target platform to target.

    Returns:
        A tuple of the resolved package subdir and the loaded metas.
    """
    if target_platform is not None:
        subdir = container_platform_to_package_subdir(target_platform)
    else:
        subdir = RepoData.native_subdir()
    config = load_conda_build_config(subdir=subdir)
    return subdir, load_all_meta(recipe, config=config, finalize=finalize)


def _meta_subdir(meta):
    """
    Return the package subdir implied by a conda meta object.

    Args:
        meta: Loaded conda recipe metadata.

    Returns:
        ``"noarch"`` if the meta is noarch, otherwise the host subdir.
    """
    # logic extracted from conda_build.variants.bldpkg_path
    return "noarch" if meta.noarch or meta.noarch_python else meta.config.host_subdir


def check_recipe_skippable(
    recipe: Path, check_channels, target_platform: ContainerPlatform | None = None
):
    """
    Return True if the same number of builds (per subdir) defined by the recipe
    are already in channel_packages.

    Args:
        recipe: Path to the recipe directory to check.
        check_channels: Channels to search for existing package builds.
        target_platform: Optional target platform used when loading metas.

    Returns:
        True if the package builds defined by the recipe are already present
        in the given channels for the corresponding subdirs, otherwise False.
    """
    subdir, metas = _load_platform_metas(
        recipe, finalize=False, target_platform=target_platform
    )
    # The recipe likely defined skip: True
    if not metas:
        return True
    # If on CI, handle noarch.
    if (
        os.environ.get("CI") == "true"
        and metas[0].get_value("build/noarch")
        and not subdir.startswith("linux")
    ):
        logger.info(
            "FILTER: only building %s on linux because it defines noarch.",
            recipe,
        )
        return True

    packages = {
        (meta.name(), meta.version(), int(meta.build_number() or 0)) for meta in metas
    }
    rendered_subdirs = {_meta_subdir(meta) for meta in metas}
    queried_subdirs = sorted(rendered_subdirs | {"noarch"})
    r = RepoData()
    num_existing_pkg_builds = Counter(
        (name, version, build_number, subdir)
        for name, version, build_number in packages
        for subdir in r.get_package_data(
            "subdir",
            name=name,
            version=version,
            build_number=build_number,
            channels=check_channels,
            platform=queried_subdirs,
        )
    )
    if num_existing_pkg_builds == Counter():
        # No packages with same version + build num in channels: no need to skip
        return False
    num_new_pkg_builds = Counter(
        (
            meta.name(),
            meta.version(),
            int(meta.build_number() or 0),
            _meta_subdir(meta),
        )
        for meta in metas
    )
    if num_new_pkg_builds == num_existing_pkg_builds:
        logger.info(
            "FILTER: not building recipe %s because "
            "the same number of builds are in channel(s) and it is not forced.",
            recipe,
        )
        return True
    return False


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
                    f"Both rattler_output_dir and global_variants must be set when calling get_package_paths on a rattler-recipe: {recipe.path.as_posix()}"
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


def get_recipe_paths(recipes: Iterable[RecipePath]) -> list[Path]:
    """
    Return the underlying paths for an iterable of recipe objects.

    Args:
        recipes: Iterable of RecipePath objects.

    Returns:
        A list of Path objects pointing to each recipe directory.
    """
    return [recipe.path for recipe in recipes]


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
