"""
Package Builder
"""

import subprocess as sp
from collections import defaultdict, namedtuple
import itertools
import logging
import os
import sys
import time

from typing import List, Optional
from bioconda_utils.skiplist import Skiplist
from bioconda_utils.build_failure import BuildFailureRecord
from bioconda_utils.githandler import GitHandler

from conda.exports import UnsatisfiableError
from conda_build.exceptions import DependencyNeedsBuildingError
import networkx as nx
import pandas
from ruamel.yaml import YAML

from . import utils
from . import docker_utils
from . import pkg_test
from . import upload
from . import lint
from . import graph
from . import recipe as _recipe

logger = logging.getLogger(__name__)


#: Result tuple for builds comprising success status and list of docker images
BuildResult = namedtuple("BuildResult", ["success", "mulled_images"])


def conda_build_purge() -> None:
    """Calls conda build purge and optionally conda clean

    ``conda clean --all`` is called if we haveless than 300 MB free space
    on the current disk.
    """
    utils.run(["conda", "build", "purge"], mask=False)

    free_mb = utils.get_free_space()
    if free_mb < 300:
        logger.info("CLEANING UP PACKAGE CACHE (free space: %iMB).", free_mb)
        utils.run(["conda", "clean", "--all"], mask=False)
        logger.info("CLEANED UP PACKAGE CACHE (free space: %iMB).",
                    utils.get_free_space())


def build(recipe: str, pkg_paths: List[str] = None,
          testonly: bool = False, mulled_test: bool = True,
          channels: List[str] = None,
          docker_builder: docker_utils.RecipeBuilder = None,
          raise_error: bool = False,
          linter=None,
          mulled_conda_image: str = pkg_test.CREATE_ENV_IMAGE,
          record_build_failure: bool = False,
          dag: Optional[nx.DiGraph] = None,
          skiplist_leafs: bool = False,
          live_logs: bool = True) -> BuildResult:
    """
    Build a single recipe for a single env

    Arguments:
      recipe: Path to recipe
      pkg_paths: List of paths to expected packages
      testonly: Only run the tests described in the meta.yaml
      mulled_test: Run tests in minimal docker container
      channels: Channels to include via the ``--channel`` argument to
        conda-build. Higher priority channels should come first.
      docker_builder : docker_utils.RecipeBuilder object
        Use this docker builder to build the recipe, copying over the built
        recipe to the host's conda-bld directory.
      raise_error: Instead of returning a failed build result, raise the
        error instead. Used for testing.
      linter: Linter to use for checking recipes
      record_build_failure: If True, record build failures in a file next to the meta.yaml
      dag: optional nx.DiGraph with dependency information
      skiplist_leafs: If True, blacklist leaf packages that fail to build
      live_logs: If True, enable live logging during the build process
    """
    if record_build_failure and not dag:
        raise ValueError("record_build_failure requires dag to be set")

    if linter:
        logger.info('Linting recipe %s', recipe)
        linter.clear_messages()
        if linter.lint([recipe]):
            logger.error('\n\nThe recipe %s failed linting. See '
                         'https://bioconda.github.io/contributor/linting.html for details:\n\n%s\n',
                         recipe, linter.get_report())
            return BuildResult(False, None)
        logger.info("Lint checks passed")

    # Copy env allowing only whitelisted vars
    whitelisted_env = {
        k: str(v)
        for k, v in os.environ.items()
        if utils.allowed_env_var(k, docker_builder is not None)
    }

    logger.info("BUILD START %s", recipe)

    args = ['--override-channels']
    if testonly:
        args += ["--test"]
    else:
        args += ["--no-anaconda-upload"]

    for channel in channels or ['local']:
        args += ['-c', channel]

    logger.debug('Build and Channel Args: %s', args)

    # Even though there may be variants of the recipe that will be built, we
    # will only be checking attributes that are independent of variants (pkg
    # name, version, noarch, whether or not an extended container was used)
    meta = utils.load_first_metadata(recipe, finalize=False)
    is_noarch = bool(meta.get_value('build/noarch', default=False))
    use_base_image = meta.get_value('extra/container', {}).get('extended-base', False)
    if use_base_image:
        base_image = 'quay.io/bioconda/base-glibc-debian-bash:3.1'
    else:
        base_image = 'quay.io/bioconda/base-glibc-busybox-bash:3.1'

    build_failure_record = BuildFailureRecord(recipe)
    build_failure_record_existed_before_build = build_failure_record.exists()
    if build_failure_record_existed_before_build:
        # remove record to avoid that it is leaked into the package
        build_failure_record.remove()

    try:
        report_resources(f"Starting build for {recipe}", docker_builder is not None)
        if docker_builder is not None:
            docker_builder.build_recipe(recipe_dir=os.path.abspath(recipe),
                                        build_args=' '.join(args),
                                        env=whitelisted_env,
                                        noarch=is_noarch,
                                        live_logs=live_logs)
            # Use presence of expected packages to check for success
            if docker_builder.pkg_dir is not None:
                platform = utils.RepoData.native_platform()
                subfolder = utils.RepoData.platform2subdir(platform)
                conda_build_config = utils.load_conda_build_config(platform=subfolder)
                pkg_paths = [p.replace(conda_build_config.output_folder, docker_builder.pkg_dir) for p in pkg_paths]
            
            for pkg_path in pkg_paths:
                if not os.path.exists(pkg_path):
                    logger.error(
                        "BUILD FAILED: the built package %s "
                        "cannot be found", pkg_path)
                    return BuildResult(False, None)
        else:
            conda_build_cmd = [utils.bin_for('conda-build')]
            # - Temporarily reset os.environ to avoid leaking env vars
            # - Also pass filtered env to run()
            # - Point conda-build to meta.yaml, to avoid building subdirs
            with utils.sandboxed_env(whitelisted_env):
                cmd = conda_build_cmd + args
                for config_file in utils.get_conda_build_config_files():
                    cmd += [config_file.arg, config_file.path]
                cmd += [os.path.join(recipe, 'meta.yaml')]
                with utils.Progress():
                    utils.run(cmd, mask=False, live=live_logs)

        logger.info('BUILD SUCCESS %s',
                    ' '.join(os.path.basename(p) for p in pkg_paths))
        if record_build_failure:
            # Success, hence the record is obsolete. Remove it.
            if build_failure_record_existed_before_build:
                # record is already removed (see above), but change has to be committed
                build_failure_record.commit_and_push_changes()

    except (docker_utils.DockerCalledProcessError, sp.CalledProcessError) as exc:
        logger.error('BUILD FAILED %s', recipe)
        if record_build_failure:
            store_build_failure_record(recipe, exc.output, meta, dag, skiplist_leafs)
        if raise_error:
            raise exc
        return BuildResult(False, None)
    finally:
        report_resources(f"Finished build for {recipe}", docker_builder is not None)

    if mulled_test:
        logger.info('TEST START via mulled-build %s', recipe)
        mulled_images = []
        for pkg_path in pkg_paths:
            try:
                report_resources(f"Starting mulled build for {pkg_path}")
                pkg_test.test_package(pkg_path, base_image=base_image,
                                      conda_image=mulled_conda_image,
                                      live_logs=live_logs)
            except sp.CalledProcessError:
                logger.error('TEST FAILED: %s', recipe)
                return BuildResult(False, None)
            finally:
                report_resources(f"Finished mulled build for {pkg_path}")
            logger.info("TEST SUCCESS %s", recipe)
            mulled_images.append(pkg_test.get_image_name(pkg_path))
        return BuildResult(True, mulled_images)

    return BuildResult(True, None)


def store_build_failure_record(recipe, output, meta, dag, skiplist_leafs):
    """
    Write the exception to a file next to the meta.yaml
    """
    pkg_name = meta.meta["package"]["name"]
    is_leaf = graph.is_leaf(dag, pkg_name)

    build_failure_record = BuildFailureRecord(recipe)
    # if recipe is a leaf (i.e. not used by others as dependency)
    # we can automatically blacklist it if desired
    build_failure_record.fill(log=output, skiplist=skiplist_leafs and is_leaf)

    build_failure_record.write()
    build_failure_record.commit_and_push_changes()


def remove_cycles(dag, name2recipes, failed, skip_dependent):
    nodes_in_cycles = set()
    for cycle in list(nx.simple_cycles(dag)):
        logger.error('BUILD ERROR: dependency cycle found: %s', cycle)
        nodes_in_cycles.update(cycle)

    for name in sorted(nodes_in_cycles):
        cycle_fail_recipes = sorted(name2recipes[name])
        logger.error('BUILD ERROR: cannot build recipes for %s since '
                     'it cyclically depends on other packages in the '
                     'current build job. Failed recipes: %s',
                     name, cycle_fail_recipes)
        failed.extend(cycle_fail_recipes)
        for node in nx.algorithms.descendants(dag, name):
            if node not in nodes_in_cycles:
                skip_dependent[node].extend(cycle_fail_recipes)
    return dag.subgraph(name for name in dag if name not in nodes_in_cycles)


def get_subdags(dag, n_workers, worker_offset, subdag_depth):
    if n_workers > 1 and worker_offset >= n_workers:
        raise ValueError(
            "n-workers is less than the worker-offset given! "
            "Either decrease --n-workers or decrease --worker-offset!")

    # Get connected subdags and sort by nodes
    # If subdag_depth is None, each root node and all children (not previously assigned) are assigned to the same worker. 
    #   This may fail when attempting to build child nodes with parents assigned to other workers.
    # If subdag_depth is set, only nodes of a certain depth will be built (i.e., 0: only root nodes, 
    #   1: only nodes with parents that are root nodes, etc.). They are assigned evenly across workers.
    if n_workers > 1:
        root_nodes = sorted([k for (k, v) in dag.in_degree() if v == 0])
        nodes = set()
        found = set()
        children = []

        if subdag_depth is not None:
            working_dag = nx.DiGraph(dag)
            # Only build the current "root" nodes after removing 
            for i in range(0, subdag_depth + 1):
                print("{} recipes at depth {}".format(len(root_nodes), i))
                if len(root_nodes) == 0:
                    break
                if i < subdag_depth:
                    working_dag.remove_nodes_from(root_nodes)
                    root_nodes = sorted([k for (k, v) in working_dag.in_degree() if v == 0])

        for idx, root_node in enumerate(root_nodes):
            if subdag_depth is None:
                # Flatten the nested list
                children = itertools.chain(*nx.dfs_successors(dag, root_node).values())
            # This is the only obvious way of ensuring that all nodes are included
            # in exactly 1 subgraph
            found.add(root_node)
            if idx % n_workers == worker_offset:
                nodes.add(root_node)
                for child in children:
                    if child not in found:
                        nodes.add(child)
                        found.add(child)
            else:
                for child in children:
                    found.add(child)

        subdags = dag.subgraph(list(nodes))
        logger.info("Building and testing sub-DAGs %i in each group of %i, which is %i packages", worker_offset, n_workers, len(subdags.nodes()))
    else:
        subdags = dag

    return subdags


def do_not_consider_for_additional_platform(recipe_folder: str, recipe: str, platform: str):
    """
    Given a recipe, check this recipe should skip in current platform or not.

    Arguments:
      recipe_folder: Directory containing possibly many, and possibly nested, recipes.
      recipe: Relative path to recipe
      platform: current native platform

    Returns:
      Return True if current native platform are not included in recipe's additional platforms (no need to build).
    """
    recipe_obj = _recipe.Recipe.from_file(recipe_folder, recipe)
    # On linux-aarch64 or osx-arm64 env, only build recipe with matching extra_additional_platforms
    if platform == "linux-aarch64":
        if "linux-aarch64" not in recipe_obj.extra_additional_platforms:
            return True
    if platform == "osx-arm64":
        if "osx-arm64" not in recipe_obj.extra_additional_platforms:
            return True
    return False


def build_recipes(recipe_folder: str, config_path: str, recipes: List[str],
                  mulled_test: bool = True, testonly: bool = False,
                  force: bool = False,
                  docker_builder: docker_utils.RecipeBuilder = None,
                  label: str = None,
                  anaconda_upload: bool = False,
                  mulled_upload_target=None,
                  check_channels: List[str] = None,
                  do_lint: bool = None,
                  lint_exclude: List[str] = None,
                  n_workers: int = 1,
                  worker_offset: int = 0,
                  keep_old_work: bool = False,
                  mulled_conda_image: str = pkg_test.CREATE_ENV_IMAGE,
                  record_build_failures: bool = False,
                  skiplist_leafs: bool = False,
                  live_logs: bool = True,
                  exclude: List[str] = None,
                  subdag_depth: int = None
                  ):
    """
    Build one or many bioconda packages.

    Arguments:
      recipe_folder: Directory containing possibly many, and possibly nested, recipes.
      config_path: Path to config file
      packages: Glob indicating which packages should be considered. Note that packages
        matching the glob will still be filtered out by any blacklists
        specified in the config.
      mulled_test: If true, test the package in a minimal container.
      testonly: If true, only run test.
      force: If true, build the recipe even though it would otherwise be filtered out.
      docker_builder: If specified, use to build all recipes
      label: If specified, use to label uploaded packages on anaconda. Default is "main" label.
      anaconda_upload: If true, upload the package(s) to anaconda.org.
      mulled_upload_target: If specified, upload the mulled docker image to the given target
        on quay.io.
      check_channels: Channels to check to see if packages already exist in them.
        Defaults to every channel in the config file except "defaults".
      do_lint: Whether to run linter
      lint_exclude: List of linting functions to exclude.
      n_workers: The number of parallel instances of bioconda-utils being run. The
        sub-DAGs are then split into groups of n_workers size.
      worker_offset: If n_workers is >1, then every worker_offset within a given group of
        sub-DAGs will be processed.
      keep_old_work: Do not remove anything from environment, even after successful build and test.
      skiplist_leafs: If True, blacklist leaf packages that fail to build
      live_logs: If True, enable live logging during the build process
      exclude: list of recipes to exclude. Typically used for
        temporary exclusion; otherwise consider adding recipe to skiplist.
      subdag_depth: Number of levels of nodes to skip. (Optional, only if using n_workers)
    """
    if not recipes:
        logger.info("Nothing to be done.")
        return True

    config = utils.load_config(config_path)
    blacklist = Skiplist(config, recipe_folder)

    # get channels to check
    if check_channels is None:
        if config['channels']:
            check_channels = [c for c in config['channels'] if c != "defaults"]
        else:
            check_channels = []

    # setup linting
    if do_lint:
        always_exclude = ('build_number_needs_bump',)
        if not lint_exclude:
            lint_exclude = always_exclude
        else:
            lint_exclude = tuple(set(lint_exclude) | set(always_exclude))
        linter = lint.Linter(config, recipe_folder, lint_exclude)
    else:
        linter = None

    failed = []

    dag, name2recipes = graph.build(recipes, config=config_path, blacklist=blacklist)
    if exclude:
        for name in exclude:
            dag.remove_node(name)

    if not dag:
        logger.info("Nothing to be done.")
        return True

    skip_dependent = defaultdict(list)
    dag = remove_cycles(dag, name2recipes, failed, skip_dependent)
    subdag = get_subdags(dag, n_workers, worker_offset, subdag_depth)
    if not subdag:
        logger.info("Nothing to be done.")
        return True
    logger.info("%i recipes to build and test: \n%s", len(subdag), "\n".join(subdag.nodes()))

    recipe2name = {}
    for name, recipe_list in name2recipes.items():
        for recipe in recipe_list:
            recipe2name[recipe] = name

    recipes = [(recipe, recipe2name[recipe])
               for package in nx.topological_sort(subdag)
               for recipe in name2recipes[package]]

    built_recipes = []
    skipped_recipes = []
    failed_uploads = []

    for recipe, name in recipes:
        platform = utils.RepoData().native_platform()
        if not force and do_not_consider_for_additional_platform(recipe_folder, recipe, platform):
            logger.info("BUILD SKIP: skipping %s for additional platform %s", recipe, platform)
            continue

        if name in skip_dependent:
            logger.info('BUILD SKIP: skipping %s because it depends on %s '
                        'which had a failed build.',
                        recipe, skip_dependent[name])
            skipped_recipes.append(recipe)
            continue

        logger.info('Determining expected packages for %s', recipe)
        try:
            pkg_paths = utils.get_package_paths(recipe, check_channels, force=force)
        except utils.DivergentBuildsError as exc:
            logger.error('BUILD ERROR: packages with divergent build strings in repository '
                         'for recipe %s. A build number bump is likely needed: %s',
                         recipe, exc)
            failed.append(recipe)
            for pkg in nx.algorithms.descendants(subdag, name):
                skip_dependent[pkg].append(recipe)
            continue
        except (UnsatisfiableError, DependencyNeedsBuildingError) as exc:
            logger.error('BUILD ERROR: could not determine dependencies for recipe %s: %s',
                         recipe, exc)
            failed.append(recipe)
            for pkg in nx.algorithms.descendants(subdag, name):
                skip_dependent[pkg].append(recipe)
            continue
        if not pkg_paths:
            logger.info("Nothing to be done for recipe %s", recipe)
            continue

        res = build(recipe=recipe,
                    pkg_paths=pkg_paths,
                    testonly=testonly,
                    mulled_test=mulled_test,
                    channels=config['channels'],
                    docker_builder=docker_builder,
                    linter=linter,
                    mulled_conda_image=mulled_conda_image,
                    dag=dag,
                    record_build_failure=record_build_failures,
                    skiplist_leafs=skiplist_leafs,
                    live_logs=live_logs)

        if not res.success:
            failed.append(recipe)
            for pkg in nx.algorithms.descendants(subdag, name):
                skip_dependent[pkg].append(recipe)
        else:
            built_recipes.append(recipe)
            if not testonly:
                if anaconda_upload:
                    for pkg in pkg_paths:
                        if not upload.anaconda_upload(pkg, label=label):
                            failed_uploads.append(pkg)
                if mulled_upload_target:
                    for img in res.mulled_images:
                        upload.mulled_upload(img, mulled_upload_target)
                        docker_utils.purgeImage(mulled_upload_target, img)

        # remove traces of the build
        if not keep_old_work:
            conda_build_purge()
            # prune stopped containers
            if docker_builder is not None:
                docker_utils.pruneStoppedContainers()

    if failed or failed_uploads:
        logger.error('BUILD SUMMARY: of %s recipes, '
                     '%s failed and %s were skipped. '
                     'Details of recipes and environments follow.',
                     len(recipes), len(failed), len(skipped_recipes))
        if built_recipes:
            logger.error('BUILD SUMMARY: while the entire build failed, '
                         'the following recipes were built successfully:\n%s',
                         '\n'.join(built_recipes))
        for recipe in failed:
            logger.error('BUILD SUMMARY: FAILED recipe %s', recipe)
        for name, dep in skip_dependent.items():
            logger.error('BUILD SUMMARY: SKIPPED recipe %s '
                         'due to failed dependencies %s', name, dep)
        if failed_uploads:
            logger.error('UPLOAD SUMMARY: the following packages failed to upload:\n%s',
                         '\n'.join(failed_uploads))
        return False

    logger.info("BUILD SUMMARY: successfully built %s of %s recipes",
                len(built_recipes), len(recipes))
    return True

def report_resources(message, show_docker=True):
    free_space_mb = utils.get_free_space()
    free_mem_mb = utils.get_free_memory_mb()
    free_mem_percent = utils.get_free_memory_percent()
    logger.info("{0} Free disk space: {1:.2f} MB. Free memory: {2:.2f} MB ({3:.2f}%)".format(message, free_space_mb, free_mem_mb, free_mem_percent))
    if 0:
        # Locally, getting errors like:
        # INFO     bioconda_utils.utils:utils.py:636 (COMMAND) podman system df
        # INFO     bioconda_utils.utils:utils.py:664 (ERR) panic: runtime error: invalid memory address or nil pointer dereference
        # INFO     bioconda_utils.utils:utils.py:664 (ERR) [signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0xf3cb02]

        cmd = ['podman', 'system', 'df']
        utils.run(cmd, mask=False, live=True)
        cmd = ['podman', 'ps', '-a']
        utils.run(cmd, mask=False, live=True)
