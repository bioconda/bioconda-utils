"""Wrappers for interacting with ``git``"""

import asyncio
import logging
import re
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


BranchName = NewType("BranchName", str)
RemoteRef = NewType("RemoteRef", str)
CommitSha = NewType("CommitSha", str)
SymbolicRef = NewType("SymbolicRef", str)
RevisionExpression = NewType("RevisionExpression", str)

# Git accepts an open-ended revision grammar: branches, tags, remote refs,
# commit SHAs, symbolic refs such as HEAD, and expressions such as HEAD~1.
# Keep that open-ended category explicit instead of falling back to bare str.
type GitRev = BranchName | RemoteRef | CommitSha | SymbolicRef | RevisionExpression


@dataclass(frozen=True)
class GitRange:
    """Changes on ``ref`` since it diverged from ``base``.

    This matches the directional merge-base behavior of ``git diff A...B``.
    """

    base: GitRev
    ref: GitRev = SymbolicRef("HEAD")

    @classmethod
    def parse(cls, spec: str) -> "GitRange":
        spec = spec.strip()
        if not spec:
            raise ValueError("git range cannot be empty")

        if "..." in spec:
            if spec.count("...") != 1:
                raise ValueError("git range must contain exactly one '...' separator")
            base, ref = spec.split("...", maxsplit=1)
            if not base or not ref or base.endswith(".") or ref.startswith("."):
                raise ValueError("git range must have the form BASE...REF")
            if ".." in base or ".." in ref:
                raise ValueError("git refs cannot contain '..'")
            return cls(RevisionExpression(base), RevisionExpression(ref))

        if ".." in spec:
            raise ValueError(
                "two-dot ranges are not supported; use BASE...REF to select "
                "changes on REF since its merge base with BASE"
            )

        return cls(RevisionExpression(spec))

    def __str__(self) -> str:
        return f"{self.base}...{self.ref}"


def install_gpg_key(key) -> str:
    """Install GPG key

    Args:
      key: Key to import to GPG as string

    Returns:
      GPG key ID

    Raises:
      ValueError if importing the key failed
    """
    proc = subprocess.run(
        ["gpg", "--import"],
        input=key,
        stderr=subprocess.PIPE,
        encoding="ascii",
        check=False,
    )
    for line in proc.stderr.splitlines():
        match = re.match(
            r"gpg: key ([\dA-F]{8,16}): "
            r"(secret key imported|already in secret keyring)",
            line,
        )
        if match:
            keyid = match.group(1)
            break
    else:
        # If the key has escaped newlines (\\n literally), replace those
        # and try again
        if r"\n" in key:
            return install_gpg_key(key.replace(r"\n", "\n"))
        raise ValueError(f"Unable to import GPG key: {proc.stderr}")
    return keyid


class GitHandlerFailure(Exception):
    """Something went wrong interacting with git"""


@dataclass(frozen=True)
class GitActor:
    """Git author or committer representation."""

    name: str
    email: str | None = None

    def __str__(self) -> str:
        if self.email:
            return f"{self.name} <{self.email}>"
        return self.name


class GitRemote:
    """Represents a git remote."""

    def __init__(self, name: str, urls: list[str], repo: "GitHandlerBase") -> None:
        self.name = name
        self.urls = urls
        self.repo = repo

    @property
    def url(self) -> str:
        return self.urls[0] if self.urls else ""

    def fetch(self, *args: str, depth: int | None = None, prune: bool = False) -> None:
        cmd = ["fetch"]
        if depth:
            cmd.extend(["--depth", str(depth)])
        if prune:
            cmd.append("--prune")
        cmd.append(self.name)
        cmd.extend(args)
        self.repo._git(cmd)

    def pull(self, branch: BranchName) -> None:
        self.repo._git(["pull", self.name, branch])

    def push(self, *args: str) -> None:
        cmd = ["push", self.name] + list(args)
        self.repo._git(cmd)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"GitRemote({self.name!r})"


class GitHandlerBase:
    """Git abstraction using native git CLI subprocesses.

    We have to work with three git repositories, the local checkout,
    the project primary repository and a working repository. The
    latter may be a fork or may be the same as the primary.

    Arguments:
      repo_path: Path to repository root directory
      dry_run: Don't push anything to remote
      home: string occurring in remote url marking primary project repo
      fork: string occurring in remote url marking forked repo
      allow_dirty: don't bail out if repo is dirty
    """

    def __init__(
        self,
        repo_path: Path,
        dry_run: bool,
        home: str = "bioconda/bioconda-recipes",
        fork: str | None = None,
        allow_dirty: bool = False,
    ) -> None:
        self._working_dir = repo_path.resolve()

        if not allow_dirty and self.is_dirty():
            raise RuntimeError("Repository is in dirty state. Bailing out")
        #: Dry-Run mode - don't push or commit anything
        self.dry_run = dry_run
        #: Remote pointing to primary project repo
        self.home_remote = self.get_remote(home)
        if fork is not None:
            #: Remote to pull from
            self.fork_remote = self.get_remote(fork)
        else:
            self.fork_remote = self.home_remote

        #: Semaphore for things that mess with working directory
        self.lock_working_dir = asyncio.Semaphore(1)

        #: GPG key ID or bool, indicating whether/how to sign commits
        self._sign: bool | str = False

        #: Committer and Author
        self.actor: GitActor | None = None

    @property
    def working_dir(self) -> Path:
        return self._working_dir

    def _git(
        self,
        args: list[str],
        check: bool = True,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["git"] + args
        try:
            res = subprocess.run(
                cmd,
                cwd=self._working_dir,
                capture_output=True,
                text=True,
                input=input,
                check=False,
            )
        except Exception as exc:
            raise GitHandlerFailure(f"Failed to execute {cmd}: {exc}") from exc

        if check and res.returncode != 0:
            logger.debug("git command failed: %s\nstderr: %s", cmd, res.stderr)
            raise GitHandlerFailure(
                f"Command '{' '.join(cmd)}' returned non-zero exit status {res.returncode}: {res.stderr.strip()}"
            )
        return res

    def rev_parse(self, ref: GitRev) -> CommitSha:
        res = self._git(["rev-parse", str(ref)])
        return CommitSha(res.stdout.strip())

    def is_dirty(self) -> bool:
        """Checks if there are unstaged or staged changes in tracked files."""
        p1 = self._git(["diff", "--quiet"], check=False)
        if p1.returncode != 0:
            return True
        p2 = self._git(["diff", "--cached", "--quiet"], check=False)
        return p2.returncode != 0

    def close(self) -> None:
        """Release resources allocated"""

    def __str__(self) -> str:
        def get_name(remote: GitRemote) -> str:
            url = next(iter(remote.urls))
            return url[url.rfind("/", 0, url.rfind("/")) + 1 :]

        name = get_name(self.home_remote)
        if self.fork_remote != self.home_remote:
            name = f"{name} <- {get_name(self.fork_remote)}"
        return f"{self.__class__.__name__}({name})"

    def enable_signing(self, key: bool | str = True) -> None:
        """Enable signing of commits

        Args:
          key: Keyid to use for signing. Set to ``True`` to enable
               using the default key or to ``False`` to disable
               signing.
        """
        self._sign = key

    def get_remotes(self) -> list[GitRemote]:
        """Returns all configured remotes."""
        res = self._git(["config", "--get-regexp", r"^remote\..*\.url"], check=False)
        remotes_map: dict[str, list[str]] = {}
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    key, url = parts
                    # key format: remote.<name>.url
                    key_parts = key.split(".")
                    if len(key_parts) >= 3:
                        name = key_parts[1]
                        remotes_map.setdefault(name, []).append(url)
        return [GitRemote(name, urls, self) for name, urls in remotes_map.items()]

    def get_remote(self, desc: str) -> GitRemote:
        """Finds first remote containing **desc** in one of its URLs"""
        remotes = self.get_remotes()
        # try if desc is the name
        for r in remotes:
            if r.name == desc:
                return r

        # perhaps it's an URL. If so, first apply insteadOf config
        cfg_res = self._git(
            ["config", "--get-regexp", r"^url\..*\.insteadof"], check=False
        )
        if cfg_res.returncode == 0 and cfg_res.stdout:
            for line in cfg_res.stdout.splitlines():
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    key, old = parts
                    # key is url.<new>.insteadof
                    new = key[len("url.") : -len(".insteadof")]
                    desc = desc.replace(old, new)

        matching = [r for r in remotes if any(desc in url for url in r.urls)]
        if not matching:
            raise KeyError(f"No remote matching '{desc}' found")
        if len(matching) > 1:
            logger.warning("Multiple remotes found. Using first")
        return matching[0]

    async def branch_is_current(
        self, branch: GitRev, path: Path, master: GitRev = BranchName("master")
    ) -> bool:
        """Checks if **branch** is missing any commits to **path**
        as compared to **master**"""
        branch_name = str(branch)
        master_name = str(master)
        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            f"{branch_name}..{master_name}",
            "--",
            str(path),
            cwd=self._working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise GitHandlerFailure(
                "Unable to compare branch "
                f"{branch_name} with {master}: {stderr.decode().strip()}"
            )
        return len(stdout) == 0

    def delete_local_branch(self, branch: BranchName) -> None:
        """Deletes **branch** locally"""
        self._git(["branch", "-D", str(branch)])

    def delete_remote_branch(self, branch_name: BranchName) -> None:
        """Deletes **branch** on fork remote"""
        if not self.dry_run:
            logger.info("Deleting branch %s", branch_name)
            self.fork_remote.push(":" + str(branch_name))
        else:
            logger.info("Would delete branch %s", branch_name)

    def has_local_branch(self, branch_name: BranchName) -> bool:
        """Checks if local branch named **branch_name** exists"""
        res = self._git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            check=False,
        )
        return res.returncode == 0

    def get_local_branch(self, branch_name: BranchName) -> BranchName | None:
        """Finds local branch named **branch_name**"""
        if self.has_local_branch(branch_name):
            return branch_name
        return None

    def get_active_branch(self) -> BranchName:
        """Returns the currently checked out branch name, or raises TypeError if detached HEAD."""
        res = self._git(["symbolic-ref", "--short", "HEAD"], check=False)
        if res.returncode != 0:
            raise TypeError("HEAD is detached")
        return BranchName(res.stdout.strip())

    def checkout(self, branch_name: GitRev) -> None:
        """Checks out **branch_name**"""
        self._git(["checkout", str(branch_name)])

    @staticmethod
    def is_sha(ref: str) -> bool:
        """Checks if **ref** is a commit checksum

        Verifies that **ref** is a hex value of length 40
        """
        if len(ref) == 40:
            try:
                int(ref, 16)
                return True
            except ValueError:
                pass
        return False

    def has_remote_branch(
        self, branch_name: BranchName, remote: str | GitRemote | None = None
    ) -> bool:
        """Checks if remote branch named **branch_name** exists"""
        remote_name = (
            remote.name
            if isinstance(remote, GitRemote)
            else (remote or self.fork_remote.name)
        )
        res = self._git(
            [
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/remotes/{remote_name}/{branch_name}",
            ],
            check=False,
        )
        return res.returncode == 0

    def get_remote_branch(self, branch_name: BranchName) -> RemoteRef | None:
        """Finds a cached fork remote branch without fetching or resolving revisions."""
        if self.has_remote_branch(branch_name):
            return RemoteRef(f"{self.fork_remote.name}/{branch_name}")
        return None

    def get_latest_master(self) -> CommitSha:
        self.home_remote.fetch("master")
        return self.rev_parse(SymbolicRef("FETCH_HEAD"))

    def read_from_branch(self, branch: GitRev, file_path: Path) -> str:
        """Reads contents of file **file_path** from git branch **branch**"""
        target = (self._working_dir / file_path).resolve()
        if not target.is_relative_to(self._working_dir):
            raise RuntimeError(f"File {target} not inside {self._working_dir}")
        rel_path = target.relative_to(self._working_dir)
        ref = str(branch)
        res = self._git(["show", f"{ref}:{rel_path.as_posix()}"], check=False)
        if res.returncode == 0:
            return res.stdout

        raise GitHandlerFailure(
            f"File {rel_path} not found on branch {branch} commit {ref}"
        )

    def create_local_branch(
        self,
        branch_name: BranchName,
        start_point: GitRev | None = None,
    ) -> BranchName:
        """Creates a branch at an explicit revision or its cached fork remote branch.

        Explicit revisions are passed directly to Git. No remote fetch is performed.
        """
        if start_point is None:
            start_point = self.get_remote_branch(branch_name)
        if start_point is None:
            raise GitHandlerFailure(f"Unable to find remote branch {branch_name}")
        self._git(["branch", str(branch_name), str(start_point)])
        return branch_name

    def merge_base(self, other: GitRev, ref: GitRev) -> list[CommitSha]:
        """Runs git merge-base -a other ref"""
        res = self._git(["merge-base", "-a", str(other), str(ref)], check=False)
        if res.returncode == 0 and res.stdout.strip():
            return [
                CommitSha(line.strip())
                for line in res.stdout.splitlines()
                if line.strip()
            ]
        return []

    def get_merge_base(
        self,
        ref: GitRev | None = None,
        other: GitRev | None = None,
        try_fetch: bool = False,
    ) -> CommitSha:
        """Determines the merge base for **other** and **ref**

        See git merge-base. Returns the commit at which **ref** split
        from **other** and from which point on changes would be
        merged.

        Args:
          ref: One of the two tips for which a merge base is sought.
               Defaults to the currently checked out HEAD. This is the
               second argument to ``git merge-base``.
          other: One of the two tips for which a merge base is sought.
               Defaults to ``origin/master`` (``home_remote``). This is
               the first argument to ``git merge-base``.

        Returns:
          The first merge base commit SHA for the two references provided.

        Raises:
          GitHandlerFailure: If no merge base was found. This may for
          example happen if branches were deleted or if the repository is
          shallow and the merge base commit is not available.
        """
        if ref is None:
            try:
                ref = self.get_active_branch()
            except TypeError:
                ref = self.rev_parse(SymbolicRef("HEAD"))

        if other is None:
            other = RemoteRef(f"{self.home_remote.name}/master")

        depths = (0, 50, 200) if try_fetch else (0,)
        merge_bases: list[CommitSha] = []
        for depth in depths:
            if depth:
                self.fork_remote.fetch(str(ref), depth=depth)
                self.home_remote.fetch("master", depth=depth)
            merge_bases = self.merge_base(other, ref)
            if merge_bases:
                break
            logger.debug(
                "No merge base found for %s and master at depth %i", ref, depth
            )
        else:
            raise GitHandlerFailure(f"No merge base found for {ref} and master")
        if len(merge_bases) > 1:
            logger.error(
                "Multiple merge bases found for %s and master: %s",
                ref,
                merge_bases,
            )
        return merge_bases[0]

    def list_changed_files(
        self, ref: GitRev | None = None, other: GitRev | None = None
    ) -> Generator[Path]:
        """Lists files that would be added/modified by merge of **other** into **ref**

        See also `get_merge_base()`.

        Args:
          ref: Defaults to ``HEAD`` (active branch), one of the tips compared
          other: Defaults to ``origin/master``, other tip compared

        Returns:
          Generator over modified or created (**not deleted**) files.
        """
        ref_str = "HEAD" if not ref else str(ref)
        merge_base = self.get_merge_base(ref, other)
        res = self._git(
            ["diff", "--name-only", "--diff-filter=d", str(merge_base), ref_str]
        )
        for path in res.stdout.splitlines():
            path = path.strip()
            if path:
                yield Path(path)

    def list_modified_files(self) -> Generator[Path]:
        """Lists files modified in working directory"""
        res = self._git(["diff", "--name-only"])
        seen = set()
        for fname in res.stdout.splitlines():
            fname = fname.strip()
            if fname and fname not in seen:
                seen.add(fname)
                yield Path(fname)

    def prepare_branch(self, branch_name: BranchName) -> None:
        """Checks out **branch_name**, creating it from home remote master if needed"""
        if not self.has_local_branch(branch_name):
            logger.info("Creating new branch %s", branch_name)
            from_commit = self.get_latest_master()
            self._git(["branch", str(branch_name), str(from_commit)])
        logger.info("Checking out branch %s", branch_name)
        self.checkout(branch_name)

    def commit_and_push_changes(
        self,
        files: list[Path],
        branch_name: BranchName | None,
        msg: str,
        sign: bool | str = False,
    ) -> bool:
        """Create recipe commit and pushes to upstream remote

        Returns:
          Boolean indicating whether there were changes committed
        """
        if branch_name is None:
            try:
                push_ref: GitRev = self.get_active_branch()
            except TypeError:
                push_ref = SymbolicRef("HEAD")
        else:
            push_ref = branch_name
        if not files:
            files = [Path(f) for f in self.list_modified_files()]
        if files:
            self._git(["add", "--"] + [str(f) for f in files])
        res = self._git(["diff", "--cached", "--quiet"], check=False)
        if res.returncode == 0:
            return False

        if self._sign and not sign:
            sign = self._sign
        commit_cmd = ["commit", "-m", msg]
        if sign:
            commit_cmd.append("-S" + sign if isinstance(sign, str) else "-S")
        if self.actor:
            email = self.actor.email
            if email is None:
                ident = self._git(["var", "GIT_AUTHOR_IDENT"]).stdout.strip()
                match = re.search(r"<([^<>]+)>", ident)
                if match is None:
                    raise GitHandlerFailure(
                        f"Unable to determine author email from {ident!r}"
                    )
                email = match.group(1)
            author_str = f"{self.actor.name} <{email}>"
            commit_cmd.extend(["--author", author_str])

        self._git(commit_cmd)

        if not self.dry_run:
            logger.info("Pushing branch %s", push_ref)
            push_res = self._git(
                ["push", self.fork_remote.name, str(push_ref)], check=False
            )
            if push_res.returncode != 0:
                logger.error("Failed to push branch %s: %s", push_ref, push_res.stderr)
                raise GitHandlerFailure(push_res.stderr or push_res.stdout)
        else:
            logger.info("Would push branch %s", push_ref)
        return True

    def set_user(self, user: str, email: str | None = None) -> None:
        """Set the user and email to use for committing"""
        self.actor = GitActor(user, email)


class BiocondaRepoMixin(GitHandlerBase):
    """Githandler with logic specific to Bioconda Repo"""

    #: location of recipes folder within repo
    recipes_folder = Path("recipes")

    #: location of configuration file within repo
    config_file = Path("config.yml")

    def get_changed_recipes(
        self,
        ref: GitRev | None = None,
        other: GitRev | None = None,
        files: list[Path] | None = None,
    ) -> list[Path]:
        """Returns list of modified recipes

        Args:
          ref: See `get_merge_base`. Defaults to HEAD
          other: See `get_merge_base`. Defaults to origin/master
          files: List of file basenames to consider. Defaults to ``meta.yaml``
                 ``recipe.yaml`` and ``build.sh``
        Result:
          List of unique recipe folders with changes as Paths from repo
          root (e.g. ``Path('recipes/blast')``). Recipes outside of
          ``recipes_folder`` are ignored.
        """
        if files is None:
            files = [Path("meta.yaml"), Path("recipe.yaml"), Path("build.sh")]
        wanted_names = {file.name for file in files}
        changed: set[Path] = set()
        for path_str in self.list_changed_files(ref, other):
            path = Path(path_str)
            if not path.is_relative_to(self.recipes_folder):
                continue  # skip things outside the recipes folder
            if path.name in wanted_names:
                changed.add(path.parent)
        return list(changed)

    def get_blacklisted(self, ref: GitRev | None = None) -> set[Path]:
        """Get blacklisted recipes as of **ref**

        Args:
          ref: Name of branch or commit (HEAD~1 is allowed), defaults to
               currently checked out branch
        Returns:
          `set` of blacklisted recipe Paths (relative to repo root)
        """
        if ref is None:
            try:
                branch: GitRev = self.get_active_branch()
            except TypeError:
                branch = SymbolicRef("HEAD")
        else:
            branch = ref
        config_data = self.read_from_branch(branch, self.config_file)
        config = YAML(typ="safe").load(config_data)
        blacklists = config.get("blacklists", [])
        blacklisted: set[Path] = set()
        for blacklist in blacklists:
            try:
                blacklist_data = self.read_from_branch(branch, Path(blacklist))
            except GitHandlerFailure as exc:
                raise GitHandlerFailure(
                    f"Unable to read configured blacklist {blacklist!r} from {branch}"
                ) from exc
            for line in blacklist_data.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                recipe_folder, _, _ = line.partition(" #")
                blacklisted.add(Path(recipe_folder.strip()))
        return blacklisted

    def get_unblacklisted(
        self, ref: GitRev | None = None, other: GitRev | None = None
    ) -> set[Path]:
        """Get recipes unblacklisted by a merge of **ref** into **other**

        Args:
          ref: Branch or commit or reference, defaults to current branch
          other: Same as **ref**, defaults to ``origin/master``

        Returns:
          `set` of unblacklisted recipe Paths (relative to repo root)
        """
        merge_base = self.get_merge_base(ref, other)
        orig_blacklist = self.get_blacklisted(merge_base)
        cur_blacklist = self.get_blacklisted(ref)
        return orig_blacklist.difference(cur_blacklist)

    def get_recipes_to_build(
        self, ref: GitRev | None = None, other: GitRev | None = None
    ) -> list[Path]:
        """Returns `list` of recipes to build for merge of **ref** into **other**

        This includes all recipes returned by `get_changed_recipes` and
        all newly unblacklisted, extant recipes within `recipes_folder`

        Returns:
          `list` of recipe Paths that should be built
        """
        tobuild = set(self.get_changed_recipes(ref, other))
        tobuild.update(
            [
                recipe
                for recipe in self.get_unblacklisted(ref, other)
                if recipe.is_relative_to(self.recipes_folder)
                and (self._working_dir / recipe).exists()
            ]
        )
        return list(tobuild)


class GitHandler(GitHandlerBase):
    """GitHandler for working with a pre-existing local checkout of bioconda-recipes

    Restores the branch active when created upon calling `close()`.
    """

    def __init__(
        self,
        folder: Path = Path("."),
        dry_run: bool = False,
        home: str = "bioconda/bioconda-recipes",
        fork: str | None = None,
        allow_dirty: bool = True,
        depth: int = 1,
    ) -> None:
        folder_path = folder.resolve()
        if folder_path.exists():
            res = subprocess.run(
                ["git", "-C", str(folder_path), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                raise GitHandlerFailure(
                    f"{folder_path} is not a git repository: {res.stderr}"
                )
            repo_root = Path(res.stdout.strip())
        else:
            try:
                folder_path.mkdir(parents=True, exist_ok=True)
                logger.error("cloning %s into %s", home, folder_path)
                clone_cmd = ["git", "clone", home, str(folder_path)]
                if depth:
                    clone_cmd.extend(["--depth", str(depth)])
                res = subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode != 0:
                    raise GitHandlerFailure(f"Failed to clone {home}: {res.stderr}")
                repo_root = folder_path
            except Exception:
                if folder_path.exists():
                    folder_path.rmdir()
                raise
        super().__init__(repo_root, dry_run, home, fork, allow_dirty)

        #: Branch to restore after running
        try:
            self.prev_active_branch: BranchName | None = self.get_active_branch()
        except TypeError:
            # This will fail on CI nodes from forks, but we don't need to switch back and forth between branches there
            logger.warning(
                "Couldn't get the active branch name, we must be on detached HEAD"
            )
            self.prev_active_branch = None

    def checkout_master(self) -> None:
        """Check out master branch (original branch restored by `close()`)"""
        logger.warning("Checking out master")
        self.checkout(BranchName("master"))
        logger.info("Updating master to latest project master")
        self.home_remote.pull(BranchName("master"))
        logger.info("Updating and pruning remotes")
        self.home_remote.fetch(prune=True)
        self.fork_remote.fetch(prune=True)

    def close(self) -> None:
        """Release resources allocated"""
        if self.prev_active_branch:
            logger.warning("Switching back to %s", self.prev_active_branch)
            self.checkout(self.prev_active_branch)
        super().close()


class BiocondaRepo(GitHandler, BiocondaRepoMixin):
    pass
