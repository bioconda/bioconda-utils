"""Tests for recipe-change detection in GitHandler.

These exercise the name-matching logic in ``get_changed_recipes`` without
touching a real repository: the git plumbing it depends on (``list_changed_files``)
is stubbed so the file-selection rule can be tested directly.
"""

from pathlib import Path

import pytest

from bioconda_utils.githandler import BiocondaRepoMixin


class FakeRepoHandler(BiocondaRepoMixin):
    """BiocondaRepoMixin with the git-dependent listing stubbed out."""

    def __init__(self, changed_files):
        self._changed_files = [Path(p) for p in changed_files]

    def list_changed_files(self, ref=None, other=None):
        return iter(self._changed_files)


@pytest.fixture
def handler():
    return FakeRepoHandler(
        [
            "recipes/one/meta.yaml",
            "recipes/one/build.sh",
            "recipes/two/recipe.yaml",
            "recipes/three/notes.txt",
            "docs/one/meta.yaml",
            "recipes/nested/deep/one/meta.yaml",
        ]
    )


def test_get_changed_recipes_matches_on_file_name_not_full_path(handler):
    """list_changed_files() yields repo-relative paths, so the file names in
    ``files`` must be matched against the path's name."""
    assert set(handler.get_changed_recipes()) == {
        Path("recipes/one"),
        Path("recipes/two"),
        Path("recipes/nested/deep/one"),
    }


def test_get_changed_recipes_ignores_files_outside_recipes_folder(handler):
    """docs/one/meta.yaml is a meta.yaml but lives outside recipes_folder."""
    assert Path("docs/one") not in handler.get_changed_recipes()


def test_get_changed_recipes_ignores_unlisted_file_names(handler):
    """notes.txt is under recipes/ but is not one of the watched files."""
    assert Path("recipes/three") not in handler.get_changed_recipes()


def test_get_changed_recipes_honors_explicit_files_argument():
    handler = FakeRepoHandler(
        ["recipes/one/meta.yaml", "recipes/one/build.sh", "recipes/one/notes.txt"]
    )

    changed = handler.get_changed_recipes(files=[Path("notes.txt")])

    assert set(changed) == {Path("recipes/one")}


def test_get_changed_recipes_returns_empty_when_nothing_changed():
    assert FakeRepoHandler([]).get_changed_recipes() == []
