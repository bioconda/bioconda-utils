import shutil
from copy import deepcopy

import pandas as pd
import pytest
from ruamel.yaml import YAML

from bioconda_utils.conda import repodata

yaml = YAML(typ="rt")  # pylint: disable=invalid-name

# common settings
TEST_RECIPES_FOLDER = "recipes"
TEST_CONFIG_YAML_FNAME = "config.yaml"
TEST_CONFIG_YAML = {"blacklists": [], "channels": []}


def pytest_runtest_makereport(item, call):
    # If we failed, mark the parent with the callspec ID (name from test args).
    if "successive" in item.keywords and call.excinfo is not None:
        item.parent.failedcallspec = item.callspec.id


def pytest_runtest_setup(item):
    if (
        "successive" in item.keywords
        and getattr(item.parent, "failedcallspec", None) == item.callspec.id
    ):
        pytest.xfail("preceding test failed")


@pytest.fixture
def mock_repodata(case, monkeypatch):
    """Provide mock repository data

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
    if "repodata" in case:
        dataframe = pd.DataFrame(
            (
                {
                    "channel": channel,
                    "name": name,
                    "build": "",
                    "build_number": 0,
                    "version": 0,
                    "depends": [],
                    "subdir": "",
                    "platform": "noarch",
                    **item,
                }
                for channel, packages in case["repodata"].items()
                for name, versions in packages.items()
                for item in versions
            ),
            columns=repodata.RepoData.columns,
        )
    else:
        dataframe = pd.DataFrame({}, columns=repodata.RepoData.columns)

    monkeypatch.setattr(
        repodata.RepoData,
        "_get_repository_dataframes",
        lambda _self, _channels, _subdirs: [dataframe],
    )


@pytest.fixture(autouse=True)
def isolated_repodata_cache(monkeypatch, tmp_path):
    """Tests never read or write the user's persistent repodata cache."""
    monkeypatch.setattr(repodata.RepoData, "_repository_cache", {})
    monkeypatch.setattr(repodata.RepoData, "cache_dir", tmp_path / "repodata")
    monkeypatch.setattr(repodata.RepoData, "refresh_after", None)


@pytest.fixture
def recipes_folder(tmp_path, monkeypatch):
    """Prepares a temp dir with '/recipes' folder as configured"""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / TEST_RECIPES_FOLDER
    folder.mkdir()
    return folder


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
def config_file(tmp_path, case):
    """Prepares Bioconda config.yaml"""
    if "add_root_files" in case:
        for fname, data in case["add_root_files"].items():
            with (tmp_path / fname).open("w") as fdes:
                fdes.write(data)

    data = deepcopy(TEST_CONFIG_YAML)
    if "config" in case:
        dict_merge(data, case["config"])
    config_fname = tmp_path / TEST_CONFIG_YAML_FNAME
    with config_fname.open("w") as fdes:
        yaml.dump(data, fdes)

    yield config_fname


@pytest.fixture
def recipe_dirs(recipes_folder, case):
    """Prepares a recipe from recipe_data in recipes_folder"""
    recipe_dirs = []
    recipes = case.get("recipes")
    if not recipes:
        raise LookupError(
            "No `recipes:` entry found in this test case's YAML file, and testing nothing is not expected. Check folder lint_cases for the YAML file and include a `recipes:` entry."
        )
    for recipe_name in case.get("recipes", []):
        recipe = deepcopy(case.get("recipes").get(recipe_name))
        recipe_dir = recipes_folder / recipe_name
        recipe_dir.mkdir()

        with (recipe_dir / "meta.yaml").open("w") as fdes:
            yaml.dump(
                recipe,
                fdes,
                transform=lambda string: string.replace("#{%", "{%").replace(
                    "#{{", "{{"
                ),
            )

        if "add_files" in case:
            for fname, data in case["add_files"].items():
                with (recipe_dir / fname).open("w") as fdes:
                    fdes.write(data)

        if "move_files" in case:
            for src, dest in case["move_files"].items():
                src_path = recipe_dir / src
                if not dest:
                    if src_path.is_dir():
                        shutil.rmtree(src_path)
                    else:
                        src_path.unlink()
                else:
                    dest_path = recipe_dir / dest
                    shutil.move(src_path, dest_path)

        recipe_dirs.append(recipe_dir)

    yield recipe_dirs
