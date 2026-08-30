"""
Construction and Manipulation of Package/Recipe Graphs
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from fnmatch import fnmatch
from itertools import chain
from token import ISTERMINAL
from typing import (
    Any,
    Literal,
)

import networkx as nx
import rattler_build as rb
from conda_build.build import render_recipe
from regex import R

from bioconda_utils.recipe import Recipe
from bioconda_utils.skiplist import Skiplist

from . import utils

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def build(
    recipes: Iterable[utils.RecipePath],
    config: dict[str, Any],
    blacklist: Skiplist | None = None,
    restrict: bool = True,
) -> tuple[nx.DiGraph, defaultdict[str, set[utils.RecipePath]]]:
    """
    Returns the DAG of recipe paths and a dictionary that maps package names to
    lists of recipe paths to all defined versions of the package.  defined
    versions.

    Parameters
    ----------
    recipes : iterable
        An iterable of recipe paths, typically obtained via `get_recipes()`

    blacklist : set
        Package names to skip

    restrict : bool
        If True, then dependencies will be included in the DAG only if they are
        themselves in `recipes`. Otherwise, include all dependencies of
        `recipes`.

    Returns
    -------
    dag : nx.DiGraph
        Directed graph of packages -- nodes are package names; edges are
        dependencies (both run and build dependencies)

    name2recipe : dict
        Dictionary mapping package names to recipe paths. These recipe path
        values are lists and contain paths to all defined versions.
    """
    logger.info("Generating DAG")
    recipes: list[utils.RecipePath] = list(recipes)
    # TODO (rb): is it possible to load global variants here and pass them on?
    # it seems that it doesn't work because utils.parallel_iter wants to pickle them
    # which fails
    #
    # global_variants: rb.VariantConfig = utils.load_rattler_build_global_variants()

    meta_rattler_data: list[utils.MetaOrRattler] = list(
        utils.parallel_iter(
            utils.load_meta_and_recipe_fast,
            recipes,
            "Loading Recipes",
        )
    )

    # name2recipe is meta.yaml's / recipe.yaml's package:name mapped to the recipe path.
    #
    # A name should map to exactly one recipe. It is possible for multiple
    # names to map to the same recipe, if the package name somehow depends on
    # the environment.
    name2recipe: defaultdict[str, set[utils.RecipePath]] = defaultdict(set)

    for rendered_recipe in meta_rattler_data:
        name: str = rendered_recipe.get_package_name()

        if blacklist is None or not blacklist.is_skiplisted(rendered_recipe.path.path):
            name2recipe[name].update([rendered_recipe.path])

    def get_inner_deps(dependencies: Iterable[str]) -> Iterable[str]:
        dependencies = list(dependencies)
        for dep in dependencies:
            if dep in name2recipe or not restrict:
                yield dep

    # TODO (rb): is it more efficient to merge this with the loop above?
    dag: nx.DiGraph = nx.DiGraph()
    dag.add_nodes_from(name2recipe.keys())
    for rendered_recipe in meta_rattler_data:
        name: str = rendered_recipe.get_package_name()
        dag.add_edges_from(
            (dep, name)
            for dep in set(
                chain(
                    get_inner_deps(rendered_recipe.get_dependencies("build")),
                    get_inner_deps(rendered_recipe.get_dependencies("host")),
                    get_inner_deps(rendered_recipe.get_dependencies("run")),
                )
            )
        )
    return dag, name2recipe


def build_from_recipes(recipes: Iterable[Recipe]) -> nx.DiGraph:
    logger.info("Building Recipe DAG")

    package2recipes = {}
    recipe_list = []
    for recipe in recipes:
        for package in recipe.package_names:
            package2recipes.setdefault(package, set()).add(recipe)
        recipe_list.append(recipe)

    dag = nx.DiGraph()
    dag.add_nodes_from(recipe for recipe in recipe_list)
    dag.add_edges_from(
        (recipe2, recipe)
        for recipe in recipe_list
        for dep in recipe.get_deps()
        for recipe2 in package2recipes.get(dep, [])
    )

    logger.info(
        "Building Recipe DAG: done (%i nodes, %i edges)",
        len(dag),
        len(dag.edges()),
    )
    return dag


def filter_recipe_dag(
    dag: nx.DiGraph, include: Sequence[str], exclude: Sequence[str]
) -> nx.DiGraph:
    """Reduces **dag** to packages in **names** and their requirements"""
    nodes = set()
    for recipe in dag:
        if (
            recipe not in nodes
            and any(fnmatch(recipe.reldir, p) for p in include)
            and not any(fnmatch(recipe.reldir, p) for p in exclude)
        ):
            nodes.add(recipe)
            nodes |= nx.ancestors(dag, recipe)
    return nx.subgraph(dag, nodes)


def filter(dag: nx.DiGraph, packages: Iterable[str]) -> nx.DiGraph:
    nodes = set()
    for package in packages:
        if package in nodes:
            continue  # already got all ancestors
        nodes.add(package)
        try:
            nodes |= nx.ancestors(dag, package)
        except nx.exception.NetworkXError:
            if package not in nx.nodes(dag):
                logger.error("Can't find %s in dag", package)
            else:
                raise

    return nx.subgraph(dag, nodes)


def is_leaf(dag: nx.DiGraph, pkg_name: str) -> bool:
    return dag.out_degree(pkg_name) == 0
