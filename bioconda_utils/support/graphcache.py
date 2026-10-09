"""Content-keyed graph metadata, independent of skiplist and package selection."""

import importlib.metadata
import json
import logging
import sys
from hashlib import sha256
from pathlib import Path

from ruamel.yaml import CommentedMap

from .. import recipe as recipe_module
from ..recipe import Recipe, RecipeError
from ..recipes import get_recipes
from .caching import file_lock, get_cache_root, read_json, write_json
from .parallel import parallel_iter

logger = logging.getLogger(__name__)


def parser_identity() -> str:
    # Editable installs also invalidate entries when parsing code changes.
    identity = sha256(Path(recipe_module.__file__).read_bytes())
    identity.update(Path(__file__).read_bytes())
    identity.update(str(sys.version_info[:2]).encode())
    for package in ("jinja2", "ruamel.yaml"):
        identity.update(importlib.metadata.version(package).encode())
    return identity.hexdigest()


def metadata_digest(meta) -> str:
    return sha256(json.dumps(meta, separators=(",", ":")).encode()).hexdigest()


def load_graph_recipes(root: Path) -> list[Recipe]:
    """Cache parsed YAML only; scripts, config, skiplists and graph edges stay live."""
    root = root.resolve()
    directory = get_cache_root() / "graph-v1"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha256(str(root).encode()).hexdigest() + ".json")
    with file_lock(path.with_suffix(".lock")):
        stored = read_json(path)
        identity = parser_identity()
        entries = (
            stored.get("entries", {})
            if isinstance(stored, dict) and stored.get("parser") == identity
            else {}
        )
        if not isinstance(entries, dict):
            entries = {}
        current = {}
        recipes = []
        missing = []
        hashes = {}
        for item in get_recipes(root):
            # Autobump currently handles meta.yaml recipes only.
            if not (item.path / "meta.yaml").is_file():
                continue
            relative = item.path.relative_to(root).as_posix()
            digest = sha256((item.path / "meta.yaml").read_bytes()).hexdigest()
            hashes[relative] = digest
            entry = entries.get(relative)
            if (
                isinstance(entry, dict)
                and entry.get("digest") == digest
                and isinstance(entry.get("meta"), dict)
                and isinstance(entry["meta"].get("package"), dict)
                and {"name", "version"} <= entry["meta"]["package"].keys()
                and entry.get("meta_digest") == metadata_digest(entry["meta"])
            ):
                recipe = Recipe(item.path, root)
                recipe.meta = CommentedMap(entry["meta"])
                # These files affect pinning checks, not graph parsing. Read them
                # afresh even when metadata was reused.
                current[relative] = entry
                try:
                    recipe.read_conda_build_config()
                    recipe.read_build_scripts()
                except (OSError, UnicodeError) as exc:
                    logger.error("Could not load recipe %s: %s", recipe, exc)
                    continue
                recipes.append(recipe)
            else:
                missing.append(item.path)
        reused = len(recipes)
        for recipe in (
            parallel_iter(
                Recipe.from_file,
                missing,
                "Loading changed recipes",
                root,
                return_exceptions=True,
            )
            if missing
            else ()
        ):
            if isinstance(recipe, RecipeError):
                recipe.log()
            elif isinstance(recipe, Exception):
                logger.error("Could not load recipe: %s", recipe)
            else:
                relative = recipe.reldir.as_posix()
                # Do not publish a parse if its input changed during parsing.
                if sha256(recipe.path.read_bytes()).hexdigest() != hashes[relative]:
                    raise RuntimeError(
                        f"Recipe changed while constructing graph: {recipe}"
                    )
                current[relative] = {
                    "digest": hashes[relative],
                    "meta": recipe.meta,
                    "meta_digest": metadata_digest(recipe.meta),
                }
                recipes.append(recipe)
        # Deleted recipes disappear; unparseable recipes are retried next time.
        write_json(path, {"parser": identity, "entries": current})
        logger.info(
            "Graph metadata: %i reused, %i parsed",
            reused,
            len(missing),
        )
        return recipes
