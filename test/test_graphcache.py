"""Graph cache invalidation must preserve current dependencies and policy."""

import pytest

from bioconda_utils import graph
from bioconda_utils.support import graphcache
from bioconda_utils.support.caching import get_cache_root


def write_recipe(root, name, deps=(), enabled=True):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.yaml").write_text(
        f"package:\n  name: {name}\n  version: '1'\n"
        "build:\n  number: 0\nrequirements:\n  run:\n"
        + "".join(f"    - {dep}\n" for dep in deps)
        + f"extra:\n  autobump:\n    enable: {str(enabled).lower()}\n"
    )


@pytest.fixture
def collection(tmp_path, monkeypatch):
    # Parse synchronously in these tests; real spawned parsing is exercised by
    # CLI tests and the full-checkout benchmark.
    def parse(func, items, description, *args, **kwargs):
        return (func(*args, item, **kwargs) for item in items)

    monkeypatch.setattr(graphcache, "parallel_iter", parse)
    root = tmp_path / "recipes"
    write_recipe(root, "library")
    write_recipe(root, "consumer", ["library"])
    return root


def edges(recipes):
    return {(str(a), str(b)) for a, b in graph.build_from_recipes(recipes).edges}


def test_cached_graph_equals_fresh_and_does_not_reparse(collection, monkeypatch):
    first = graphcache.load_graph_recipes(collection)
    assert edges(first) == {("library", "consumer")}
    monkeypatch.setattr(
        graphcache, "parallel_iter", lambda *a, **k: pytest.fail("reparsed")
    )
    second = graphcache.load_graph_recipes(collection)
    assert edges(second) == edges(first)
    assert {str(r): r.meta for r in second} == {str(r): r.meta for r in first}
    assert all(not r.is_modified() for r in second)


def test_content_changes_additions_and_deletions_invalidate_graph(collection):
    graphcache.load_graph_recipes(collection)
    write_recipe(collection, "consumer", ["new-library"], enabled=False)
    write_recipe(collection, "new-library")
    (collection / "library/meta.yaml").unlink()
    (collection / "library").rmdir()
    recipes = graphcache.load_graph_recipes(collection)
    assert edges(recipes) == {("new-library", "consumer")}
    consumer = next(r for r in recipes if str(r) == "consumer")
    assert consumer.get("extra/autobump/enable") is False


def test_scripts_and_pinning_config_are_always_current(collection):
    graphcache.load_graph_recipes(collection)
    (collection / "consumer/build.sh").write_text("new build script")
    (collection / "consumer/conda_build_config.yaml").write_text("new config")
    consumer = next(
        r for r in graphcache.load_graph_recipes(collection) if str(r) == "consumer"
    )
    assert consumer.build_scripts == {"build.sh": "new build script"}
    assert consumer.conda_build_config == "new config"


@pytest.mark.parametrize("corrupt", [False, True])
def test_parser_change_or_corruption_reparses(collection, monkeypatch, corrupt):
    graphcache.load_graph_recipes(collection)
    if corrupt:
        next((get_cache_root() / "graph-v1").glob("*.json")).write_text("broken {")
    else:
        monkeypatch.setattr(graphcache, "parser_identity", lambda: "changed-parser")
    calls = []
    original = graphcache.parallel_iter

    def parse(*args, **kwargs):
        calls.extend(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(graphcache, "parallel_iter", parse)
    assert edges(graphcache.load_graph_recipes(collection)) == {("library", "consumer")}
    assert len(calls) == 2


def test_skiplists_are_applied_after_metadata_cache(collection):
    from bioconda_utils.autobump import RecipeGraphSource
    from bioconda_utils.build_failure import BuildFailureRecord

    source = RecipeGraphSource.__new__(RecipeGraphSource)
    source.recipe_base = collection
    source.config = {"channels": [], "blacklists": []}
    assert len(source.load_graph()) == 2
    failure = BuildFailureRecord(collection / "consumer")
    failure.fill(skiplist=True)
    failure.write()
    assert len(source.load_graph()) == 1
    failure.remove()
    assert len(source.load_graph()) == 2


def test_same_named_recipes_in_different_roots_do_not_share_entries(
    collection, tmp_path
):
    graphcache.load_graph_recipes(collection)
    other = tmp_path / "other"
    write_recipe(other, "consumer", ["different"])
    assert graphcache.load_graph_recipes(other)[0].get_deps() == ["different"]


def test_metadata_bitrot_is_reparsed(collection):
    import json

    graphcache.load_graph_recipes(collection)
    path = next((get_cache_root() / "graph-v1").glob("*.json"))
    stored = json.loads(path.read_text())
    stored["entries"]["consumer"]["meta"]["requirements"]["run"] = ["wrong"]
    path.write_text(json.dumps(stored))
    assert edges(graphcache.load_graph_recipes(collection)) == {("library", "consumer")}


def test_changed_input_during_parse_is_not_published(collection, monkeypatch):
    original = graphcache.parallel_iter

    def parse(*args, **kwargs):
        for recipe in original(*args, **kwargs):
            recipe.path.write_text(
                recipe.path.read_text() + "\n# changed while parsing\n"
            )
            yield recipe

    monkeypatch.setattr(graphcache, "parallel_iter", parse)
    with pytest.raises(RuntimeError, match="changed while constructing graph"):
        graphcache.load_graph_recipes(collection)
    assert not list((get_cache_root() / "graph-v1").glob("*.json"))
