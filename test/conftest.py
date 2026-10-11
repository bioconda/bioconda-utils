import json
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path

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
    records = {}
    for channel, packages in case.get("repodata", {}).items():
        for name, versions in packages.items():
            for item in versions:
                record = {
                    "name": name,
                    "build": "",
                    "build_number": 0,
                    "version": 0,
                    "depends": [],
                    "subdir": "",
                    "platform": "noarch",
                    **item,
                }
                records.setdefault((channel, record["platform"]), []).append(record)

    config = repodata.RepoData.config or {}
    monkeypatch.setattr(
        repodata.RepoData,
        "config",
        {
            **config,
            "channels": list(
                dict.fromkeys(
                    [
                        *config.get("channels", []),
                        *(channel for channel, _subdir in records),
                    ]
                )
            ),
        },
    )

    monkeypatch.setattr(
        repodata.RepoData,
        "_repositories",
        lambda self, channels, subdirs: (
            (c, s) for c, s in records if c in channels and s in subdirs
        ),
    )

    @contextmanager
    def open_repository(self, channel, subdir):
        raw = json.dumps(
            {
                "info": {"subdir": subdir},
                "packages": {
                    str(i): record
                    for i, record in enumerate(records[(channel, subdir)])
                },
            }
        ).encode()
        with closing(sqlite3.connect(":memory:")) as connection:
            self._populate_database(connection, raw, fetched_at=0)
            yield connection

    monkeypatch.setattr(repodata.RepoData, "_open_repository", open_repository)


@pytest.fixture(autouse=True)
def isolated_repodata_cache(monkeypatch, tmp_path):
    """Tests never read or write the user's persistent caches."""
    from bioconda_utils.support import caching

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    # Isolate the platform provider on macOS too, where XDG is not consulted.
    monkeypatch.setattr(
        caching.platformdirs,
        "user_cache_path",
        lambda app: Path(os.environ["XDG_CACHE_HOME"]) / app,
    )
    monkeypatch.setattr(caching, "_cache_root", None)
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
