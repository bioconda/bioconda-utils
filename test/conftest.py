import datetime
import os
import shutil
from copy import deepcopy

from ruamel.yaml import YAML
import pandas as pd
import pytest
import py

from bioconda_utils import utils

yaml = YAML(typ="rt")  # pylint: disable=invalid-name


# common settings
TEST_RECIPES_FOLDER = "recipes"
TEST_CONFIG_YAML_FNAME = "config.yaml"
TEST_CONFIG_YAML = {"blacklists": [], "channels": []}


def pytest_runtest_makereport(item, call):
    if "successive" in item.keywords:
        # if we failed, mark parent with callspec id (name from test args)
        if call.excinfo is not None:
            item.parent.failedcallspec = item.callspec.id


def pytest_runtest_setup(item):
    if "successive" in item.keywords:
        if getattr(item.parent, "failedcallspec", None) == item.callspec.id:
            pytest.xfail("preceding test failed")


@pytest.fixture
def mock_repodata(case):
    """Pepares RepoData singleton to contain mock data

    Expects function to be parametrized with ``case``, where ``case`` may
    contain a ``repodata`` key. If none exists, empty repodata is generated.

    ``repodata:`` entry in a case YAML file should be of this form::

       <channel>:
          <package_name>:
             - <key>: value
               <key>: value

    E.g.::
       bioconda:
         package_one:
             - version: 0.1
               build_number: 0
    """
    if 'repodata' in case:
        dataframe = pd.DataFrame(
            (
                {
                    'channel': channel,
                    'name': name,
                    'build': '',
                    'build_number': 0,
                    'version': 0,
                    'depends': [],
                    'subdir': '',
                    'platform': 'noarch',
                    **item,
                }
                for channel, packages in case["repodata"].items()
                for name, versions in packages.items()
                for item in versions
            ),
            columns=utils.RepoData.columns,
        )
    else:
        dataframe = pd.DataFrame(dict(), columns=utils.RepoData.columns)

    backup = utils.RepoData()._df, utils.RepoData()._df_ts
    utils.RepoData()._df = dataframe
    utils.RepoData()._df_ts = datetime.datetime.now()
    yield
    utils.RepoData()._df, utils.RepoData()._df_ts = backup


@pytest.fixture
def recipes_folder(tmpdir: py.path.local):
    """Prepares a temp dir with '/recipes' folder as configured"""
    orig_cwd = tmpdir.chdir()
    yield tmpdir.mkdir(TEST_RECIPES_FOLDER)
    orig_cwd.chdir()


def dict_merge(base, add):
    for key, value in add.items():
        if isinstance(value, dict):
            base[key] = dict_merge(base.get(key, {}), value)
        elif isinstance(base, list):
            for num in range(len(base)):
                base[num][key] = dict_merge(base[num].get(key, {}), add)
        else:
            base[key] = value
    return base


@pytest.fixture
def config_file(tmpdir: py.path.local, case):
    """Prepares Bioconda config.yaml"""
    if "add_root_files" in case:
        for fname, data in case["add_root_files"].items():
            with tmpdir.join(fname).open("w") as fdes:
                fdes.write(data)

    data = deepcopy(TEST_CONFIG_YAML)
    if "config" in case:
        dict_merge(data, case["config"])
    config_fname = tmpdir.join(TEST_CONFIG_YAML_FNAME)
    with config_fname.open("w") as fdes:
        yaml.dump(data, fdes)

    yield config_fname


@pytest.fixture
def recipe_dirs(recipes_folder: py.path.local, tmpdir: py.path.local,
               case):
    """Prepares a recipe from recipe_data in recipes_folder"""
    recipe_dirs = []
    recipes = case.get("recipes")
    if not recipes:
        raise LookupError("No `recipes:` entry found in this test case's YAML file, and testing nothing is not expected. Check folder lint_cases for the YAML file and include a `recipes:` entry.")
    for recipe_name in case.get("recipes", []):
        recipe = deepcopy(case.get("recipes").get(recipe_name))
        recipe_dir = recipes_folder.mkdir(recipe_name)

        with recipe_dir.join('meta.yaml').open('w') as fdes:
            yaml.dump(recipe, fdes,
                      transform=lambda l: l.replace('#{%', '{%').replace("#{{", "{{"))

        if 'add_files' in case:
            for fname, data in case['add_files'].items():
                with recipe_dir.join(fname).open('w') as fdes:
                    fdes.write(data)

        if 'move_files' in case:
            for src, dest in case['move_files'].items():
                src_path = recipe_dir.join(src)
                if not dest:
                    if os.path.isdir(src_path):
                        shutil.rmtree(src_path)
                    else:
                        os.remove(src_path)
                else:
                    dest_path = recipe_dir.join(dest)
                    shutil.move(src_path, dest_path)
        
        recipe_dirs.append(recipe_dir)

    yield recipe_dirs
