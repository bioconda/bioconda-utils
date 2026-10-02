"""
Recipe discovery and build planning.

Finding recipe directories, querying their dependencies, and deciding
which package outputs a build would produce -- and whether it can be
skipped because they already exist in the target channels.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from itertools import chain
from pathlib import Path

from conda_build import api

from .._types import ContainerPlatform, container_platform_to_package_subdir
from .conda_build_bridge import load_all_meta, load_conda_build_config
from .repodata import RepoData

logger = logging.getLogger(__name__)


# TODO: change to Path only
def get_deps(recipe: Path | str, build=True):
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
    recipe = Path(recipe)
    metadata = load_all_meta(recipe, finalize=False)

    all_deps = set()
    for meta in metadata:
        if build:
            deps = meta.get_value("requirements/build", [])
        else:
            deps = meta.get_value("requirements/run", [])
        all_deps.update(dep.split()[0] for dep in deps)
    return all_deps


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


# TODO change to Path only
def recipe_requires_finalized_render(recipe: Path | str):
    """
    Return True if the recipe's rendered hash can depend on solver state and
    therefore must be rendered with ``finalize=True`` to match what conda-build
    will produce during a real build.

    Detects use of ``stdlib(...)``, ``compiler(...)``, or ``pin_compatible(...)``
    jinja functions, whose run_exports are only applied during a real solve.
    See https://github.com/bioconda/bioconda-utils/issues/1095.
    """
    meta_path: Path = Path(recipe) / "meta.yaml"
    try:
        with open(meta_path, encoding="utf-8") as f:
            text = re.sub(r"#.*", "", f.read())
    except OSError:
        return False
    return bool(_SOLVER_DEPENDENT_JINJA.search(text))


def _load_platform_metas(recipe: Path, finalize: bool = True, target_platform=None):
    if target_platform is not None:
        subdir = container_platform_to_package_subdir(target_platform)
    else:
        subdir = RepoData.native_subdir()
    config = load_conda_build_config(subdir=subdir)
    return subdir, load_all_meta(recipe, config=config, finalize=finalize)


def _meta_subdir(meta):
    # logic extracted from conda_build.variants.bldpkg_path
    return "noarch" if meta.noarch or meta.noarch_python else meta.config.host_subdir


def _meta_pkg_key(meta):
    """Package identity of a meta: (name, version, build_number)."""
    return (meta.name(), meta.version(), int(meta.build_number() or 0))


def _meta_build_key(meta):
    """Exact build a meta produces, as (name, version, build_number, subdir, build).

    Must stay in sync with the key built in :func:`_filter_existing_packages`,
    which has the package identity split off into ``pkg_key``.
    """
    return (*_meta_pkg_key(meta), _meta_subdir(meta), meta.build_id())


def check_recipe_skippable(
    recipe: Path, check_channels: list[str], target_platform=None
):
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
    existing_channels = set()
    num_existing_pkg_builds = Counter()
    for name, version, build_number in packages:
        for channel, subdir in r.get_package_data(
            ["channel", "subdir"],
            name=name,
            version=version,
            build_number=build_number,
            channels=check_channels,
            platform=queried_subdirs,
        ):
            existing_channels.add(channel)
            num_existing_pkg_builds[(name, version, build_number, subdir)] += 1
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
            "the same number of builds are in channel(s) [%s] and it is not forced.",
            recipe,
            ", ".join(sorted(existing_channels)),
        )
        return True
    return False


def _filter_existing_packages(metas, check_channels):
    new_metas = []  # MetaData instances of packages not yet in channel
    existing_metas = []  # MetaData instances of packages already in channel
    divergent_builds = set()  # set of Dist (i.e., name-version-build) strings
    # (name, version, build_number, subdir, build) -> channels containing it.
    # The package identity is part of the key: sibling outputs of one
    # multi-output recipe can share a (subdir, build) pair.
    pkg_build_channels = defaultdict(set)

    key_build_meta = defaultdict(dict)
    for meta in metas:
        pkg_key = _meta_pkg_key(meta)
        pkg_build = (_meta_subdir(meta), meta.build_id())
        key_build_meta[pkg_key][pkg_build] = meta

    r = RepoData()
    for pkg_key, build_meta in key_build_meta.items():
        target_subdirs = {pkg_build[0] for pkg_build in build_meta}
        queried_subdirs = sorted(target_subdirs | {"noarch"})
        existing_rows = list(
            r.get_package_data(
                ["channel", "subdir", "build"],
                name=pkg_key[0],
                version=pkg_key[1],
                build_number=pkg_key[2],
                channels=check_channels,
                platform=queried_subdirs,
            )
        )
        existing_pkg_builds = {(row.subdir, row.build) for row in existing_rows}
        for row in existing_rows:
            pkg_build_channels[(*pkg_key, row.subdir, row.build)].add(row.channel)
        for pkg_build, meta in build_meta.items():
            if pkg_build not in existing_pkg_builds:
                new_metas.append(meta)
            else:
                existing_metas.append(meta)
        # A build for one target must not be treated as divergent merely because
        # another target has a different build string.
        target_pkg_builds = {
            (x.subdir, x.build)
            for x in existing_rows
            if x.subdir in target_subdirs | {"noarch"}
        }
        for divergent_build in target_pkg_builds - set(build_meta.keys()):
            divergent_builds.add("-".join((pkg_key[0], pkg_key[1], divergent_build[1])))
    return new_metas, existing_metas, divergent_builds, pkg_build_channels


def get_package_paths(
    recipe: Path,
    check_channels: list[str],
    force: bool = False,
    finalize: bool = True,
    target_platform: ContainerPlatform | None = None,
) -> list[Path]:
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

    new_metas, existing_metas, divergent_builds, pkg_build_channels = (
        _filter_existing_packages(metas, check_channels)
    )

    if divergent_builds:
        raise DivergentBuildsError(*sorted(divergent_builds))

    if force:
        for meta in existing_metas:
            channels = sorted(pkg_build_channels.get(_meta_build_key(meta), set()))
            logger.info(
                "FORCE: building %s although it is already in channel(s) [%s].",
                meta.pkg_fn(),
                ", ".join(channels),
            )
        build_metas = new_metas + existing_metas
    else:
        for meta in existing_metas:
            channels = sorted(pkg_build_channels.get(_meta_build_key(meta), set()))
            logger.info(
                "FILTER: not building %s because it is in channel(s) [%s] "
                "and it is not forced.",
                meta.pkg_fn(),
                ", ".join(channels),
            )
        # yield all pkgs that do not yet exist
        build_metas = new_metas
    package_paths: list[str] = list(
        chain.from_iterable((api.get_output_file_paths(meta)) for meta in build_metas)
    )
    return [Path(p) for p in package_paths]
