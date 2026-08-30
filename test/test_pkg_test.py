from pathlib import Path
import sys
from textwrap import dedent
import subprocess as sp

import pytest

from helpers import Recipes, ensure_missing, get_rattler_params
from bioconda_utils import pkg_test
from bioconda_utils import utils
from bioconda_utils import build

# TODO:
# need tests for channel order and extra channels (see
# https://github.com/bioconda/bioconda-utils/issues/31)
#

SKIP_OSX = sys.platform.startswith("darwin")

RECIPE_ONE = dedent("""
one:
  meta.yaml: |
    package:
      name: one
      version: 0.1
    test:
      commands:
        - "ls -la"
""")

RECIPE_CUSTOM_BASE = dedent("""
one:
  meta.yaml: |
    package:
      name: one
      version: 0.1
    test:
      commands:
        - "ls -la"
        - locale  # not present in default image
    extra:
      container:
        base: "debian:latest"
""")


@pytest.fixture
def build_pkg(request):
    """Build test packages and remove every registered output afterward."""
    registered_packages = set()

    def _build_pkg(recipe, mulled_test=False, docker_builder=None):
        r = Recipes(recipe, from_string=True)
        r.write_recipes()
        recipe_dir = r.recipe_dirs["one"]
        built_packages = utils.built_package_paths_conda_build(recipe_dir)
        registered_packages.update(built_packages)
        for pkg in built_packages:
            ensure_missing(pkg)

        recipe_path, global_variants, tool_config, render_config, rattler_output_dir = (
            get_rattler_params(Path(recipe_dir), "conda", docker_builder)
        )

        pkg_paths = [Path(p) for p in built_packages]

        build.build(
            recipe=recipe_path,
            global_variants=global_variants,
            tool_config=tool_config,
            render_config=render_config,
            rattler_output_dir=rattler_output_dir,
            pkg_paths=pkg_paths,
            mulled_test=mulled_test,
            docker_builder=docker_builder,
        )
        return built_packages

    def remove_built_packages():
        for pkg in registered_packages:
            ensure_missing(pkg)

    request.addfinalizer(remove_built_packages)
    return _build_pkg


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test(build_pkg):
    """
    Running a mulled-build test shouldn't cause any errors.
    """
    built_packages = build_pkg(RECIPE_ONE)
    for pkg in built_packages:
        pkg_test.test_package(pkg)


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test_mulled_build_error(build_pkg):
    """
    Make sure calling mulled-build with the wrong arg fails correctly.
    """
    built_packages = build_pkg(RECIPE_ONE)
    with pytest.raises(sp.CalledProcessError):
        for pkg in built_packages:
            pkg_test.test_package(pkg, mulled_args="--wrong-arg")


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test_custom_base_image(build_pkg):
    """
    Running a mulled-build test with a custom base image.
    """
    built_packages = build_pkg(RECIPE_CUSTOM_BASE)
    for pkg in built_packages:
        pkg_test.test_package(pkg, base_image="debian:latest")
