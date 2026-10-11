"""Tests for build-failure record handling."""

from hashlib import sha256
from types import SimpleNamespace

import networkx as nx

from bioconda_utils import build_failure
from bioconda_utils._types import RATTLER, RecipePath


def test_recipe_sha_uses_rattler_recipe_file(tmp_path):
    from bioconda_utils.build_failure import BuildFailureRecord

    recipe_dir = tmp_path / "foo"
    recipe_dir.mkdir()
    content = "package:\n  name: foo\n"
    (recipe_dir / "recipe.yaml").write_text(content)

    record = BuildFailureRecord(recipe_dir)
    assert record.get_recipe_sha() == sha256(content.encode()).hexdigest()


def test_recipe_sha_prefers_conda_meta_yaml(tmp_path):
    from bioconda_utils.build_failure import BuildFailureRecord

    recipe_dir = tmp_path / "foo"
    recipe_dir.mkdir()
    content = "package:\n  name: foo\n"
    (recipe_dir / "meta.yaml").write_text(content)
    (recipe_dir / "recipe.yaml").write_text("package:\n  name: ignored\n")

    record = BuildFailureRecord(recipe_dir)
    assert record.get_recipe_sha() == sha256(content.encode()).hexdigest()


def test_collect_build_failure_records_supports_rattler_recipes(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_dir = recipe_folder / "foo"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "recipe.yaml").write_text("package:\n  name: foo\n")
    (recipe_dir / "build_failure.linux-64.yaml").write_text("recipe_sha: abc\n")
    recipe = RecipePath(path=recipe_dir, build_system=RATTLER)

    monkeypatch.setattr(build_failure, "get_recipes", lambda _folder: iter([recipe]))
    monkeypatch.setattr(
        build_failure,
        "load_meta_and_recipe_fast",
        lambda _recipe: SimpleNamespace(get_package_name=lambda: "foo"),
    )
    monkeypatch.setattr(
        build_failure.graph,
        "build",
        lambda _recipes, _config: (nx.DiGraph([("foo", "bar")]), {}),
    )
    monkeypatch.setattr(build_failure, "get_package_downloads", lambda *_: 0)

    rows = build_failure.collect_build_failure_records(recipe_folder, {}, "bioconda")

    assert len(rows) == 1
    assert rows[0]["recipe"] == "foo"
    assert rows[0]["depending"] == 1
