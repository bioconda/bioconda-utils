"""Bioconda Utils command-line interface built with Typer."""

import logging
from typing import Annotated, Any, Literal

import typer

from . import __version__ as VERSION
from . import utils
from bioconda_utils import bulk
from bioconda_utils.artifacts import UploadResult, upload_pr_artifacts
from bioconda_utils.skiplist import Skiplist
from bioconda_utils.build_failure import (
    BuildFailureRecord,
    collect_build_failure_dataframe,
)
import sys
import os
import shlex
from collections import defaultdict, Counter
from functools import partial
import conda
import conda.base.constants
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
import pandas
from .build import build_recipes
from . import docker_utils
from . import lint as _lint
from . import bioconductor_skeleton as _bioconductor_skeleton
from . import cran_skeleton
from . import update_pinnings
from . import graph
from .githandler import BiocondaRepo, install_gpg_key

app = typer.Typer(
    help="Utilities for building and maintaining Bioconda recipes.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=None,
)
logger = logging.getLogger(__name__)
LogLevel = Literal["debug", "info", "warning", "error", "critical"]
PackagePatterns = str | list[str]

# Shared CLI parameter type aliases
LoglevelOpt = Annotated[
    LogLevel,
    typer.Option(
        "--loglevel", help="Set logging level (debug, info, warning, error, critical)"
    ),
]
LogfileOpt = Annotated[str | None, typer.Option("--logfile", help="Write log to file")]
LogfileLevelOpt = Annotated[
    LogLevel, typer.Option("--logfile-level", help="Log level for log file")
]
LogCommandMaxLinesOpt = Annotated[
    int | None,
    typer.Option(
        "--log-command-max-lines", help="Limit lines emitted for commands executed"
    ),
]
RecipeFolderArg = Annotated[
    str | None,
    typer.Argument(help="Path to folder containing recipes (default: recipes/)"),
]
ConfigArg = Annotated[
    str | None, typer.Argument(help="Path to Bioconda config (default: config.yml)")
]
ThreadsOpt = Annotated[
    int,
    typer.Option("-t", "--threads", help="Limit maximum number of processes used."),
]
PdbOpt = Annotated[
    bool, typer.Option("-P", "--pdb", help="Drop into debugger on exception")
]


def get_recipes_to_build(
    git_range: tuple[str, ...] | list[str], recipe_folder: str
) -> list[str]:
    """Gets list of modified recipes according to git_range and blacklist

    See `BiocondaRepoMixin.get_recipes_to_build()`.

    Arguments:
      git_range: one or two-tuple containing "from" and "to" git refs,
                 with "to" defaulting to "HEAD"
    Returns:
      List of recipes for which meta.yaml or build.sh was modified or
      which were unblacklisted.
    """
    if not git_range or len(git_range) > 2:
        sys.exit("--git-range may have only one or two arguments")
    other = git_range[0]
    ref = "HEAD" if len(git_range) == 1 else git_range[1]
    repo = BiocondaRepo(recipe_folder)
    return repo.get_recipes_to_build(ref, other)


def get_recipes(
    config: dict[str, Any],
    recipe_folder: str,
    packages: PackagePatterns,
    git_range: list[str] | None,
    include_blacklisted: bool = False,
) -> list[str]:
    """Gets list of paths to recipe folders to be built

    Considers all recipes matching globs in packages, constrains to
    recipes modified or unblacklisted in the git_range if given, then
    removes blacklisted recipes (unless include_blacklisted=True).

    """
    recipes = list(utils.get_recipes(recipe_folder, packages))
    logger.info(
        "Considering total of %s recipes%s.",
        len(recipes),
        utils.ellipsize_recipes(recipes, recipe_folder),
    )
    if git_range:
        changed_recipes = get_recipes_to_build(git_range, recipe_folder)
        logger.info(
            "Constraining to %s git modified recipes%s.",
            len(changed_recipes),
            utils.ellipsize_recipes(changed_recipes, recipe_folder),
        )
        recipes = [recipe for recipe in recipes if recipe in set(changed_recipes)]
        if len(recipes) != len(changed_recipes):
            logger.info(
                "Overlap was %s recipes%s.",
                len(recipes),
                utils.ellipsize_recipes(recipes, recipe_folder),
            )
    if not include_blacklisted:
        skiplist = Skiplist(config, recipe_folder)
        all_len = len(recipes)
        recipes = [recipe for recipe in recipes if not skiplist.is_skiplisted(recipe)]
        if all_len > len(recipes):
            logger.info(f"Ignoring {all_len - len(recipes)} skiplisted recipes.")
    logger.info(
        "Processing %s recipes%s.",
        len(recipes),
        utils.ellipsize_recipes(recipes, recipe_folder),
    )
    return recipes


def _setup_runtime(
    loglevel="info",
    logfile=None,
    logfile_level="debug",
    log_command_max_lines=None,
    threads=None,
):
    utils.setup_logger(
        "bioconda_utils", loglevel, logfile, logfile_level, log_command_max_lines
    )
    if threads is not None:
        utils.set_max_threads(threads)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"This is bioconda-utils version {VERSION}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Bioconda Utils command-line interface."""


@app.command("build")
def build(
    recipe_folder: Annotated[
        str | None,
        typer.Argument(help="Path to folder containing recipes (default: recipes/)"),
    ] = None,
    config: Annotated[
        str | None, typer.Argument(help="Path to Bioconda config (default: config.yml)")
    ] = None,
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob for package[s] to build. Default is to build all packages. Can be specified more than once",
        ),
    ] = None,
    git_range: Annotated[
        list[str] | None,
        typer.Option(
            "--git-range",
            help='Git range (e.g. commits or something like\n     "master HEAD" to check commits in HEAD vs master, or just "HEAD" to\n     include uncommitted changes). All recipes modified within this range will\n     be built if not present in the channel.',
        ),
    ] = None,
    test_only: Annotated[
        bool, typer.Option("--test-only", help="Test packages instead of building")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Force building the recipe even if it already exists in the\n     bioconda channel. If --force is specified, --git-range is ignored and only\n     those packages matching --packages globs will be built.",
        ),
    ] = False,
    docker: Annotated[
        bool, typer.Option("--docker", help="Build packages in docker container.")
    ] = False,
    mulled_test: Annotated[
        bool,
        typer.Option(
            "--mulled-test", help="Run a mulled-build test on the built package"
        ),
    ] = False,
    build_script_template: Annotated[
        str | None,
        typer.Option(
            "--build-script-template",
            help="Filename to optionally replace build\n     script template used by the Docker container. By default use\n     docker_utils.BUILD_SCRIPT_TEMPLATE. Only used if --docker is True.",
        ),
    ] = None,
    package_dir: Annotated[
        str | None,
        typer.Option(
            "--package-dir",
            help="Specifies the directory to which container-built\n     packages should be stored on the host. Default is to use the host's\n     conda-bld dir. If --docker is not specified, then this argument is\n     ignored.",
        ),
    ] = None,
    anaconda_upload: Annotated[
        bool,
        typer.Option(
            "--anaconda-upload",
            help="After building recipes, upload\n     them to Anaconda. This requires $ANACONDA_TOKEN to be set.",
        ),
    ] = False,
    mulled_upload_target: Annotated[
        str | None,
        typer.Option(
            "--mulled-upload-target",
            help="Provide a quay.io target to push mulled docker images to.",
        ),
    ] = None,
    build_image: Annotated[
        bool,
        typer.Option(
            "--build-image",
            help="Build temporary docker build\n     image with conda/conda-build version matching local versions",
        ),
    ] = False,
    keep_image: Annotated[
        bool,
        typer.Option(
            "--keep-image",
            help="After building recipes, the\n     created Docker image is removed by default to save disk space. Use this\n     argument to disable this behavior.",
        ),
    ] = False,
    lint: Annotated[
        bool,
        typer.Option(
            "--lint",
            help="Just before each recipe, apply\n     the linting functions to it. This can be used as an alternative to linting\n     all recipes before any building takes place with the `bioconda-utils lint`\n     command.",
        ),
    ] = False,
    lint_exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--lint-exclude",
            help="Exclude this linting function. Can be used multiple times.",
        ),
    ] = None,
    check_channels: Annotated[
        list[str] | None,
        typer.Option(
            "--check-channels",
            help="Channels to check recipes against before building. Any recipe\n     already present in one of these channels will be skipped. The default is\n     the first two channels specified in the config file. Note that this is\n     ignored if you specify --git-range.",
        ),
    ] = None,
    n_workers: Annotated[
        int,
        typer.Option(
            "--n-workers",
            help='The number of parallel workers that are in use. This is intended\n     for use in cases such as the "bulk" branch, where there are multiple\n     parallel workers building and uploading recipes. In essence, this causes\n     bioconda-utils to process every Nth sub-DAG, where N is the value you give\n     to this option. The default is 1, which is intended for cases where there\n     are NOT parallel workers (i.e., the majority of cases). This should\n     generally NOT be used in conjunctions with the --packages or --git-range\n     options!',
        ),
    ] = 1,
    worker_offset: Annotated[
        int,
        typer.Option(
            "--worker-offset",
            help='This is only used if --n-workers is greater than 1. Each\n     instance of bioconda-utils processes every Nth sub-DAG. This option\n     gives the zero-based offset for that. For example, "--n-workers 5\n     --worker-offset 0" processes the 1st, 6th, and 11th sub-DAGs.',
        ),
    ] = 0,
    keep_old_work: Annotated[
        bool,
        typer.Option(
            "--keep-old-work",
            help="Do not remove anything\nfrom environment, even after successful build and test.",
        ),
    ] = False,
    mulled_conda_image: Annotated[
        str,
        typer.Option(
            "--mulled-conda-image",
            help="Conda Docker image to install the package with during\n     the mulled based tests.",
        ),
    ] = "quay.io/bioconda/create-env:latest",
    docker_base_image: Annotated[
        str | None,
        typer.Option(
            "--docker-base-image",
            help="Name of base image that can be used in\n     Dockerfile template.",
        ),
    ] = None,
    record_build_failures: Annotated[
        bool,
        typer.Option(
            "--record-build-failures",
            help="Record build failures in build_failure.yaml next to the recipe.",
        ),
    ] = False,
    skiplist_leaves: Annotated[
        bool,
        typer.Option(
            "--skiplist-leaves",
            help="Skiplist leaf recipes (i.e. ones that are not depended on by any other recipes) that fail to build.",
        ),
    ] = False,
    disable_live_logs: Annotated[
        bool,
        typer.Option(
            "--disable-live-logs", help="Disable live logging during the build process"
        ),
    ] = False,
    presolved_mulled_test: Annotated[
        bool,
        typer.Option(
            "--presolved-mulled-test/--no-presolved-mulled-test",
            help="Use the pre-solved mulled test path.",
        ),
    ] = True,
    no_fast_resolve: Annotated[
        bool,
        typer.Option(
            "--no-fast-resolve",
            help="Disable fast resolve: always run the full finalized conda solver on the host, even when building with Docker. Useful for debugging build string mismatches.",
        ),
    ] = False,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Packages to exclude during this run"),
    ] = None,
    subdag_depth: Annotated[
        int | None,
        typer.Option(
            "--subdag-depth",
            help="Number of levels of root nodes to skip. (Optional, and only if using n_workers)",
        ),
    ] = None,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Build and test Bioconda recipes."""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    package_patterns: PackagePatterns = packages or "*"
    cfg = utils.load_config(config)
    setup = cfg.get("setup", None)
    if setup:
        logger.debug("Running setup: %s", setup)
        for cmd in setup:
            utils.run(shlex.split(cmd), mask=False)
    recipes = get_recipes(cfg, recipe_folder, package_patterns, git_range)
    if docker:
        if build_script_template is not None:
            build_script_template = open(build_script_template).read()
        else:
            build_script_template = docker_utils.BUILD_SCRIPT_TEMPLATE
        if package_dir is None:
            use_host_conda_bld = True
        else:
            use_host_conda_bld = False
        if not utils.is_stable_version(VERSION):
            image_tag = utils.extract_stable_version(VERSION)
            logger.warning(
                f"Using tag {image_tag} for docker image, since there is no image for a not yet release version ({VERSION})."
            )
        else:
            image_tag = VERSION
        docker_base_image = (
            docker_base_image
            or os.getenv("BUILD_ENV_IMAGE", None)
            or f"quay.io/bioconda/bioconda-utils-build-env-cos7:{image_tag}"
        )
        logger.info(f"Using docker image {docker_base_image} for building.")
        docker_builder = docker_utils.RecipeBuilder(
            build_script_template=build_script_template,
            pkg_dir=package_dir,
            use_host_conda_bld=use_host_conda_bld,
            keep_image=keep_image,
            build_image=build_image,
            docker_base_image=docker_base_image,
        )
    else:
        docker_builder = None
    if lint_exclude and (not lint):
        logger.warning("--lint-exclude has no effect unless --lint is specified.")
    label = os.getenv("BIOCONDA_LABEL", None) or None
    success = build_recipes(
        recipe_folder,
        config,
        recipes,
        testonly=test_only,
        force=force,
        mulled_test=mulled_test,
        docker_builder=docker_builder,
        anaconda_upload=anaconda_upload,
        mulled_upload_target=mulled_upload_target,
        do_lint=lint,
        lint_exclude=lint_exclude,
        check_channels=check_channels,
        label=label,
        n_workers=n_workers,
        worker_offset=worker_offset,
        keep_old_work=keep_old_work,
        mulled_conda_image=mulled_conda_image,
        record_build_failures=record_build_failures,
        skiplist_leafs=skiplist_leaves,
        live_logs=not disable_live_logs,
        exclude=exclude,
        subdag_depth=subdag_depth,
        presolved_mulled_test=presolved_mulled_test,
        fast_resolve=not no_fast_resolve,
    )
    exit(0 if success else 1)


@app.command("dag")
def dag(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob for package[s] to show in DAG. Default is to show all packages. Can be specified more than once",
        ),
    ] = None,
    format: Annotated[
        Literal["gml", "dot", "txt"],
        typer.Option(
            "--format",
            help='Set format to print\n     graph. "gml" and "dot" can be imported into graph visualization tools\n     (graphviz, gephi, cytoscape). "txt" will print out recipes grouped by\n     independent subdags, largest subdag first, each in topologically sorted\n     order. Singleton subdags (if not hidden with --hide-singletons) are\n     reported as one large group at the end.',
        ),
    ] = "gml",
    hide_singletons: Annotated[
        bool,
        typer.Option("--hide-singletons", help="Hide singletons in the printed graph."),
    ] = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Export the DAG of packages to a graph format file for visualization"""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    package_patterns: PackagePatterns = packages or "*"
    config_data = utils.load_config(config)
    dag, name2recipes = graph.build(
        utils.get_recipes(recipe_folder, package_patterns), config_data
    )
    if hide_singletons:
        for node in nx.nodes(dag):
            if dag.degree(node) == 0:
                dag.remove_node(node)
    if format == "gml":
        nx.write_gml(dag, sys.stdout.buffer)
    elif format == "dot":
        write_dot(dag, sys.stdout)
    elif format == "txt":
        subdags: list[list[str]] = sorted(
            map(sorted, nx.connected_components(dag.to_undirected()))
        )
        subdags.sort(key=len, reverse=True)
        singletons: list[str] = []
        for i, s in enumerate(subdags):
            if len(s) == 1:
                singletons.extend(s)
                continue
            print(f"# subdag {i}")
            subdag = dag.subgraph(s)
            recipes = [
                recipe
                for package in nx.topological_sort(subdag)
                for recipe in name2recipes[package]
            ]
            print("\n".join(recipes) + "\n")
        if not hide_singletons:
            print("# singletons")
            recipes = [
                recipe for package in singletons for recipe in name2recipes[package]
            ]
            print("\n".join(recipes) + "\n")


@app.command("dependent")
def dependent(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    restrict: Annotated[
        bool,
        typer.Option(
            "--restrict",
            help="Restrict --dependencies to packages in `recipe_folder`. Has no\n     effect if --reverse-dependencies, which always looks just in the recipe\n     dir.",
        ),
    ] = False,
    dependencies: Annotated[
        list[str] | None,
        typer.Option(
            "--dependencies",
            help='Return recipes in `recipe_folder` in the dependency chain for the\n     packages listed here. Answers the question "what does PACKAGE need?"',
        ),
    ] = None,
    reverse_dependencies: Annotated[
        list[str] | None,
        typer.Option(
            "--reverse-dependencies",
            help='Return recipes in `recipe_folder` in the reverse dependency chain\n     for packages listed here. Answers the question "what depends on\n     PACKAGE?"',
        ),
    ] = None,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Print recipes dependent on a package"""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    if dependencies and reverse_dependencies:
        raise ValueError(
            "`dependencies` and `reverse_dependencies` are mutually exclusive"
        )
    if not any([dependencies, reverse_dependencies]):
        raise ValueError(
            "One of `--dependencies` or `--reverse-dependencies` is required."
        )
    config_data = utils.load_config(config)
    d, _ = graph.build(
        utils.get_recipes(recipe_folder, "*"), config_data, restrict=restrict
    )
    if reverse_dependencies is not None:
        dependency_func = nx.algorithms.descendants
        selected_packages = reverse_dependencies
    else:
        dependency_func = nx.algorithms.ancestors
        selected_packages = dependencies or []
    pkgs = []
    for pkg in selected_packages:
        pkgs.extend(dependency_func(d, pkg))
    print("\n".join(sorted(list(set(pkgs)))))


@app.command("lint")
def lint(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob for package[s] to build. Default is to build all packages. Can be specified more than once",
        ),
    ] = None,
    cache: Annotated[
        str | None,
        typer.Option(
            "--cache",
            help="To speed up debugging, use repodata cached locally in\n     the provided filename. If the file does not exist, it will be created the\n     first time.",
        ),
    ] = None,
    list_checks: Annotated[
        bool,
        typer.Option(
            "--list-checks",
            help="List the linting functions to be used and then\n     exit",
        ),
    ] = False,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="Exclude this linting function. Can be used\n     multiple times.",
        ),
    ] = None,
    git_range: Annotated[
        list[str] | None,
        typer.Option(
            "--git-range",
            help='Git range (e.g. commits or something like\n     "master HEAD" to check commits in HEAD vs master, or just "HEAD" to\n     include uncommitted changes). All recipes modified within this range will\n     be built if not present in the channel.',
        ),
    ] = None,
    try_fix: Annotated[
        bool, typer.Option("--try-fix", help="Attempt to fix problems where found")
    ] = False,
    pdb: PdbOpt = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Lint recipes

    Reports a TSV of linting results to stdout."""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    package_patterns: PackagePatterns = packages or "*"
    try:
        if list_checks:
            print("\n".join((str(check) for check in _lint.get_checks())))
            sys.exit(0)
        config_data = utils.load_config(config)
        if cache is not None:
            utils.RepoData().set_cache(cache)
        recipes = get_recipes(
            config_data,
            recipe_folder,
            package_patterns,
            git_range,
            include_blacklisted=True,
        )
        linter = _lint.Linter(config_data, recipe_folder, exclude)
        result = linter.lint(recipes, fix=try_fix)
        messages = linter.get_messages()
        if messages:
            print(
                "The following problems have been found (visit https://bioconda.github.io/contributor/linting.html for details on the particular lints you get below.):\n"
            )
            print(linter.get_report())
        if not result:
            print("All checks OK")
        else:
            sys.exit("Errors were found")
    except Exception:
        if pdb:
            import pdb as debugger

            logger.exception("Dropping into debugger")
            debugger.post_mortem()
            return None
        raise


@app.command("duplicates")
def duplicates(
    config: Annotated[
        str, typer.Argument(help="Path to yaml file specifying the configuration")
    ],
    strict_version: Annotated[
        bool,
        typer.Option("--strict-version", help="Require version to strictly match."),
    ] = False,
    strict_build: Annotated[
        bool,
        typer.Option(
            "--strict-build", help="Require version and build to strictly match."
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Only print removal plan.")
    ] = False,
    remove: Annotated[
        bool, typer.Option("--remove", help="Remove packages from anaconda.")
    ] = False,
    url: Annotated[bool, typer.Option("--url", help="Print anaconda urls.")] = False,
    channel: Annotated[
        str, typer.Option("--channel", help="Channel to check for duplicates")
    ] = "bioconda",
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Detect packages in bioconda that have duplicates in the other defined
    channels."""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    if remove and (not strict_build):
        raise ValueError(
            "Removing packages is only supported in case of --strict-build."
        )
    config_data = utils.load_config(config)
    if channel not in config_data["channels"]:
        raise ValueError("Channel given with --channel must be in config channels")
    our_channel = channel
    channels = [c for c in config_data["channels"] if c != our_channel]
    logger.info(
        "Checking for packages from %s also present in %s", our_channel, channels
    )
    check_fields = ["name"]
    if strict_version or strict_build:
        check_fields += ["version"]
    if strict_build:
        check_fields += ["build"]

    def remove_package(spec):
        for ext in (".tar.bz2", ".conda"):
            name, version = spec[:2]
            dist = "{}-{}-{}".format(*spec)
            fn = f"{dist}{ext}"
            subcmd = ["remove", "-f", f"{our_channel}/{name}/{version}/{fn}"]
            if dry_run:
                logger.info(" ".join([utils.bin_for("anaconda")] + subcmd))
            else:
                token = os.environ.get("ANACONDA_TOKEN")
                if token is None:
                    token = []
                else:
                    token = ["-t", token]
                logger.info(
                    utils.run(
                        [utils.bin_for("anaconda")] + token + subcmd, mask=token
                    ).stdout
                )

    repodata = utils.RepoData()
    our_package_specs = set(repodata.get_package_data(check_fields, our_channel))
    logger.info(
        "%s unique packages specs to consider in %s",
        len(our_package_specs),
        our_channel,
    )
    duplicate = defaultdict(list)
    for channel in channels:
        package_specs = set(repodata.get_package_data(check_fields, channel))
        logger.info(
            "%s unique packages specs to consider in %s", len(package_specs), channel
        )
        dups = our_package_specs & package_specs
        logger.info("  (of which %s are duplicate)", len(dups))
        for spec in dups:
            duplicate[spec].append(channel)
    print("\t".join(check_fields + ["channels"]))
    for spec, dup_channels in sorted(duplicate.items()):
        if remove:
            remove_package(spec)
        elif url:
            if not strict_version and (not strict_build):
                print(f"https://anaconda.org/{our_channel}/{spec[0]}")
            print(
                "https://anaconda.org/{}/{}/files?version={}".format(our_channel, *spec)
            )
        else:
            print(*spec, ",".join(dup_channels), sep="\t")


@app.command("update-pinning")
def update_pinning(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob for package[s] to update, as needed due to a change in pinnings",
        ),
    ] = None,
    skip_additional_channels: Annotated[
        list[str] | None,
        typer.Option(
            "--skip-additional-channels",
            help="Skip updating/bumping packges that are already built with\n     compatible pinnings in one of the given channels in addition to those\n     listed in 'config'.",
        ),
    ] = None,
    skip_variants: Annotated[
        list[str] | None,
        typer.Option(
            "--skip-variants",
            help="Skip packages that use one of the given variant keys.",
        ),
    ] = None,
    max_bumps: Annotated[
        int | None,
        typer.Option(
            "--max-bumps", help="Maximum number of recipes that will be updated."
        ),
    ] = None,
    no_leaves: Annotated[
        bool,
        typer.Option(
            "--no-leaves", help="Only update recipes with dependent packages."
        ),
    ] = False,
    cache: Annotated[
        str | None,
        typer.Option(
            "--cache",
            help="To speed up debugging, use repodata cached locally in\n     the provided filename. If the file does not exist, it will be created the\n     first time.",
        ),
    ] = None,
    pdb: PdbOpt = False,
    threads: ThreadsOpt = 16,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Bump a package build number and all dependencies as required due
    to a change in pinnings"""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines, threads)
    package_patterns: PackagePatterns = packages or "*"
    try:
        config_data = utils.load_config(config)
        if skip_additional_channels:
            config_data["channels"] += skip_additional_channels
        variant_keys = frozenset(skip_variants or ())
        if cache:
            utils.RepoData().set_cache(cache)
        utils.RepoData().df
        build_config = utils.load_conda_build_config()
        skiplist = Skiplist(config_data, recipe_folder)
        from . import recipe

        dag = graph.build_from_recipes(
            (
                r
                for r in recipe.load_parallel_iter(recipe_folder, "*")
                if not skiplist.is_skiplisted(r)
            )
        )
        dag = graph.filter_recipe_dag(dag, package_patterns, [])
        if no_leaves:
            dag = nx.subgraph(
                dag, (node for node, degree in dag.out_degree() if degree > 0)
            )
        logger.warning("Considering %i recipes", len(dag))
        if max_bumps is None or max_bumps < 0:
            max_bumps = len(dag)
        stats = Counter()
        hadErrors = set()
        bumpErrors = set()
        needs_bump = partial(
            update_pinnings.check,
            build_config=build_config,
            skip_variant_keys=variant_keys,
        )
        num_recipes_needing_bump = 0
        for status, recip in utils.parallel_iter(needs_bump, dag, "Processing..."):
            logger.debug("Recipe %s status: %s", recip, status)
            stats[status] += 1
            if status.needs_bump():
                num_recipes_needing_bump += 1
                if num_recipes_needing_bump <= max_bumps:
                    logger.info("Bumping %s", recip)
                    recip.reset_buildnumber(int(recip["build"]["number"]) + 1)
                    recip.save()
                else:
                    logger.info(
                        "Bumping %s -- theoretically (%d out of %d allowed bumps)",
                        recip,
                        num_recipes_needing_bump,
                        max_bumps,
                    )
            elif status.failed():
                logger.info("Failed to inspect %s", recip)
                hadErrors.add(recip)
            else:
                logger.info("OK: %s", recip)
        print("Packages requiring the following:")
        print(stats)
        if num_recipes_needing_bump > max_bumps:
            print(
                f"Only bumped {max_bumps} out of {num_recipes_needing_bump} recipes that needed a build number bump."
            )
        if hadErrors:
            print(
                f"{len(hadErrors)} packages produced an error in conda-build: {list(hadErrors)}"
            )
        if bumpErrors:
            print(
                f"The build numbers in the following recipes could not be incremented: {list(bumpErrors)}"
            )
    except Exception:
        if pdb:
            import pdb as debugger

            logger.exception("Dropping into debugger")
            debugger.post_mortem()
            return None
        raise


@app.command("bioconductor-skeleton")
def bioconductor_skeleton(
    package: Annotated[
        str,
        typer.Argument(
            help='Bioconductor package name. This is case-sensitive, and\n     must match the package name on the Bioconductor site. If "update-all-packages"\n     is specified, then all packages in a given bioconductor release will be\n     created/updated (--force is then implied).'
        ),
    ],
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    bioc_data_packages: Annotated[
        str | None,
        typer.Argument(
            help="Path to folder containing the recipe for the bioconductor-data-packages\n     (default: recipes/bioconductor-data-packages)"
        ),
    ] = None,
    versioned: Annotated[
        bool,
        typer.Option(
            "--versioned",
            help="If specified, recipe will be\n     created in RECIPES/<package>/<version>",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite the contents of an\n     existing recipe. If --recursive is also used, then overwrite *all* recipes\n     created.",
        ),
    ] = False,
    pkg_version: Annotated[
        str | None,
        typer.Option(
            "--pkg-version",
            help="Package version to use instead of the current\n     one",
        ),
    ] = None,
    bioc_version: Annotated[
        str | None,
        typer.Option(
            "--bioc-version",
            help="Version of Bioconductor to target. If not\n     specified, then automatically finds the latest version of Bioconductor\n     with the specified version in --pkg-version, or if --pkg-version not\n     specified, then finds the the latest package version in the latest\n     Bioconductor version",
        ),
    ] = None,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            help="Creates the recipes for all\n     Bioconductor and CRAN dependencies of the specified package.",
        ),
    ] = False,
    skip_if_in_channels: Annotated[
        list[str] | None,
        typer.Option(
            "--skip-if-in-channels",
            help="When --recursive is used, it will build\n     *all* recipes. Use this argument to skip recursive building for packages\n     that already exist in the packages listed here.",
        ),
    ] = None,
    loglevel: LoglevelOpt = "debug",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Build a Bioconductor recipe. The recipe will be created in the 'recipes'
    directory and will be prefixed by "bioconductor-". If --recursive is set,
    then any R dependency recipes will be prefixed by "r-".

    These R recipes must be evaluated on a case-by-case basis to determine if
    they are relevant to biology (in which case they should be submitted to
    bioconda) or not (submit to conda-forge).

    Biology-related:
        'bioconda-utils clean-cran-skeleton <recipe> --no-windows'
        and submit to Bioconda.

    Not bio-related:
        'bioconda-utils clean-cran-skeleton <recipe>'
        and submit to conda-forge."""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    skip_if_in_channels = (
        skip_if_in_channels
        if skip_if_in_channels is not None
        else ["conda-forge", "bioconda"]
    )
    seen_dependencies = set()
    if bioc_data_packages is None:
        bioc_data_packages = os.path.join(recipe_folder, "bioconductor-data-packages")
    if package == "update-all-packages":
        if not bioc_version:
            bioc_version = _bioconductor_skeleton.latest_bioconductor_release_version()
        packages = _bioconductor_skeleton.fetchPackages(bioc_version)
        needs_x = _bioconductor_skeleton.packagesNeedingX(packages)
        problems = []
        for k, v in packages.items():
            try:
                _bioconductor_skeleton.write_recipe(
                    k,
                    recipe_folder,
                    config,
                    bioc_data_packages=bioc_data_packages,
                    force=True,
                    bioc_version=bioc_version,
                    pkg_version=v["Version"],
                    versioned=versioned,
                    packages=packages,
                    skip_if_in_channels=skip_if_in_channels,
                    needs_x=k in needs_x,
                )
            except Exception:
                problems.append(k)
        if len(problems):
            sys.exit(
                "The following recipes had problems and were not finished: {}".format(
                    ", ".join(problems)
                )
            )
    else:
        _bioconductor_skeleton.write_recipe(
            package,
            recipe_folder,
            config,
            bioc_data_packages,
            force=force,
            bioc_version=bioc_version,
            pkg_version=pkg_version,
            versioned=versioned,
            recursive=recursive,
            seen_dependencies=seen_dependencies,
            skip_if_in_channels=skip_if_in_channels,
        )
    sys.stderr.write(
        "Warning! Make sure to bump bioconductor-data-packages if needed!\n"
    )


@app.command("clean-cran-skeleton")
def clean_cran_skeleton(
    recipe: Annotated[str, typer.Argument(help="Path to recipe to be cleaned")],
    no_windows: Annotated[
        bool,
        typer.Option(
            "--no-windows",
            help="Use this when submitting an\n     R package to Bioconda. After a CRAN skeleton is created, any\n     Windows-related lines will be removed and the bld.bat file will be\n     removed.",
        ),
    ] = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Cleans skeletons created by ``conda skeleton cran``.

    Before submitting to conda-forge or Bioconda, recipes generated with ``conda
    skeleton cran`` need to be cleaned up: comments removed, licenses fixed, and
    other linting.

    Use --no-windows for a Bioconda submission."""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    cran_skeleton.clean_skeleton_files(recipe, no_windows=no_windows)


@app.command("autobump")
def autobump(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob(s) for package[s] to scan. Can be specified more than once",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="Globs for package[s] to exclude from scan. Can be specified more than once",
        ),
    ] = None,
    cache: Annotated[
        str | None,
        typer.Option(
            "--cache",
            help="To speed up debugging, use repodata cached locally in\n     the provided filename. If the file does not exist, it will be created\n     the first time. Caution: The cache will not be updated if\n     exclude-channels is changed",
        ),
    ] = None,
    failed_urls: Annotated[
        str | None,
        typer.Option(
            "--failed-urls", help="Write urls with permanent failure to this file"
        ),
    ] = None,
    unparsed_urls: Annotated[
        str | None,
        typer.Option("--unparsed-urls", help="Write unrecognized urls to this file"),
    ] = None,
    recipe_status: Annotated[
        str | None,
        typer.Option(
            "--recipe-status", help="Write status for each recipe to this file"
        ),
    ] = None,
    exclude_subrecipes: Annotated[
        str | None,
        typer.Option(
            "--exclude-subrecipes",
            help="By default, only subrecipes explicitly\n     enabled for watch in meta.yaml are considered. Set to 'always' to\n     exclude all subrecipes.  Set to 'never' to include all subrecipes",
        ),
    ] = None,
    exclude_channels: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-channels",
            help="Exclude recipes\n     building packages present in other channels. Set to 'none' to disable\n     check.",
        ),
    ] = None,
    ignore_skiplists: Annotated[
        bool,
        typer.Option("--ignore-skiplists", help="Do not exclude skiplisted recipes"),
    ] = False,
    fetch_requirements: Annotated[
        bool,
        typer.Option(
            "--fetch-requirements",
            help="Try to fetch python requirements. Please note that this requires\n     downloading packages and executing setup.py, so presents a potential\n     security problem.",
        ),
    ] = False,
    check_branch: Annotated[
        bool, typer.Option("--check-branch", help="Check if recipe has active branch")
    ] = False,
    create_branch: Annotated[
        bool,
        typer.Option("--create-branch", help="Create branch for each\n     update"),
    ] = False,
    create_pr: Annotated[
        bool,
        typer.Option(
            "--create-pr",
            help="Create PR for each update.\n     Implies create-branch.",
        ),
    ] = False,
    only_active: Annotated[
        bool,
        typer.Option("--only-active", help="Check only recipes with active update"),
    ] = False,
    no_shuffle: Annotated[
        bool, typer.Option("--no-shuffle", help="Do not shuffle recipe order")
    ] = False,
    max_updates: Annotated[
        int, typer.Option("--max-updates", help="Stop after this many updates")
    ] = 0,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Don't update remote git or github\"")
    ] = False,
    no_check_pinnings: Annotated[
        bool,
        typer.Option("--no-check-pinnings", help="Don't check for pinning updates"),
    ] = False,
    no_follow_graph: Annotated[
        bool,
        typer.Option(
            "--no-follow-graph",
            help="Don't process recipes in graph order or add dependent recipes\n     to checks. Implies --no-skip-pending-deps.",
        ),
    ] = False,
    no_check_version_update: Annotated[
        bool,
        typer.Option(
            "--no-check-version-update",
            help="Don't check for version updates to recipes",
        ),
    ] = False,
    no_check_pending_deps: Annotated[
        bool,
        typer.Option(
            "--no-check-pending-deps",
            help="Don't check for recipes having a dependency with a pending update.\n     Update all recipes, including those having deps in need or rebuild.",
        ),
    ] = False,
    sign: Annotated[
        bool,
        typer.Option("--sign", help="Sign commits using Git's default signing key."),
    ] = False,
    sign_key: Annotated[
        str | None, typer.Option("--sign-key", help="Sign commits using this key ID.")
    ] = None,
    commit_as: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--commit-as",
            help="Set user and email to use for committing. Takes exactly two arguments.",
        ),
    ] = None,
    threads: ThreadsOpt = 16,
    pdb: PdbOpt = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Updates recipes in recipe_folder"""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines, threads)
    package_patterns: PackagePatterns = packages or "*"
    excluded_channels = exclude_channels or ["conda-forge"]
    use_default_signing_key = sign and sign_key is None
    try:
        config_dict = utils.load_config(config)
        from . import autobump
        from . import githubhandler
        from . import hosters

        if no_follow_graph:
            recipe_source = autobump.RecipeSource(
                recipe_folder, package_patterns, exclude or [], not no_shuffle
            )
        else:
            recipe_source = autobump.RecipeGraphSource(
                recipe_folder,
                package_patterns,
                exclude or [],
                not no_shuffle,
                config_dict,
                cache_fn=cache and cache + "_dag.pkl",
            )
        scanner = autobump.Scanner(
            recipe_source,
            cache_fn=cache and cache + "_scan.pkl",
            status_fn=recipe_status,
        )
        scanner.add(autobump.ExcludeDisabled)
        if not ignore_skiplists:
            scanner.add(autobump.ExcludeBlacklisted, recipe_folder, config_dict)
        if exclude_subrecipes != "never":
            scanner.add(
                autobump.ExcludeSubrecipe, always=exclude_subrecipes == "always"
            )
        if not no_check_pending_deps and isinstance(
            recipe_source, autobump.RecipeGraphSource
        ):
            scanner.add(autobump.ExcludeDependencyPending, recipe_source.dag)
        git_handler = None
        if check_branch or create_branch or create_pr or only_active:
            git_handler = BiocondaRepo(recipe_folder, dry_run)
            git_handler.checkout_master()
            if only_active:
                scanner.add(autobump.ExcludeNoActiveUpdate, git_handler)
            scanner.add(autobump.GitLoadRecipe, git_handler)
            env_key = os.environ.get("CODE_SIGNING_KEY")
            if use_default_signing_key:
                git_handler.enable_signing()
            elif sign_key is not None:
                git_handler.enable_signing(sign_key)
            elif env_key:
                try:
                    git_handler.enable_signing(install_gpg_key(env_key))
                except ValueError as exc:
                    logger.error(
                        "Failed to use CODE_SIGNING_KEY from environment: %s", exc
                    )
            if commit_as:
                git_handler.set_user(*commit_as)
        else:
            scanner.add(autobump.LoadRecipe)
            if sign or sign_key is not None:
                logger.warning("Not using git. --sign has no effect")
        if excluded_channels != ["none"]:
            scanner.add(
                autobump.ExcludeOtherChannel,
                excluded_channels,
                cache and cache + "_repodata.txt",
            )
        if not no_check_pinnings:
            scanner.add(autobump.CheckPinning)
        if not no_check_version_update:
            scanner.add(
                autobump.UpdateVersion, hosters.Hoster.select_hoster, unparsed_urls
            )
            if fetch_requirements:
                scanner.add(autobump.FetchUpstreamDependencies)
            scanner.add(autobump.UpdateChecksums, failed_urls)
        if create_branch or create_pr:
            scanner.add(autobump.GitWriteRecipe, git_handler)
        else:
            scanner.add(autobump.WriteRecipe)
        if create_pr:
            token = os.environ.get("GITHUB_TOKEN")
            if not token and (not dry_run):
                logger.critical("GITHUB_TOKEN required to create PRs")
                exit(1)
            github_handler = githubhandler.AiohttpGitHubHandler(
                token, dry_run, "bioconda", "bioconda-recipes"
            )
            scanner.add(autobump.CreatePullRequest, git_handler, github_handler)
        if max_updates:
            scanner.add(autobump.MaxUpdates, max_updates)
        scanner.run()
        if git_handler:
            git_handler.close()
    except Exception:
        if pdb:
            import pdb as debugger

            logger.exception("Dropping into debugger")
            debugger.post_mortem()
            return None
        raise


@app.command("handle-merged-pr")
def handle_merged_pr(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help="Name of the github repository to check (e.g. bioconda/bioconda-recipes).",
        ),
    ] = None,
    git_range: Annotated[
        list[str] | None,
        typer.Option(
            "--git-range",
            help='Git range (e.g. commits or something like\n     "master HEAD" to check commits in HEAD vs master, or just "HEAD" to\n     include uncommitted changes). All recipes modified within this range will\n     be built if not present in the channel.',
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Do not actually upload anything.")
    ] = False,
    fallback: Annotated[
        Literal["build", "ignore"],
        typer.Option(
            "--fallback", help="What to do if no artifacts are found in the PR."
        ),
    ] = "build",
    quay_upload_target: Annotated[
        str | None,
        typer.Option(
            "--quay-upload-target",
            help="Provide a quay.io target to push docker images to.",
        ),
    ] = None,
    artifact_source: Annotated[
        Literal["azure", "circleci", "github-actions"],
        typer.Option(
            "--artifact-source",
            help="Application hosting build artifacts (e.g., Azure, Circle CI, or GitHub Actions).",
        ),
    ] = "azure",
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Upload artifacts from a merged pull request."""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    label = os.getenv("BIOCONDA_LABEL", None) or None
    if repo is None:
        raise ValueError("repo is required")
    if git_range is None:
        raise ValueError("git_range is required")
    res = upload_pr_artifacts(
        config,
        repo,
        git_range[1],
        dryrun=dry_run,
        mulled_upload_target=quay_upload_target,
        label=label,
        artifact_source=artifact_source,
    )
    if res == UploadResult.NO_ARTIFACTS and fallback == "build":
        success = build(
            recipe_folder,
            config,
            git_range=git_range,
            anaconda_upload=not dry_run,
            mulled_upload_target=quay_upload_target if not dry_run else None,
            mulled_test=True,
        )
    else:
        success = res != UploadResult.FAILURE
    exit(0 if success else 1)


@app.command("annotate-build-failures")
def annotate_build_failures(
    recipes: Annotated[
        list[str], typer.Argument(help="Paths to recipes that shall be skiplisted")
    ],
    skiplist: Annotated[
        bool, typer.Option("--skiplist", help="Skiplist recipes.")
    ] = False,
    reason: Annotated[
        str | None,
        typer.Option(
            "--reason",
            help="Reason for skiplisting. If omitted, will fail if there is no existing build failure record with a log entry.",
        ),
    ] = None,
    category: Annotated[
        Literal[
            "compiler error",
            "conda/mamba bug",
            "test failure",
            "dependency issue",
            "checksum mismatch",
            "source download error",
        ]
        | None,
        typer.Option(
            "--category",
            help="Category of build failure. If omitted, will fail if there is no existing build failure record with a log entry.",
        ),
    ] = None,
    platforms: Annotated[
        list[str] | None, typer.Option("--platforms", help="Platforms to annotate")
    ] = None,
    existing_only: Annotated[
        bool,
        typer.Option(
            "--existing-only",
            help="Only annotate already existing build failure records. The platform setting is ignored in this case.",
        ),
    ] = False,
) -> None:
    """Create or update recipe build-failure records."""
    valid_platform_names = set(conda.base.constants.PLATFORM_DIRECTORIES)
    if platforms is None:
        platforms = [
            utils.RepoData.platform2subdir(p)
            for p in utils.RepoData.platforms
            if p != "noarch"
        ]
    for recipe in recipes:
        if existing_only:
            platforms = [
                platform
                for platform in conda.base.constants.PLATFORM_DIRECTORIES
                if BuildFailureRecord(recipe, platform=platform).exists()
            ]
        for platform in platforms:
            if platform not in valid_platform_names:
                logger.error(
                    f"Invalid platform {platform}, choose from: {', '.join(valid_platform_names)}"
                )
                continue
            failure_record = BuildFailureRecord(recipe, platform=platform)
            if not reason and failure_record.exists():
                if not failure_record.log:
                    logger.error(
                        f"Recipe {recipe} has a build failure record ({failure_record.path}), but no log entry. Please add a log entry or specify a reason."
                    )
                    continue
                if failure_record.recipe_sha != failure_record.get_recipe_sha():
                    logger.error(
                        f"Recipe {recipe} has a build failure record ({failure_record.path}), but the recipe has changed since recording the build log. Please specify a reason for skipping or rebuild for updating the log."
                    )
                    continue
            failure_record.fill(reason=reason, category=category, skiplist=skiplist)
            failure_record.write()


@app.command("list-build-failures")
def list_build_failures(
    recipe_folder: RecipeFolderArg = None,
    config: ConfigArg = None,
    channel: Annotated[
        str, typer.Option("--channel", help="Channel with packages to check")
    ] = "bioconda",
    output_format: Annotated[
        Literal["txt", "markdown"],
        typer.Option("--output-format", help="Output format"),
    ] = "txt",
    link_prefix: Annotated[
        str, typer.Option("--link-prefix", help="Prefix for links to build failures")
    ] = "",
    git_range: Annotated[
        list[str] | None,
        typer.Option(
            "--git-range",
            help='Git range (e.g. commits or something like\n     "master HEAD" to check commits in HEAD vs master, or just "HEAD" to\n     include uncommitted changes).',
        ),
    ] = None,
) -> None:
    """List recipes with build failure records"""
    recipe_folder = recipe_folder or "recipes/"
    config = config or "config.yml"
    config_data = utils.load_config(config)
    df = collect_build_failure_dataframe(
        recipe_folder,
        config_data,
        channel,
        link_fmt=output_format,
        link_prefix=link_prefix,
        git_range=git_range,
    )
    if output_format == "markdown":
        fmt_writer = pandas.DataFrame.to_markdown
    elif output_format == "txt":
        fmt_writer = pandas.DataFrame.to_string
    else:
        logger.error("Invalid output format, must be txt or markdown.")
        exit(1)
    fmt_writer(df, sys.stdout, index=False)


@app.command("bulk-trigger-ci")
def bulk_trigger_ci() -> None:
    """Create an empty commit with the string "[ci run]" and push, which
    triggers a bulk CI run. Must be on the `bulk` branch."""
    bulk.trigger_ci()


def main() -> None:
    app()
