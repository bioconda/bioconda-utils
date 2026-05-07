import logging
import sys

from aiohttp import ClientSession

from .common import (
    async_exec,
    get_job_context,
    get_pr_comment,
    get_pr_info,
    send_comment,
)

logger = logging.getLogger(__name__)
log = logger.info


# Update a branch from upstream master, this should be run in a try/catch
async def update_from_master_runner(session: ClientSession, pr: int) -> None:
    async def git(*args: str) -> None:
        return await async_exec("git", *args)

    # Setup git, otherwise we can't push
    await git("config", "--global", "user.email", "biocondabot@gmail.com")
    await git("config", "--global", "user.name", "BiocondaBot")

    pr_info = await get_pr_info(session, pr)
    remote_branch = pr_info["head"]["ref"]
    remote_repo = pr_info["head"]["repo"]["full_name"]

    max_depth = 2000
    # Clone
    await git(
        "clone",
        f"--depth={max_depth}",
        f"--branch={remote_branch}",
        f"git@github.com:{remote_repo}.git",
        "bioconda-recipes",
    )

    async def git_c(*args: str) -> None:
        return await git("-C", "bioconda-recipes", *args)

    # Add/pull upstream
    await git_c("remote", "add", "upstream", "https://github.com/bioconda/bioconda-recipes")
    await git_c("fetch", f"--depth={max_depth}", "upstream", "master")

    # Merge
    await git_c("merge", "upstream/master")

    await git_c("push")


# Merge the upstream master branch into a PR branch, leave a message on error
async def update_from_master(session: ClientSession, pr: int) -> None:
    try:
        await update_from_master_runner(session, pr)
    except Exception as e:
        await send_comment(
            session,
            pr,
            "I encountered an error updating your PR branch. You can report this to bioconda/core if you'd like.\n-The Bot",
        )
        sys.exit(1)


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context = await get_job_context()
    issue_number, original_comment = await get_pr_comment(job_context)
    if issue_number is None or original_comment is None:
        return

    comment = original_comment.lower()
    if comment.startswith(("@bioconda-bot", "@biocondabot")):
        if "please update" in comment:
            async with ClientSession() as session:
                await update_from_master(session, issue_number)
