import contextlib
import datetime
import importlib
import logging
import os
import re
import shutil
import subprocess as sp
import sys
import tempfile
import uuid
from collections import namedtuple
from pathlib import Path
from textwrap import dedent
from unittest.mock import Mock

import pandas as pd
import pytest
from conda_build import api, exceptions, metadata
from helpers import Recipes, ensure_missing
from jsonschema import ValidationError

from bioconda_utils import (
    __version__,
    build,
    docker_utils,
    pkg_test,
    upload,
    utils,
)
from bioconda_utils._types import Config, ContainerPlatform, PackageSubdir
from bioconda_utils.utils import validate_config

logger = logging.getLogger(__name__)

# TODO: need channel order tests. Could probably do this by adding different
# file:// channels with different variants of the same package

# Label that will be used for uploading test packages to anaconda/binstar
TEST_LABEL = "bioconda-utils-test"

# PARAMS and ID are used with pytest.fixture. The end result is that, on Linux,
# any tests that depend on a fixture that uses PARAMS will run twice (once with
# docker, once without). On OSX, only the non-docker runs.

# Docker ref for build container
BUILD_ENV_IMAGE = os.getenv(
    "BUILD_ENV_IMAGE", "quay.io/bioconda/bioconda-utils-test-env-cos7:latest"
)

SKIP_DOCKER_TESTS = sys.platform.startswith("darwin")
SKIP_NOT_OSX = not sys.platform.startswith("darwin")

if SKIP_DOCKER_TESTS:
    PARAMS = [False]
    IDS = ["system conda"]
else:
    PARAMS = [True, False]
    IDS = ["with docker", "system conda"]


@contextlib.contextmanager
def ensure_env_missing(env_name):
    """
    context manager that makes sure a conda env of a particular name does not
    exist, deleting it if needed.
    """

    def _clean():
        proc = sp.run(
            ["conda", "env", "list"],
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            check=True,
            text=True,
        )

        if env_name in proc.stdout:
            sp.run(
                ["conda", "env", "remove", "-y", "-n", env_name],
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                check=True,
                text=True,
            )

    _clean()
    try:
        yield
    finally:
        _clean()


# ----------------------------------------------------------------------------
# FIXTURES
#
@pytest.fixture(scope="module")
def recipes_fixture():
    """
    Writes example recipes (based on test_case.yaml), figures out the package
    paths and attaches them to the Recipes instance, and cleans up afterward.
    """
    rcp = Recipes("test_case.yaml")
    rcp.write_recipes()
    rcp.pkgs = {}
    for key, val in rcp.recipe_dirs.items():
        rcp.pkgs[key] = utils.built_package_paths(val)
    yield rcp
    for pkgs in rcp.pkgs.values():
        for pkg in pkgs:
            ensure_missing(pkg)


@pytest.fixture(scope="module")
def config_fixture():
    """Loads config"""
    config = utils.load_config(
        Path(os.path.join(os.path.dirname(__file__), "test-config.yaml"))
    )
    yield config


@pytest.fixture(scope="function", params=PARAMS, ids=IDS)
def single_build(request, recipes_fixture):
    """
    Builds the "one" recipe.
    """
    if request.param:
        logger.error("Making recipe builder")
        docker_builder = docker_utils.RecipeBuilder(
            use_host_conda_bld=True, docker_base_image=BUILD_ENV_IMAGE
        )
        mulled_build_and_test = True
        logger.error("DONE")
    else:
        docker_builder = None
        mulled_build_and_test = False
    logger.error(
        "Fixture: Building 'one' %s",
        "within docker" if docker_builder else "locally",
    )
    build.build(
        recipe=recipes_fixture.recipe_dirs["one"],
        pkg_paths=recipes_fixture.pkgs["one"],
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
    )
    logger.error(
        "Fixture: Building 'one' %s -- DONE",
        "within docker" if docker_builder else "locally",
    )
    yield recipes_fixture.pkgs["one"]
    for pkg in recipes_fixture.pkgs["one"]:
        ensure_missing(pkg)


@pytest.fixture(scope="module", params=PARAMS, ids=IDS)
def multi_build(request, recipes_fixture, config_fixture):
    """
    Builds the "one", "two", and "three" recipes.
    """
    if request.param:
        docker_builder = docker_utils.RecipeBuilder(
            use_host_conda_bld=True, docker_base_image=BUILD_ENV_IMAGE
        )
        mulled_build_and_test = True
    else:
        docker_builder = None
        mulled_build_and_test = False
    logger.error(
        "Fixture: Building one/two/three %s",
        "within docker" if docker_builder else "locally",
    )
    build.build_recipes(
        recipes_fixture.basedir,
        config_fixture,
        recipes_fixture.recipe_dirnames,
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
    )
    logger.error(
        "Fixture: Building one/two/three %s -- DONE",
        "within docker" if docker_builder else "locally",
    )
    built_packages = recipes_fixture.pkgs
    yield built_packages
    for pkgs in built_packages.values():
        for pkg in pkgs:
            ensure_missing(pkg)


@pytest.fixture(scope="module", params=PARAMS, ids=IDS)
def multi_build_exclude(request, recipes_fixture, config_fixture):
    """
    Builds the "one" and "two" recipes; provides (but then excludes) the
    "three" recipe.
    """
    if request.param:
        docker_builder = docker_utils.RecipeBuilder(
            use_host_conda_bld=True, docker_base_image=BUILD_ENV_IMAGE
        )
        mulled_build_and_test = True
    else:
        docker_builder = None
        mulled_build_and_test = False
    logger.error(
        "Fixture: Building one/two (and not three) %s",
        "within docker" if docker_builder else "locally",
    )
    build.build_recipes(
        recipes_fixture.basedir,
        config_fixture,
        recipes_fixture.recipe_dirnames,
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
        exclude=["three"],
    )
    logger.error(
        "Fixture: Building one/two (and not three) %s -- DONE",
        "within docker" if docker_builder else "locally",
    )
    built_packages = recipes_fixture.pkgs
    yield built_packages
    for pkgs in built_packages.values():
        for pkg in pkgs:
            ensure_missing(pkg)


@pytest.fixture(scope="module")
def single_upload(request):
    """
    Creates a randomly-named recipe and uploads it using a label so that it
    doesn't affect the main bioconda channel. Tests that depend on this fixture
    get a tuple of name, pakage, recipe dir. Cleans up when it's done.
    """
    name = "upload-test-" + str(uuid.uuid4()).split("-")[0]
    r = Recipes(
        f"""
        {name}:
          meta.yaml: |
            package:
              name: {name}
              version: "0.1"
        """,
        from_string=True,
    )
    r.write_recipes()
    r.pkgs = {}
    r.pkgs[name] = utils.built_package_paths(r.recipe_dirs[name])

    pkg = r.pkgs[name][0]
    ensure_missing(pkg)
    request.addfinalizer(lambda: ensure_missing(pkg))

    build_result = build.build(
        recipe=Path(r.recipe_dirs[name]),
        pkg_paths=r.pkgs[name],
        docker_builder=None,
        mulled_build_and_test=False,
    )
    assert build_result.success

    assert upload.anaconda_upload(pkg, label=TEST_LABEL)

    yield (name, pkg, r.recipe_dirs[name])

    sp.run(
        [
            "anaconda",
            "-t",
            os.environ["ANACONDA_TOKEN"],
            "remove",
            f"bioconda/{name}",
            "--force",
        ],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        check=True,
        text=True,
    )


# ----------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANACONDA_TOKEN"), reason="No ANACONDA_TOKEN found"
)
def test_upload(single_upload):
    name, _pkg, _recipe = single_upload
    env_name = "bioconda-utils-test-" + str(uuid.uuid4()).split("-")[0]
    with ensure_env_missing(env_name):
        sp.run(
            [
                "conda",
                "create",
                "-n",
                env_name,
                "-c",
                f"bioconda/label/{TEST_LABEL}",
                name,
            ],
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            check=True,
            text=True,
        )


@pytest.mark.long_running_2
def test_single_build_only(single_build):
    for pkg in single_build:
        assert os.path.exists(pkg)
        ensure_missing(pkg)


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
@pytest.mark.long_running_2
def test_single_build_pkg_dir(recipes_fixture):
    """
    Builds the "one" recipe with pkg_dir.
    """
    logger.error("Making recipe builder")
    docker_builder = docker_utils.RecipeBuilder(
        use_host_conda_bld=True,
        pkg_dir=os.getcwd() + "/output",
        docker_base_image=BUILD_ENV_IMAGE,
    )
    mulled_build_and_test = False
    logger.error("DONE")
    logger.error("Fixture: Building 'one' within docker with pkg_dir")
    res = build.build(
        recipe=recipes_fixture.recipe_dirs["one"],
        pkg_paths=recipes_fixture.pkgs["one"],
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
    )
    logger.error("Fixture: Building 'one' within docker and pkg_dir -- DONE")
    assert res.success


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_single_build_with_post_test(single_build):
    for pkg in single_build:
        pkg_test.build_and_test_mulled_image(pkg)


@pytest.mark.long_running_1
def test_multi_build(multi_build):
    for v in multi_build.values():
        for pkg in v:
            assert os.path.exists(pkg)
            ensure_missing(pkg)


@pytest.mark.long_running_1
def test_multi_build_exclude(multi_build_exclude):
    for k, v in multi_build_exclude.items():
        for pkg in v:
            if k == "three":
                assert not os.path.exists(pkg)
            else:
                assert os.path.exists(pkg)
                ensure_missing(pkg)


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_docker_bioconda_utils_version():
    """
    Test for same bioconda-utils version in build container.
    """
    docker_builder = docker_utils.RecipeBuilder(
        build_script_template=(
            """
#! /usr/bin/env bash
python -c '
import bioconda_utils
with open("{self.container_staging}/version", "w") as version_file:
    version_file.write(bioconda_utils.__version__)
'
"""
        ),
        docker_base_image=BUILD_ENV_IMAGE,
    )
    temp_dir = docker_builder.pkg_dir
    # Set recipe_dir to any temporary directory, e.g., docker_builder.pkg_dir.
    docker_builder.build_recipe(temp_dir, build_args="", env={})
    with open(os.path.join(temp_dir, "version")) as container_version_file:
        assert container_version_file.read() == __version__


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_docker_builder_build(recipes_fixture):
    """
    Tests just the build_recipe method of a RecipeBuilder object.
    """
    docker_builder = docker_utils.RecipeBuilder(
        use_host_conda_bld=True, docker_base_image=BUILD_ENV_IMAGE
    )
    pkgs = recipes_fixture.pkgs["one"]
    docker_builder.build_recipe(
        recipes_fixture.recipe_dirs["one"], build_args="", env={}
    )
    for pkg in pkgs:
        assert os.path.exists(pkg)
        ensure_missing(pkg)


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_docker_build_fails(recipes_fixture, config_fixture):
    """
    Test for expected failure when a recipe fails to build
    """
    docker_builder = docker_utils.RecipeBuilder(
        docker_base_image=BUILD_ENV_IMAGE, build_script_template="exit 1"
    )
    assert docker_builder.build_script_template == "exit 1"
    result = build.build_recipes(
        recipes_fixture.basedir,
        config_fixture,
        recipes_fixture.recipe_dirnames,
        docker_builder=docker_builder,
        mulled_build_and_test=True,
    )
    assert not result


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_docker_build_image_fails():
    template = f"""
        FROM {BUILD_ENV_IMAGE}
        RUN nonexistent command
        """
    with pytest.raises(sp.CalledProcessError):
        docker_utils.RecipeBuilder(dockerfile_template=template, build_image=True)


def test_get_deps():
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
        two:
          meta.yaml: |
            package:
              name: two
              version: 0.1
            requirements:
              build:
                - one
        three:
          meta.yaml: |
            package:
              name: three
              version: 0.1
            requirements:
              build:
                - one
              run:
                - two
    """,
        from_string=True,
    )
    r.write_recipes()
    assert list(utils.get_deps(r.recipe_dirs["two"])) == ["one"]
    assert list(utils.get_deps(r.recipe_dirs["three"], build=True)) == ["one"]
    assert list(utils.get_deps(r.recipe_dirs["three"], build=False)) == ["two"]


@pytest.mark.long_running_1
@pytest.mark.parametrize("mulled_build_and_test", PARAMS, ids=IDS)
def test_conda_as_dep(config_fixture, mulled_build_and_test):
    docker_builder = None
    if mulled_build_and_test:
        docker_builder = docker_utils.RecipeBuilder(
            use_host_conda_bld=True,
            docker_base_image=BUILD_ENV_IMAGE,
        )
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: bioconda_utils_test_conda_as_dep
              version: 0.1
            requirements:
              host:
                - conda
              run:
                - conda
            test:
              commands:
                - test -e "${PREFIX}/bin/conda"
        """,
        from_string=True,
    )
    r.write_recipes()
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
    )
    assert build_result

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


def test_recipe_requires_finalized_render(tmp_path):
    def _write(meta):
        recipe = tmp_path / uuid.uuid4().hex
        recipe.mkdir()
        (recipe / "meta.yaml").write_text(meta)
        return str(recipe)

    assert not utils.recipe_requires_finalized_render(
        _write("package:\n  name: pure-python\n  version: '1.0'\n")
    )
    assert utils.recipe_requires_finalized_render(
        _write("requirements:\n  build:\n    - {{ stdlib('c') }}\n")
    )
    assert utils.recipe_requires_finalized_render(
        _write("requirements:\n  build:\n    - {{ compiler('c') }}\n")
    )
    assert utils.recipe_requires_finalized_render(
        _write("requirements:\n  run:\n    - {{ pin_compatible('foo') }}\n")
    )
    # ignore if within comments
    assert not utils.recipe_requires_finalized_render(
        _write("requirements:\n  run:\n    - foo  # {{ pin_compatible('foo') }}\n")
    )
    # Missing meta.yaml -> False (don't crash)
    missing = tmp_path / "missing"
    missing.mkdir()
    assert not utils.recipe_requires_finalized_render(str(missing))


# TODO replace the filter tests with tests for utils.get_package_paths()
# def test_filter_recipes_no_skipping():
#     """
#     No recipes have skip so make sure none are filtered out.
#     """
#     r = Recipes(
#         """
#         one:
#           meta.yaml: |
#             package:
#               name: one
#               version: "0.1"
#         """, from_string=True)
#     r.write_recipes()
#     recipes = list(r.recipe_dirs.values())
#     assert len(recipes) == 1
#     filtered = list(
#         utils.filter_recipes(recipes, channels=['bioconda']))
#     assert len(filtered) == 1
#
#
# def test_filter_recipes_skip_is_true():
#     r = Recipes(
#         """
#         one:
#           meta.yaml: |
#             package:
#               name: one
#               version: "0.1"
#             build:
#               skip: true
#         """, from_string=True)
#     r.write_recipes()
#     recipes = list(r.recipe_dirs.values())
#     filtered = list(
#         utils.filter_recipes(recipes))
#     print(filtered)
#     assert len(filtered) == 0
#
#
# def test_filter_recipes_skip_is_true_with_CI_env_var():
#     """
#     utils.filter_recipes has a conditional that checks to see if there's
#     a CI=true env var which in some cases only causes failure when running on
#     CI. So temporarily fake it here so that local tests catch errors.
#     """
#     with utils.temp_env(dict(CI="true")):
#         r = Recipes(
#             """
#             one:
#               meta.yaml: |
#                 package:
#                   name: one
#                   version: "0.1"
#                 build:
#                   skip: true
#             """, from_string=True)
#         r.write_recipes()
#         recipes = list(r.recipe_dirs.values())
#         filtered = list(
#             utils.filter_recipes(recipes))
#         print(filtered)
#         assert len(filtered) == 0
#
#
# def test_filter_recipes_skip_not_py27():
#     """
#     When all but one Python version is skipped, filtering should do that.
#     """
#
#     r = Recipes(
#         """
#         one:
#           meta.yaml: |
#             package:
#               name: one
#               version: "0.1"
#             build:
#               skip: True # [not py27]
#             requirements:
#               build:
#                 - python
#               run:
#                 - python
#         """, from_string=True)
#     r.write_recipes()
#     recipes = list(r.recipe_dirs.values())
#     filtered = list(
#         utils.filter_recipes(recipes, channels=['bioconda']))
#
#     # one recipe, one target
#     assert len(filtered) == 1
#     assert len(filtered[0][1]) == 1
#
#
# def test_filter_recipes_existing_package():
#     "use a known-to-exist package in bioconda"
#
#     # note that we need python as a run requirement in order to get the "pyXY"
#     # in the build string that matches the existing bioconda built package.
#     r = Recipes(
#         """
#         one:
#           meta.yaml: |
#             package:
#               name: gffutils
#               version: "0.8.7.1"
#             requirements:
#               build:
#                 - python
#               run:
#                 - python
#         """, from_string=True)
#     r.write_recipes()
#     recipes = list(r.recipe_dirs.values())
#     filtered = list(
#         utils.filter_recipes(recipes, channels=['bioconda']))
#     assert len(filtered) == 0
#
#
# def test_filter_recipes_force_existing_package():
#     "same as above but force the recipe"
#
#     # same as above, but this time force the recipe
#     # TODO: refactor as py.test fixture
#     r = Recipes(
#         """
#         one:
#           meta.yaml: |
#             package:
#               name: gffutils
#               version: "0.8.7.1"
#             requirements:
#               run:
#                 - python
#         """, from_string=True)
#     r.write_recipes()
#     recipes = list(r.recipe_dirs.values())
#     filtered = list(
#         utils.filter_recipes(
#             recipes, channels=['bioconda'], force=True))
#     assert len(filtered) == 1
#
#
# def test_zero_packages():
#     """
#     Regression test; make sure filter_recipes exits cleanly if no recipes were
#     provided.
#     """
#     assert list(utils.filter_recipes([])) == []


def test_built_package_paths():
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"
            requirements:
              build:
                - python 3.6
              run:
                - python 3.6

        two:
          meta.yaml: |
            package:
              name: two
              version: "0.1"
            build:
              number: 0
              string: ncurses{{ CONDA_NCURSES }}_{{ PKG_BUILDNUM }}
        """,
        from_string=True,
    )
    r.write_recipes()

    # Newer conda-build versions add the channel_targets and target_platform to the hash
    platform = "linux" if sys.platform == "linux" else "osx"
    d = {
        "channel_targets": "bioconda main",
        "target_platform": f"{platform}-64",
    }
    h = metadata._hash_dependencies(d, 7)

    assert (
        os.path.basename(utils.built_package_paths(r.recipe_dirs["one"])[0])
        == f"one-0.1-py36{h}_0.conda"
    )


def test_string_or_float_to_integer_python():
    f = utils._string_or_float_to_integer_python
    assert f(27) == f("27") == f(2.7) == f("2.7") == 27


def test_rendering_sandboxing():
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
            extra:
              var: {{ GITHUB_TOKEN }}
    """,
        from_string=True,
    )

    r.write_recipes()
    # env = {
    #     # None of these should be passed to the recipe
    #     "CONDA_ARBITRARY_VAR": "conda-val-here",
    #     "GITHUB_TOKEN": "asdf",
    #     "BUILDKITE_TOKEN": "asdf",
    # }

    # If GITHUB_TOKEN is already set in the bash environment, then we get
    # a message on stdout+stderr (this is the case in GitHub Actions).
    #
    # However if GITHUB_TOKEN is not already set in the bash env (e.g., when
    # testing locally), then we get a SystemError.
    #
    # In both cases we're passing in the `env` dict, which does contain
    # GITHUB_TOKEN.

    if "GITHUB_TOKEN" in os.environ:
        with pytest.raises(sp.CalledProcessError) as excinfo:
            pkg_paths = utils.built_package_paths(r.recipe_dirs["one"])
            build.build(
                recipe=Path(r.recipe_dirs["one"]),
                pkg_paths=pkg_paths,
                mulled_build_and_test=False,
                raise_error=True,
            )
        assert "'GITHUB_TOKEN' is undefined" in str(excinfo.value.stdout)
    else:
        # recipe for "one" should fail because GITHUB_TOKEN is not a jinja var.
        with pytest.raises(exceptions.CondaBuildUserError) as excinfo:
            pkg_paths = utils.built_package_paths(r.recipe_dirs["one"])
            build.build(
                recipe=Path(r.recipe_dirs["one"]),
                pkg_paths=pkg_paths,
                mulled_build_and_test=False,
            )
        assert "'GITHUB_TOKEN' is undefined" in str(excinfo.value)


def test_sandboxed():
    env = {
        "PATH": "/foo/bar",
        "CONDA_ARBITRARY_VAR": "conda-val-here",
        "GITHUB_TOKEN": "asdf",
        "BUILDKITE_TOKEN": "asdf",
    }
    with utils.sandboxed_env(env):
        print(os.environ)
        assert os.environ["PATH"] == "/foo/bar"
        assert "CONDA_ARBITRARY_VAR" not in os.environ
        assert "GITHUB_TOKEN" not in os.environ
        assert "BUILDKITE_TOKEN" not in os.environ


def test_env_sandboxing():
    r = Recipes(
        r"""
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
          build.sh: |
            #!/bin/bash
            if [[ -z $GITHUB_TOKEN ]]
            then
                exit 0
            else
                echo "\$GITHUB_TOKEN has leaked into the build environment!"
                exit 1
            fi
        """,
        from_string=True,
    )
    r.write_recipes()
    pkg_paths = utils.built_package_paths(r.recipe_dirs["one"])

    with utils.temp_env({"GITHUB_TOKEN": "token_here"}):
        build.build(
            recipe=Path(r.recipe_dirs["one"]),
            pkg_paths=pkg_paths,
            mulled_build_and_test=False,
        )

    for pkg in pkg_paths:
        assert os.path.exists(pkg)
        ensure_missing(pkg)


def test_skip_dependencies(config_fixture):
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: skip_dependencies_one
              version: 0.1
        two:
          meta.yaml: |
            package:
              name: skip_dependencies_two
              version: 0.1
            requirements:
              build:
                - skip_dependencies_one
                - nonexistent
        three:
          meta.yaml: |
            package:
              name: skip_dependencies_three
              version: 0.1
            requirements:
              build:
                - skip_dependencies_one
              run:
                - skip_dependencies_two
    """,
        from_string=True,
    )
    r.write_recipes()
    pkgs = {}
    for k, v in r.recipe_dirs.items():
        pkgs[k] = utils.built_package_paths(v)

    for _pkgs in pkgs.values():
        for pkg in _pkgs:
            ensure_missing(pkg)

    build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    for pkg in pkgs["one"]:
        assert os.path.exists(pkg)
    for pkg in pkgs["two"]:
        assert not os.path.exists(pkg)
    for pkg in pkgs["three"]:
        assert not os.path.exists(pkg)

    # clean up
    for _pkgs in pkgs.values():
        for pkg in _pkgs:
            ensure_missing(pkg)


class TestSubdags:
    def _build(self, recipes_fixture, config_fixture, n_workers, worker_offset):
        build.build_recipes(
            recipes_fixture.basedir,
            config_fixture,
            recipes_fixture.recipe_dirnames,
            n_workers=n_workers,
            worker_offset=worker_offset,
            mulled_build_and_test=False,
        )

    def test_subdags_out_of_range(self, recipes_fixture, config_fixture):
        with pytest.raises(ValueError):
            self._build(recipes_fixture, config_fixture, 2, 4)


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
def test_build_empty_extra_container():
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
            extra:
              container:
                # empty
        """,
        from_string=True,
    )
    r.write_recipes()
    pkgs = utils.built_package_paths(r.recipe_dirs["one"])

    build_result = build.build(
        recipe=Path(r.recipe_dirs["one"]),
        pkg_paths=pkgs,
        mulled_build_and_test=True,
    )
    assert build_result.success
    for pkg in pkgs:
        assert os.path.exists(pkg)
        ensure_missing(pkg)


@pytest.mark.skipif(SKIP_DOCKER_TESTS, reason="skipping on osx")
@pytest.mark.long_running_1
@pytest.mark.xfail
def test_build_container_no_default_gcc(tmpdir):
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
            test:
              commands:
                - gcc --version
        """,
        from_string=True,
    )
    r.write_recipes()

    # Tests with the repository's Dockerfile instead of already uploaded images.
    # Copy repository to image build directory so everything is in docker context.
    image_build_dir = os.path.join(tmpdir, "repo")
    src_repo_dir = os.path.join(os.path.dirname(__file__), "..")
    shutil.copytree(src_repo_dir, image_build_dir)
    # Dockerfile will be recreated by RecipeBuilder => extract template and delete file
    dockerfile = os.path.join(image_build_dir, "Dockerfile")
    with open(dockerfile) as f:
        dockerfile_template = f.read().replace("{", "{{").replace("}", "}}")
    os.remove(dockerfile)

    docker_builder = docker_utils.RecipeBuilder(
        dockerfile_template=dockerfile_template,
        use_host_conda_bld=True,
        image_build_dir=image_build_dir,
    )

    pkg_paths = utils.built_package_paths(r.recipe_dirs["one"])
    build_result = build.build(
        recipe=Path(r.recipe_dirs["one"]),
        pkg_paths=pkg_paths,
        docker_builder=docker_builder,
        mulled_build_and_test=False,
    )
    assert build_result.success

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


def test_bioconda_pins(caplog, config_fixture):
    """
    htslib currently only provided by bioconda pinnings
    """
    caplog.set_level(logging.DEBUG)
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
            requirements:
              run:
                - htslib
        """,
        from_string=True,
    )
    r.write_recipes()
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_result

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


def test_load_meta_skipping():
    """
    Ensure that a skipped recipe returns no metadata
    """
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"
            build:
              skip: true
        """,
        from_string=True,
    )
    r.write_recipes()
    recipe = r.recipe_dirs["one"]
    assert utils.load_all_meta(recipe) == []


@pytest.mark.parametrize(
    ("target_platform", "expected_subdir"),
    [
        (ContainerPlatform.LINUX_AMD64, PackageSubdir.LINUX_64),
        (ContainerPlatform.LINUX_ARM64, PackageSubdir.LINUX_AARCH64),
        (ContainerPlatform.LINUX_RISCV64, PackageSubdir.LINUX_RISCV64),
    ],
)
def test_load_platform_metas_preserves_complete_target_platform(
    target_platform, expected_subdir
):
    recipes = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"
        """,
        from_string=True,
    )
    recipes.write_recipes()

    subdir, metas = utils._load_platform_metas(
        recipes.recipe_dirs["one"],
        finalize=False,
        target_platform=target_platform,
    )

    assert subdir == expected_subdir
    assert len(metas) == 1
    meta = metas[0]
    assert meta.config.build_subdir == expected_subdir
    assert meta.config.host_subdir == expected_subdir
    assert meta.config.target_subdir == expected_subdir
    assert meta.config.variant["target_platform"] == expected_subdir
    assert Path(api.get_output_file_paths(meta)[0]).parent.name == expected_subdir


def test_repodata_loads_and_reuses_only_requested_repositories(monkeypatch):
    monkeypatch.setattr(
        utils.RepoData, "config", {"channels": ["bioconda", "conda-forge"]}
    )
    repodata = utils.RepoData()
    monkeypatch.setattr(repodata, "_df", None)
    monkeypatch.setattr(repodata, "cache_file", None)
    monkeypatch.setattr(repodata, "_repository_cache", {})
    monkeypatch.setattr(repodata, "platforms", [PackageSubdir.LINUX_AARCH64, "noarch"])
    loaded_repositories = []

    def load(repositories=None):
        assert repositories is not None
        selected = tuple(repositories)
        loaded_repositories.append(selected)
        return pd.DataFrame(
            [
                {
                    "name": f"package-{channel}-{subdir}",
                    "version": "1",
                    "build": "0",
                    "build_number": 0,
                    "depends": [],
                    "channel": channel,
                    "platform": subdir,
                    "subdir": subdir,
                }
                for channel, subdir in selected
            ],
            columns=utils.RepoData.columns,
        )

    monkeypatch.setattr(repodata, "_load_channel_dataframe", load)

    assert set(
        repodata.get_package_data(
            "name",
            channels="bioconda",
            platform=[PackageSubdir.LINUX_AARCH64, "noarch"],
        )
    ) == {
        "package-bioconda-linux-aarch64",
        "package-bioconda-noarch",
    }
    assert set(
        repodata.get_package_data(
            "name",
            channels=["bioconda", "conda-forge"],
            platform=[PackageSubdir.LINUX_AARCH64],
        )
    ) == {
        "package-bioconda-linux-aarch64",
        "package-conda-forge-linux-aarch64",
    }
    assert not repodata.get_package_data(
        channels="unconfigured", platform=PackageSubdir.LINUX_AARCH64
    )
    assert set(repodata.df["name"]) == {
        "package-bioconda-linux-aarch64",
        "package-bioconda-noarch",
        "package-conda-forge-linux-aarch64",
        "package-conda-forge-noarch",
    }
    assert repodata._repository_cache == {}

    assert loaded_repositories == [
        (
            ("bioconda", PackageSubdir.LINUX_AARCH64),
            ("bioconda", "noarch"),
        ),
        (("conda-forge", PackageSubdir.LINUX_AARCH64),),
        (("conda-forge", "noarch"),),
    ]


def _repodata_dataframe(name):
    return pd.DataFrame(
        [
            {
                "name": name,
                "version": "1",
                "build": "0",
                "build_number": 0,
                "depends": [],
                "channel": "bioconda",
                "platform": PackageSubdir.LINUX_AARCH64,
                "subdir": PackageSubdir.LINUX_AARCH64,
            }
        ],
        columns=utils.RepoData.columns,
    )


def test_repodata_refreshes_expired_repository(monkeypatch):
    monkeypatch.setattr(utils.RepoData, "config", {"channels": ["bioconda"]})
    repodata = utils.RepoData()
    repository = ("bioconda", PackageSubdir.LINUX_AARCH64)
    expired_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=30)
    monkeypatch.setattr(repodata, "_df", None)
    monkeypatch.setattr(repodata, "cache_file", None)
    monkeypatch.setattr(
        repodata,
        "_repository_cache",
        {repository: utils._CachedRepoData(_repodata_dataframe("old"), expired_at)},
    )
    loaded_repositories = []

    def load(repositories=None):
        loaded_repositories.append(tuple(repositories or ()))
        return _repodata_dataframe("fresh")

    monkeypatch.setattr(repodata, "_load_channel_dataframe", load)

    assert repodata.get_package_data(
        "name",
        channels="bioconda",
        platform=PackageSubdir.LINUX_AARCH64,
    ) == ["fresh"]
    assert loaded_repositories == [(repository,)]


def test_repodata_refreshes_full_dataframe_older_than_one_day(monkeypatch):
    monkeypatch.setattr(utils.RepoData, "config", {"channels": ["bioconda"]})
    repodata = utils.RepoData()
    monkeypatch.setattr(repodata, "_df", _repodata_dataframe("old"))
    monkeypatch.setattr(
        repodata,
        "_df_ts",
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=30),
    )
    monkeypatch.setattr(repodata, "cache_file", None)
    monkeypatch.setattr(repodata, "_repository_cache", {})
    monkeypatch.setattr(repodata, "platforms", [PackageSubdir.LINUX_AARCH64])
    monkeypatch.setattr(
        repodata,
        "_load_channel_dataframe",
        lambda _repositories=None: _repodata_dataframe("fresh"),
    )

    assert list(repodata.df["name"]) == ["fresh"]


def test_repodata_refreshes_disk_cache_older_than_one_day(monkeypatch, tmp_path):
    monkeypatch.setattr(utils.RepoData, "config", {"channels": ["bioconda"]})
    cache_file = tmp_path / "repodata.pkl"
    _repodata_dataframe("old").to_pickle(cache_file)
    expired_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=30)
    os.utime(cache_file, (expired_at.timestamp(), expired_at.timestamp()))

    repodata = utils.RepoData()
    monkeypatch.setattr(repodata, "_df", None)
    monkeypatch.setattr(repodata, "_df_ts", None)
    monkeypatch.setattr(repodata, "cache_file", str(cache_file))
    monkeypatch.setattr(repodata, "_repository_cache", {})
    monkeypatch.setattr(
        repodata,
        "_load_channel_dataframe",
        lambda _repositories=None: _repodata_dataframe("fresh"),
    )

    assert list(repodata.df["name"]) == ["fresh"]


def test_filter_existing_packages_queries_rendered_target_subdir(monkeypatch):
    meta = Mock()
    meta.name.return_value = "samtools"
    meta.version.return_value = "1.24"
    meta.build_number.return_value = 1
    meta.build_id.return_value = "h391949c_1"
    meta.noarch = False
    meta.noarch_python = False
    meta.config.host_subdir = PackageSubdir.LINUX_AARCH64
    queried_platforms = []

    def get_package_data(_self, _keys, **kwargs):
        queried_platforms.append(kwargs["platform"])
        return []

    monkeypatch.setattr(utils.RepoData, "get_package_data", get_package_data)

    assert utils._filter_existing_packages([meta], ["bioconda"]) == ([meta], [], set())
    assert queried_platforms == [[PackageSubdir.LINUX_AARCH64, "noarch"]]


def test_get_package_paths_force_builds_existing_and_logs_force(caplog, monkeypatch):
    # get_package_data yields pandas itertuples rows for ["subdir", "build"]
    ExistingBuild = namedtuple("ExistingBuild", ["subdir", "build"])
    meta = Mock()
    meta.name.return_value = "samtools"
    meta.version.return_value = "1.24"
    meta.build_number.return_value = 1
    meta.build_id.return_value = "h391949c_1"
    meta.pkg_fn.return_value = "samtools-1.24-h391949c_1"
    meta.noarch = False
    meta.noarch_python = False
    meta.config.host_subdir = PackageSubdir.LINUX_64
    existing_builds = [ExistingBuild(subdir=PackageSubdir.LINUX_64, build="h391949c_1")]

    monkeypatch.setattr(utils.RepoData, "config", {"channels": ["bioconda"]})
    monkeypatch.setattr(
        utils,
        "_load_platform_metas",
        lambda *_a, **_k: (PackageSubdir.LINUX_64, [meta]),
    )
    monkeypatch.setattr(
        utils.RepoData, "get_package_data", lambda _self, _keys, **_k: existing_builds
    )
    monkeypatch.setattr(
        utils.api, "get_output_file_paths", lambda m: [f"/tmp/{m.pkg_fn()}.tar.bz2"]
    )

    caplog.set_level(logging.INFO, logger="bioconda_utils.utils")
    paths = utils.get_package_paths("recipes/samtools", ["bioconda"], force=True)
    assert paths == ["/tmp/samtools-1.24-h391949c_1.tar.bz2"]
    assert "FORCE: building samtools-1.24-h391949c_1" in caplog.text
    assert "it is not forced" not in caplog.text

    caplog.clear()
    paths = utils.get_package_paths("recipes/samtools", ["bioconda"], force=False)
    assert paths == []
    assert "it is not forced" in caplog.text


def test_check_recipe_skippable_queries_requested_target(monkeypatch):
    meta = Mock()
    meta.name.return_value = "samtools"
    meta.version.return_value = "1.24"
    meta.build_number.return_value = 1
    meta.get_value.return_value = None
    meta.noarch = False
    meta.noarch_python = False
    meta.config.host_subdir = PackageSubdir.LINUX_AARCH64
    loaded_targets = []
    queried_platforms = []

    def load_platform_metas(_recipe, *, finalize, target_platform):
        loaded_targets.append((finalize, target_platform))
        return PackageSubdir.LINUX_AARCH64, [meta]

    def get_package_data(_self, _key, **kwargs):
        queried_platforms.append(kwargs["platform"])
        return []

    monkeypatch.setattr(utils, "_load_platform_metas", load_platform_metas)
    monkeypatch.setattr(utils.RepoData, "get_package_data", get_package_data)

    assert not utils.check_recipe_skippable(
        "samtools", ["bioconda"], target_platform=ContainerPlatform.LINUX_ARM64
    )
    assert loaded_targets == [(False, ContainerPlatform.LINUX_ARM64)]
    assert queried_platforms == [[PackageSubdir.LINUX_AARCH64, "noarch"]]


def test_native_platform_skipping(config_fixture):
    expections = (
        # Don't skip linux-x86 for any recipes
        ("one", "linux-64", False),
        ("two", "linux-64", False),
        ("three", "linux-64", False),
        ("four", "linux-64", False),
        # Skip recipes without linux aarch64 enable on linux-aarch64 platform
        ("one", "linux-aarch64", True),
        ("three", "linux-aarch64", True),
        # Don't skip recipes with linux aarch64 enable on linux-aarch64 platform
        ("two", "linux-aarch64", False),
        ("four", "linux-aarch64", False),
        ("one", "osx-arm64", True),
        ("two", "osx-arm64", True),
        ("three", "osx-arm64", False),
        ("four", "osx-arm64", False),
        ("one", "linux-riscv64", True),
        ("two", "linux-riscv64", True),
        ("four", "linux-riscv64", True),
        ("five", "linux-riscv64", False),
    )
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"
        two:
          meta.yaml: |
            package:
              name: two
              version: "0.1"
            extra:
              additional-platforms:
                - linux-aarch64
        three:
          meta.yaml: |
            package:
              name: three
              version: "0.1"
            extra:
              additional-platforms:
                - osx-arm64
        four:
          meta.yaml: |
            package:
              name: four
              version: "0.1"
            extra:
              additional-platforms:
                - linux-aarch64
                - osx-arm64
        five:
          meta.yaml: |
            package:
              name: five
              version: "0.1"
            extra:
              additional-platforms:
                - linux-riscv64
        """,
        from_string=True,
    )
    r.write_recipes()
    for recipe_name, platform, result in expections:
        recipe_folder = os.path.dirname(r.recipe_dirs[recipe_name])
        assert (
            build.should_skip_platform(
                Path(recipe_folder),
                Path(r.recipe_dirs[recipe_name]),
                PackageSubdir(platform),
            )
            == result
        )

    # When osx-64 is not in primary_platforms, it requires opt-in
    assert build.should_skip_platform(
        Path(os.path.dirname(r.recipe_dirs["one"])),
        Path(r.recipe_dirs["one"]),
        PackageSubdir.OSX_64,
        primary_platforms=[PackageSubdir.LINUX_64],
    )
    assert not build.should_skip_platform(
        Path(os.path.dirname(r.recipe_dirs["one"])),
        Path(r.recipe_dirs["one"]),
        PackageSubdir.LINUX_64,
        primary_platforms=[PackageSubdir.LINUX_64],
    )
    # If recipe opts into osx-64, it is not skipped even when osx-64 is non-primary
    r_osx_optin = Recipes(
        """
        osx_pkg:
          meta.yaml: |
            package:
              name: osx_pkg
              version: "0.1"
            extra:
              additional-platforms:
                - osx-64
        """,
        from_string=True,
    )
    r_osx_optin.write_recipes()
    assert not build.should_skip_platform(
        Path(os.path.dirname(r_osx_optin.recipe_dirs["osx_pkg"])),
        Path(r_osx_optin.recipe_dirs["osx_pkg"]),
        PackageSubdir.OSX_64,
        primary_platforms=[PackageSubdir.LINUX_64],
    )


def test_variants():
    """
    Multiple variants should return multiple metadata
    """
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"
            requirements:
              build:
                - mypkg {{ mypkg }}
        """,
        from_string=True,
    )
    r.write_recipes()
    recipe = r.recipe_dirs["one"]

    # Write a temporary conda_build_config.yaml that we'll point the config
    # object to:
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as fout:
        tmp = fout.name
        fout.write(
            dedent("""
                mypkg:
                  - 1.0
                  - 2.0
                """)
        )
    config = utils.load_conda_build_config()
    config.exclusive_config_files = [tmp]

    assert len(utils.load_all_meta(recipe, config)) == 2


def test_load_conda_build_config_resolves_symlink(monkeypatch, tmp_path):
    env_root = tmp_path / "env"
    executable = env_root / "bin" / "bioconda-utils"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (env_root / "conda_build_config.yaml").write_text("{}\n")

    symlink = tmp_path / "bin" / "bioconda-utils"
    symlink.parent.mkdir()
    symlink.symlink_to(executable)
    monkeypatch.setattr(utils.shutil, "which", lambda _: str(symlink))

    config = utils.load_conda_build_config()

    assert config.exclusive_config_files[0] == str(env_root / "conda_build_config.yaml")


@pytest.mark.long_running_2
def test_cb3_outputs(config_fixture):
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: "0.1"

            outputs:
              - name: libone
              - name: py-one
                requirements:
                  - {{ pin_subpackage('libone', exact=True) }}
                  - python  {{ python }}

        """,
        from_string=True,
    )
    r.write_recipes()
    r.recipe_dirs["one"]

    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_result

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


@pytest.mark.long_running_2
def test_compiler(config_fixture):
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.1
            requirements:
              build:
                - {{ compiler('c') }}
              host:
                - python
              run:
                - python
        """,
        from_string=True,
    )
    r.write_recipes()
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_result

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


@pytest.mark.long_running_2
def test_nested_recipes(config_fixture):
    """
    Test get_recipes ability to identify different nesting depths of recipes
    """
    r = Recipes(
        """
        shallow:
            meta.yaml: |
                package:
                    name: shallow
                    version: "0.1"
            build.sh: |
                #!/bin/bash
                echo "Shallow Created"
                pwd
        normal/normal:
            meta.yaml: |
                package:
                    name: normal
                    version: "0.1"
                build:
                    skip: true
                requirements:
                    build:
                        - python 3.6
            build.sh: |
                #!/bin/bash
                echo "Testing build.sh through python"
                python -h
        deep/deep/deep:
            meta.yaml: |
                package:
                    name: deep
                    version: "0.1"
                requirements:
                    build:
                        - python
                    run:
                        - python
            build.sh: |
                #!/bin/bash
                ## Empty script
        F/I/V/E/deep:
            meta.yaml: |
                package:
                    name: fivedeep
                    version: "0.1"
                requirements:
                    build:
                        - python 3.6
                    run:
                        - python 3.6
        """,
        from_string=True,
    )
    r.write_recipes()

    build_results = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_results

    assert len(list(utils.get_recipes(Path(r.basedir)))) == 4

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


@pytest.mark.skipif(SKIP_NOT_OSX, reason="osx-only test")
def test_conda_build_sysroot(config_fixture):
    """
    Test if CONDA_BUILD_SYSROOT is empty/unset and correctly set after compiler activation.
    """
    # conda-build >=3.18.0 sets CONDA_BUILD_SYSROOT to a hard-coded default path.
    # We clear its value in our bioconda_utils-conda_build_config.yaml.
    # With CONDA_BUILD_SYSROOT being empty, the activation script of clang_osx-64
    # can set it to a valid path.
    r = Recipes(
        """
        sysroot_var_is_unset_or_empty_without_c_compiler:
          meta.yaml: |
            package:
              name: sysroot_var_is_unset_or_empty_without_c_compiler
              version: 0.1
            build:
              script: '[ -z "${CONDA_BUILD_SYSROOT:-}" ]'
        sysroot_is_existing_directory_with_c_compiler:
          meta.yaml: |
            package:
              name: sysroot_is_existing_directory_with_c_compiler
              version: 0.1
            build:
              script: 'test -d "${CONDA_BUILD_SYSROOT}"'
            requirements:
              build:
                - {{ compiler('c') }}
        """,
        from_string=True,
    )
    r.write_recipes()
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_result

    for v in r.recipe_dirs.values():
        for i in utils.built_package_paths(v):
            assert os.path.exists(i)
            ensure_missing(i)


@pytest.mark.long_running_1
def test_skip_unsatisfiable_pin_compatible(config_fixture):
    """
    Test unsatisfiable variants which are skipped get filtered out.
    """
    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 0.2
        two:
          meta.yaml: |
            package:
              name: two
              version: 0.1
            build:
              skip: True  # [one == '0.1']
            requirements:
              host:
                - one
                - one >=0.2
              run:
                - {{ pin_compatible('one') }}
          conda_build_config.yaml: |
            one:
              - 0.1
              - 0.2
        """,
        from_string=True,
    )
    r.write_recipes()
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        [Path(r.recipe_dirs["one"])],
        testonly=False,
        force=False,
        mulled_build_and_test=False,
    )
    assert build_result
    assert len(utils.load_all_meta(r.recipe_dirs["two"])) == 1


@pytest.mark.parametrize("mulled_build_and_test", PARAMS, ids=IDS)
@pytest.mark.parametrize("pkg_format", ["1", "2"])
def test_pkg_test_conda_package_format(
    config_fixture, pkg_format, mulled_build_and_test, tmp_path, monkeypatch
):
    """
    Running a mulled-build test with .tar.bz2/.conda package formats
    """
    # ("1" is .tar.bz2 and "2" is .conda)
    try:
        cc_conda_build = importlib.import_module(
            "conda_build.conda_interface"
        ).cc_conda_build
    except ImportError:
        pass
    else:
        monkeypatch.setitem(cc_conda_build, "pkg_format", pkg_format)
    from conda.base.context import context

    monkeypatch.setitem(context.conda_build, "pkg_format", pkg_format)
    condarc = Path(tmp_path, ".condarc")
    condarc.write_text(f"conda_build:\n  pkg_format: {pkg_format}\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setattr(
        utils, "ENV_VAR_WHITELIST", ["CONDARC", *utils.ENV_VAR_WHITELIST]
    )

    r = Recipes(
        """
        one:
          meta.yaml: |
            package:
              name: one
              version: 1.1
            build:
              script:
               - touch "${PREFIX}/one-file"
            test:
              commands:
                - test -f "${PREFIX}/one-file"
        """,
        from_string=True,
    )
    r.write_recipes()
    docker_builder = None
    if mulled_build_and_test:
        # Override conda_build.pkg_format in build_script_template.
        build_script_template = re.sub(
            "^(conda config.*)",
            f"conda config --set conda_build.pkg_format {pkg_format}\n\\1",
            docker_utils.BUILD_SCRIPT_TEMPLATE,
            count=1,
            flags=re.MULTILINE,
        )
        docker_builder = docker_utils.RecipeBuilder(
            use_host_conda_bld=True,
            docker_base_image=BUILD_ENV_IMAGE,
            build_script_template=build_script_template,
        )
    build_result = build.build_recipes(
        Path(r.basedir),
        config_fixture,
        r.recipe_dirnames,
        docker_builder=docker_builder,
        mulled_build_and_test=mulled_build_and_test,
    )
    assert build_result

    for recipe_dir in r.recipe_dirnames:
        for pkg_file in utils.built_package_paths(recipe_dir):
            assert pkg_file.endswith({"1": ".tar.bz2", "2": ".conda"}[pkg_format])
            assert os.path.exists(pkg_file)
            ensure_missing(pkg_file)


def test_validate_config_smoke():
    """Minimal config that satisfies schema types should validate without error."""
    cfg = {
        "channels": ["conda-forge", "bioconda"],
        "docker_image": "quay.io/bioconda/bioconda-utils",
        "upload_channel": "bioconda",
        "conda_build_version": "3.28.4",
        "blacklists": [],
    }
    # Should not raise
    validate_config(cfg)


@pytest.mark.parametrize(
    "primary_platforms",
    [None, [], ["linx-64"], ["linux-64", "linux-64"]],
)
def test_validate_config_rejects_invalid_primary_platforms(primary_platforms):
    with pytest.raises(ValidationError):
        validate_config({"primary_platforms": primary_platforms})


@pytest.mark.parametrize("platform", PackageSubdir)
def test_validate_config_accepts_every_package_subdir(platform):
    validate_config({"primary_platforms": [platform.value]})


def test_normalize_config_applies_defaults_without_mutating_input():
    config = {
        "blacklists": ["blacklists/temporary.txt"],
    }

    normalized = utils.normalize_config(config)

    assert isinstance(normalized, Config)
    assert config == {
        "blacklists": ["blacklists/temporary.txt"],
    }
    assert normalized == {
        "blacklists": ["blacklists/temporary.txt"],
        "channels": ["conda-forge", "bioconda"],
        "requirements": None,
        "upload_channel": "bioconda",
        "primary_platforms": [PackageSubdir.LINUX_64, PackageSubdir.OSX_64],
    }


def test_normalize_config_custom_primary_platforms():
    config = {
        "primary_platforms": ["linux-64"],
    }

    normalized = utils.normalize_config(config)

    assert normalized["primary_platforms"] == [PackageSubdir.LINUX_64]


def test_normalize_config_is_idempotent():
    normalized = utils.normalize_config({"channels": ["bioconda"]})

    assert utils.normalize_config(normalized) is normalized


def test_build_recipes_normalizes_raw_config_at_boundary(monkeypatch):
    class NormalizationObserved(Exception):
        pass

    registered = []

    def register_config(config):
        registered.append(config)

    def observe_config(config, _recipe_folder):
        assert isinstance(config, Config)
        assert config["requirements"] is None
        assert registered == [config]
        raise NormalizationObserved

    monkeypatch.setattr(utils.RepoData, "register_config", register_config)
    monkeypatch.setattr(build, "Skiplist", observe_config)

    with pytest.raises(NormalizationObserved):
        build.build_recipes(Path("recipes"), {"channels": []}, [Path("example")])


def test_load_config_registers_config_after_resolving_paths(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "blacklists:\n  - blacklists/temporary.txt\n",
        encoding="utf-8",
    )

    registered = []
    monkeypatch.setattr(
        utils.RepoData,
        "register_config",
        lambda config: registered.append(config.copy()),
    )

    config = utils.load_config(config_path)

    assert config["blacklists"] == [str(tmp_path / "blacklists/temporary.txt")]
    assert config["channels"] == ["conda-forge", "bioconda"]
    assert config["primary_platforms"] == [PackageSubdir.LINUX_64, PackageSubdir.OSX_64]
    assert registered == [config]


def test_load_meta_fast_allows_duplicate_keys(tmp_path):
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    meta_path = recipe_dir / "meta.yaml"
    meta_path.write_text(
        "package:\n"
        "  name: test-pkg\n"
        "  version: '1.0'\n"
        "source:\n"
        "  url: http://example.com/linux.tar.gz  # [linux]\n"
        "  url: http://example.com/osx.tar.gz    # [osx]\n"
        "build:\n"
        "  number: 0\n"
        "  skip: true  # [osx]\n"
        "  skip: true  # [win]\n",
        encoding="utf-8",
    )
    meta, loaded_recipe = utils.load_meta_fast(str(recipe_dir))
    assert meta["package"]["name"] == "test-pkg"
    assert meta["build"]["number"] == 0
    assert loaded_recipe == str(recipe_dir)
