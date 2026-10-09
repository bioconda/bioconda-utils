"""Upstream release selection and metadata attachment."""

import asyncio

import pytest

from bioconda_utils.autobump import RecipeSource, Scanner, UpdateVersion
from bioconda_utils.recipe import Recipe


@pytest.mark.parametrize(
    "current,upstream",
    [
        ("1.0", {"1.0.0": {"source": {"link": "unused"}}}),
        ("2.0", {"1.0": {}}),
        ("2.0", {"3.0rc1": {}}),
        ("1.0", {"1.0": {"source": {"link": "original"}}}),
    ],
)
def test_no_update_does_not_require_current_release_upstream(
    tmp_path, monkeypatch, current, upstream
):
    recipe = Recipe(tmp_path / "example", tmp_path)
    recipe.load_from_string(
        f'package:\n  name: example\n  version: "{current}"\n'
        "source:\n  url: https://example.org/source.tar.gz\n"
        "build:\n  number: 4\n"
    )
    recipe.set_original()
    original = recipe.dump()
    scanner = Scanner(RecipeSource(tmp_path, ["*"], [], False))
    update = UpdateVersion(scanner)

    async def versions(_recipe):
        return upstream

    monkeypatch.setattr(update, "get_version_map", versions)
    asyncio.run(update.apply(recipe))

    assert recipe.dump() == original
    assert recipe.version_data == upstream.get(current, {})
    assert recipe.orig.version_data == upstream.get(current, {})
