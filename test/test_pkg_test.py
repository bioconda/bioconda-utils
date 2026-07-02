import sys
from textwrap import dedent
import subprocess as sp

import pytest

from helpers import Recipes, ensure_missing
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


# Skip mulled_build_and_test on default since these tests call mulled-build directly.
@pytest.fixture
def build_pkg(request):
    """Build test packages and remove every registered output afterward."""
    registered_packages = set()

    def _build_pkg(recipe, mulled_build_and_test=False, docker_builder=None):
        r = Recipes(recipe, from_string=True)
        r.write_recipes()
        recipe_dir = r.recipe_dirs["one"]
        built_packages = utils.built_package_paths(recipe_dir)
        registered_packages.update(built_packages)
        for pkg in built_packages:
            ensure_missing(pkg)
        build.build(
            recipe=recipe_dir,
            pkg_paths=built_packages,
            mulled_build_and_test=mulled_build_and_test,
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
        pkg_test.build_and_test_mulled_image(pkg)


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test_mulled_build_error(build_pkg):
    """
    Make sure calling mulled-build with the wrong arg fails correctly.
    """
    built_packages = build_pkg(RECIPE_ONE)
    with pytest.raises(sp.CalledProcessError):
        for pkg in built_packages:
            pkg_test.build_and_test_mulled_image(pkg, mulled_args="--wrong-arg")


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test_custom_base_image(build_pkg):
    """
    Running a mulled-build test with a custom base image.
    """
    built_packages = build_pkg(RECIPE_CUSTOM_BASE)
    for pkg in built_packages:
        pkg_test.build_and_test_mulled_image(pkg, base_image="debian:latest")
