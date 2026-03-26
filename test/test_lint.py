import os.path as op
from ruamel.yaml import YAML

import glob
import pytest

from bioconda_utils import lint, utils
from bioconda_utils.utils import ensure_list


yaml = YAML(typ="rt")  # pylint: disable=invalid-name

TEST_DATA = dict()

# gather all linting test case YAML files from lint_cases/ subdirectory
linting_case_files = glob.glob(op.join(op.dirname(__file__), "lint_cases", "*.yaml"))

for case_file in linting_case_files:
    with open(case_file) as data:
        # the case YAML file name is unique by default, so we can use the
        # basename as a unique case_name here
        case_name = op.splitext( op.basename(case_file) )[0]
        case_data = yaml.load(data)
        TEST_DATA[case_name] = case_data
        # we need the case_name accessible in some cases
        TEST_DATA[case_name]["name"] = case_name


TEST_CASES = list(TEST_DATA.values())
TEST_CASE_IDS = list(TEST_DATA.keys())


@pytest.fixture
def linter(config_file, recipes_folder):
    """Prepares a linter given config_folder and recipes_folder"""
    config = utils.load_config(config_file)
    yield lint.Linter(config, str(recipes_folder), nocatch=True)


@pytest.mark.parametrize('case', TEST_CASES, ids=TEST_CASE_IDS)
def test_lint(linter, recipe_dirs, mock_repodata, case):
    recipes = [str(p) for p in recipe_dirs]
    linter.clear_messages()
    linter.lint(recipes)
    messages = linter.get_messages()
    expected = set(ensure_list(case.get('expected_failures', [])))
    found = set()
    for msg in messages:
        assert str(msg.check) in expected, (
            f"In test '{case['name']}' on '{msg.recipe.basedir}':"
            f"'{msg.check}' emitted unexpectedly")
        found.add(str(msg.check))
    assert len(expected) == len(found), (
        f"In test '{case['name']}': "
        "missed expected lint failures. Expected: "
        f"{expected}")

    canfix = set(msg for msg in messages if msg.canfix and str(msg.check) in expected)
    if canfix:
        linter.clear_messages()
        linter.order_and_load_checks()
        linter.lint(recipes, fix=True)
        found_fix = set(str(msg.check) for msg in linter.get_messages())
        for msg in canfix:
            assert str(msg.check) not in found_fix
        linter.clear_messages()
        linter.order_and_load_checks()
        linter.lint(recipes)
        found_postfix = set(str(msg.check) for msg in linter.get_messages())
        for msg in canfix:
            assert str(msg.check) not in found_postfix
        for msgstr in found_postfix:
            assert msgstr in found
