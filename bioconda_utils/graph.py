import logging
import networkx as nx

from collections import defaultdict
from itertools import chain

from . import utils

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def build(recipes, config, blacklist=None, restrict=True):
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

    if blacklist is None:
        blacklist = set()

    # name2recipe is meta.yaml's package:name mapped to the recipe path.
    #
    # A name should map to exactly one recipe. It is possible for multiple
    # names to map to the same recipe, if the package name somehow depends on
    # the environment.
    #
    # Note that this may change once we support conda-build 3.
    name2recipe = defaultdict(set)
    for meta, recipe in metadata:
        name = meta["package"]["name"]
        if name not in blacklist:
            name2recipe[name].update([recipe])

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
    dag.add_nodes_from(recipe.reldir for recipe in recipes)
    dag.add_edges_from(
        (recipe2, recipe)
        for recipe in recipe_list
        for dep in recipe.get_deps()
        for recipe2 in package2recipes.get(dep, [])
    )

    logger.info("Building Recipe DAG: done (%i nodes, %i edges)", len(dag), len(dag.edges()))
    return dag


def filter_recipe_dag(dag, names):
    name_set = set(names)
    nodes = set()
    for recipe in dag:
        if recipe.reldir in name_set and recipe not in nodes:
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


def get_subdag(dag, name2recipes, n, i, ignore_dependencies=False):
    """Return ith of n chunks over dependency graph, such that there is no
       dependency pointing outside of the chunk, unless ignore_dependencies is
       set to True.

       Returns None if there are no nodes left to put into chunk i.
    """
    # Get connected subdags and sort by nodes
    if ignore_dependencies:
        # use each node as a subdag (they are grouped into equal sizes below)
        subdags = sorted([[n] for n in nx.nodes(dag)])
    else:
        # take connected components as subdags, remove cycles
        subdags = []
        for cc_nodes in nx.connected_components(dag.to_undirected()):
            cc = dag.subgraph(sorted(cc_nodes))
            nodes_in_cycles = set()
            for cycle in list(nx.simple_cycles(cc)):
                logger.error(
                    'BUILD ERROR: '
                    'dependency cycle found: %s',
                    cycle,
                )
                nodes_in_cycles.update(cycle)
            for name in sorted(nodes_in_cycles):
                cycle_fail_recipes = sorted(name2recipes[name])
                logger.error(
                    'BUILD ERROR: '
                    'cannot build recipes for %s since it cyclically depends '
                    'on other packages in the current build job. Failed '
                    'recipes: %s',
                    name, cycle_fail_recipes,
                )
                failed.extend(cycle_fail_recipes)
                for n in nx.algorithms.descendants(cc, name):
                    if n in nodes_in_cycles:
                        continue  # don't count packages twice (failed/skipped)
                    skip_dependent[n].extend(cycle_fail_recipes)
            cc_without_cycles = dag.subgraph(
                name for name in cc if name not in nodes_in_cycles
            )
            # ensure that packages which need a build are built in the right order
            subdags.append(nx.topological_sort(cc_without_cycles))
    # chunk subdags such that we have at most subdags_n many
    if n < len(subdags):
        chunks = [[node for subdag in subdags[i::n] for node in subdag]
                  for i in range(n)]
    else:
        chunks = subdags
    if i >= len(chunks):
        return None
    # merge subdags of the selected chunk
    subdag = dag.subgraph(chunks[i])

    return subdag, len(chunks)
