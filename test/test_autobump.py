"""Upstream release selection and metadata attachment."""

import asyncio

import pytest

from bioconda_utils.autobump import (
    RecipeSource,
    Scanner,
    UpdateChecksums,
    UpdateVersion,
)
from bioconda_utils.recipe import Recipe


@pytest.mark.parametrize("valid_mirror", [False, True])
def test_checksum_update_handles_unsupported_url_scheme(
    tmp_path, monkeypatch, valid_mirror
):
    old_checksum, new_checksum = "a" * 64, "b" * 64
    recipe = Recipe(tmp_path / "example", tmp_path)
    recipe.load_from_string(
        "package:\n  name: example\n  version: '1.0'\n"
        "build:\n  number: 0\n"
        "source:\n  url:\n    - s3://bucket/source.tgz\n"
        + ("    - https://example.org/source.tgz\n" if valid_mirror else "")
        + f"  sha256: {old_checksum}\n"
    )
    scanner = Scanner(RecipeSource(tmp_path, ["*"], [], False))
    update = UpdateChecksums(scanner)

    async def checksum(url, desc):
        return new_checksum

    monkeypatch.setattr(scanner.req, "get_checksum_from_http", checksum)
    operation = update.update_source(recipe, recipe.meta["source"], 0)
    if valid_mirror:
        asyncio.run(operation)
        recipe.render()
        assert recipe.meta["source"]["sha256"] == new_checksum
    else:
        with pytest.raises(UpdateChecksums.NoValidUrls):
            asyncio.run(operation)
    assert update.failed_urls == ["unsupported scheme\ts3://bucket/source.tgz"]


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
