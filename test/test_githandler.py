"""Tests for Git path handling."""

from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bioconda_utils.githandler import GitHandlerBase


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
