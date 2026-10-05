import tempfile
from pathlib import Path
from textwrap import dedent

import rattler_build as rb
from conda_index.index import update_index
from ruamel.yaml import YAML

from bioconda_utils._types import BuildSystem, PackageSubdir, RecipePath
from bioconda_utils.conda.conda_build_bridge import load_conda_build_config
from bioconda_utils.conda.repodata import RepoData
from bioconda_utils.rattler.rattler_build_bridge import (
    load_rattler_build_global_variants,
)


def ensure_missing(package: Path) -> None:
    """
    Delete a package if it exists and re-index the conda-bld dir.

    If a package is deleted from the conda-bld directory but conda-index is not
    re-run, it remains in the metadata (.index.json, repodata.json) files and
    appears to conda as if the recipe still exists.  This ensures that the
    package is deleted and is removed from the index. Useful for test cases.

    Parameters
    ----------
    package : Path
        Path to tarball of built package. If all you have is a recipe path, use
        `built_package_path()` to get the tarball path.
    """
    package.unlink(missing_ok=True)
    assert not package.exists()
    update_index(str(package.parent.parent))


class Recipes:
    def __init__(self, data, from_string=False):
        """
        Handles the creation of a directory of recipes.

        This class, combined with YAML files describing test cases, can be used
        for building test cases of interdependent recipes in an isolated
        directory.

        Recipes are specified in a YAML file. Each top-level key represents
        a recipe, and the recipe will be written in a temp dir named after that
        key. Sub-keys are filenames to create in that directory, and the value
        of each sub-key is a string (likely a multi-line string indicated with
        a "|").

        For example, this YAML file::

            one:
              meta.yaml: |
                package:
                  name: one
                  version: 0.1
              build.sh: |
                  #!/bin/bash
                  # do installation
            two:
              meta.yaml: |
                package:
                  name: two
                  version: 0.1
              build.sh:
                  #!/bin/bash
                  python setup.py install

        will result in these files::

            /tmp/tmpdirname/
              one/
                meta.yaml
                build.sh
              two/
                meta.yaml
                build.sh

        Parameters
        ----------

        data : str
            If `from_string` is False, this is a filename relative to this
            module's file. If `from_string` is True, then use the contents of
            the string directly.

        from_string : bool

        Useful attributes:

        * recipes: a dict mapping recipe names to parsed meta.yaml contents
        * basedir: the tempdir containing all recipes. Many bioconda-utils
                   functions need the "recipes dir"; that's this basedir.
        * recipe_dirs: a dict mapping recipe names to newly-created recipe
                   dirs. These are full paths to subdirs in `basedir`.
        """

        yaml = YAML(typ="safe")
        if from_string:
            self.data = dedent(data)
            self.recipes = yaml.load(self.data)
        else:
            self.data = Path(__file__).parent / data
            self.recipes = yaml.load(self.data.read_text())
        self.pkgs: dict[str, list[Path]] = {}

    def write_recipes(self):
        basedir = Path(tempfile.mkdtemp())
        self.recipe_dirs: dict[str, Path] = {}
        for name, recipe in self.recipes.items():
            rdir = basedir / name
            rdir.mkdir(parents=True)
            self.recipe_dirs[name] = rdir
            for key, value in recipe.items():
                (rdir / key).write_text(value)
        self.basedir = basedir

    @property
    def recipe_dirnames(self) -> list[Path]:
        return list(self.recipe_dirs.values())


def get_rattler_params(
    path: Path,
    build_system: BuildSystem,
    docker_builder,
    platform: PackageSubdir | None = None,
) -> tuple[RecipePath, rb.VariantConfig, rb.ToolConfiguration, rb.RenderConfig, Path]:
    platform_config: rb.PlatformConfig = rb.PlatformConfig(target_platform=platform)
    skip_rattler: str = "all"
    render_config: rb.RenderConfig = rb.RenderConfig(platform=platform_config)
    global_variants: rb.VariantConfig = load_rattler_build_global_variants(platform)
    tool_config: rb.ToolConfiguration = rb.ToolConfiguration(
        skip_existing=skip_rattler, test_strategy="native", keep_build=False
    )
    if docker_builder is not None:
        rattler_output_dir: Path = Path(docker_builder.pkg_dir)
    else:
        repodata = RepoData()
        subdir: PackageSubdir = repodata.native_subdir()
        conda_build_config = load_conda_build_config(subdir=subdir)
        rattler_output_dir: Path = Path(conda_build_config.output_folder)
    recipe_path: RecipePath = RecipePath(path=path, build_system=build_system)
    return recipe_path, global_variants, tool_config, render_config, rattler_output_dir
