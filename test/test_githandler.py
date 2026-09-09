"""Unit tests for bioconda_utils.githandler"""

import subprocess
from pathlib import Path

import pytest
import yaml

from bioconda_utils.githandler import (
    BiocondaRepoMixin,
    BranchName,
    GitActor,
    GitHandler,
    GitHandlerFailure,
    GitRange,
)


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sample_git_repo(tmp_path: Path):
    """Creates a temporary git repository with initial commit and dummy structure."""
    repo_dir = tmp_path / "sample_repo"
    repo_dir.mkdir()

    run_git(["init", "-b", "master"], cwd=repo_dir)
    run_git(["config", "user.name", "Test User"], cwd=repo_dir)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_dir)

    # Initial file and commit
    recipes_dir = repo_dir / "recipes" / "pkg_a"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "meta.yaml").write_text("package:\n  name: pkg_a\n  version: 1.0\n")
    (recipes_dir / "build.sh").write_text("#!/bin/bash\necho build\n")

    # Blacklist and config
    blacklist_file = repo_dir / "blacklist.txt"
    blacklist_file.write_text("recipes/pkg_blacklisted # reason\n")

    config = {
        "blacklists": ["blacklist.txt"],
    }
    (repo_dir / "config.yml").write_text(yaml.dump(config))

    run_git(["add", "."], cwd=repo_dir)
    run_git(["commit", "-m", "Initial commit"], cwd=repo_dir)

    # Add a dummy remote so get_remote works
    run_git(
        ["remote", "add", "origin", "https://github.com/bioconda/bioconda-recipes.git"],
        cwd=repo_dir,
    )

    return repo_dir


def test_git_range_parsing():
    r = GitRange.parse("master...feature")
    assert r.base == "master"
    assert r.ref == "feature"
    assert str(r) == "master...feature"

    r_single = GitRange.parse("master")
    assert r_single.base == "master"
    assert r_single.ref == "HEAD"

    with pytest.raises(ValueError, match="git range cannot be empty"):
        GitRange.parse("   ")

    with pytest.raises(ValueError, match="two-dot ranges are not supported"):
        GitRange.parse("master..feature")

    with pytest.raises(ValueError, match="exactly one '\\.\\.\\.'"):
        GitRange.parse("a...b...c")


def test_git_actor():
    actor = GitActor("Alice", "alice@example.com")
    assert actor.name == "Alice"
    assert actor.email == "alice@example.com"
    assert str(actor) == "Alice <alice@example.com>"

    actor_no_email = GitActor("Bob")
    assert str(actor_no_email) == "Bob"


def test_githandler_clean_and_dirty(sample_git_repo: Path):
    handler = GitHandler(sample_git_repo, dry_run=True, allow_dirty=True)
    assert not handler.is_dirty()

    # Modify a file
    (sample_git_repo / "README.md").write_text("hello")
    # Untracked files do not make git diff / cached dirty
    assert not handler.is_dirty()

    # Track it
    run_git(["add", "README.md"], cwd=sample_git_repo)
    assert handler.is_dirty()

    # Commit it
    run_git(["commit", "-m", "add readme"], cwd=sample_git_repo)
    assert not handler.is_dirty()

    # Modify tracked file
    (sample_git_repo / "README.md").write_text("world")
    assert handler.is_dirty()


def test_githandler_branches_and_read(sample_git_repo: Path):
    handler = GitHandler(sample_git_repo, dry_run=True, allow_dirty=True)

    # Active branch
    active = handler.get_active_branch()
    assert active == "master"

    # Read from branch
    content = handler.read_from_branch("master", sample_git_repo / "config.yml")
    assert "blacklists" in content

    with pytest.raises(GitHandlerFailure):
        handler.read_from_branch("master", sample_git_repo / "nonexistent.yml")

    # Create a new branch with a change
    run_git(["checkout", "-b", "feature"], cwd=sample_git_repo)
    pkg_b = sample_git_repo / "recipes" / "pkg_b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "meta.yaml").write_text("package:\n  name: pkg_b\n  version: 2.0\n")
    run_git(["add", "."], cwd=sample_git_repo)
    run_git(["commit", "-m", "add pkg_b"], cwd=sample_git_repo)

    # Merge base between master and feature
    mb = handler.get_merge_base("feature", "master")
    assert handler.is_sha(mb)

    # Changed files
    changed = list(handler.list_changed_files("feature", "master"))
    assert "recipes/pkg_b/meta.yaml" in changed


class MockBiocondaRepo(GitHandler, BiocondaRepoMixin):
    pass


def test_bioconda_repo_recipes_to_build(sample_git_repo: Path):
    repo = MockBiocondaRepo(sample_git_repo, dry_run=True, allow_dirty=True)

    # Create branch feature
    run_git(["checkout", "-b", "feature"], cwd=sample_git_repo)

    # Add pkg_c
    pkg_c = sample_git_repo / "recipes" / "pkg_c"
    pkg_c.mkdir(parents=True)
    (pkg_c / "meta.yaml").write_text("package:\n  name: pkg_c\n  version: 1.0\n")
    run_git(["add", "."], cwd=sample_git_repo)
    run_git(["commit", "-m", "add pkg_c"], cwd=sample_git_repo)

    to_build = repo.get_recipes_to_build("feature", "master")
    assert Path("recipes/pkg_c") in to_build


def test_get_remote(sample_git_repo: Path):
    handler = GitHandler(sample_git_repo, dry_run=True, allow_dirty=True)
    rem = handler.get_remote("origin")
    assert rem.name == "origin"
    assert "bioconda/bioconda-recipes" in rem.url

    rem_url = handler.get_remote("bioconda/bioconda-recipes")
    assert rem_url.name == "origin"

    with pytest.raises(KeyError):
        handler.get_remote("nonexistent-remote")


def test_commit_and_push_changes(sample_git_repo: Path):
    handler = GitHandler(sample_git_repo, dry_run=True, allow_dirty=True)
    handler.set_user("Bioconda Bot", "bot@bioconda.org")

    # When no changes, commit_and_push_changes returns False
    assert not handler.commit_and_push_changes([], "master", "empty commit")

    # Case 1: Modify a tracked file, files=[]
    (sample_git_repo / "config.yml").write_text("blacklists: []\n")
    committed = handler.commit_and_push_changes([], "master", "update config")
    assert committed

    # Case 2: Explicit untracked file
    (sample_git_repo / "README.md").write_text("modified readme")
    committed2 = handler.commit_and_push_changes(
        [sample_git_repo / "README.md"], "master", "update readme"
    )
    assert committed2

    # Check commit log for author
    log = run_git(
        ["log", "-1", "--format=%an <%ae> %s"], cwd=sample_git_repo
    ).stdout.strip()
    assert log == "Bioconda Bot <bot@bioconda.org> update readme"


def test_branch_lifecycle_and_restore(sample_git_repo: Path):
    handler = GitHandler(sample_git_repo, dry_run=True, allow_dirty=True)
    assert handler.prev_active_branch == BranchName("master")

    # Create and checkout a new branch
    run_git(["checkout", "-b", "temp_branch"], cwd=sample_git_repo)
    assert handler.get_active_branch() == BranchName("temp_branch")

    # close() should restore previous branch (master)
    handler.close()
    assert handler.get_active_branch() == BranchName("master")

    # Delete the temporary branch
    handler.delete_local_branch(BranchName("temp_branch"))
    assert handler.get_local_branch("temp_branch") is None

    # Delete remote branch dry run
    handler.delete_remote_branch(BranchName("temp_branch"))


def test_bioconda_repo_unblacklisted(sample_git_repo: Path):
    repo = MockBiocondaRepo(sample_git_repo, dry_run=True, allow_dirty=True)

    # In master, recipes/pkg_blacklisted is blacklisted.
    # Now create a branch where it is removed from blacklist.txt
    run_git(["checkout", "-b", "unblacklist_branch"], cwd=sample_git_repo)
    (sample_git_repo / "blacklist.txt").write_text("# empty blacklist\n")

    # Also create the recipe folder
    blacklisted_recipe = sample_git_repo / "recipes" / "pkg_blacklisted"
    blacklisted_recipe.mkdir(parents=True)
    (blacklisted_recipe / "meta.yaml").write_text("package:\n  name: pkg_blacklisted\n")

    run_git(["add", "."], cwd=sample_git_repo)
    run_git(["commit", "-m", "unblacklist pkg_blacklisted"], cwd=sample_git_repo)

    unblacklisted = repo.get_unblacklisted("unblacklist_branch", "master")
    assert Path("recipes/pkg_blacklisted") in unblacklisted

    to_build = repo.get_recipes_to_build("unblacklist_branch", "master")
    assert Path("recipes/pkg_blacklisted") in to_build
