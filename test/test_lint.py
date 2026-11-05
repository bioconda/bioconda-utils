import json
from pathlib import Path
from typing import ClassVar

import pytest
from ruamel.yaml import YAML

from bioconda_utils import lint
from bioconda_utils._types import CONDA, Channel, Config, RecipePath, ensure_list
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


def test_lint_prefetches_selected_names_before_checks(monkeypatch, tmp_path):
    from bioconda_utils._types import RATTLER
    from bioconda_utils.conda.repodata import RepoData
    from bioconda_utils.lint.check_policy import cran_packages_to_conda_forge
    from bioconda_utils.lint.check_repo import in_other_channels

    monkeypatch.setenv("LINT_SKIP", "")
    monkeypatch.setattr(
        lint, "get_checks", lambda: [in_other_channels, cran_packages_to_conda_forge]
    )
    events = []
    selected = []
    for directory, text in (
        ("first", "package: {name: real-package, version: 1}\n"),
        (
            "second",
            (
                "package: {name: real-package, version: 2}\n"
                "requirements: {run: [R, 'r-dependency >=1']}\n"
            ),
        ),
        (
            "skipped",
            (
                "package: {name: skipped-package, version: 1}\n"
                "extra: {skip-lints: [in_other_channels, cran_packages_to_conda_forge]}\n"
            ),
        ),
        ("invalid", "package: [\n"),
    ):
        path = tmp_path / directory
        path.mkdir()
        (path / "meta.yaml").write_text(text)
        selected.append(RecipePath(path, CONDA))
    selected.append(RecipePath(tmp_path / "rattler", RATTLER))
    # An unselected recipe must not participate in metadata planning.
    (tmp_path / "unselected").mkdir()
    (tmp_path / "unselected/meta.yaml").write_text(
        "package: {name: unselected-package, version: 1}\n"
    )
    monkeypatch.setattr(
        RepoData, "prefetch", lambda self, names: events.append(set(names))
    )
    monkeypatch.setattr(RepoData, "needs_prefetch", lambda self: True)
    monkeypatch.setattr(
        lint.Linter, "lint_one", lambda *args, **kwargs: events.append("lint") or []
    )
    monkeypatch.setattr(lint.Linter, "lint_one_rattler", lambda *args, **kwargs: [])

    assert not lint.Linter(Config(), tmp_path).lint(selected)
    assert events == [
        {"real-package", "R", "r-dependency"},
        "lint",
        "lint",
        "lint",
        "lint",
    ]


@pytest.mark.parametrize(
    "skip", ["global", "recipe", "commit", "prerequisite", "commit-prerequisite"]
)
def test_metadata_prefetch_respects_disabled_checks(monkeypatch, tmp_path, skip):
    from bioconda_utils.conda.repodata import RepoData

    monkeypatch.setattr(lint.LintCheck, "registry", [])

    class prerequisite(lint.LintCheck):
        pass

    class metadata_check(lint.LintCheck):
        requires: ClassVar = [prerequisite]

        @classmethod
        def get_repodata_names(cls, recipe):
            return [recipe.name]

    monkeypatch.setattr(lint, "get_checks", lambda: [prerequisite, metadata_check])
    path = tmp_path / "example"
    path.mkdir()
    (path / "meta.yaml").write_text(
        "package: {name: example, version: 1}\n"
        + ("extra: {skip-lints: [metadata_check]}\n" if skip == "recipe" else "")
    )
    monkeypatch.setenv(
        "LINT_SKIP",
        f"[lint skip metadata_check for {path}]"
        if skip == "commit"
        else f"[lint skip prerequisite for {path}]"
        if skip == "commit-prerequisite"
        else "",
    )
    calls = []
    monkeypatch.setattr(RepoData, "prefetch", lambda *args: calls.append(args))
    inspected = []
    monkeypatch.setattr(
        RepoData, "needs_prefetch", lambda self: inspected.append(True) or True
    )
    loads = []
    load = lint._recipe.Recipe.from_file

    def load_recipe(*args, **kwargs):
        loads.append(args[1])
        return load(*args, **kwargs)

    monkeypatch.setattr(lint._recipe.Recipe, "from_file", load_recipe)
    exclude = {"global": ["metadata_check"], "prerequisite": ["prerequisite"]}.get(skip)

    lint.Linter(Config(), tmp_path, exclude=exclude).prefetch_repodata(
        [RecipePath(path, CONDA)]
    )

    assert calls == []
    assert loads == ([path] if skip == "recipe" else [])
    assert inspected == ([True] if skip == "recipe" else [])


@pytest.mark.parametrize("cache_state", ["warm", "cold", "expired", "refresh"])
def test_lint_only_parses_recipes_once_when_full_metadata_is_fresh(
    monkeypatch, tmp_path, cache_state
):
    from bioconda_utils.conda.repodata import RepoData
    from bioconda_utils.conda.shards import ShardClient
    from bioconda_utils.lint.check_repo import in_other_channels

    monkeypatch.setenv("LINT_SKIP", "")
    monkeypatch.setattr(lint, "get_checks", lambda: [in_other_channels])
    config = Config(channels=[Channel("bioconda")])
    monkeypatch.setattr(RepoData, "config", config)
    monkeypatch.setattr(RepoData, "platforms", ["noarch"])
    monkeypatch.setattr(ShardClient, "get_repository_shards", lambda *args: None)
    path = tmp_path / "example"
    path.mkdir()
    (path / "meta.yaml").write_text("package: {name: example, version: 1}\n")
    raw = json.dumps({"info": {"subdir": "noarch"}, "packages": {}}).encode()
    repository = RepoData()
    if cache_state != "cold":
        cache_path = repository._cache_path(Channel("bioconda").repodata_url("noarch"))
        cache_path.parent.mkdir(parents=True)
        repository._write_cache(
            cache_path, raw, fetched_at=0 if cache_state == "expired" else None
        )
    if cache_state == "refresh":
        RepoData.configure_cache(repository.get_cache_dir(), refresh=True)
    downloads = []
    monkeypatch.setattr(
        RepoData, "_download_repository", lambda *args: downloads.append(True) or raw
    )
    loads = []
    load = lint._recipe.Recipe.from_file

    def load_recipe(*args, **kwargs):
        loads.append(kwargs.get("return_exceptions", False))
        return load(*args, **kwargs)

    monkeypatch.setattr(lint._recipe.Recipe, "from_file", load_recipe)
    linter = lint.Linter(config, tmp_path)
    assert not linter.lint([RecipePath(path, CONDA)])
    assert linter.get_messages() == []
    assert loads == ([False] if cache_state == "warm" else [True, False])
    assert downloads == ([] if cache_state == "warm" else [True])


@pytest.mark.parametrize("failure", ["inspection", "download"])
def test_failed_prefetch_leaves_normal_lint_checks_running(
    monkeypatch, tmp_path, caplog, failure
):
    from bioconda_utils.conda.repodata import RepoData
    from bioconda_utils.lint.check_repo import in_other_channels

    monkeypatch.setenv("LINT_SKIP", "")
    monkeypatch.setattr(lint, "get_checks", lambda: [in_other_channels])
    path = tmp_path / "example"
    path.mkdir()
    (path / "meta.yaml").write_text("package: {name: example, version: 1}\n")

    def fail_prefetch(*args):
        raise OSError("download failed")

    monkeypatch.setattr(RepoData, "prefetch", fail_prefetch)
    monkeypatch.setattr(
        RepoData,
        "needs_prefetch",
        fail_prefetch if failure == "inspection" else lambda self: True,
    )
    calls = []
    monkeypatch.setattr(
        lint.Linter, "lint_one", lambda *args, **kwargs: calls.append("lint") or []
    )

    assert not lint.Linter(Config(), tmp_path).lint([RecipePath(path, CONDA)])
    assert calls == ["lint"]
    expected = (
        "Metadata cache inspection failed"
        if failure == "inspection"
        else "Metadata prefetch failed"
    )
    assert expected in caplog.text
