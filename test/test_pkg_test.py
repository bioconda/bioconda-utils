import sys
from textwrap import dedent
import subprocess as sp

import pytest

from helpers import Recipes, ensure_missing
from bioconda_utils import docker_utils, pkg_test
from bioconda_utils import utils
from bioconda_utils import build

from conda import __version__ as conda_version

# TODO:
# need tests for channel order and extra channels (see
# https://github.com/bioconda/bioconda-utils/issues/31)
#


SKIP_OSX = sys.platform.startswith('darwin')


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


# Skip mulled_test on default since we already run pkg_test.test_package for every test case.
def _build_pkg(recipe, mulled_test=False, docker_builder=None):
    r = Recipes(recipe, from_string=True)
    r.write_recipes()
    recipe = r.recipe_dirs['one']
    built_packages = utils.built_package_paths(recipe)
    for pkg in built_packages:
        ensure_missing(pkg)
    build.build(
        recipe=r.recipe_dirs['one'],
        pkg_paths=built_packages,
        mulled_test=mulled_test,
        docker_builder=docker_builder,
    )
    return built_packages


@pytest.mark.skipif(SKIP_OSX, reason='skipping on osx')
def test_pkg_test():
    """
    Running a mulled-build test shouldn't cause any errors.
    """
    built_packages = _build_pkg(RECIPE_ONE)
    for pkg in built_packages:
        pkg_test.test_package(pkg)


@pytest.mark.skipif(SKIP_OSX, reason='skipping on osx')
def test_pkg_test_mulled_build_error():
    """
    Make sure calling mulled-build with the wrong arg fails correctly.
    """
    built_packages = _build_pkg(RECIPE_ONE)
    with pytest.raises(sp.CalledProcessError):
        for pkg in built_packages:
            pkg_test.test_package(pkg, mulled_args='--wrong-arg')


@pytest.mark.skipif(SKIP_OSX, reason='skipping on osx')
def test_pkg_test_custom_base_image():
    """
    Running a mulled-build test with a custom base image.
    """
    built_packages = _build_pkg(RECIPE_CUSTOM_BASE)
    for pkg in built_packages:
        pkg_test.test_package(pkg, base_image='debian:latest')
