import logging
import os

from typing import Any, Dict, List, Optional, Set, Tuple

from aiohttp import ClientSession
from yaml import safe_load

from .common import (
    get_job_context,
    get_prs_for_sha,
    get_sha_for_status_check,
    get_sha_for_workflow_run,
)
from .merge import MergeState, request_merge

logger = logging.getLogger(__name__)
log = logger.info


async def get_pr_labels(session: ClientSession, pr: int) -> Set[str]:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/issues/{pr}/labels"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    labels = safe_load(res)
    return {label["name"] for label in labels}


async def is_automerge_labeled(session: ClientSession, pr: int) -> bool:
    labels = await get_pr_labels(session, pr)
    return "automerge" in labels


async def merge_if_labeled(session: ClientSession, pr: int) -> MergeState:
    if not await is_automerge_labeled(session, pr):
        return MergeState.UNKNOWN
    return await request_merge(session, pr)


async def get_check_runs(session: ClientSession, sha: str) -> Any:
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/commits/{sha}/check-runs"

    headers = {
        "User-Agent": "BiocondaCommentResponder",
        "Accept": "application/vnd.github.antiope-preview+json",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    check_runs = [
        check_run
        for check_run in safe_load(res)["check_runs"] or []
        if check_run["name"] != "bioconda-bot automerge"
    ]
    log("Got %d check_runs for SHA %s", len(check_runs or []), sha)
    return check_runs


async def all_checks_completed(session: ClientSession, sha: str) -> bool:
    check_runs = await get_check_runs(session, sha)

    is_all_completed = all(check_run["status"] == "completed" for check_run in check_runs)
    if not is_all_completed:
        log("Some check_runs are not completed yet.")
        for i, check_run in enumerate(check_runs, 1):
            log("check_run %d / %d: %s", i, len(check_runs), check_run)
    return is_all_completed


async def all_checks_passed(session: ClientSession, sha: str) -> bool:
    check_runs = await get_check_runs(session, sha)

    # TODO: "neutral" might be a valid conclusion to consider in the future.
    valid_conclusions = {"success", "skipped"}
    if any(check_run["conclusion"] not in valid_conclusions for check_run in check_runs):
        log(f"Some check_runs are not marked as {'/'.join(valid_conclusions)} yet.")
        for i, check_run in enumerate(check_runs, 1):
            log("check_run %d / %d: %s", i, len(check_runs), check_run)
        return False
    return True


async def merge_automerge_passed(sha: str) -> None:
    async with ClientSession() as session:
        if not await all_checks_passed(session, sha):
            return
        prs = await get_prs_for_sha(session, sha)
        if not prs:
            log("No PRs found for SHA %s", sha)
        for pr in prs:
            merge_state = await merge_if_labeled(session, pr)
            log("PR %d has merge state %s", pr, merge_state)
            if merge_state is MergeState.MERGED:
                break


async def get_sha_for_review(job_context: Dict[str, Any]) -> Optional[str]:
    if job_context["event_name"] != "pull_request_review":
        return None
    log("Got %s event", "pull_request_review")
    event = job_context["event"]
    if event["review"]["state"] != "approved":
        return None
    sha: Optional[str] = event["pull_request"]["head"]["sha"]
    log("Use %s event SHA %s", "pull_request_review", sha)
    return sha


async def get_sha_for_labeled_pr(job_context: Dict[str, Any]) -> Optional[str]:
    if job_context["event_name"] != "pull_request":
        return None
    log("Got %s event", "pull_request")
    event = job_context["event"]
    if event["action"] != "labeled" or event["label"]["name"] != "automerge":
        return None
    sha: Optional[str] = event["pull_request"]["head"]["sha"]
    log("Use %s event SHA %s", "pull_request", sha)
    return sha


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context = await get_job_context()

    sha = (
        await get_sha_for_status_check(job_context)
        or await get_sha_for_workflow_run(job_context)
        or await get_sha_for_review(job_context)
        or await get_sha_for_labeled_pr(job_context)
    )
    if sha:
        await merge_automerge_passed(sha)
