#!/usr/bin/env python

import os
import glob
import subprocess as sp
import argparse
import sys
import shutil
from collections import defaultdict, Iterable
from itertools import product, chain
import logging
import pkg_resources
import networkx as nx

from conda_build.metadata import MetaData
import yaml

logger = logging.getLogger(__name__)

def flatten_dict(dict):
    for key, values in dict.items():
        if isinstance(values, str) or not isinstance(values, Iterable):
            values = [values]
        yield [(key, value) for value in values]


def merged_env(env):
    _env = dict(os.environ)
    _env.update(env)
    return _env


class EnvMatrix:
    """
    Intended to be initialized with a YAML file and iterated over to yield all
    combinations of environments.

    YAML file has the following format::

        CONDA_PY:
          - "2.7"
          - "3.5"
        CONDA_BOOST: "1.60"
        CONDA_PERL: "5.22.0"
        CONDA_NPY: "110"
        CONDA_NCURSES: "5.9"
        CONDA_GSL: "1.16"

    """

    def __init__(self, path):
        with open(path) as f:
            self.env = yaml.load(f)
            for key, val in self.env.items():
                if key != "CONDA_PY" and not isinstance(val, str):
                    raise ValueError(
                        "All versions except CONDA_PY must be strings.")

    def __iter__(self):
        """
        Given the YAML::

            CONDA_PY:
              - "2.7"
              - "3.5"
            CONDA_BOOST: "1.60"
            CONDA_NPY: "110"

        We get the following sets of env vars::

          [('CONDA_BOOST', '1.60'), ('CONDA_PY', '2.7'), ('CONDA_NPY', '110')]
          [('CONDA_BOOST', '1.60'), ('CONDA_PY', '3.5'), ('CONDA_NPY', '110')]

        A copy of the entire os.environ dict is updated and yielded for each of
        these sets.
        """
        for env in product(*flatten_dict(self.env)):
            yield env


def get_deps(recipe, build=True):
    """
    Generator of dependencies for a single recipe

    Only names (not versions) of dependencies are yielded.

    Parameters
    ----------
    recipe : str or MetaData
        If string, it is a path to the recipe; otherwise assume it is a parsed
        conda_build.metadata.MetaData instance.

    build : bool
        If True yield build dependencies, if False yield run dependencies.
    """
    if isinstance(recipe, str):
        metadata = MetaData(recipe)
    else:
        metadata = recipe
    for dep in metadata.get_value(
            "requirements/{}".format("build" if build else "run"), []):
        yield dep.split()[0]


def get_dag(recipes, blacklist=None, restrict=True):
    """
    Returns the DAG of recipe paths and a dictionary that maps package names to
    recipe paths. These recipe path values are lists and contain paths to all
    defined versions.

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
    recipes = list(recipes)
    metadata = [MetaData(recipe) for recipe in recipes]
    if blacklist is None:
        blacklist = set()

    # meta.yaml's package:name mapped to the recipe path
    name2recipe = defaultdict(list)
    for meta, recipe in zip(metadata, recipes):
        name = meta.get_value('package/name')
        if name not in blacklist:
            name2recipe[name].append(recipe)

    def get_inner_deps(dependencies):
        for dep in dependencies:
            name = dep.split()[0]
            if name in name2recipe or not restrict:
                yield name

    dag = nx.DiGraph()
    dag.add_nodes_from(meta.get_value("package/name") for meta in metadata)
    for meta in metadata:
        name = meta.get_value("package/name")
        dag.add_edges_from((dep, name)
                           for dep in set(get_inner_deps(chain(
                               get_deps(meta),
                               get_deps(meta,
                                        build=False)))))

    #nx.relabel_nodes(dag, name2recipe, copy=False)
    return dag, name2recipe


def get_recipes(recipe_folder, package="*"):
    """
    Generator of recipes.

    Finds (possibly nested) directories containing a `meta.yaml` file.

    Parameters
    ----------
    recipe_folder : str
        Top-level dir of the recipes

    package : str or iterable
        Pattern or patterns to restrict the results.
    """
    if isinstance(package, str):
        package = [package]
    for p in package:
        logger.debug("get_recipes(%s, package='%s'): %s", recipe_folder, package, p)
        path = os.path.join(recipe_folder, p)
        yield from map(os.path.dirname,
                       glob.glob(os.path.join(path, "meta.yaml")))
        yield from map(os.path.dirname,
                       glob.glob(os.path.join(path, "*", "meta.yaml")))


def filter_recipes(recipes, env_matrix, config):
    """
    Generator yielding only those recipes that do not already exist.

    Relies on `conda build --skip-existing` to determine if a recipe already
    exists.

    Parameters
    ----------
    recipes : iterable
        Iterable of candidate recipes

    env_matrix : EnvMatrix
    """

    channel_args = []
    for c in config['channels']:
        channel_args.extend(['--channel', c])

    # Given the startup time of conda-build, provide all recipes at once.
    def msgs(env):
        logger.debug(env)
        cmds = ["conda", "build", "--skip-existing", "--override-channels", "--output", "--dirty"] + channel_args + recipes
        p = sp.run(
            cmds,
            check=True,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            universal_newlines=True,
            env=merged_env(env))
        return [
            msg
            for msg in p.stdout.strip().split("\n") if "Ignoring non-recipe" not in msg
        ]
    skip = lambda msg: \
        "already built" in msg or "defines build/skip" in msg

    try:
        for item in zip(recipes, *map(msgs, env_matrix)):
            recipe = item[0]
            msg = item[1:]
            logger.debug("recipe %s: %s", recipe, '\n\t' + '\n\t'.join(msg))
            if not all(map(skip, msg)):
                yield recipe
    except sp.CalledProcessError as e:
        logger.error("%s" % e.stderr)
        exit(1)


def build(recipe,
          recipe_folder,
          env,
          config,
          testonly=False,
          force=False,
          channels=None,
          docker=None):
    """
    Build a single recipe for a single env

    Parameters
    ----------
    recipe : str
        Path to recipe

    env : dict
        Environment (typically a single yielded dictionary from EnvMatrix
        instance)

    config : dict
        Configuration dictionary.

    testonly : bool
        If True, skip building and instead run the test described in the
        meta.yaml.

    force : bool
        If True, the recipe will be built even if it already exists. Note that
        typically you'd want to bump the build number rather than force
        a build.

    channels : list
        Channels to include via the `--channel` argument to conda-build

    docker : docker.Client object
        Run docker container using this client
    """
    logger.info("Building and testing '%s' for environment %s", recipe, ';'.join(['='.join(i) for i in sorted(env)]))
    build_args = []
    if testonly:
        build_args.append("--test")
    else:
        build_args += ["--no-anaconda-upload"]
    if not force:
        build_args += ["--skip-existing"]

    channel_args = []
    for c in config['channels']:
        channel_args.extend(['--channel', c])

    if docker is not None:
        conda_build_folder = os.path.join(os.path.dirname(os.path.dirname(shutil.which("conda"))), "conda-bld")
        logger.info("Using client's conda-build folder: %s", conda_build_folder)
        recipe = os.path.relpath(recipe, recipe_folder)
        binds={
            pkg_resources.resource_filename('bioconda_utils', 'bioconda_startup.sh'): {
                "bind": "/opt/share/bioconda_startup.sh",
                "mode": "ro"
            },
            os.path.abspath(recipe_folder): {
                "bind": "/opt/recipes",
                "mode": "ro"
            },
        }
        for subdir in ['svn-cache', 'hg-cache', 'git-cache', 'src-cache', 'linux-64']:
            binds[os.path.join(conda_build_folder, subdir)] = {
                'bind': os.path.join('/opt/miniconda/conda-bld/', subdir),
                'mode': 'rw'
            }
        env = dict(env)
        env["ABI"] = 4
        logger.debug('Docker binds: %s', binds)
        logger.debug('Docker env: %s', env)

        user_id = 1000  #os.getuid()
        command = ("bash /opt/share/bioconda_startup.sh {uid} "
                "conda build {args} --override-channels --quiet recipes/{recipe}".format(
                    uid=user_id, args=' '.join(channel_args), recipe=recipe))
        logger.debug('Docker command: %s', command)
        container = docker.create_container(
            image=config['docker_image'],
            volumes=["/opt/recipes", "/opt/miniconda"],
            user=user_id,
            environment=env,
            command=command,
            host_config=docker.create_host_config(binds=binds, network_mode="host"))
        cid = container["Id"]
        docker.start(container=cid)
        status = docker.wait(container=cid)
        logger.debug('Docker status: %s', status)
        if status != 0:
            logger.error(docker.logs(container=cid, stdout=True, stderr=True).decode())
            return False
        logger.info('Successfully built %s', recipe)
        return True
    else:
        try:
            out = None if logger.level <= logging.DEBUG else sp.PIPE
            sp.run(["conda", "build", "--quiet", "--override-channels", recipe] + build_args + channel_args,
                   stderr=out,
                   stdout=out,
                   check=True,
                   universal_newlines=True,
                   env=merged_env(env))
            logger.info('Successfully built %s', recipe)
            return True
        except sp.CalledProcessError as e:
            if e.stdout is not None:
                logger.error(e.stdout)
                logger.error(e.stderr)
            return False


def test_recipes(recipe_folder,
                 config,
                 packages="*",
                 testonly=False,
                 force=False,
                 docker=None):
    """
    Build one or many bioconda packages.
    """
    config = load_config(config)
    env_matrix = EnvMatrix(config['env_matrix'])
    blacklist = get_blacklist(config['blacklists'], recipe_folder)

    logger.info('blacklist: %s', ', '.join(sorted(blacklist)))

    if packages == "*":
        packages = ["*"]
    recipes = []
    for package in packages:
        for recipe in get_recipes(recipe_folder, package):
            if os.path.relpath(recipe, recipe_folder) in  blacklist:
                logger.debug('blacklisted: %s', recipe)
                continue
            recipes.append(recipe)
            logger.debug(recipe)
    if not recipes:
        logger.info("Nothing to be done.")
        return

    if not force:
        logger.info('Filtering recipes')
        recipes = list(filter_recipes(recipes, env_matrix, config))


    dag, name2recipes = get_dag(recipes, blacklist=blacklist)

    if not dag:
        logger.info("Nothing to be done.")
        return True
    else:
        logger.info("Building and testing %s recipes in total", len(dag))
        logger.info("Recipes to build: %s", "\n".join(dag.nodes()))

    subdags_n = int(os.environ.get("SUBDAGS", 1))
    subdag_i = int(os.environ.get("SUBDAG", 0))

    # Get connected subdags and sort by nodes
    if testonly:
        # use each node as a subdag (they are grouped into equal sizes below)
        subdags = sorted([[n] for n in nx.nodes(dag)])
    else:
        # take connected components as subdags
        subdags = sorted(map(sorted, nx.connected_components(dag.to_undirected(
        ))))
    # chunk subdags such that we have at most subdags_n many
    if subdags_n < len(subdags):
        chunks = [[n for subdag in subdags[i::subdags_n] for n in subdag]
                  for i in range(subdags_n)]
    else:
        chunks = subdags
    if subdag_i >= len(chunks):
        logger.info("Nothing to be done.")
        return True
    # merge subdags of the selected chunk
    subdag = dag.subgraph(chunks[subdag_i])
    # ensure that packages which need a build are built in the right order
    recipes = [recipe
               for package in nx.topological_sort(subdag)
               for recipe in name2recipes[package]]

    logger.info("Building and testing subdag %s of %s (%s recipes)", subdag_i, subdags_n, len(recipes))

    if docker is not None:
        logger.info('Pulling docker image...')
        docker.pull(config['docker_image'])
        logger.info('Done.')

    success = True
    for recipe in recipes:
        envs = iter(env_matrix)
        # Use only first envionment if package does not depend on Python.
        if "python" not in get_deps(recipe):
            envs = [next(envs)]
        for env in envs:
            success &= build(recipe,
                             recipe_folder,
                             env,
                             config,
                             testonly,
                             force,
                             docker=docker)

    if not testonly:
        # upload builds
        if (os.environ.get("TRAVIS_BRANCH") == "master" and
                os.environ.get("TRAVIS_PULL_REQUEST") == "false"):
            for recipe in recipes:
                packages = {
                    sp.run(
                        ["conda", "build", "--override-channels", "--output", "--dirty", recipe],
                        stdout=sp.PIPE,
                        env=merged_env(env),
                        check=True).stdout.strip().decode()
                    for env in env_matrix
                }
                for package in packages:
                    if os.path.exists(package):
                        try:
                            labels = config['labels']
                            labels = ['-l'] + labels if labels else []
                            sp.run(
                                ["anaconda", "-t",
                                 os.environ.get("ANACONDA_TOKEN"), "upload"] +
                                 labels + [package],
                                stdout=sp.PIPE,
                                stderr=sp.STDOUT,
                                check=True)
                        except sp.CalledProcessError as e:
                            print(e.stdout.decode(), file=sys.stderr)
                            if b"already exists" in e.stdout:
                                # ignore error assuming that it is caused by
                                # existing package
                                pass
                            else:
                                raise e
    return success


def get_blacklist(blacklists, recipe_folder):
    "Return list of recipes to skip from blacklists"
    blacklist = set()
    for p in blacklists:
        blacklist.update(
            [
                os.path.relpath(i.strip(), recipe_folder)
                for i in open(p) if not i.startswith('#') and i.strip()
            ]
        )
    return blacklist


def load_config(path):
    relpath = lambda p: os.path.relpath(p, os.path.dirname(path))
    config = yaml.load(open(path))

    def get_list(key):
        # always return empty list, also if NoneType is defined in yaml
        value = config.get(key)
        if value is None:
            return []
        return value

    config['env_matrix'] = relpath(config['env_matrix'])
    config['blacklists'] = [relpath(p) for p in get_list('blacklists')]
    config['index_dirs'] = [relpath(p) for p in get_list('index_dirs')]
    config['channels'] = get_list('channels')
    config['labels'] = get_list('labels')
    return config
