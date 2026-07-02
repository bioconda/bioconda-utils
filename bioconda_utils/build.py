"""
Package Builder
"""

from __future__ import annotations

import subprocess as sp
from collections.abc import Sequence
from collections import defaultdict
import itertools
import logging
import os
from pathlib import Path

from typing import Any, NamedTuple
from bioconda_utils.skiplist import Skiplist
from bioconda_utils.build_failure import BuildFailureRecord

from conda.exports import UnsatisfiableError
from conda_build.exceptions import DependencyNeedsBuildingError
from conda_build.metadata import MetaData
import networkx as nx

from . import utils
from . import docker_utils
from . import pkg_test
from . import upload
from . import lint
from . import graph
from . import recipe as _recipe
from ._types import (
    ContainerPlatform,
    PACKAGE_SUBDIRS,
    PackageSubdir,
    PkgBuildRef,
    QuayUploadTarget,
    container_platform_is_native,
    native_container_platform,
)
from .container_manifests import write_image_record

logger = logging.getLogger(__name__)


class BuildResult(NamedTuple):
    """Result tuple for builds comprising success status and docker images."""

    success: bool
    mulled_images: list[MulledImage] | None


class MulledImage(NamedTuple):
    """Mulled image metadata for one built package and target platform."""

    pkg_ref: PkgBuildRef
    target_platform: ContainerPlatform | None


def mulled_image_metadata(
    pkg_ref: PkgBuildRef,
    target_platform: ContainerPlatform | None = None,
) -> MulledImage:
    """Return mulled image metadata for a built package."""
    return MulledImage(
        pkg_ref=pkg_ref,
        target_platform=target_platform or native_container_platform(),
    )


def conda_build_purge() -> None:
    """Calls conda build purge and optionally conda clean

    ``conda clean --all`` is called if we haveless than 300 MB free space
    on the current disk.
    """
    utils.run(["conda", "build", "purge"], redacted_secrets=False)

    free_mb = utils.get_free_space()
    if free_mb < 300:
        logger.info("CLEANING UP PACKAGE CACHE (free space: %iMB).", free_mb)
        utils.run(["conda", "clean", "--all"], redacted_secrets=False)
        logger.info(
            "CLEANED UP PACKAGE CACHE (free space: %iMB).",
            utils.get_free_space(),
        )


def build(
    recipe: str,
    pkg_paths: list[str] | None = None,
    testonly: bool = False,
    mulled_build_and_test: bool = True,
    channels: list[str] | None = None,
    docker_builder: docker_utils.RecipeBuilder | None = None,
    raise_error: bool = False,
    linter: lint.Linter | None = None,
    mulled_conda_image: str = pkg_test.CREATE_ENV_IMAGE,
    record_build_failure: bool = False,
    dag: nx.DiGraph | None = None,
    skiplist_leaves: bool = False,
    live_logs: bool = True,
    presolved_mulled_build_and_test: bool = True,
    mulled_upload_target: QuayUploadTarget | None = None,
    container_platforms: Sequence[ContainerPlatform] | None = None,
    use_existing_auth: bool = False,
) -> BuildResult:
    """
    Build a single recipe for a single env

    Arguments:
      recipe: Path to recipe
      pkg_paths: List of paths to expected packages
      testonly: Only run the tests described in the meta.yaml
      mulled_build_and_test: Build the mulled container and run the recipe's
        tests inside it (wraps `mulled-build build-and-test`).
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
      skiplist_leaves: If True, blacklist leaf packages that fail to build
      live_logs: If True, enable live logging during the build process
      use_existing_auth: Use existing Docker/skopeo registry auth when no
        QUAY_LOGIN or QUAY_OAUTH_TOKEN is configured.
    """
    if record_build_failure and not dag:
        raise ValueError("record_build_failure requires dag to be set")

    if pkg_paths is None:
        pkg_paths = []

    if linter:
        logger.info("Linting recipe %s", recipe)
        linter.clear_messages()
        if linter.lint([recipe]):
            logger.error(
                "\n\nThe recipe %s failed linting. See "
                "https://bioconda.github.io/contributor/linting.html for details:\n\n%s\n",
                recipe,
                linter.get_report(),
            )
            return BuildResult(False, None)
        logger.info("Lint checks passed")

    # Copy env allowing only whitelisted vars
    whitelisted_env = {
        k: str(v)
        for k, v in os.environ.items()
        if utils.allowed_env_var(k, docker_builder is not None)
    }

    logger.info("BUILD START %s", recipe)

    args = ["--override-channels"]
    if testonly:
        args += ["--test"]
    else:
        args += ["--no-anaconda-upload"]

    channels_to_use = ["local"] + [c for c in (channels or []) if c != "local"]
    for channel in channels_to_use:
        args += ["-c", channel]

    logger.debug("Build and Channel Args: %s", args)

    # Even though there may be variants of the recipe that will be built, we
    # will only be checking attributes that are independent of variants (pkg
    # name, version, noarch, whether or not an extended container was used)
    meta = utils.load_first_metadata(recipe, finalize=False)
    is_noarch = bool(meta.get_value("build/noarch", default=False))
    use_base_image = meta.get_value("extra/container", {}).get("extended-base", False)
    if use_base_image:
        base_image = "quay.io/bioconda/base-glibc-debian-bash:3.1"
    else:
        base_image = "quay.io/bioconda/base-glibc-busybox-bash:3.1"

    build_failure_record = BuildFailureRecord(recipe)
    build_failure_record_existed_before_build = build_failure_record.exists()
    if build_failure_record_existed_before_build:
        # remove record to avoid that it is leaked into the package
        build_failure_record.remove()

    try:
        report_resources(f"Starting build for {recipe}", docker_builder is not None)
        if docker_builder is not None:
            docker_builder.build_recipe(
                recipe_dir=os.path.abspath(recipe),
                build_args=" ".join(args),
                env=whitelisted_env,
                noarch=is_noarch,
                live_logs=live_logs,
            )
            # Use presence of expected packages to check for success
            if docker_builder.pkg_dir is not None:
                conda_build_config = utils.load_conda_build_config()
                pkg_paths = [
                    p.replace(conda_build_config.output_folder, docker_builder.pkg_dir)
                    for p in pkg_paths
                ]

            for pkg_path in pkg_paths:
                if not os.path.exists(pkg_path):
                    logger.error(
                        "BUILD FAILED: the built package %s cannot be found",
                        pkg_path,
                    )
                    return BuildResult(False, None)
        else:
            conda_build_cmd = [utils.bin_for("conda-build")]
            # - Temporarily reset os.environ to avoid leaking env vars
            # - Also pass filtered env to run()
            # - Point conda-build to meta.yaml, to avoid building subdirs
            with utils.sandboxed_env(whitelisted_env):
                cmd = conda_build_cmd + args
                for config_file in utils.get_conda_build_config_files():
                    cmd += [config_file.arg, config_file.path]
                cmd += [os.path.join(recipe, "meta.yaml")]
                with utils.Progress():
                    utils.run(cmd, redacted_secrets=False, live=live_logs)

        logger.info(
            "BUILD SUCCESS %s", " ".join(os.path.basename(p) for p in pkg_paths)
        )
        if record_build_failure:
            # Success, hence the record is obsolete. Remove it.
            if build_failure_record_existed_before_build:
                # record is already removed (see above), but change has to be committed
                build_failure_record.commit_and_push_changes()

    except (
        docker_utils.DockerCalledProcessError,
        sp.CalledProcessError,
    ) as exc:
        logger.error("BUILD FAILED %s", recipe)
        if hasattr(exc, "output") and exc.output:
            logger.error("Build output:\n%s", exc.output)
        if record_build_failure:
            assert dag is not None
            store_build_failure_record(recipe, exc.output, meta, dag, skiplist_leaves)
        if raise_error:
            raise exc
        return BuildResult(False, None)
    finally:
        report_resources(f"Finished build for {recipe}", docker_builder is not None)

    if mulled_build_and_test:
        logger.info("BUILD AND TEST START via mulled-build %s", recipe)
        mulled_images: list[MulledImage] = []
        # Use pre-solved test env unless we need the mulled-build image for upload
        requested_platforms: list[ContainerPlatform | None] = (
            list(container_platforms) if container_platforms else [None]
        )
        for pkg_path in pkg_paths:
            for target_platform in requested_platforms:
                use_temporary_test_container = (
                    presolved_mulled_build_and_test
                    and not mulled_upload_target
                    and container_platform_is_native(target_platform)
                )
                built_mulled_image = False
                try:
                    report_resources(
                        f"Starting mulled build for {pkg_path} on {target_platform or 'native'}"
                    )
                    if use_temporary_test_container:
                        try:
                            result = pkg_test.test_package_in_temporary_container(
                                pkg_path,
                                base_image=base_image,
                                conda_image=mulled_conda_image,
                                live_logs=live_logs,
                            )
                        except Exception as exc:
                            logger.info(
                                "Pre-solved test failed (%s), falling back to "
                                "mulled-build",
                                exc,
                            )
                            result = None
                        if result is None:
                            pkg_test.build_and_test_mulled_image(
                                pkg_path,
                                base_image=base_image,
                                conda_image=mulled_conda_image,
                                live_logs=live_logs,
                                target_platform=target_platform,
                            )
                            built_mulled_image = True
                    else:
                        pkg_test.build_and_test_mulled_image(
                            pkg_path,
                            base_image=base_image,
                            conda_image=mulled_conda_image,
                            live_logs=live_logs,
                            target_platform=target_platform,
                        )
                        built_mulled_image = True
                except sp.CalledProcessError:
                    logger.error("TEST FAILED: %s", recipe)
                    return BuildResult(False, None)
                finally:
                    report_resources(
                        f"Finished mulled build for {pkg_path} on {target_platform or 'native'}"
                    )
                logger.info("TEST SUCCESS %s", recipe)
                if built_mulled_image:
                    image_spec = pkg_test.get_image_name(pkg_path)
                    mulled_images.append(
                        mulled_image_metadata(image_spec, target_platform)
                    )
        return BuildResult(True, mulled_images)

    return BuildResult(True, None)


def store_build_failure_record(
    recipe: str,
    output: str | None,
    meta: MetaData,
    dag: nx.DiGraph,
    skiplist_leaves: bool,
) -> None:
    """
    Write the exception to a file next to the meta.yaml
    """
    pkg_name = meta.meta["package"]["name"]
    is_leaf = graph.is_leaf(dag, pkg_name)

    build_failure_record = BuildFailureRecord(recipe)
    # if recipe is a leaf (i.e. not used by others as dependency)
    # we can automatically blacklist it if desired
    build_failure_record.fill(log=output, skiplist=skiplist_leaves and is_leaf)

    build_failure_record.write()
    build_failure_record.commit_and_push_changes()


def remove_cycles(
    dag: nx.DiGraph,
    name2recipes: dict[str, set[str]],
    failed: list[str],
    skip_dependent: defaultdict[str, list[str]],
) -> nx.DiGraph:
    nodes_in_cycles = set()
    for cycle in list(nx.simple_cycles(dag)):
        logger.error("BUILD ERROR: dependency cycle found: %s", cycle)
        nodes_in_cycles.update(cycle)

    for name in sorted(nodes_in_cycles):
        cycle_fail_recipes = sorted(name2recipes[name])
        logger.error(
            "BUILD ERROR: cannot build recipes for %s since "
            "it cyclically depends on other packages in the "
            "current build job. Failed recipes: %s",
            name,
            cycle_fail_recipes,
        )
        failed.extend(cycle_fail_recipes)
        for node in nx.algorithms.descendants(dag, name):
            if node not in nodes_in_cycles:
                skip_dependent[node].extend(cycle_fail_recipes)
    return dag.subgraph(name for name in dag if name not in nodes_in_cycles)


def get_worker_subdag(
    dag: nx.DiGraph,
    n_workers: int,
    worker_offset: int,
    subdag_depth: int | None,
) -> nx.DiGraph:
    if n_workers > 1 and worker_offset >= n_workers:
        raise ValueError(
            "n-workers is less than the worker-offset given! "
            "Either decrease --n-workers or decrease --worker-offset!"
        )

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
                print(f"{len(root_nodes)} recipes at depth {i}")
                if len(root_nodes) == 0:
                    break
                if i < subdag_depth:
                    working_dag.remove_nodes_from(root_nodes)
                    root_nodes = sorted(
                        [k for (k, v) in working_dag.in_degree() if v == 0]
                    )

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
        logger.info(
            "Building and testing sub-DAGs %i in each group of %i, which is %i packages",
            worker_offset,
            n_workers,
            len(subdags.nodes()),
        )
    else:
        subdags = dag

    return subdags


def should_skip_platform(
    recipe_folder: str, recipe: str, platform: PackageSubdir
) -> bool:
    """
    Return True if *platform* is a non-primary subdir (``linux-aarch64``,
    ``osx-arm64``, ``linux-riscv64``) and the recipe does not list it in
    ``extra.additional-platforms``.

    The ``linux-64`` and ``osx-64`` subdirs are always built — they are assumed
    to be universally compatible.  Any other subdir requires explicit opt-in
    via ``extra.additional-platforms`` in ``meta.yaml``.  Without this gate,
    every recipe would be attempted on every non-x86_64 builder, wasting time
    on recipes that have not been verified for that platform.
    """
    recipe_obj = _recipe.Recipe.from_file(recipe_folder, recipe)
    primary_platforms: set[PackageSubdir] = {"linux-64", "osx-64"}
    additional_platforms = set(PACKAGE_SUBDIRS) - primary_platforms
    return (
        platform in additional_platforms
        and platform not in recipe_obj.additional_platforms
    )


def build_recipes(
    recipe_folder: str,
    config: dict[str, Any],
    recipes: list[str],
    mulled_build_and_test: bool = True,
    testonly: bool = False,
    force: bool = False,
    docker_builder: docker_utils.RecipeBuilder | None = None,
    label: str | None = None,
    anaconda_upload: bool = False,
    mulled_upload_target: QuayUploadTarget | None = None,
    check_channels: list[str] | None = None,
    do_lint: bool | None = None,
    lint_exclude: list[str] | None = None,
    n_workers: int = 1,
    worker_offset: int = 0,
    keep_old_work: bool = False,
    mulled_conda_image: str = pkg_test.CREATE_ENV_IMAGE,
    record_build_failures: bool = False,
    skiplist_leaves: bool = False,
    live_logs: bool = True,
    exclude: list[str] | None = None,
    subdag_depth: int | None = None,
    presolved_mulled_build_and_test: bool = True,
    fast_resolve: bool = True,
    container_platforms: Sequence[ContainerPlatform] | None = None,
    mulled_upload_records: Path | None = None,
    use_existing_auth: bool = False,
) -> bool:
    """
    Build one or many bioconda packages.

    Arguments:
      recipe_folder: Directory containing possibly many, and possibly nested, recipes.
      config: Parsed Bioconda configuration, normalized at this boundary.
      packages: Glob indicating which packages should be considered. Note that packages
        matching the glob will still be filtered out by any blacklists
        specified in the config.
      mulled_build_and_test: If true, build the mulled container and run the
        recipe's tests inside it.
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
      use_existing_auth: Use existing Docker/skopeo registry auth when no
        QUAY_LOGIN or QUAY_OAUTH_TOKEN is configured.
      skiplist_leaves: If True, blacklist leaf packages that fail to build
      live_logs: If True, enable live logging during the build process
      exclude: list of recipes to exclude. Typically used for
        temporary exclusion; otherwise consider adding recipe to skiplist.
      subdag_depth: Number of levels of nodes to skip. (Optional, only if using n_workers)
    """
    if not recipes:
        logger.info("Nothing to be done.")
        return True

    config = utils.normalize_config(config)
    utils.RepoData.register_config(config)
    blacklist = Skiplist(config, recipe_folder)

    # get channels to check
    if check_channels is None:
        if config["channels"]:
            check_channels = [c for c in config["channels"] if c != "defaults"]
        else:
            check_channels = []

    # setup linting
    if do_lint:
        always_exclude = ["build_number_needs_bump"]
        if not lint_exclude:
            lint_exclude = always_exclude
        else:
            lint_exclude = list(set(lint_exclude) | set(always_exclude))
        linter = lint.Linter(config, recipe_folder, lint_exclude)
    else:
        linter = None

    failed = []

    dag, name2recipes = graph.build(recipes, config=config, blacklist=blacklist)
    if exclude:
        for name in exclude:
            dag.remove_node(name)

    if not dag:
        logger.info("Nothing to be done.")
        return True

    skip_dependent = defaultdict(list)
    dag = remove_cycles(dag, name2recipes, failed, skip_dependent)
    subdag = get_worker_subdag(dag, n_workers, worker_offset, subdag_depth)
    if not subdag:
        logger.info("Nothing to be done.")
        return True
    logger.info(
        "%i recipes to build and test: \n%s",
        len(subdag),
        "\n".join(subdag.nodes()),
    )

    recipe2name = {}
    for name, recipe_list in name2recipes.items():
        for recipe in recipe_list:
            recipe2name[recipe] = name

    recipe_jobs: list[tuple[str, str]] = [
        (recipe, recipe2name[recipe])
        for package in nx.topological_sort(subdag)
        for recipe in name2recipes[package]
    ]

    built_recipes = []
    skipped_recipes = []
    failed_uploads = []

    for recipe, name in recipe_jobs:
        platform = utils.RepoData().native_subdir()
        if not force and should_skip_platform(recipe_folder, recipe, platform):
            logger.info(
                "BUILD SKIP: skipping %s for additional platform %s",
                recipe,
                platform,
            )
            continue

        if name in skip_dependent:
            logger.info(
                "BUILD SKIP: skipping %s because it depends on %s which had a failed build.",
                recipe,
                skip_dependent[name],
            )
            skipped_recipes.append(recipe)
            continue

        logger.info("Determining expected packages for %s", recipe)
        try:
            # When building with Docker, skip the expensive finalized render
            # on the host since Docker's conda-build will re-solve anyway.
            # Non-finalized metas use bypass_env_check which avoids costly
            # dependency resolution. The --no-fast-resolve flag can override this.
            #
            # Two cases force finalize on to keep host and Docker hashes in sync
            # (see https://github.com/bioconda/bioconda-utils/issues/1095):
            #   1. Recipes using stdlib()/compiler()/pin_compatible() — their
            #      run_exports (e.g. sysroot_linux-64 -> __glibc) are only
            #      applied during a real solve.
            #   2. linux-64 hosts — sysroot run_exports inject __glibc here
            #      regardless of the recipe's text form.
            finalize = docker_builder is None or not fast_resolve
            if (
                not finalize
                and utils.subdir_to_oslabel(utils.RepoData.native_subdir()) == "linux"
            ):
                finalize = True
            if not finalize and utils.recipe_requires_finalized_render(recipe):
                finalize = True
            pkg_paths = utils.get_package_paths(
                recipe,
                check_channels,
                force=force,
                finalize=finalize,
            )
        except utils.DivergentBuildsError as exc:
            logger.error(
                "BUILD ERROR: packages with divergent build strings in repository "
                "for recipe %s. A build number bump is likely needed: %s",
                recipe,
                exc,
            )
            failed.append(recipe)
            for pkg in nx.algorithms.descendants(subdag, name):
                skip_dependent[pkg].append(recipe)
            continue
        except (UnsatisfiableError, DependencyNeedsBuildingError) as exc:
            logger.error(
                "BUILD ERROR: could not determine dependencies for recipe %s: %s",
                recipe,
                exc,
            )
            failed.append(recipe)
            for pkg in nx.algorithms.descendants(subdag, name):
                skip_dependent[pkg].append(recipe)
            continue
        if not pkg_paths:
            logger.info("Nothing to be done for recipe %s", recipe)
            continue

        res = build(
            recipe=recipe,
            pkg_paths=pkg_paths,
            testonly=testonly,
            mulled_build_and_test=mulled_build_and_test,
            channels=config["channels"],
            docker_builder=docker_builder,
            linter=linter,
            mulled_conda_image=mulled_conda_image,
            dag=dag,
            record_build_failure=record_build_failures,
            skiplist_leaves=skiplist_leaves,
            live_logs=live_logs,
            presolved_mulled_build_and_test=presolved_mulled_build_and_test,
            mulled_upload_target=mulled_upload_target,
            container_platforms=container_platforms,
            use_existing_auth=use_existing_auth,
        )

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
                    for img in res.mulled_images or []:
                        record = upload.mulled_upload(
                            img.pkg_ref,
                            mulled_upload_target,
                            img.target_platform,
                            use_existing_auth=use_existing_auth,
                        )
                        if mulled_upload_records is not None:
                            write_image_record(mulled_upload_records, record)
                        docker_utils.purgeImage(
                            img.pkg_ref, img.target_platform
                        )

        # remove traces of the build
        if not keep_old_work:
            conda_build_purge()
            # prune stopped containers
            if docker_builder is not None:
                docker_utils.pruneStoppedContainers()

    if failed or failed_uploads:
        logger.error(
            "BUILD SUMMARY: of %s recipes, "
            "%s failed and %s were skipped. "
            "Details of recipes and environments follow.",
            len(recipes),
            len(failed),
            len(skipped_recipes),
        )
        if built_recipes:
            logger.error(
                "BUILD SUMMARY: while the entire build failed, "
                "the following recipes were built successfully:\n%s",
                "\n".join(built_recipes),
            )
        for recipe in failed:
            logger.error("BUILD SUMMARY: FAILED recipe %s", recipe)
        for name, dep in skip_dependent.items():
            logger.error(
                "BUILD SUMMARY: SKIPPED recipe %s due to failed dependencies %s",
                name,
                dep,
            )
        if failed_uploads:
            logger.error(
                "UPLOAD SUMMARY: the following packages failed to upload:\n%s",
                "\n".join(failed_uploads),
            )
        return False

    logger.info(
        "BUILD SUMMARY: successfully built %s of %s recipes",
        len(built_recipes),
        len(recipes),
    )
    return True


def report_resources(message: str, show_docker: bool = True) -> None:
    free_space_mb = utils.get_free_space()
    free_mem_mb = utils.get_free_memory_mb()
    free_mem_percent = utils.get_free_memory_percent()
    logger.info(
        f"{message} Free disk space: {free_space_mb:.2f} MB. Free memory: {free_mem_mb:.2f} MB ({free_mem_percent:.2f}%)"
    )
    if show_docker:
        cmd = ["docker", "system", "df"]
        utils.run(cmd, redacted_secrets=False, live=True)
        cmd = ["docker", "ps", "-a"]
        utils.run(cmd, redacted_secrets=False, live=True)
