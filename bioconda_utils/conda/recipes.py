"""
Recipe discovery and build planning.

Finding recipe directories, querying their dependencies, and deciding
which package outputs a build would produce -- and whether it can be
skipped because they already exist in the target channels.
"""

from __future__ import annotations

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

from .._types import (
    container_platform_to_package_subdir,
)
from .conda_build_bridge import load_all_meta, load_conda_build_config
from .repodata import RepoData

logger = logging.getLogger(__name__)


def get_deps(recipe, build=True):
    """
    Generator of dependencies for a single recipe

    Only names (not versions) of dependencies are yielded.

    If the variant/version matrix yields multiple instances of the metadata,
    the union of these dependencies is returned.

    Parameters
    ----------
    recipe : str or MetaData
        If string, it is a path to the recipe; otherwise assume it is a parsed
        conda_build.metadata.MetaData instance.

    build : bool
        If True yield build dependencies, if False yield run dependencies.
    """
    assert isinstance(recipe, str)
    metadata = load_all_meta(recipe, finalize=False)

    all_deps = set()
    for meta in metadata:
        if build:
            deps = meta.get_value("requirements/build", [])
        else:
            deps = meta.get_value("requirements/run", [])
        all_deps.update(dep.split()[0] for dep in deps)
    return all_deps


def get_recipes(
    recipe_folder: Path,
    package_patterns: Sequence[str] = ("*",),
    exclude_patterns: Sequence[str] = (),
) -> Iterator[Path]:
    """
    Generator of recipes.

    Finds (possibly nested) directories containing a ``meta.yaml`` file.

    Parameters
    ----------
    recipe_folder : Path
        Top-level dir of the recipes

    package_patterns : sequence of str
        Pattern or patterns to restrict the results.

    exclude_patterns : sequence of str
        Patterns to exclude from the results.
    """
    recipe_folder_text = os.fspath(recipe_folder)
    for pattern in package_patterns:
        logger.debug(
            "get_recipes(%s, package_patterns=%s): %s",
            recipe_folder,
            package_patterns,
            pattern,
        )
        path = os.path.join(recipe_folder, pattern)
        for new_dir in glob.glob(path):
            meta_yaml_found_or_excluded = False
            for dir_path, _, file_names in os.walk(new_dir):
                if any(
                    fnmatch.fnmatch(dir_path[len(recipe_folder_text) :], pat)
                    for pat in exclude_patterns
                ):
                    meta_yaml_found_or_excluded = True
                    continue
                if "meta.yaml" in file_names:
                    meta_yaml_found_or_excluded = True
                    yield Path(dir_path)
            if not meta_yaml_found_or_excluded and os.path.isdir(new_dir):
                logger.warning(
                    "No meta.yaml found in %s."
                    " If you want to ignore this directory, add it to the blacklist.",
                    new_dir,
                )
                yield Path(new_dir)


class DivergentBuildsError(Exception):
    pass


def _string_or_float_to_integer_python(s: str | float) -> int:
    """
    conda-build 2.0.4 expects CONDA_PY values to be integers (e.g., 27, 35) but
    older versions were OK with strings or even floats.

    To avoid editing existing config files, we support those values here.
    """

    try:
        s = float(s)
        if s < 10:  # it'll be a looong time before we hit Python 10.0
            s = int(s * 10)
        else:
            s = int(s)
    except ValueError:
        raise ValueError(f"{s} is an unrecognized Python version")
    return s


def built_package_paths(recipe: str) -> list[str]:
    """
    Returns the path to which a recipe would be built.

    Does not necessarily exist; equivalent to ``conda build --output recipename``
    but without the subprocess.
    """
    config = load_conda_build_config()
    # NB: Setting bypass_env_check disables ``pin_compatible`` parsing, which
    #     these days does not change the package build string, so should be fine.
    paths = api.get_output_file_paths(recipe, config=config, bypass_env_check=True)
    return paths


# Recipe patterns whose rendered hash depends on solver state (run_exports from
# e.g. sysroot_linux-64 inject __glibc into the variant during a real solve but
# not under bypass_env_check=True). When any of these appear we must finalize.
_SOLVER_DEPENDENT_JINJA = re.compile(r"\{\{\s*(stdlib|compiler|pin_compatible)\s*\(")


def recipe_requires_finalized_render(recipe):
    """
    Return True if the recipe's rendered hash can depend on solver state and
    therefore must be rendered with ``finalize=True`` to match what conda-build
    will produce during a real build.

    Detects use of ``stdlib(...)``, ``compiler(...)``, or ``pin_compatible(...)``
    jinja functions, whose run_exports are only applied during a real solve.
    See https://github.com/bioconda/bioconda-utils/issues/1095.
    """
    meta_path = os.path.join(recipe, "meta.yaml")
    try:
        with open(meta_path, encoding="utf-8") as f:
            text = re.sub(r"#.*", "", f.read())
    except OSError:
        return False
    return bool(_SOLVER_DEPENDENT_JINJA.search(text))


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


def _filter_existing_packages(metas, check_channels):
    new_metas = []  # MetaData instances of packages not yet in channel
    existing_metas = []  # MetaData instances of packages already in channel
    divergent_builds = set()  # set of Dist (i.e., name-version-build) strings

    key_build_meta = defaultdict(dict)
    for meta in metas:
        pkg_key = (meta.name(), meta.version(), int(meta.build_number() or 0))
        pkg_build = (_meta_subdir(meta), meta.build_id())
        key_build_meta[pkg_key][pkg_build] = meta

    r = RepoData()
    for pkg_key, build_meta in key_build_meta.items():
        target_subdirs = {pkg_build[0] for pkg_build in build_meta}
        queried_subdirs = sorted(target_subdirs | {"noarch"})
        existing_pkg_builds = set(
            r.get_package_data(
                ["subdir", "build"],
                name=pkg_key[0],
                version=pkg_key[1],
                build_number=pkg_key[2],
                channels=check_channels,
                platform=queried_subdirs,
            )
        )
        for pkg_build, meta in build_meta.items():
            if pkg_build not in existing_pkg_builds:
                new_metas.append(meta)
            else:
                existing_metas.append(meta)
        # A build for one target must not be treated as divergent merely because
        # another target has a different build string.
        target_pkg_builds = {
            x for x in existing_pkg_builds if x.subdir in target_subdirs | {"noarch"}
        }
        for divergent_build in target_pkg_builds - set(build_meta.keys()):
            divergent_builds.add("-".join((pkg_key[0], pkg_key[1], divergent_build[1])))
    return new_metas, existing_metas, divergent_builds


def get_package_paths(
    recipe, check_channels, force=False, finalize=True, target_platform=None
):
    if not force and check_recipe_skippable(
        recipe, check_channels, target_platform=target_platform
    ):
        # NB: If we skip early here, we don't detect possible divergent builds.
        return []
    if not finalize:
        logger.debug("Using non-finalized render for %s (fast resolve)", recipe)
    _, metas = _load_platform_metas(
        recipe, finalize=finalize, target_platform=target_platform
    )

    # The recipe likely defined skip: True
    if not metas:
        return []

    new_metas, existing_metas, divergent_builds = _filter_existing_packages(
        metas, check_channels
    )

    if divergent_builds:
        raise DivergentBuildsError(*sorted(divergent_builds))

    if force:
        for meta in existing_metas:
            logger.info(
                "FORCE: building %s although it is already in channel(s).",
                meta.pkg_fn(),
            )
        build_metas = new_metas + existing_metas
    else:
        for meta in existing_metas:
            logger.info(
                "FILTER: not building %s because it is in channel(s) and it is not forced.",
                meta.pkg_fn(),
            )
        # yield all pkgs that do not yet exist
        build_metas = new_metas
    return list(
        chain.from_iterable(api.get_output_file_paths(meta) for meta in build_metas)
    )
