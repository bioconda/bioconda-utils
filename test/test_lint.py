from pathlib import Path
from typing import ClassVar

import pytest
from ruamel.yaml import YAML

from bioconda_utils import lint
from bioconda_utils._types import CONDA, Config, RecipePath, ensure_list
from bioconda_utils.config import load_config

yaml = YAML(typ="rt")  # pylint: disable=invalid-name

TEST_DATA = {}

# gather all linting test case YAML files from lint_cases/ subdirectory
linting_case_files = sorted((Path(__file__).parent / "lint_cases").glob("*.yaml"))

for case_file in linting_case_files:
    with case_file.open() as data:
        # the case YAML file name is unique by default, so we can use the
        # stem as a unique case_name here
        case_name = case_file.stem
        case_data = yaml.load(data)
        TEST_DATA[case_name] = case_data
        # we need the case_name accessible in some cases
        TEST_DATA[case_name]["name"] = case_name

TEST_CASES = list(TEST_DATA.values())
TEST_CASE_IDS = list(TEST_DATA.keys())


@pytest.fixture
def linter(config_file, recipes_folder):
    """Prepares a linter given config_folder and recipes_folder"""
    config = load_config(config_file)
    yield lint.Linter(config, recipes_folder, nocatch=True)


@pytest.mark.parametrize("case", TEST_CASES, ids=TEST_CASE_IDS)
def test_lint(linter, recipe_dirs, mock_repodata, case):
    recipes: list[RecipePath] = [RecipePath(p, CONDA) for p in recipe_dirs]
    linter.clear_messages()
    linter.lint(recipes)
    messages = linter.get_messages()
    expected = set(ensure_list(case.get("expected_failures", [])))
    found = set()
    for msg in messages:
        assert str(msg.check) in expected, (
            f"In test '{case['name']}' on '{msg.recipe.basedir}':'{msg.check}' emitted unexpectedly"
        )
        found.add(str(msg.check))
    assert len(expected) == len(found), (
        f"In test '{case['name']}': missed expected lint failures. Expected: {expected}"
    )

    canfix = {msg for msg in messages if msg.canfix and str(msg.check) in expected}
    if canfix:
        linter.clear_messages()
        linter.order_and_load_checks()
        linter.lint(recipes, fix=True)
        found_fix = {str(msg.check) for msg in linter.get_messages()}
        for msg in canfix:
            assert str(msg.check) not in found_fix
        linter.clear_messages()
        linter.order_and_load_checks()
        linter.lint(recipes)
        found_postfix = {str(msg.check) for msg in linter.get_messages()}
        for msg in canfix:
            assert str(msg.check) not in found_postfix
        for msgstr in found_postfix:
            assert msgstr in found


def test_rattler_lint_message_formatting():
    from bioconda_utils._types import RATTLER
    from bioconda_utils.lint import WARNING, RattlerLintMessage

    msg = RattlerLintMessage(
        recipe=RecipePath(Path("recipes/samtools/1.7"), RATTLER),
        lint_or_hint="warning message",
        severity=WARNING,
    )
    assert (
        msg.get_report_message()
        == "WARNING: recipes/samtools/1.7/recipe.yaml: warning message"
    )
    assert msg.get_table_row() == (
        "WARNING",
        "recipes/samtools/1.7/recipe.yaml",
        "rattler_build",
        "warning message",
    )


@pytest.mark.parametrize("mode", ["pass", "fail", "skip"])
def test_lint_dependency_order_and_transitive_skips(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(lint.LintCheck, "registry", [])
    monkeypatch.setenv("LINT_SKIP", "")
    calls = []

    class prerequisite(lint.LintCheck):
        """Prerequisite check."""

        def check_recipe(self, recipe):
            calls.append("prerequisite")
            if mode == "fail":
                self.message(section="package/name")

    class dependent(lint.LintCheck):
        """Dependent check."""

        requires: ClassVar = [prerequisite]

        def check_recipe(self, recipe):
            calls.append("dependent")

    class transitive(lint.LintCheck):
        """Transitively dependent check."""

        requires: ClassVar = [dependent]

        def check_recipe(self, recipe):
            calls.append("transitive")

    monkeypatch.setattr(
        lint, "get_checks", lambda: [transitive, dependent, prerequisite]
    )
    recipe_dir = tmp_path / "example"
    recipe_dir.mkdir()
    (recipe_dir / "meta.yaml").write_text(
        "package:\n  name: example\n  version: '1.0'\nbuild:\n  number: 0\n"
    )
    linter = lint.Linter(
        Config(),
        tmp_path,
        exclude=["prerequisite"] if mode == "skip" else [],
        nocatch=True,
    )

    assert linter.lint([RecipePath(recipe_dir, CONDA)]) == (mode == "fail")
    assert (
        calls
        == {
            "pass": ["prerequisite", "dependent", "transitive"],
            "fail": ["prerequisite"],
            "skip": [],
        }[mode]
    )


def test_lint_rejects_unregistered_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(lint.LintCheck, "registry", [])
    monkeypatch.setenv("LINT_SKIP", "")

    class unregistered(lint.LintCheck):
        """Unregistered prerequisite."""

    class dependent(lint.LintCheck):
        """Dependent check."""

        requires: ClassVar = [unregistered]

    monkeypatch.setattr(lint, "get_checks", lambda: [dependent])
    with pytest.raises(
        ValueError, match="Unregistered lint dependency.*required by dependent"
    ):
        lint.Linter(Config(), tmp_path)
