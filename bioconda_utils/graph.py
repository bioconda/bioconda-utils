"""
Construction and Manipulation of Package/Recipe Graphs
"""

import logging

from collections import defaultdict
from fnmatch import fnmatch
from itertools import chain
from typing import Any, Dict, Optional

import networkx as nx

from bioconda_utils.skiplist import Skiplist

from . import utils

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def build(recipes, config: Dict[str, Any], blacklist: Optional[Skiplist]=None, restrict: bool=True):
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
    recipes = list(recipes)
    metadata = list(utils.parallel_iter(utils.load_meta_fast, recipes, "Loading Recipes"))

    # name2recipe is meta.yaml's package:name mapped to the recipe path.
    #
    # A name should map to exactly one recipe. It is possible for multiple
    # names to map to the same recipe, if the package name somehow depends on
    # the environment.
    name2recipe = defaultdict(set)
    subpackage2parent = dict()
    for meta, recipe in metadata:
        name = meta["package"]["name"]
        if blacklist is None or not blacklist.is_skiplisted(recipe):
            name2recipe[name].update([recipe])
        if "outputs" in meta:
            for output in meta["outputs"]:
                subpackage2parent[output["name"]] = name


    def get_deps(meta, sec):
        reqs = meta.get("requirements")
        if not reqs:
            return []
        deps = reqs.get(sec)
        if not deps:
            return []
        return [dep.split()[0] for dep in deps if dep]

    def get_inner_deps(dependencies):
        dependencies = list(dependencies)
        for dep in dependencies:
            if dep in name2recipe or not restrict:
                yield dep
            else:
                parentdep = subpackage2parent.get(dep)
                if parentdep is not None:
                    yield parentdep

    dag = nx.DiGraph()
    dag.add_nodes_from(meta["package"]["name"]
                       for meta, recipe in metadata)
    for meta, recipe in metadata:
        name = meta["package"]["name"]
        dag.add_edges_from(
            (dep, name)
            for dep in set(chain(
                get_inner_deps(get_deps(meta, "build")),
                get_inner_deps(get_deps(meta, "host")),
                get_inner_deps(get_deps(meta, "run")),
            ))
        )

    return dag, name2recipe


def build_from_recipes(recipes):
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

    logger.info("Building Recipe DAG: done (%i nodes, %i edges)", len(dag), len(dag.edges()))
    return dag


def filter_recipe_dag(dag, include, exclude):
    """Reduces **dag** to packages in **names** and their requirements"""
    nodes = set()
    for recipe in dag:
        if (recipe not in nodes
            and any(fnmatch(recipe.reldir, p) for p in include)
            and not any(fnmatch(recipe.reldir, p) for p in exclude)):
            nodes.add(recipe)
            nodes |= nx.ancestors(dag, recipe)
    return nx.subgraph(dag, nodes)


def filter(dag, packages):
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


def is_leaf(dag, pkg_name: str):
    return dag.out_degree(pkg_name) == 0