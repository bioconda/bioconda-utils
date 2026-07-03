"""Tests for the Typer command-line interface."""

import logging
from typing import Any, cast

import pytest
from typer.main import get_command
from typer.testing import CliRunner
from typer.core import TyperArgument

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
    option_names = {option for param in command.params for option in param.opts}

    assert "--test-only" in option_names
    assert "--build-script-template" in option_names
    assert "--package-dir" in option_names
    assert "--skiplist-leaves" in option_names
    assert "--prelint" not in option_names
    assert all("_" not in option for option in option_names if option.startswith("-"))


def test_choices_are_enforced_before_command_execution():
    result = runner.invoke(cli.app, ["dag", "--format", "invalid"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.output


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
    monkeypatch.setattr(cli._lint, "get_checks", lambda: [])

    result = runner.invoke(
        cli.app, ["lint", "--list-checks", "--git-range", "main..HEAD"]
    )

    assert result.exit_code == 2
    assert "two-dot ranges are not supported" in result.output
    assert "main...HEAD" not in result.output


def test_recipe_selection_uses_range_base_and_ref(monkeypatch):
    calls = []

    class Repo:
        def __init__(self, recipe_folder):
            assert recipe_folder == "recipes"

        def get_recipes_to_build(self, ref, base):
            calls.append((ref, base))
            return ["recipes/example"]

    monkeypatch.setattr(cli, "BiocondaRepo", Repo)

    result = cli.get_recipes_to_build(cli.GitRange.parse("main...feature"), "recipes")

    assert result == ["recipes/example"]
    assert calls == [("feature", "main")]


def test_build_parses_typed_and_repeated_options():
    command = cast(Any, get_command(cli.app)).commands["build"]

    context = command.make_context(
        "build", ["--docker", "--packages", "one", "--packages", "two"]
    )

    assert context.params["docker"] is True
    assert context.params["packages"] == ("one", "two")
    assert context.params["n_workers"] == 1
    assert context.params["recipe_folder"] == "recipes/"
    assert context.params["config"] == "config.yml"


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
        cli.lint(str(tmp_path), str(tmp_path))

    assert "Lint command failed" in caplog.text


def test_handle_merged_pr_accepts_single_git_ref(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_setup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "upload_pr_artifacts",
        lambda config, repo, ref, **kwargs: (
            calls.append(ref) or cli.UploadResult.SUCCESS
        ),
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
