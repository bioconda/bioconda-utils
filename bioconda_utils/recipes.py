"""
Recipe discovery and build planning.

Finding recipe directories, querying their dependencies, and deciding
which package outputs a build would produce -- and whether it can be
skipped because they already exist in the target channels.

Redirects to the backend-specific methods from the conda and rattler modules.
"""

from __future__ import annotations
from typing import Iterable

import rattler_build as rb
import fnmatch
import glob
import logging
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from itertools import chain
from pathlib import Path

from conda_build import api

from bioconda_utils.conda.conda_build_bridge import (
    load_all_meta,
    load_conda_build_config,
    load_meta_fast,
)
from bioconda_utils.conda.repodata import RepoData

from ._types import (
    BuildSystem,
    ContainerPlatform,
    container_platform_to_package_subdir,
    RecipePath,
    MetaOrRattler,
    CONDA,
    RATTLER,
)
from .rattler.rattler_build_bridge import (
    load_rattler_build_global_variants,
    render_rattler_recipe_to_dicts,
)
from .conda.recipes import get_deps as conda_get_deps
from .conda.recipes import get_package_paths as conda_get_package_paths
from .rattler.recipes import get_package_paths as rattler_get_package_paths
# from .conda_build_bridge import load_all_meta, load_conda_build_config
# from .repodata import RepoData

logger = logging.getLogger(__name__)


# def get_deps(recipe: RecipePath, build=True):
#     """
#     Generator of dependencies for a single recipe

#     Only names (not versions) of dependencies are yielded.

#     If the variant/version matrix yields multiple instances of the metadata,
#     the union of these dependencies is returned.

#     Parameters
#     ----------
#     recipe : str or MetaData
#         If string, it is a path to the recipe; otherwise assume it is a parsed
#         conda_build.metadata.MetaData instance.

#     build : bool
#         If True yield build dependencies, if False yield run dependencies.
#     """
#     assert isinstance(recipe, RecipePath)

#     match recipe.build_system:
#         case BuildSystem.CONDA:
#             conda_get_deps(recipe.path, build)
#     metadata = load_all_meta(recipe, finalize=False)

#     all_deps = set()
#     for meta in metadata:
#         if build:
#             deps = meta.get_value("requirements/build", [])
#         else:
#             deps = meta.get_value("requirements/run", [])
#         all_deps.update(dep.split()[0] for dep in deps)
#     return all_deps


# def built_package_paths(recipe: RecipePath) -> list[str]:
#     """
#     Returns the path to which a recipe would be built.

#     Does not necessarily exist; equivalent to ``conda build --output recipename``
#     but without the subprocess.
#     """
#     config = load_conda_build_config()
#     # NB: Setting bypass_env_check disables ``pin_compatible`` parsing, which
#     #     these days does not change the package build string, so should be fine.
#     paths = api.get_output_file_paths(recipe, config=config, bypass_env_check=True)
#     return paths


def _load_platform_metas(recipe, finalize=True, target_platform=None):
    if target_platform is not None:
        subdir = container_platform_to_package_subdir(target_platform)
    else:
        subdir = RepoData.native_subdir()
    config = load_conda_build_config(subdir=subdir)
    return subdir, load_all_meta(recipe, config=config, finalize=finalize)


def _meta_subdir(meta):
    # logic extracted from conda_build.variants.bldpkg_path
    return "noarch" if meta.noarch or meta.noarch_python else meta.config.host_subdir


def check_recipe_skippable(recipe, check_channels, target_platform=None):
    """
    Return True if the same number of builds (per subdir) defined by the recipe
    are already in channel_packages.
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


# TODO (rb): can this also be implemented for rattler-build?
# for now in build.build we simply add the package paths of the packages
# build with rattler-build **after** they have been built.
def get_package_paths(
    recipe: RecipePath,
    check_channels: list[str],
    force: bool = False,
    finalize: bool = True,
    rattler_output_dir: Path | None = None,
    global_variants: rb.VariantConfig | None = None,
    target_platform: ContainerPlatform | None = None,
) -> list[Path]:
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


def load_meta_and_recipe_fast(recipe: RecipePath, env=None) -> MetaOrRattler:
    """
    Given a RecipePath, check whether the given package should be build with conda build
    or rattler. Returns a MetaOrRattler object containing the original RecipePath and either
    the contents of the recipe's meta.yaml (for conda build recipes) or a rattler build
    RenderedVariant (for rattler build recipes). The other field will be set to None.
    """
    match recipe.build_system:
        case BuildSystem.CONDA:
            meta, _ = load_meta_fast(recipe.path, env)
            return MetaOrRattler(path=recipe, meta=meta, rattler=None)
        case BuildSystem.RATTLER:
            # TODO (rb): is it possible to pass the global variants to the function
            # so we don't have to reload it constantly?
            # as far as I know we have to reload it, otherwise the parallelisation calls pickle on it
            global_variants: rb.VariantConfig = load_rattler_build_global_variants()
            rattler = render_rattler_recipe_to_dicts(recipe.path, global_variants)
            return MetaOrRattler(path=recipe, meta=None, rattler=rattler)


def get_recipe_paths(recipes: Iterable[RecipePath]) -> list[Path]:
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
