"""Tests for the Typer command-line interface."""

import logging
from io import StringIO
from pathlib import Path
from typing import Any, cast

import click
import networkx as nx
import pytest
import typer
from rich.console import Console
from typer.core import TyperArgument
from typer.main import get_command
from typer.testing import CliRunner

from bioconda_utils import cli
from bioconda_utils._types import CONDA, RecipePath
from bioconda_utils.containers.artifacts import UploadResult
from bioconda_utils.containers.pkg_test import CREATE_ENV_IMAGE
from bioconda_utils.githandler import GitRange
from bioconda_utils.support.progress import ProgressDisplay

runner = CliRunner()


@pytest.mark.parametrize("fail", [False, True])
def test_command_owns_progress_lifetime(monkeypatch, fail):
    display = ProgressDisplay(Console(file=StringIO(), force_terminal=True))
    monkeypatch.setattr(cli, "progress_display", display)

    def callback():
        assert display.live.is_started
        with display.count_task("processing", total=1) as (progress, task):
            assert not progress.live.is_started
            progress.update(task, advance=1)
            if fail:
                raise ValueError("command failed")
        cli._write_output("result\n")

    command = next(
        info for info in cli.app.registered_commands if info.name == "diagnostics"
    )
    monkeypatch.setattr(command, "callback", callback)
    result = runner.invoke(cli.app, ["diagnostics"])
    assert result.exit_code == int(fail)
    if fail:
        assert isinstance(result.exception, ValueError)
    else:
        assert result.stdout == "result\n"
    assert display.counts.tasks == []
    assert not display.live.is_started
    assert display.live.console._live_stack == []


def test_all_commands_render_help():
    root = cast(Any, get_command(cli.app))

    assert set(root.commands) == {
        "annotate-build-failures",
        "autobump",
        "bioconductor-skeleton",
        "build",
        "bulk-trigger-ci",
        "clean-cran-skeleton",
        "create-mulled-manifests",
        "dag",
        "dependent",
        "diagnostics",
        "duplicates",
        "handle-merged-pr",
        "lint",
        "list-build-failures",
        "update-pinning",
    }
    for command_name in root.commands:
        result = runner.invoke(cli.app, [command_name, "--help"])
        assert result.exit_code == 0, result.output


def test_version_option():
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"This is bioconda-utils version {cli.VERSION}\n"


def test_diagnostics(monkeypatch, tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("python:\n  - 3.13\n")
    second.write_text("zlib:\n  - 1.3\n")
    config = type(
        "BuildConfig",
        (),
        {
            "subdir": "linux-64",
            "croot": tmp_path / "conda-bld",
            "exclusive_config_files": [first, second],
        },
    )()
    monkeypatch.setattr(
        "bioconda_utils.conda.conda_build_bridge.load_conda_build_config",
        lambda: config,
    )

    result = runner.invoke(cli.app, ["diagnostics"])

    assert result.exit_code == 0, result.output
    assert f"bioconda-utils version: {cli.VERSION}" in result.output
    assert "package subdir: linux-64" in result.output
    assert f"conda-build root: {tmp_path / 'conda-bld'}" in result.output
    assert f"{first}:\npython:\n  - 3.13" in result.output
    assert f"{second}:\nzlib:\n  - 1.3" in result.output


def test_recipe_and_config_are_optional():
    command = cast(Any, get_command(cli.app)).commands["lint"]
    arguments = [param for param in command.params if isinstance(param, TyperArgument)]

    assert [param.name for param in arguments] == ["recipe_folder", "config"]
    assert all(not param.required for param in arguments)


def test_build_uses_normalized_option_names():
    command = cast(Any, get_command(cli.app)).commands["build"]
    option_names = {
        option
        for param in command.params
        for option in [*param.opts, *param.secondary_opts]
    }

    assert "--test-only" in option_names
    assert "--mulled-build-and-test" in option_names
    assert "--build-script-template" in option_names
    assert "--package-dir" in option_names
    assert "--skiplist-leaves" in option_names
    assert "--mulled-test" not in option_names
    assert "--presolved-mulled-build-and-test" in option_names
    assert "--no-presolved-mulled-build-and-test" in option_names
    assert "--presolved-mulled-test" not in option_names
    assert "--no-presolved-mulled-test" not in option_names
    assert "--container-upload-target" in option_names
    assert "--mulled-upload-target" not in option_names
    assert "--image-records-dir" in option_names
    assert "--mulled-upload-records" not in option_names
    assert "--quay-upload-target" not in option_names
    assert "--testonly" not in option_names
    assert "--prelint" not in option_names
    assert all("_" not in option for option in option_names if option.startswith("-"))


def test_platform_options_have_one_source_of_truth_per_command():
    commands = cast(Any, get_command(cli.app)).commands
    build_options = {
        option for param in commands["build"].params for option in param.opts
    }
    merged_pr_options = {
        option for param in commands["handle-merged-pr"].params for option in param.opts
    }

    assert "--platform" in build_options
    assert "--container-platform" not in build_options
    assert "--container-upload-target" in build_options
    assert "--mulled-upload-target" not in build_options
    assert "--quay-upload-target" not in build_options
    assert "--image-records-dir" in build_options
    assert "--mulled-upload-records" not in build_options

    assert "--platform" in merged_pr_options
    assert "--package-platform" not in merged_pr_options
    assert "--container-platform" not in merged_pr_options
    assert "--container-upload-target" in merged_pr_options
    assert "--quay-upload-target" not in merged_pr_options
    assert "--mulled-upload-target" not in merged_pr_options
    assert "--image-records-dir" in merged_pr_options
    assert "--mulled-upload-records" not in merged_pr_options

    create_mulled_manifests_options = {
        option
        for param in commands["create-mulled-manifests"].params
        for option in param.opts
    }
    assert "--platform" in create_mulled_manifests_options
    assert "--platforms" not in create_mulled_manifests_options
    assert "--container-platform" not in create_mulled_manifests_options

    annotate_options = {
        option
        for param in commands["annotate-build-failures"].params
        for option in param.opts
    }
    assert "--platform" in annotate_options
    assert "--platforms" not in annotate_options

    dag_options = {option for param in commands["dag"].params for option in param.opts}
    assert "--output-format" in dag_options
    assert "--format" not in dag_options

    list_failures_options = {
        option
        for param in commands["list-build-failures"].params
        for option in param.opts
    }
    assert "--output-format" in list_failures_options
    assert "--format" not in list_failures_options


def test_handle_merged_pr_requires_repository_and_git_range():
    command = cast(Any, get_command(cli.app)).commands["handle-merged-pr"]
    parameters = {parameter.name: parameter for parameter in command.params}

    assert parameters["repo"].required is True
    assert parameters["git_range"].required is True


def test_choices_are_enforced_before_command_execution():
    result = runner.invoke(cli.app, ["dag", "--output-format", "invalid"])

    assert result.exit_code == 2
    # Typer renders errors with rich and force-colors them when
    # GITHUB_ACTIONS is set, which interleaves ANSI escapes into
    # result.output (even splitting the option name into spans).
    assert "Invalid value for '--output-format'" in click.unstyle(result.output)


def test_dag_help_describes_dependency_edges():
    result = runner.invoke(cli.app, ["dag", "--help"])

    assert result.exit_code == 0, result.output
    assert "dependency DAG among selected packages" in result.output
    assert "An edge from A to B means that B has A as a build" in result.output


def test_dag_hides_singletons(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yml"
    config.write_text("{}")
    package_dag = nx.DiGraph([("dependency", "package")])
    package_dag.add_node("singleton")
    name2recipes = {
        name: {RecipePath(Path("recipes") / name, CONDA)} for name in package_dag.nodes
    }
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda _: {})
    monkeypatch.setattr(cli, "get_recipes", lambda *_: [])
    monkeypatch.setattr(
        "bioconda_utils.graph.build", lambda *_: (package_dag, name2recipes)
    )

    result = runner.invoke(
        cli.app,
        [
            "dag",
            str(recipe_folder),
            str(config),
            "--output-format",
            "txt",
            "--hide-singletons",
        ],
    )

    assert result.exit_code == 0, result.output
    assert set(package_dag) == {"dependency", "package"}
    assert "singleton" not in result.output


def test_dag_text_output_does_not_wrap_recipe_paths(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yml"
    config.write_text("{}")
    long_recipe = Path("recipes") / ("very-long-recipe-name-" * 6)
    package_dag = nx.DiGraph([("dependency", "package")])
    name2recipes = {
        "dependency": {RecipePath(Path("recipes/dependency"), CONDA)},
        "package": {RecipePath(long_recipe, CONDA)},
    }
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda _: {})
    monkeypatch.setattr(cli, "get_recipes", lambda *_: [])
    monkeypatch.setattr(
        "bioconda_utils.graph.build", lambda *_: (package_dag, name2recipes)
    )

    result = runner.invoke(
        cli.app,
        ["dag", str(recipe_folder), str(config), "--output-format", "txt"],
    )

    assert result.exit_code == 0, result.output
    assert str(long_recipe) in result.output.splitlines()


@pytest.mark.parametrize(
    ("spec", "base", "ref"),
    [
        ("origin/master", "origin/master", "HEAD"),
        ("origin/master...HEAD", "origin/master", "HEAD"),
        ("HEAD~1...HEAD", "HEAD~1", "HEAD"),
    ],
)
def test_git_range_parsing(spec, base, ref):
    parsed = GitRange.parse(spec)

    assert parsed.base == base
    assert parsed.ref == ref
    assert str(parsed) == f"{base}...{ref}"


@pytest.mark.parametrize(
    "spec",
    ["", "main..HEAD", "main....HEAD", "...HEAD", "main...", "a...b...c"],
)
def test_invalid_git_ranges_are_rejected(spec):
    with pytest.raises(ValueError):
        GitRange.parse(spec)


def test_cli_rejects_two_dot_git_range(monkeypatch):
    monkeypatch.setattr("bioconda_utils.lint.get_checks", list)

    result = runner.invoke(
        cli.app, ["lint", "--list-checks", "--git-range", "main..HEAD"]
    )

    assert result.exit_code == 2
    assert "two-dot ranges are not supported" in result.output
    assert "main...HEAD" not in result.output


def test_cli_rejects_invalid_quay_target_before_building(tmp_path):
    result = runner.invoke(
        cli.app, ["build", "--container-upload-target", "namespace/repository"]
    )

    assert result.exit_code == 2
    assert "must be a single quay.io namespace" in result.output

    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yml"
    config.touch()

    result_pr = runner.invoke(
        cli.app,
        [
            "handle-merged-pr",
            str(recipe_folder),
            str(config),
            "--repo",
            "bioconda/bioconda-recipes",
            "--git-range",
            "HEAD~1...HEAD",
            "--container-upload-target",
            "namespace/repository",
        ],
    )

    assert result_pr.exit_code == 2
    assert "must be a single quay.io namespace" in result_pr.output


def test_recipe_selection_uses_range_base_and_ref(monkeypatch):
    calls = []

    class Repo:
        def __init__(self, recipe_folder):
            assert recipe_folder == Path("recipes")

        def get_recipes_to_build(self, ref, base):
            calls.append((ref, base))
            return ["recipes/example"]

    monkeypatch.setattr("bioconda_utils.githandler.BiocondaRepo", Repo)

    result = cli.get_recipes_to_build(GitRange.parse("main...feature"), Path("recipes"))

    assert result == [Path("recipes/example")]
    assert calls == [("feature", "main")]


def test_autobump_closes_git_handler_on_keyboard_interrupt(monkeypatch):
    from bioconda_utils import autobump

    closed = []

    class RecipeSource:
        def __init__(self, *_args, **_kwargs):
            pass

    class Scanner:
        def __init__(self, *_args, **_kwargs):
            pass

        def add(self, *_args, **_kwargs):
            pass

        def run(self):
            raise KeyboardInterrupt

    class Repo:
        def __init__(self, *_args, **_kwargs):
            pass

        def checkout_master(self):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cli, "_setup_runtime", lambda *_args: None)
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda *_args: {})
    monkeypatch.setattr("bioconda_utils.githandler.BiocondaRepo", Repo)
    monkeypatch.setattr(autobump, "RecipeSource", RecipeSource)
    monkeypatch.setattr(autobump, "Scanner", Scanner)

    with pytest.raises(KeyboardInterrupt):
        cli.autobump(
            no_follow_graph=True,
            check_branch=True,
            ignore_skiplists=True,
            exclude_channels=["none"],
            no_check_pinnings=True,
            no_check_version_update=True,
        )

    assert closed == [True]


def test_autobump_builds_all_cache_paths_from_path_prefix(monkeypatch, tmp_path):
    from bioconda_utils import autobump

    added_filters = []
    scanner_arguments = []

    class RecipeSource:
        def __init__(self, *_args, **_kwargs):
            pass

    class Scanner:
        def __init__(self, *_args, **kwargs):
            scanner_arguments.append(kwargs)

        def add(self, *args, **_kwargs):
            added_filters.append(args)

        def run(self):
            pass

    monkeypatch.setattr(cli, "_setup_runtime", lambda *_args: None)
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda *_args: {})
    monkeypatch.setattr(autobump, "RecipeSource", RecipeSource)
    monkeypatch.setattr(autobump, "Scanner", Scanner)

    cache = tmp_path / "autobump-cache"
    cli.autobump(
        cache=cache,
        no_follow_graph=True,
        ignore_skiplists=True,
        exclude_channels=["conda-forge"],
        no_check_pinnings=True,
        no_check_version_update=True,
    )

    assert scanner_arguments == [
        {"cache_file": Path(f"{cache}_scan.pkl"), "status_file": None}
    ]
    exclude_call = next(
        call for call in added_filters if call[0] is autobump.ExcludeOtherChannel
    )
    assert exclude_call[2] == Path(f"{cache}_repodata.txt")


def test_list_build_failures_markdown_is_written_verbatim(monkeypatch, tmp_path):
    from bioconda_utils.build_failure import BUILD_FAILURE_COLUMNS

    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yml"
    config.write_text("{}")
    row = {column: f"value-{column}" for column in BUILD_FAILURE_COLUMNS}
    row["build failures"] = "[linux-64](failures/linux-64.yaml)"
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda *_args: {})
    monkeypatch.setattr(
        "bioconda_utils.build_failure.collect_build_failure_records",
        lambda *_args, **_kwargs: [row],
    )

    result = runner.invoke(
        cli.app,
        [
            "list-build-failures",
            str(recipe_folder),
            str(config),
            "--output-format",
            "markdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.startswith("| recipe | downloads |")
    assert "[linux-64](failures/linux-64.yaml)" in result.output
    assert "─" not in result.output


def test_build_parses_typed_platform_option():
    command = cast(Any, get_command(cli.app)).commands["build"]

    context = command.make_context(
        "build",
        [
            "--docker",
            "--platform",
            "linux-aarch64",
            "--packages",
            "one",
            "--packages",
            "two",
        ],
    )

    assert context.params["docker"] is True
    assert context.params["packages"] == ("one", "two")
    assert context.params["platform"] == cli.PackageSubdir.LINUX_AARCH64
    assert context.params["n_workers"] == 1
    assert context.params["recipe_folder"] == Path("recipes")
    assert context.params["config"] == Path("config.yml")


def test_build_derives_container_platform_from_package_subdir():
    assert (
        cli._container_platform_for_build(cli.PackageSubdir.LINUX_AARCH64, True)
        == cli.ContainerPlatform.LINUX_ARM64
    )


def test_build_rejects_container_platform_notation():
    result = runner.invoke(cli.app, ["build", "--docker", "--platform", "linux/arm64"])

    assert result.exit_code == 2
    assert "linux-aarch64" in result.output


def test_build_rejects_macos_package_platform_for_docker():
    result = runner.invoke(cli.app, ["build", "--docker", "--platform", "osx-arm64"])

    assert result.exit_code == 2
    assert "cannot be installed" in result.output
    assert "mulled containers" in result.output


def test_handle_merged_pr_parses_conda_platform_option(tmp_path):
    command = cast(Any, get_command(cli.app)).commands["handle-merged-pr"]
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    config = tmp_path / "config.yml"
    config.touch()

    context = command.make_context(
        "handle-merged-pr",
        [
            str(recipes),
            str(config),
            "--repo",
            "bioconda/bioconda-recipes",
            "--git-range",
            "HEAD~1...HEAD",
            "--platform",
            "linux-aarch64",
        ],
    )

    assert context.params["package_platform"] == cli.PackageSubdir.LINUX_AARCH64


def test_create_mulled_manifests_parses_conda_platform_option():
    command = cast(Any, get_command(cli.app)).commands["create-mulled-manifests"]
    context = command.make_context(
        "create-mulled-manifests",
        ["--platform", "linux-aarch64", "--platform", "linux-64"],
    )
    assert context.params["platform"] == (
        cli.PackageSubdir.LINUX_AARCH64,
        cli.PackageSubdir.LINUX_64,
    )


def test_create_mulled_manifests_rejects_container_platform_notation():
    result = runner.invoke(
        cli.app, ["create-mulled-manifests", "--platform", "linux/arm64"]
    )
    assert result.exit_code == 2
    assert "is not one of" in result.output
    assert "linux-64" in result.output
    assert "linux-aarch64" in result.output


def test_create_mulled_manifests_rejects_macos_package_platform():
    result = runner.invoke(cli.app, ["create-mulled-manifests", "--platform", "osx-64"])
    assert result.exit_code == 2
    assert "cannot be installed" in result.output
    assert "mulled containers" in result.output


def test_annotate_build_failures_parses_conda_platform_option():
    command = cast(Any, get_command(cli.app)).commands["annotate-build-failures"]
    context = command.make_context(
        "annotate-build-failures",
        ["recipes/samtools", "--platform", "linux-aarch64", "--platform", "osx-64"],
    )
    assert context.params["platform"] == (
        cli.PackageSubdir.LINUX_AARCH64,
        cli.PackageSubdir.OSX_64,
    )


def test_annotate_build_failures_rejects_container_platform_notation():
    result = runner.invoke(
        cli.app,
        [
            "annotate-build-failures",
            "recipes/samtools",
            "--platform",
            "linux/arm64",
        ],
    )
    assert result.exit_code == 2
    assert "is not one of" in result.output


def test_build_uses_environment_aware_mulled_image_default():
    command = cast(Any, get_command(cli.app)).commands["build"]
    parameter = next(p for p in command.params if p.name == "mulled_conda_image")

    assert parameter.default == CREATE_ENV_IMAGE


def test_lint_list_checks_allows_missing_paths(monkeypatch):
    monkeypatch.setattr("bioconda_utils.lint.get_checks", lambda: ["first", "second"])

    result = runner.invoke(
        cli.app,
        ["lint", "/missing/recipes", "/missing/config.yml", "--list-checks"],
    )

    assert result.exit_code == 0
    assert result.output == "first\nsecond\n"


def test_lint_logs_exceptions_without_pdb(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "bioconda_utils.config.load_config",
        lambda path: (_ for _ in ()).throw(RuntimeError("bad")),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="bad"):
        cli.lint(tmp_path, tmp_path)

    assert "Lint command failed" in caplog.text


def test_lint_exit_is_not_reported_as_a_command_failure(monkeypatch, caplog, tmp_path):
    """Lint errors are an exit code, not a crash to trace back.

    ``typer.Exit`` derives from ``RuntimeError``, so the command's own
    ``except Exception`` used to log a traceback (and offer a post-mortem)
    whenever a recipe had lint errors.
    """
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr("bioconda_utils.config.load_config", lambda _path: {})
    monkeypatch.setattr(cli, "get_recipes", lambda *_args, **_kwargs: [])

    class ErroringLinter:
        def __init__(self, *_args, **_kwargs):
            pass

        def lint(self, *_args, **_kwargs):
            return True

        def get_messages(self):
            return []

    monkeypatch.setattr("bioconda_utils.lint.Linter", ErroringLinter)

    with caplog.at_level(logging.ERROR), pytest.raises(typer.Exit) as exc_info:
        cli.lint(tmp_path, tmp_path)

    assert exc_info.value.exit_code == 1
    assert "Lint command failed" not in caplog.text


def test_lint_bad_parameter_is_not_reported_as_a_command_failure(
    monkeypatch, caplog, tmp_path
):
    """A mistyped path is a usage error, so click renders it without a traceback."""
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(
            cli.app, ["lint", str(tmp_path / "missing"), str(tmp_path / "config.yml")]
        )

    assert result.exit_code == 2
    assert "does not exist" in click.unstyle(result.output)
    assert "Lint command failed" not in caplog.text


def test_handle_merged_pr_accepts_single_git_ref(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "bioconda_utils.containers.artifacts.upload_pr_artifacts",
        lambda repo, ref, **kwargs: calls.append(ref) or UploadResult.SUCCESS,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.handle_merged_pr(repo="bioconda/bioconda-recipes", git_range="HEAD")

    assert exc_info.value.code == 0
    assert calls == ["HEAD"]


def test_shared_runtime_options_are_applied(monkeypatch):
    logger_calls = []
    thread_calls = []
    monkeypatch.setattr(cli, "setup_logger", lambda *args: logger_calls.append(args))
    monkeypatch.setattr(
        "bioconda_utils.support.parallel.set_max_threads", thread_calls.append
    )
    cli._setup_runtime(
        loglevel="warning",
        log_command_max_lines=12,
        threads=4,
    )

    assert logger_calls == [("bioconda_utils", "warning", None, "debug", 12)]
    assert thread_calls == [4]
