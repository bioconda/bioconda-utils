"""Git-limited recipe discovery must not traverse unrelated directories."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bioconda_utils import cli, recipes
from bioconda_utils._types import CONDA, RATTLER, Config, RecipePath
from bioconda_utils.githandler import GitRange


@pytest.fixture
def recipe_collection(tmp_path):
    root = tmp_path / "recipes"
    for directory, filename in (
        ("alpha", "meta.yaml"),
        ("alpha/1.0", "meta.yaml"),
        ("alpha/old/0.5", "recipe.yaml"),
        ("beta", "recipe.yaml"),
        (".hidden", "meta.yaml"),
    ):
        path = root / directory
        path.mkdir(parents=True)
        (path / filename).write_text("package: {}\n")
    return root


@pytest.mark.parametrize(
    "patterns",
    [
        ["*"],
        ["alpha"],
        ["a*"],
        ["?eta"],
        ["[ab]*"],
        ["alpha/*"],
        ["*/1.*"],
        ["alpha/old"],
        ["alpha/**"],
        ["alpha/**/**"],
        ["**/alpha/**"],
        ["**/0.5"],
        ["**/old"],
        ["**"],
        ["**/"],
        ["./alpha/"],
        ["missing"],
        ["alpha", "beta"],
        ["*", "alpha"],
    ],
)
def test_git_selection_preserves_package_patterns(recipe_collection, patterns):
    root = recipe_collection
    all_recipes = list(recipes.get_recipes(root))
    changed_paths = {recipe.path for recipe in all_recipes} - {root / "alpha/1.0"}
    expected = {
        recipe
        for recipe in recipes.get_recipes(root, patterns)
        if recipe.path in changed_paths
    }

    assert (
        set(recipes.get_recipes_from_paths(root, sorted(changed_paths), patterns))
        == expected
    )


@pytest.mark.parametrize("include_blacklisted", [False, True])
def test_git_selection_does_not_walk_or_glob(
    recipe_collection, tmp_path, monkeypatch, caplog, include_blacklisted
):
    root = recipe_collection
    blacklist = tmp_path / "blacklist.txt"
    blacklist.write_text(f"{root / 'beta'}\n")
    config = Config(blacklists=[blacklist])
    # Git selection includes modified recipes and newly unblacklisted paths.
    # A changed build.sh without a recipe file and a deleted recipe are ignored.
    (root / "script-only").mkdir()
    (root / "script-only/build.sh").write_text("echo test\n")
    changed = [
        root / "alpha/1.0",
        root / "beta",
        root / "script-only",
        root / "deleted",
    ]

    def changed_recipes(git_range, recipe_folder):
        assert git_range == GitRange.parse("master...HEAD")
        assert recipe_folder == root
        return changed

    def forbid_traversal(*args, **kwargs):
        pytest.fail("Git-limited selection must not walk or glob directories")

    monkeypatch.setattr(cli, "get_recipes_to_build", changed_recipes)
    monkeypatch.setattr(Path, "walk", forbid_traversal)
    monkeypatch.setattr(Path, "glob", forbid_traversal)
    caplog.set_level("INFO")

    selected = cli.get_recipes(
        config,
        root,
        ["*"],
        GitRange.parse("master...HEAD"),
        include_blacklisted=include_blacklisted,
    )

    expected = [RecipePath(path=root / "alpha/1.0", build_system=CONDA)]
    if include_blacklisted:
        expected.append(RecipePath(path=root / "beta", build_system=RATTLER))
    assert selected == expected
    assert "Considering total" not in caplog.text
    assert f"Processing {len(expected)} recipes" in caplog.text


def test_empty_git_range_does_not_discover_recipes(recipe_collection, monkeypatch):
    monkeypatch.setattr(cli, "get_recipes_to_build", lambda *args: [])

    def forbid_traversal(*args, **kwargs):
        pytest.fail("An empty Git selection must not scan recipes")

    monkeypatch.setattr(Path, "walk", forbid_traversal)
    monkeypatch.setattr(Path, "glob", forbid_traversal)

    assert (
        cli.get_recipes(Config(), recipe_collection, ["*"], GitRange.parse("master"))
        == []
    )


def test_lint_git_range_only_loads_changed_and_unblacklisted_recipes(
    recipe_collection, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("init", "-b", "master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("remote", "add", "origin", "https://github.com/bioconda/bioconda-recipes.git")
    Path("config.yml").write_text("blacklists: [blacklist.txt]\n")
    Path("blacklist.txt").write_text("recipes/beta\n")
    git("add", ".")
    git("commit", "-m", "base recipes")
    git("checkout", "-b", "feature")
    Path("recipes/alpha/1.0/meta.yaml").write_text("package: {version: 2}\n")
    Path("recipes/alpha/old/0.5/recipe.yaml").unlink()
    Path("blacklist.txt").write_text("")
    git("add", ".")
    git("commit", "-m", "update, delete, and unblacklist recipes")

    selected = []

    class CapturingLinter:
        def __init__(self, *args):
            pass

        def lint(self, recipe_paths, **kwargs):
            selected.extend(recipe_paths)
            return False

        def get_messages(self):
            return []

    def forbid_traversal(*args, **kwargs):
        pytest.fail("Lint with --git-range must not walk the recipe collection")

    monkeypatch.setattr("bioconda_utils.lint.Linter", CapturingLinter)
    monkeypatch.setattr(Path, "walk", forbid_traversal)
    monkeypatch.setattr(Path, "glob", forbid_traversal)

    result = CliRunner().invoke(cli.app, ["lint", "--git-range", "master...HEAD"])

    assert result.exit_code == 0, result.output
    assert selected == [
        RecipePath(path=Path("recipes/alpha/1.0"), build_system=CONDA),
        RecipePath(path=Path("recipes/beta"), build_system=RATTLER),
    ]
