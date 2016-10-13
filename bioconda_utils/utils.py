#!/usr/bin/env python

import os
import glob
import subprocess as sp
import argparse
import itertools
import sys
import shutil
from collections import defaultdict, Iterable
from itertools import product, chain
import logging
import pkg_resources
import networkx as nx
import requests
from jsonschema import validate

from conda_build.metadata import MetaData
import yaml

from . import docker_utils
from . import pkg_test

logger = logging.getLogger(__name__)


def flatten_dict(dict):
    for key, values in dict.items():
        if isinstance(values, str) or not isinstance(values, Iterable):
            values = [values]
        yield [(key, value) for value in values]


def merged_env(env):
    """
    Merges dict `env` with current os.environ
    """
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

    def __init__(self, env):
        """
        Parameters
        ----------

        env : str or dict
            If str, assume it's a path to a YAML-format filename and load it
            into a dict. If a dict is provided, use it directly.
        """
        if isinstance(env, str):
            with open(env) as f:
                self.env = yaml.load(f)
        else:
            self.env = env
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
        logger.debug(
            "get_recipes(%s, package='%s'): %s", recipe_folder, package, p)
        path = os.path.join(recipe_folder, p)
        yield from map(os.path.dirname,
                       glob.glob(os.path.join(path, "meta.yaml")))
        yield from map(os.path.dirname,
                       glob.glob(os.path.join(path, "*", "meta.yaml")))


def get_channel_packages(channel='bioconda', platform=None):
    """
    Retrieves the existing packages for a channel from conda.anaconda.org

    Parameters
    ----------
    channel : str
        Channel to retrieve packages for

    platform : None | linux | osx
        Platform (OS) to retrieve packages for from `channel`. If None, use the
        currently-detected platform.
    """
    url_template = 'https://conda.anaconda.org/{channel}/{arch}/repodata.json'
    if (
        (platform == 'linux') or
        (platform is None and sys.platform.startswith("linux"))
    ):
        arch = 'linux-64'
    elif (
        (platform == 'osx') or
        (platform is None and sys.platform.startswith("darwin"))
    ):
        arch = 'osx-64'
    else:
        raise ValueError(
            'Unsupported OS: bioconda only supports linux and osx.')

    url = url_template.format(channel=channel, arch=arch)
    repodata = requests.get(url)
    if repodata.status_code != 200:
        raise requests.HTTPError(
            '{0.status_code} {0.reason} for {1}'
            .format(repodata, url))

    noarch_url = url_template.format(channel=channel, arch='noarch')
    noarch_repodata = requests.get(noarch_url)
    if noarch_repodata.status_code != 200:
        raise requests.HTTPError(
            '{0.status_code} {0.reason} for {1}'
            .format(noarch_repodata, noarch_url))

    channel_packages = set(repodata.json()['packages'].keys())
    channel_packages.update(noarch_repodata.json()['packages'].keys())
    return channel_packages


def built_package_path(recipe, env=None):
    """
    Returns the path to which a recipe would be built.

    Does not necessarily exist; equivalent to `conda build --output recipename`
    but without the subprocess.
    """

    m = MetaData(recipe)
    config = m.config
    output_dir = m.info_index()['subdir']
    return os.path.join(
        os.path.dirname(config.bldpkgs_dir), output_dir, '%s.tar.bz2'
        % m.dist()
    )



class Target:
    def __init__(self, pkg, env):
        """
        Class to represent a package built with a particular environment
        (e.g. from EnvMatirix).
        """
        self.pkg = pkg
        self.env = env

    def __hash__(self):
        return self.pkg.__hash__()

    def __eq__(self, other):
        return self.pkg == other.pkg

    def __str__(self):
        return os.path.basename(self.pkg)


def filter_recipes(recipes, env_matrix, channels=None, force=False):
    """
    Generator yielding only those recipes that do not already exist.

    Relies on `conda build --skip-existing` to determine if a recipe already
    exists.

    Parameters
    ----------
    recipes : iterable
        Iterable of candidate recipes

    env_matrix : str, dict, or EnvMatrix
        If str or dict, create an EnvMatrix; if EnvMatrix already use it as-is.

    channels : None or list
        Optional list of channels to check for existing recipes

    force : bool
        Build the package even if it is already available in supplied channels.
    """
    if not isinstance(env_matrix, EnvMatrix):
        env_matrix = EnvMatrix(env_matrix)

    if channels is None:
        channels = []

    channel_packages = set()
    channel_args = []
    for channel in channels:
        channel_packages.update(get_channel_packages(channel=channel))
        channel_args.extend(['--channel', channel])

    logger.debug(sorted(list(channel_packages)))
    def tobuild(f):
        logger.debug('f is %s', f)
        return (
            # note recent conda-build versions do not report "Skipped", so this
            # will primarily be checking for presence in channel_packages.
            not f.startswith("Skipped:") and
            (
                force or (os.path.basename(f) not in channel_packages)
            )
        )

    # conda build no longer supports multiple recipes as input and instead only
    # takes the first.
    def pkgname(recipe, env):
        cmd = [
            "conda", "build", "--no-source", "--override-channels", "--output"
        ] + channel_args + [recipe]
        logger.debug(env)
        logger.debug(cmd)
        p = sp.run(
            cmd,
            check=True,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            universal_newlines=True,
            env=merged_env(env))
        pkgpaths = p.stdout.strip().split("\n")
        assert len(pkgpaths) == 1
        return pkgpaths[0]

    logger.debug('recipes: %s', recipes)
    try:
        for recipe in recipes:
            targets = set()
            for env in env_matrix:
                pkg = pkgname(recipe, env)
                logger.debug(pkg)
                if tobuild(pkg):
                    targets.update([Target(pkg, env)])
            logger.debug(
                "targets for recipe %s: %s",
                recipe, '\n\t' + '\n\t'.join(map(str, targets))
            )
            if targets:
                yield recipe, targets
    except sp.CalledProcessError as e:
        logger.debug(e.stdout)
        logger.error(e.stderr)
        exit(1)


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


def validate_config(config):
    """
    Validate config against schema

    Parameters
    ----------
    config : str or dict
        If str, assume it's a path to YAML file and load it. If dict, use it
        directly.
    """
    if not isinstance(config, dict):
        config = yaml.load(open(config))
    fn = pkg_resources.resource_filename(
        'bioconda_utils', 'config.schema.yaml'
    )
    schema = yaml.load(open(fn))
    validate(config, schema)


def load_config(path):
    validate_config(path)

    if isinstance(path, dict):
        config = path
        relpath = lambda p: p
    else:
        config = yaml.load(open(path))
        relpath = lambda p: os.path.relpath(p, os.path.dirname(path))

    def get_list(key):
        # always return empty list, also if NoneType is defined in yaml
        value = config.get(key)
        if value is None:
            return []
        return value

    default_config = {
        'env_matrix': {'CONDA_PY': '3.5'},
        'blacklists': [],
        'channels': [],
        'docker_url': 'unix://var/run/docker.sock',
        'docker_image': 'condaforge/linux-anvil',
        'requirements': None,
        'upload_channel': 'bioconda'
    }
    if 'env_matrix' in config:
        config['env_matrix'] = relpath(config['env_matrix'])
    if 'blacklists' in config:
        config['blacklists'] = [relpath(p) for p in get_list('blacklists')]
    if 'channels' in config:
        config['channels'] = get_list('channels')

    default_config.update(config)
    return default_config
