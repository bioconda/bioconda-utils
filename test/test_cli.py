"""Tests for the Typer command-line interface."""

import logging
from pathlib import Path
from typing import Any, cast

import networkx as nx
import pytest
from typer.core import TyperArgument
from typer.main import get_command
from typer.testing import CliRunner

from bioconda_utils import cli

runner = CliRunner()


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
    assert "Invalid value for '--output-format'" in result.output


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
    name2recipes = {name: {Path("recipes") / name} for name in package_dag.nodes}
    monkeypatch.setattr(cli.utils, "load_config", lambda _: {})
    monkeypatch.setattr(cli.utils, "get_recipes", lambda *_: [])
    monkeypatch.setattr(cli.graph, "build", lambda *_: (package_dag, name2recipes))

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


@pytest.mark.parametrize(
    ("spec", "base", "ref"),
    [
        ("origin/master", "origin/master", "HEAD"),
        ("origin/master...HEAD", "origin/master", "HEAD"),
        ("HEAD~1...HEAD", "HEAD~1", "HEAD"),
    ],
)
def test_git_range_parsing(spec, base, ref):
    parsed = cli.GitRange.parse(spec)

    assert parsed.base == base
    assert parsed.ref == ref
    assert str(parsed) == f"{base}...{ref}"


@pytest.mark.parametrize(
    "spec",
    ["", "main..HEAD", "main....HEAD", "...HEAD", "main...", "a...b...c"],
)
def test_invalid_git_ranges_are_rejected(spec):
    with pytest.raises(ValueError):
        cli.GitRange.parse(spec)


def test_cli_rejects_two_dot_git_range(monkeypatch):
    monkeypatch.setattr(cli._lint, "get_checks", list)

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

    monkeypatch.setattr(cli, "BiocondaRepo", Repo)

    result = cli.get_recipes_to_build(
        cli.GitRange.parse("main...feature"), Path("recipes")
    )

    assert result == [Path("recipes/example")]
    assert calls == [("feature", "main")]


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
    assert "cannot be installed in Linux mulled containers" in result.output


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
    assert "is not one of 'linux-64', 'linux-aarch64', 'linux-riscv64'" in result.output


def test_create_mulled_manifests_rejects_macos_package_platform():
    result = runner.invoke(cli.app, ["create-mulled-manifests", "--platform", "osx-64"])
    assert result.exit_code == 2
    assert "cannot be installed in Linux mulled containers" in result.output


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

    assert parameter.default == cli.pkg_test.CREATE_ENV_IMAGE


def test_lint_list_checks_allows_missing_paths(monkeypatch):
    monkeypatch.setattr(cli._lint, "get_checks", lambda: ["first", "second"])

    result = runner.invoke(
        cli.app,
        ["lint", "/missing/recipes", "/missing/config.yml", "--list-checks"],
    )

    assert result.exit_code == 0
    assert result.output == "first\nsecond\n"


def test_lint_logs_exceptions_without_pdb(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.utils,
        "load_config",
        lambda path: (_ for _ in ()).throw(RuntimeError("bad")),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="bad"):
        cli.lint(tmp_path, tmp_path)

    assert "Lint command failed" in caplog.text


def test_handle_merged_pr_accepts_single_git_ref(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "upload_pr_artifacts",
        lambda repo, ref, **kwargs: calls.append(ref) or cli.UploadResult.SUCCESS,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.handle_merged_pr(repo="bioconda/bioconda-recipes", git_range="HEAD")

    assert exc_info.value.code == 0
    assert calls == ["HEAD"]


def test_shared_runtime_options_are_applied(monkeypatch):
    logger_calls = []
    thread_calls = []
    monkeypatch.setattr(
        cli.utils, "setup_logger", lambda *args: logger_calls.append(args)
    )
    monkeypatch.setattr(cli.utils, "set_max_threads", thread_calls.append)
    cli._setup_runtime(
        loglevel="warning",
        log_command_max_lines=12,
        threads=4,
    )

    assert logger_calls == [("bioconda_utils", "warning", None, "debug", 12)]
    assert thread_calls == [4]
