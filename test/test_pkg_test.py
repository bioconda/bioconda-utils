import subprocess as sp
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from helpers import Recipes, ensure_missing, get_rattler_params

from bioconda_utils import build
from bioconda_utils._types import BuildSystem
from bioconda_utils.conda.recipes import built_package_paths
from bioconda_utils.containers import pkg_test

# TODO:
# need tests for channel order and extra channels (see
# https://github.com/bioconda/bioconda-utils/issues/31)
#

CONDA = BuildSystem.CONDA

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
        built_packages = built_package_paths(recipe_dir)
        registered_packages.update(built_packages)
        for pkg in built_packages:
            ensure_missing(pkg)

        recipe_path, global_variants, tool_config, render_config, rattler_output_dir = (
            get_rattler_params(Path(recipe_dir), CONDA, docker_builder)
        )

        pkg_paths = [Path(p) for p in built_packages]

        build.build(
            recipe=recipe_path,
            global_variants=global_variants,
            tool_config=tool_config,
            render_config=render_config,
            rattler_output_dir=rattler_output_dir,
            force=True,
            pkg_paths=pkg_paths,
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


@pytest.mark.skipif(SKIP_OSX, reason="skipping on osx")
def test_pkg_test_temporary_container(build_pkg):
    """
    Running a test in a temporary container with pre-solved spec.
    """
    built_packages = build_pkg(RECIPE_ONE)
    for pkg in built_packages:
        res = pkg_test.test_package_in_temporary_container(pkg)
        assert res is not None
        assert res.returncode == 0


def test_build_and_test_mulled_image_forwards_cuda_override(monkeypatch, tmp_path):
    """--conda-override-cuda is forwarded to the mulled-build command line."""
    pkg = tmp_path / "one-0.1-0.conda"
    pkg.touch()
    captured = {}

    monkeypatch.setattr(
        pkg_test,
        "_test_inputs",
        lambda path, channels: (
            tmp_path,
            pkg_test.PkgBuildRef("one", "0.1", "0"),
            ["conda-forge", "local", "bioconda"],
            "true",
        ),
    )

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pkg_test, "run", fake_run)

    pkg_test.build_and_test_mulled_image(pkg, conda_override_cuda="99")
    assert "--conda-override-cuda" in captured["cmd"]
    flag_index = captured["cmd"].index("--conda-override-cuda")
    assert captured["cmd"][flag_index + 1] == "99"


def test_generate_explicit_spec_forwards_cuda_override(monkeypatch, tmp_path):
    """The pre-solve dry run gets CONDA_OVERRIDE_CUDA only when requested."""
    spec = pkg_test.PkgBuildRef("one", "0.1", "0")
    envs = []

    def fake_run(cmd, **kw):
        envs.append(kw.get("env"))
        return sp.CompletedProcess(cmd, 0, stdout='{"actions": {}}')

    monkeypatch.setattr(pkg_test.sp, "run", fake_run)

    pkg_test._generate_explicit_spec(spec, ["conda-forge"], tmp_path, tmp_path)
    assert envs[-1] is None

    pkg_test._generate_explicit_spec(
        spec, ["conda-forge"], tmp_path, tmp_path, conda_override_cuda="12.4"
    )
    assert envs[-1]["CONDA_OVERRIDE_CUDA"] == "12.4"
