"""Tests for Git path handling."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bioconda_utils.githandler import BiocondaRepoMixin, GitHandlerBase


class Tree:
    def __init__(self):
        self.requested = None

    def __truediv__(self, path):
        self.requested = path
        return SimpleNamespace(data_stream=BytesIO(b"contents"))


def make_handler(repo_root):
    handler = object.__new__(GitHandlerBase)
    cast(Any, handler).repo = SimpleNamespace(working_dir=str(repo_root))
    return handler


def test_read_from_branch_uses_repository_relative_path(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    tree = Tree()
    branch = SimpleNamespace(commit=SimpleNamespace(tree=tree))
    handler = make_handler(repo_root)

    result = handler.read_from_branch(branch, repo_root / "nested" / "file.txt")

    assert result == "contents"
    assert tree.requested == "nested/file.txt"


def test_read_from_branch_rejects_sibling_with_common_prefix(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    sibling = tmp_path / "repository" / "file.txt"
    branch = SimpleNamespace(commit=SimpleNamespace(tree=Tree()))
    handler = make_handler(repo_root)

    with pytest.raises(RuntimeError, match="not inside"):
        handler.read_from_branch(branch, sibling)


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
