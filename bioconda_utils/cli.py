"""Bioconda Utils command-line interface built with Typer."""

# Workaround for spurious numpy warning message
# ".../importlib/_bootstrap.py:219: RuntimeWarning: numpy.dtype size \
# changed, may indicate binary incompatibility. Expected 96, got 88"
import importlib
import logging
import os
import shlex
import sys
import warnings
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal

import click
import networkx as nx
import pandas
import requests
import typer
from networkx.drawing.nx_pydot import write_dot

from bioconda_utils import bulk
from bioconda_utils.artifacts import ArtifactSource, UploadResult, upload_pr_artifacts
from bioconda_utils.build_failure import (
    BuildFailureRecord,
    collect_build_failure_dataframe,
)
from bioconda_utils.skiplist import Skiplist

from . import __version__ as VERSION
from . import bioconductor_skeleton as _bioconductor_skeleton
from . import cran_skeleton, docker_utils, graph, pkg_test, update_pinnings, utils
from . import lint as _lint
from ._types import (
    ALL_CONTAINER_PLATFORMS,
    ALL_PACKAGE_SUBDIRS,
    ContainerPlatform,
    PackageSubdir,
    QuayUploadTarget,
    package_subdir_to_container_platform,
    parse_quay_upload_target,
)
from .build import build_recipes
from .container_manifests import (
    DEFAULT_MULLED_RECORDS_DIR,
    load_image_records,
    reconcile_manifests,
    resolve_registry_creds,
)
from .githandler import BiocondaRepo, GitRange, install_gpg_key

warnings.filterwarnings("ignore", message="numpy.dtype size changed")

app = typer.Typer(
    help="Utilities for building and maintaining Bioconda recipes.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=None,
)
logger = logging.getLogger(__name__)

# A package is the name of the software package, like `bowtie`.
#
# A recipe is the path to the recipe of one version of a package, like
# `recipes/bowtie` or `recipes/bowtie/1.0.1`.

LogLevel = Literal["debug", "info", "warning", "error", "critical"]
PackagePatterns = list[str]

# Shared CLI parameter type aliases


def _validate_path_exists(value: Path) -> Path:
    if not value.exists():
        raise typer.BadParameter(f"path '{value}' does not exist")
    return value


def _parse_quay_upload_target(value: str | None) -> QuayUploadTarget | None:
    try:
        return parse_quay_upload_target(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _resolve_image_records_dir(
    records: Path | None, upload_target: QuayUploadTarget | None
) -> Path | None:
    if records:
        return records
    if upload_target:
        return DEFAULT_MULLED_RECORDS_DIR
    return None


def _validate_positive_int(value: int) -> int:
    if value < 1:
        raise typer.BadParameter("must be a positive integer")
    return value


def _parse_git_range(value: str) -> GitRange:
    try:
        return GitRange.parse(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _parse_git_range_if_needed(git_range: str | None) -> GitRange | None:
    if git_range is None:
        return None
    return _parse_git_range(git_range)


def _container_platform_for_build(
    platform: PackageSubdir | None, docker: bool
) -> ContainerPlatform | None:
    if platform is None:
        return None
    if not docker:
        raise typer.BadParameter("requires --docker", param_hint="--platform")
    try:
        return package_subdir_to_container_platform(platform)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--platform") from exc


def _handle_pdb_exception(command_name: str, pdb: bool) -> bool:
    """Log exception and optionally enter debugger.

    Returns True if pdb was entered (caller should return),
    False if caller should re-raise.
    """
    logger.exception(f"{command_name} command failed")
    if pdb:
        importlib.import_module("pdb").post_mortem()
        return True
    return False


LoglevelOpt = Annotated[
    LogLevel,
    typer.Option(
        "--loglevel", help="Set logging level (debug, info, warning, error, critical)"
    ),
]
LogfileOpt = Annotated[Path | None, typer.Option("--logfile", help="Write log to file")]
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
    Path,
    typer.Argument(
        help="Path to folder containing recipes (default: recipes/)",
        callback=_validate_path_exists,
    ),
]
ConfigArg = Annotated[
    Path,
    typer.Argument(
        help="Path to Bioconda config (default: config.yml)",
        callback=_validate_path_exists,
    ),
]
# Lint defers path validation so --list-checks can run without a recipe checkout.
LintRecipeFolderArg = Annotated[
    Path,
    typer.Argument(help="Path to folder containing recipes (default: recipes/)"),
]
LintConfigArg = Annotated[
    Path,
    typer.Argument(help="Path to Bioconda config (default: config.yml)"),
]
ThreadsOpt = Annotated[
    int,
    typer.Option(
        "-t",
        "--threads",
        help="Limit maximum number of processes used.",
        callback=_validate_positive_int,
    ),
]
PackagesOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--packages",
        help="Package name(s) or glob pattern(s). Can be specified more than once.",
    ),
]
PdbOpt = Annotated[
    bool, typer.Option("-P", "--pdb", help="Drop into debugger on exception")
]
GitRangeOpt = Annotated[
    str | None,
    typer.Option(
        "--git-range",
        metavar="BASE[...REF]",
        help=(
            "Select changes on REF since its merge base with BASE. "
            "BASE alone means BASE...HEAD."
        ),
    ),
]
UseExistingAuthOpt = Annotated[
    bool,
    typer.Option(
        "--use-existing-auth",
        help="Use existing Docker or skopeo registry authentication when Quay credentials are unset.",
    ),
]
ImageRecordsDirOpt = Annotated[
    Path | None,
    typer.Option(
        "--image-records-dir",
        metavar="DIR",
        help=(
            "DIR for one JSONL image record per uploaded image; consumed by "
            "the create-mulled-manifests command. Defaults to "
            "~/.local/share/bioconda-utils/mulled-records/ when "
            "--container-upload-target is set."
        ),
    ),
]


def get_recipes_to_build(git_range: GitRange, recipe_folder: Path) -> list[Path]:
    """Gets list of modified recipes according to git_range and blacklist

    See `BiocondaRepoMixin.get_recipes_to_build()`.

    Arguments:
      git_range: Base and ref whose merge-base-to-ref changes are selected.
    Returns:
      List of recipes for which meta.yaml or build.sh was modified or
      which were unblacklisted.
    """
    repo = BiocondaRepo(recipe_folder)
    return [
        Path(recipe)
        for recipe in repo.get_recipes_to_build(git_range.ref, git_range.base)
    ]


def get_recipes(
    config: dict[str, Any],
    recipe_folder: Path,
    packages: PackagePatterns,
    git_range: GitRange | None,
    include_blacklisted: bool = False,
) -> list[Path]:
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


@app.command("diagnostics")
def diagnostics() -> None:
    """Print details about the active Bioconda build environment."""
    config = utils.load_conda_build_config()

    typer.echo(f"bioconda-utils version: {VERSION}")
    typer.echo(f"package subdir: {config.subdir}")
    typer.echo(f"conda-build root: {config.croot}")
    typer.echo("conda-build configuration files:")
    for filename in config.exclusive_config_files or []:
        path = Path(filename)
        typer.echo(f"{path}:")
        contents = path.read_text(encoding="utf-8")
        typer.echo(contents, nl=not contents.endswith("\n"))


@app.command("build")
def build(
    recipe_folder: Annotated[
        Path,
        typer.Argument(help="Path to folder containing recipes (default: recipes/)"),
    ] = Path("recipes/"),
    config: Annotated[
        Path, typer.Argument(help="Path to Bioconda config (default: config.yml)")
    ] = Path("config.yml"),
    packages: Annotated[
        list[str] | None,
        typer.Option(
            "--packages",
            help="Glob for package[s] to build. Default is to build all packages. Can be specified more than once",
        ),
    ] = None,
    git_range: GitRangeOpt = None,
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
    platform: Annotated[
        PackageSubdir | None,
        typer.Option(
            "--platform",
            help="Conda package subdirectory to build. Requires --docker.",
        ),
    ] = None,
    mulled_build_and_test: Annotated[
        bool,
        typer.Option(
            "--mulled-build-and-test",
            help="Build a mulled container for the package and run the recipe's tests inside it.",
        ),
    ] = False,
    build_script_template: Annotated[
        Path | None,
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
    container_upload_target: Annotated[
        str | None,
        typer.Option(
            "--container-upload-target",
            help="Provide a quay.io target to push container images to.",
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
    ] = pkg_test.CREATE_ENV_IMAGE,
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
    presolved_mulled_build_and_test: Annotated[
        bool,
        typer.Option(
            "--presolved-mulled-build-and-test/--no-presolved-mulled-build-and-test",
            help="Use the pre-solved mulled build-and-test path.",
        ),
    ] = True,
    no_fast_resolve: Annotated[
        bool,
        typer.Option(
            "--no-fast-resolve",
            help="Disable fast resolve: always run the full finalized conda solver on the host, even when building with Docker. Useful for debugging build string mismatches.",
        ),
    ] = False,
    image_records_dir: ImageRecordsDirOpt = None,
    use_existing_auth: UseExistingAuthOpt = False,
    container_pkgs_cache: Annotated[
        Path | None,
        typer.Option(
            "--container-pkgs-cache",
            help="Host directory bind-mounted at /opt/conda/pkgs in build "
            "containers (--docker) so repodata, shards indexes and "
            "downloaded build/host env packages persist across the "
            "containers of one build run. Falls back to the "
            "BIOCONDA_UTILS_CONTAINER_PKGS_CACHE environment variable.",
        ),
    ] = None,
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
    threads: ThreadsOpt = 16,
    repodata_cache: Annotated[
        str | None,
        typer.Option(
            "--repodata-cache",
            help="To speed up startup, use repodata cached locally in\n     the provided filename. If the file does not exist, it will be created the\n     first time. The cache is refreshed when it is older than 8 hours.",
        ),
    ] = None,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Build and test Bioconda recipes."""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines, threads)
    target_platform = _container_platform_for_build(platform, docker)
    parsed_upload_target = _parse_quay_upload_target(container_upload_target)
    image_records_dir = _resolve_image_records_dir(
        image_records_dir, parsed_upload_target
    )
    package_patterns: PackagePatterns = packages or ["*"]
    parsed_git_range = _parse_git_range_if_needed(git_range)
    cfg = utils.load_config(config)
    if repodata_cache is not None:
        utils.RepoData().set_cache(repodata_cache)
    setup = cfg.get("setup", None)
    if setup:
        logger.debug("Running setup: %s", setup)
        for cmd in setup:
            utils.run(shlex.split(cmd))
    recipes = get_recipes(cfg, recipe_folder, package_patterns, parsed_git_range)
    if docker:
        if build_script_template is not None:
            build_script_content = build_script_template.read_text()
        else:
            build_script_content = docker_utils.BUILD_SCRIPT_TEMPLATE
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
            build_script_template=build_script_content,
            pkg_dir=package_dir,
            use_host_conda_bld=use_host_conda_bld,
            keep_image=keep_image,
            build_image=build_image,
            docker_base_image=docker_base_image,
            target_platform=target_platform,
            container_pkgs_cache=(
                str(container_pkgs_cache) if container_pkgs_cache else None
            ),
        )
    else:
        docker_builder = None
    if lint_exclude and (not lint):
        logger.warning("--lint-exclude has no effect unless --lint is specified.")
    label = os.getenv("BIOCONDA_LABEL", None) or None
    success = build_recipes(
        recipe_folder,
        cfg,
        recipes,
        testonly=test_only,
        force=force,
        mulled_build_and_test=mulled_build_and_test,
        docker_builder=docker_builder,
        anaconda_upload=anaconda_upload,
        mulled_upload_target=parsed_upload_target,
        do_lint=lint,
        lint_exclude=lint_exclude,
        check_channels=check_channels,
        label=label,
        n_workers=n_workers,
        worker_offset=worker_offset,
        keep_old_work=keep_old_work,
        mulled_conda_image=mulled_conda_image,
        record_build_failures=record_build_failures,
        skiplist_leaves=skiplist_leaves,
        live_logs=not disable_live_logs,
        exclude=exclude,
        subdag_depth=subdag_depth,
        presolved_mulled_build_and_test=presolved_mulled_build_and_test,
        fast_resolve=not no_fast_resolve,
        target_platform=target_platform,
        image_records_dir=image_records_dir,
        use_existing_auth=use_existing_auth,
    )
    sys.exit(0 if success else 1)


@app.command("dag")
def dag(
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
    packages: PackagesOpt = None,
    output_format: Annotated[
        Literal["gml", "dot", "txt"],
        typer.Option(
            "--output-format",
            help='Output format. "gml" and "dot" serialize the graph for visualization tools. "txt" lists recipe paths in dependency-first order, grouped by disconnected components. Disconnected packages are listed together at the end unless --hide-singletons is used.',
        ),
    ] = "gml",
    hide_singletons: Annotated[
        bool,
        typer.Option(
            "--hide-singletons",
            help="Omit packages with no dependency relationship to another selected package.",
        ),
    ] = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Export the dependency DAG among selected packages.

    Nodes are packages. An edge from A to B means that B has A as a build,
    host, or run dependency.
    """
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    package_patterns: PackagePatterns = packages or ["*"]
    config_data = utils.load_config(config)
    dag, name2recipes = graph.build(
        utils.get_recipes(recipe_folder, package_patterns), config_data
    )
    if hide_singletons:
        dag.remove_nodes_from(list(nx.isolates(dag)))
    if output_format == "gml":
        nx.write_gml(dag, sys.stdout.buffer)
    elif output_format == "dot":
        write_dot(dag, sys.stdout)
    elif output_format == "txt":
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
            print("\n".join(map(os.fspath, recipes)) + "\n")
        if not hide_singletons:
            print("# singletons")
            recipes = [
                recipe for package in singletons for recipe in name2recipes[package]
            ]
            print("\n".join(map(os.fspath, recipes)) + "\n")


@app.command("dependent")
def dependent(
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
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
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    if dependencies and reverse_dependencies:
        raise click.UsageError(
            "`dependencies` and `reverse_dependencies` are mutually exclusive"
        )
    if not any([dependencies, reverse_dependencies]):
        raise click.UsageError(
            "One of `--dependencies` or `--reverse-dependencies` is required."
        )
    config_data = utils.load_config(config)
    d, _ = graph.build(utils.get_recipes(recipe_folder), config_data, restrict=restrict)
    if reverse_dependencies is not None:
        dependency_func = nx.algorithms.descendants
        selected_packages = reverse_dependencies
    else:
        dependency_func = nx.algorithms.ancestors
        selected_packages = dependencies or []
    pkgs = []
    for pkg in selected_packages:
        pkgs.extend(dependency_func(d, pkg))
    print("\n".join(sorted(set(pkgs))))


@app.command("lint")
def lint(
    recipe_folder: LintRecipeFolderArg = Path("recipes/"),
    config: LintConfigArg = Path("config.yml"),
    packages: PackagesOpt = None,
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
    git_range: GitRangeOpt = None,
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
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    package_patterns: PackagePatterns = packages or ["*"]
    try:
        parsed_git_range = _parse_git_range_if_needed(git_range)
        if list_checks:
            print("\n".join(str(check) for check in _lint.get_checks()))
            sys.exit(0)
        _validate_path_exists(recipe_folder)
        _validate_path_exists(config)
        config_data = utils.load_config(config)
        if cache is not None:
            utils.RepoData().set_cache(cache)
        recipes = get_recipes(
            config_data,
            recipe_folder,
            package_patterns,
            parsed_git_range,
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
        if _handle_pdb_exception("Lint", pdb):
            return
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
    config_data = utils.load_config(Path(config))
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
                token_val = os.environ.get("ANACONDA_TOKEN")
                if token_val is None:
                    token_args = []
                    secrets = None
                else:
                    token_args = ["-t", token_val]
                    secrets = [token_val]
                logger.info(
                    utils.run(
                        [utils.bin_for("anaconda")] + token_args + subcmd,
                        secrets=secrets,
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
    for candidate_channel in channels:
        package_specs = set(repodata.get_package_data(check_fields, candidate_channel))
        logger.info(
            "%s unique packages specs to consider in %s",
            len(package_specs),
            candidate_channel,
        )
        dups = our_package_specs & package_specs
        logger.info("  (of which %s are duplicate)", len(dups))
        for spec in dups:
            duplicate[spec].append(candidate_channel)
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
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
    packages: PackagesOpt = None,
    skip_additional_channels: Annotated[
        list[str] | None,
        typer.Option(
            "--skip-additional-channels",
            help="Skip updating/bumping packages that are already built with\n     compatible pinnings in one of the given channels in addition to those\n     listed in 'config'.",
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
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines, threads)
    package_patterns: PackagePatterns = packages or ["*"]
    try:
        config_data = utils.load_config(config)
        if skip_additional_channels:
            config_data["channels"] += skip_additional_channels
        variant_keys = frozenset(skip_variants or ())
        if cache:
            utils.RepoData().set_cache(cache)
        _ = utils.RepoData().df
        build_config = utils.load_conda_build_config()
        skiplist = Skiplist(config_data, recipe_folder)
        from . import recipe

        dag = graph.build_from_recipes(
            r
            for r in recipe.load_parallel_iter(recipe_folder, ["*"])
            if not skiplist.is_skiplisted(r)
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
        if _handle_pdb_exception("Update-pinning", pdb):
            return
        raise


@app.command("bioconductor-skeleton")
def bioconductor_skeleton(
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
    packages: PackagesOpt = None,
    update_all: Annotated[
        bool,
        typer.Option(
            "--update-all", help="Update all packages in a given Bioconductor release."
        ),
    ] = False,
    bioc_data_packages: Annotated[
        str | None,
        typer.Option(
            "--bioc-data-packages",
            help="Path to folder containing the recipe for the bioconductor-data-packages\n     (default: recipes/bioconductor-data-packages)",
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
            help="When --recursive is used, it will build\n     *all* recipes. Use this argument to skip recipes for packages\n     that already exist in the channels listed here.",
        ),
    ] = None,
    loglevel: LoglevelOpt = "debug",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Build Bioconductor recipes. Recipes will be created in the 'recipes'
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
        and submit to conda-forge.

    Examples:
        bioconda-utils bioconductor-skeleton --packages DESeq2
        bioconda-utils bioconductor-skeleton --packages DESeq2 --packages edgeR --recursive
        bioconda-utils bioconductor-skeleton --update-all"""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    config_data = utils.load_config(config)
    skip_if_in_channels = (
        skip_if_in_channels
        if skip_if_in_channels is not None
        else ["conda-forge", "bioconda"]
    )
    seen_dependencies = set()
    if bioc_data_packages is None:
        bioc_data_packages = os.path.join(recipe_folder, "bioconductor-data-packages")
    if update_all:
        if not bioc_version:
            bioc_version = _bioconductor_skeleton.latest_bioconductor_release_version()
        all_packages = _bioconductor_skeleton.fetchPackages(bioc_version)
        needs_x = _bioconductor_skeleton.packagesNeedingX(all_packages)
        problems = []
        for k, v in all_packages.items():
            try:
                _bioconductor_skeleton.write_recipe(
                    k,
                    recipe_folder,
                    config_data,
                    bioc_data_packages=bioc_data_packages,
                    force=True,
                    bioc_version=bioc_version,
                    pkg_version=v["Version"],
                    versioned=versioned,
                    packages=all_packages,
                    skip_if_in_channels=skip_if_in_channels,
                    needs_x=k in needs_x,
                )
            except (OSError, RuntimeError, ValueError, requests.RequestException):
                problems.append(k)
        if len(problems):
            sys.exit(
                "The following recipes had problems and were not finished: {}".format(
                    ", ".join(problems)
                )
            )
    elif packages:
        for pkg in packages:
            _bioconductor_skeleton.write_recipe(
                pkg,
                recipe_folder,
                config_data,
                bioc_data_packages,
                force=force,
                bioc_version=bioc_version,
                pkg_version=pkg_version,
                versioned=versioned,
                recursive=recursive,
                seen_dependencies=seen_dependencies,
                skip_if_in_channels=skip_if_in_channels,
            )
    else:
        raise click.UsageError("Either --packages or --update-all must be specified.")
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
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
    packages: PackagesOpt = None,
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
        Path | None,
        typer.Option(
            "--failed-urls", help="Write urls with permanent failure to this file"
        ),
    ] = None,
    unparsed_urls: Annotated[
        Path | None,
        typer.Option("--unparsed-urls", help="Write unrecognized urls to this file"),
    ] = None,
    recipe_status: Annotated[
        Path | None,
        typer.Option(
            "--recipe-status", help="Write status for each recipe to this file"
        ),
    ] = None,
    exclude_subrecipes: Annotated[
        Literal["always", "never"] | None,
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
        bool, typer.Option("--dry-run", help="Don't update remote git or github.")
    ] = False,
    no_check_pinnings: Annotated[
        bool,
        typer.Option("--no-check-pinnings", help="Don't check for pinning updates"),
    ] = False,
    no_follow_graph: Annotated[
        bool,
        typer.Option(
            "--no-follow-graph",
            help="Don't process recipes in graph order or add dependent recipes\n     to checks. Implies --no-check-pending-deps.",
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
            help="Don't check for recipes having a dependency with a pending update.\n     Update all recipes, including those having deps in need of rebuild.",
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
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines, threads)
    package_patterns: PackagePatterns = packages or ["*"]
    excluded_channels = exclude_channels or ["conda-forge"]
    use_default_signing_key = sign and sign_key is None
    try:
        # load and register config
        config_dict = utils.load_config(config)
        from . import autobump, githubhandler

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
        # Setup scanning pipeline
        scanner = autobump.Scanner(
            recipe_source,
            cache_fn=cache and cache + "_scan.pkl",
            status_fn=recipe_status,
        )

        # Always exclude recipes that were explicitly disabled
        scanner.add(autobump.ExcludeDisabled)

        # Exclude packages that are on the blacklist
        if not ignore_skiplists:
            scanner.add(autobump.ExcludeBlacklisted, recipe_folder, config_dict)

        # Exclude sub-recipes
        if exclude_subrecipes != "never":
            scanner.add(
                autobump.ExcludeSubrecipe, always=exclude_subrecipes == "always"
            )

        # Exclude recipes with dependencies pending an update
        if not no_check_pending_deps and isinstance(
            recipe_source, autobump.RecipeGraphSource
        ):
            scanner.add(autobump.ExcludeDependencyPending, recipe_source.dag)

        # Load recipe
        git_handler = None
        if check_branch or create_branch or create_pr or only_active:
            # We need to take the recipe from the git repo. This
            # loads the bump/<recipe> branch if available
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
            # Just load from local file system
            scanner.add(autobump.LoadRecipe)
            if sign or sign_key is not None:
                logger.warning("Not using git. --sign has no effect")

        # Exclude recipes that are present in "other channels"
        if excluded_channels != ["none"]:
            scanner.add(
                autobump.ExcludeOtherChannel,
                excluded_channels,
                cache and cache + "_repodata.txt",
            )
        # Test if due to pinnings, the package hash would change and a rebuild
        # has become necessary. If so, bump the buildnumber.
        if not no_check_pinnings:
            scanner.add(autobump.CheckPinning)

        # Check for new versions and update the SHA afterwards
        if not no_check_version_update:
            # UpdateVersion selects a hoster per-URL via Hoster.select_hoster
            # directly (hoster_factory is no longer injected), so we pass only
            # the unparsed-urls output file.
            scanner.add(autobump.UpdateVersion, unparsed_urls)
            if fetch_requirements:
                # This attempts to determine dependencies exported by PyPi packages,
                # requires running setup.py, so only enabled on request.
                scanner.add(autobump.FetchUpstreamDependencies)
            scanner.add(autobump.UpdateChecksums, failed_urls)

        # Write the recipe. For making PRs, the recipe should be written to a branch
        # of its own.
        if create_branch or create_pr:
            scanner.add(autobump.GitWriteRecipe, git_handler)
        else:
            scanner.add(autobump.WriteRecipe)
        if create_pr:
            token = os.environ.get("GITHUB_TOKEN")
            if not token and (not dry_run):
                logger.critical("GITHUB_TOKEN required to create PRs")
                sys.exit(1)
            github_handler = githubhandler.AiohttpGitHubHandler(
                token, dry_run, "bioconda", "bioconda-recipes"
            )
            scanner.add(autobump.CreatePullRequest, git_handler, github_handler)

        # Terminate the scanning pipeline after x recipes have reached this point.
        if max_updates:
            scanner.add(autobump.MaxUpdates, max_updates)

        # And go.
        scanner.run()

        # Cleanup
        if git_handler:
            git_handler.close()
    except Exception:
        if _handle_pdb_exception("Autobump", pdb):
            return
        raise


@app.command("handle-merged-pr")
def handle_merged_pr(
    repo: Annotated[
        str,
        typer.Option(
            "--repo",
            help="Name of the github repository to check (e.g. bioconda/bioconda-recipes).",
        ),
    ],
    git_range: Annotated[
        str,
        typer.Option(
            "--git-range",
            metavar="BASE[...REF]",
            help=(
                "Select changes on REF since its merge base with BASE. "
                "BASE alone means BASE...HEAD."
            ),
        ),
    ],
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Do not actually upload anything.")
    ] = False,
    fallback: Annotated[
        Literal["build", "ignore"],
        typer.Option(
            "--fallback", help="What to do if no artifacts are found in the PR."
        ),
    ] = "build",
    container_upload_target: Annotated[
        str | None,
        typer.Option(
            "--container-upload-target",
            help="Provide a quay.io target to push container images to.",
        ),
    ] = None,
    artifact_source: Annotated[
        ArtifactSource,
        typer.Option(
            "--artifact-source",
            help="Application hosting build artifacts (e.g., Azure, Circle CI, or GitHub Actions).",
        ),
    ] = "azure",
    package_platform: Annotated[
        PackageSubdir | None,
        typer.Option(
            "--platform",
            help="Conda package subdirectory to upload from PR artifacts (for example, linux-aarch64). Defaults to the native subdirectory.",
        ),
    ] = None,
    image_records_dir: ImageRecordsDirOpt = None,
    use_existing_auth: UseExistingAuthOpt = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Upload artifacts from a merged pull request."""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    label = os.getenv("BIOCONDA_LABEL", None) or None
    parsed_git_range = _parse_git_range(git_range)
    parsed_upload_target = _parse_quay_upload_target(container_upload_target)
    image_records_dir = _resolve_image_records_dir(
        image_records_dir, parsed_upload_target
    )
    res = upload_pr_artifacts(
        repo,
        parsed_git_range.ref,
        dryrun=dry_run,
        mulled_upload_target=parsed_upload_target,
        label=label,
        artifact_source=artifact_source,
        package_platform=package_platform,
        image_records_dir=image_records_dir,
        use_existing_auth=use_existing_auth,
    )
    if res == UploadResult.NO_ARTIFACTS and fallback == "build":
        fallback_package_platform = package_platform or utils.RepoData.native_subdir()
        try:
            package_subdir_to_container_platform(fallback_package_platform)
        except ValueError:
            native_package_platform = utils.RepoData.native_subdir()
            if fallback_package_platform != native_package_platform:
                raise ValueError(
                    "--fallback build cannot build non-native macOS package platform "
                    f"{fallback_package_platform} from {native_package_platform}"
                ) from None
            fallback_docker = False
            fallback_build_platform = None
        else:
            fallback_docker = True
            fallback_build_platform = fallback_package_platform

        success = build(
            recipe_folder,
            config,
            git_range=git_range,
            docker=fallback_docker,
            platform=fallback_build_platform,
            anaconda_upload=not dry_run,
            container_upload_target=parsed_upload_target if not dry_run else None,
            mulled_build_and_test=True,
            image_records_dir=image_records_dir,
            use_existing_auth=use_existing_auth,
        )
    else:
        success = res != UploadResult.FAILURE
    sys.exit(0 if success else 1)


@app.command("create-mulled-manifests")
def create_mulled_manifests(
    record_paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Mulled image record files (JSONL) or directories containing them."
        ),
    ] = None,
    platform: Annotated[
        list[PackageSubdir] | None,
        typer.Option(
            "--platform",
            help="Conda package subdirectories to include (e.g. linux-64, linux-aarch64). Defaults to all supported platforms.",
        ),
    ] = None,
    use_existing_auth: UseExistingAuthOpt = False,
    loglevel: LoglevelOpt = "info",
    logfile: LogfileOpt = None,
    logfile_level: LogfileLevelOpt = "debug",
    log_command_max_lines: LogCommandMaxLinesOpt = None,
) -> None:
    """Create or update canonical manifests for uploaded mulled images."""
    _setup_runtime(loglevel, logfile, logfile_level, log_command_max_lines)
    try:
        target_platforms = (
            [package_subdir_to_container_platform(p) for p in platform]
            if platform
            else list(ALL_CONTAINER_PLATFORMS)
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--platform") from exc
    paths = record_paths or []
    if not paths:
        if not DEFAULT_MULLED_RECORDS_DIR.exists():
            logger.info("No mulled image records found; nothing to reconcile.")
            return
        paths = [DEFAULT_MULLED_RECORDS_DIR]
    try:
        records = load_image_records(paths)
    except FileNotFoundError as exc:
        raise typer.BadParameter(
            f"mulled image record path not found: {exc.args[0]}"
        ) from exc
    if not records:
        logger.info("No mulled image records found; nothing to reconcile.")
        return
    changed, total = reconcile_manifests(
        records,
        target_platforms,
        creds=resolve_registry_creds(use_existing_auth=use_existing_auth),
    )
    logger.info("Manifest summary: %d changed, %d checked", changed, total)


@app.command("annotate-build-failures")
def annotate_build_failures(
    recipes: Annotated[
        list[Path], typer.Argument(help="Paths to recipes with build failures")
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
    platform: Annotated[
        list[PackageSubdir] | None,
        typer.Option("--platform", help="Conda package subdirectories to annotate"),
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
    target_platforms = platform if platform is not None else list(ALL_PACKAGE_SUBDIRS)
    for recipe in recipes:
        if existing_only:
            recipe_platforms = [
                plat
                for plat in ALL_PACKAGE_SUBDIRS
                if BuildFailureRecord(recipe, platform=plat).exists()
            ]
        else:
            recipe_platforms = target_platforms
        for plat in recipe_platforms:
            failure_record = BuildFailureRecord(recipe, platform=plat)
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


# TODO add subcommand to list recipes with build failure records descendingly sorted by downloads
# in case of version subdirs, only list if the latest version also has the build failure record.
# list how many recipes depend on this and sort by it primarily if inner
@app.command("list-build-failures")
def list_build_failures(
    recipe_folder: RecipeFolderArg = Path("recipes/"),
    config: ConfigArg = Path("config.yml"),
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
    git_range: GitRangeOpt = None,
) -> None:
    """List recipes with build failure records"""
    config_data = utils.load_config(config)
    parsed_git_range = _parse_git_range_if_needed(git_range)
    df = collect_build_failure_dataframe(
        recipe_folder,
        config_data,
        channel,
        link_fmt=output_format,
        link_prefix=link_prefix,
        git_range=parsed_git_range,
    )
    fmt_writer = (
        pandas.DataFrame.to_markdown
        if output_format == "markdown"
        else pandas.DataFrame.to_string
    )
    fmt_writer(df, sys.stdout, index=False)


@app.command("bulk-trigger-ci")
def bulk_trigger_ci() -> None:
    """Create an empty commit with the string "[ci run]" and push, which
    triggers a bulk CI run. Must be on the `bulk` branch."""
    bulk.trigger_ci()


def main() -> None:
    app()
