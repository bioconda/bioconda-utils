"""
Bridge to conda-build.

All of bioconda-utils' entry points into ``conda_build.api`` (rendering
recipes into metadata, assembling conda-build configuration) live here,
together with the jinja environment used for lightweight meta.yaml
rendering.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import namedtuple
from importlib.resources import files
from itertools import chain
from pathlib import Path
from typing import Any, cast

# FIXME(upstream): For conda>=4.7.0 initialize_logging is (erroneously) called
#                  by conda.core.index.get_index which messes up our logging.
# => Prevent custom conda logging init before importing anything conda-related.
import conda.gateways.logging
import jinja2
from conda_build import api
from jinja2 import Environment
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .._types import OsLabel, PackageSubdir
from .repodata import RepoData

cast(Any, conda.gateways.logging).initialize_logging = lambda: None

logger = logging.getLogger(__name__)


class JinjaSilentUndefined(jinja2.Undefined):
    def _fail_with_undefined_error(self, *args, **kwargs):
        return ""

    __add__ = __radd__ = __mul__ = __rmul__ = __div__ = __rdiv__ = __truediv__ = (
        __rtruediv__
    ) = __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = __pos__ = __neg__ = (
        __call__
    ) = __getitem__ = __lt__ = __le__ = __gt__ = __ge__ = __int__ = __float__ = (
        __complex__
    ) = __pow__ = __rpow__ = _fail_with_undefined_error


jinja_silent_undef = Environment(undefined=JinjaSilentUndefined)


def load_all_meta(recipe, config=None, finalize=True):
    """
    For each environment, yield the rendered meta.yaml.

    Parameters
    ----------
    finalize : bool
        If True, do a full conda-build render. Determines exact package builds
        of build/host dependencies. It involves costly dependency resolution
        via conda and also download of those packages (to inspect possible
        run_exports). For fast-running tasks like linting, set to False.
    """
    if config is None:
        config = load_conda_build_config()
    # `bypass_env_check=True` prevents evaluating (=environment solving) the
    # package versions used for `pin_compatible` and the like.
    # To avoid adding a separate `bypass_env_check` alongside every `finalize`
    # parameter, just assume we do not want to bypass if `finalize is True`.
    metas = [
        meta
        for (meta, _, _) in api.render(
            recipe,
            config=config,
            finalize=False,
            bypass_env_check=True,
        )
    ]
    # Render again if we want the finalized version.
    # Rendering the non-finalized version beforehand lets us filter out
    # variants that get skipped. (E.g., with a global `numpy 1.16` pin for
    # py==27 the env check fails when evaluating `pin_compatible('numpy')` for
    # recipes that use a pinned `numpy` and also require `numpy>=1.17` but
    # actually skip py==27. Filtering out that variant beforehand avoids this.
    if finalize:
        metas = [
            meta
            for non_finalized_meta in metas
            for (meta, _, _) in api.render(
                recipe,
                config=config,
                variants=non_finalized_meta.config.variant,
                finalize=True,
                bypass_env_check=False,
            )
        ]
    return metas


def load_meta_fast(recipe: str, env=None):
    """
    Given a recipe path, find the current meta.yaml file, parse it, and return
    the dict.

    Args:
      recipe: Path to recipe (directory containing the meta.yaml file)
      env: Optional variables to expand

    Returns:
      Tuple of rendered dict and recipe path
    """
    if not env:
        env = {}

    try:
        pth = os.path.join(recipe, "meta.yaml")
        template = jinja_silent_undef.from_string(Path(pth).read_text(encoding="utf-8"))
        yaml_loader = YAML(typ="safe")
        yaml_loader.allow_duplicate_keys = True
        meta = yaml_loader.load(template.render(env))
        return (meta, recipe)
    except (OSError, jinja2.TemplateError, YAMLError) as exc:
        raise ValueError(f"Problem inspecting {recipe}") from exc


def subdir_to_oslabel(subdir: PackageSubdir) -> OsLabel:
    """Return the two-part OS label conda-build's ``config.platform`` expects.

    conda-build keys :data:`conda_build.variants.DEFAULT_COMPILERS` on the bare
    OS label (``linux``/``osx``) and joins it with the arch to form the build
    subdir. Passing a full subdir such as ``"linux-64"`` therefore raises
    ``KeyError`` at render time. This conversion deliberately discards the
    architecture; callers configuring a render target must pass the complete
    subdir to :func:`load_conda_build_config` instead.
    """
    return "linux" if subdir.startswith("linux") else "osx"


def _env_root() -> Path:
    """Return the conda prefix this installation lives in.

    ``conda-forge-pinning`` installs its ``conda_build_config.yaml`` directly
    into ``$PREFIX``, i.e. into the same environment that also provides the
    ``bioconda-utils`` entry point. The prefix of the running interpreter is
    therefore the right place to look, independent of ``PATH`` and of any
    environment that happens to be activated in the shell.
    """
    return Path(sys.prefix)


def load_conda_build_config(
    subdir: PackageSubdir | None = None, trim_skip: bool = True
):
    """
    Load conda build config while considering global pinnings from conda-forge.

    When ``subdir`` is supplied, configure conda-build to render as though it
    were running natively on that complete OS/architecture pair. This mirrors
    the environment used by architecture-specific Docker builds.
    """
    config_kwargs: dict[str, Any] = {"no_download_source": True, "set_build_id": False}
    if RepoData.config is not None:
        config_kwargs["channel_urls"] = tuple(RepoData.config["channels"])
    config = api.Config(**config_kwargs)

    pinnings = _env_root() / "conda_build_config.yaml"
    # set path to pinnings from conda forge package
    packaged_config = files("bioconda_utils") / "bioconda_utils-conda_build_config.yaml"
    config.exclusive_config_files = [
        str(pinnings),
        str(packaged_config),
    ]
    variant_config_files = getattr(config, "variant_config_files", None) or []
    for config_file in chain(config.exclusive_config_files, variant_config_files):
        if not os.path.exists(config_file):
            raise FileNotFoundError(
                f"conda-build configuration file does not exist: {config_file}\n"
                f"Expected the pinnings at {pinnings} (installed by the "
                "conda-forge-pinning package) and the packaged "
                "bioconda_utils-conda_build_config.yaml. Variant files passed via "
                "variant_config_files must exist as given."
            )
    if subdir is not None and config.subdir != subdir:
        os_label = subdir_to_oslabel(subdir)
        config.platform = os_label
        config.arch = subdir.removeprefix(f"{os_label}-")
    cast(Any, config).trim_skip = trim_skip
    return config


CondaBuildConfigFile = namedtuple(
    "CondaBuildConfigFile",
    (
        "arg",  # either '-e' or '-m'
        "path",
    ),
)


def get_conda_build_config_files(config=None):
    if config is None:
        config = load_conda_build_config()
    # TODO: open PR upstream for conda-build to support multiple exclusive_config_files
    for file_path in config.exclusive_config_files or []:
        yield CondaBuildConfigFile("-e", file_path)
    for file_path in config.variant_config_files or []:
        yield CondaBuildConfigFile("-m", file_path)


def load_first_metadata(recipe, config=None, finalize=True):
    """
    Returns just the first of possibly many metadata files. Used for when you
    need to do things like check a package name or version number (which are
    not expected to change between variants).

    If the recipe will be skipped, then returns None

    Parameters
    ----------
    finalize : bool
        If True, do a full conda-build render. Determines exact package builds
        of build/host dependencies. It involves costly dependency resolution
        via conda and also download of those packages (to inspect possible
        run_exports). For fast-running tasks like linting, set to False.
    """
    metas = load_all_meta(recipe, config, finalize=finalize)
    if len(metas) > 0:
        return metas[0]
