import logging
import os
import re

from aiohttp import ClientSession
from typing import List, Tuple
from yaml import safe_load

from .common import (
    async_exec,
    fetch_pr_sha_artifacts,
    get_job_context,
    get_pr_comment,
    get_pr_info,
    get_prs_for_sha,
    get_sha_for_status_check,
    is_bioconda_member,
    send_comment,
)

logger = logging.getLogger(__name__)
log = logger.info


# Given a PR and commit sha, post a comment with any artifacts
async def make_artifact_comment(session: ClientSession, pr: int, sha: str) -> None:
    artifactDict = await fetch_pr_sha_artifacts(session, pr, sha)
    
    header = "Package(s) built are ready for inspection:\n\n"
    header += "Arch | Package | Zip File / Repodata | CI | Instructions\n"
    header += "-----|---------|---------|-----|---------\n"

    comment = ""
    # Table of packages and zips
    for [ci_platform, artifacts] in artifactDict.items():
        if ci_platform == "azure":
            comment += compose_azure_comment(artifacts)
        elif ci_platform == "circleci":
            comment += compose_circlci_comment(artifacts)
        elif ci_platform == "github-actions":
            comment += compose_gha_comment(artifacts)
    if len(comment) == 0:
        comment = ( "No artifacts found on the most recent builds. "
            "Either the builds failed, the artifacts have been removed due to age, or the recipe was blacklisted/skipped.")
    else:
        comment = header + comment

    # Table of containers
    imageHeader = "***\n\nDocker image(s) built:\n\n"
    imageHeader += "Package | Tag | CI | Install with `docker`\n"
    imageHeader += "---------|---------|-----|---------\n"
    for [ci_platform, artifacts] in artifactDict.items():
        for URL, artifact in artifacts:
            if artifact.endswith(".tar.gz"):
                image_name = artifact.split("/").pop()[: -len(".tar.gz")]
                if ':' in image_name:
                    package_name, tag = image_name.split(':', 1)
                elif '---' in image_name:
                    package_name, tag = image_name.split('---', 1)
                else:
                    log(f"Skipping image {image_name}: missing separator")
                    continue
                comment += imageHeader
                imageHeader = "" # only add the header for the first image
                if ci_platform == "azure":
                    comment += f"{package_name} | {tag} | Azure | "
                    comment += "<details><summary>show</summary>Images for Azure are in the LinuxArtifacts zip file above."
                    comment += f"`gzip -dc LinuxArtifacts/images/{image_name}.tar.gz \\| docker load`</details>\n"
                elif ci_platform == "circleci":
                    comment += f"[{package_name}]({URL}) | {tag} | CircleCI | "
                    comment += f'<details><summary>show</summary>`curl -L "{URL}" \\| gzip -dc \\| docker load`</details>\n'
                elif ci_platform == "github-actions":
                    comment += f"{package_name} | {tag} | GitHub Actions | "
                    comment += "<details><summary>show</summary>Images are in the linux-64 zip file above."
                    comment += f"`gzip -dc images/{image_name}.tar.gz \\| docker load`</details>\n"
    comment += "\n\n"
    
    await send_comment(session, pr, comment)

def compose_azure_comment(artifacts: List[Tuple[str, str]]) -> str:
    nPackages = len(artifacts)

    if nPackages < 1:
        return ""
    
    comment = ""
    # Table of packages and zips
    for URL, artifact in artifacts:
        if not (package_match := re.match(r"^((.+)\/(.+)\/(.+)\/(.+\.conda|.+\.tar\.bz2))$", artifact)):
            continue
        url, archdir, basedir, subdir, packageName = package_match.groups()

        comment += f"{subdir} | {packageName} | [{archdir}.zip]({URL}) | Azure | "
        comment += f'<details><summary>show</summary>'
        # Conda install examples
        comment += f"You may also use `conda` to install after downloading and extracting the zip file. From the {archdir} directory: "
        comment += "`conda install -c ./packages <package name>`"
        comment +='</details>\n'

    return comment

def compose_circlci_comment(artifacts: List[Tuple[str, str]]) -> str:
    nPackages = len(artifacts)

    if nPackages < 1:
        return ""

    comment = ""
    # Table of packages and repodata.json
    for URL, artifact in artifacts:
        if not (package_match := re.match(r"^((.+)\/(.+)\/(.+\.conda|.+\.tar\.bz2))$", URL)):
            continue
        url, basedir, subdir, packageName = package_match.groups()
        repo_url = "/".join([basedir, subdir, "repodata.json"])
        conda_install_url = basedir

        comment += f"{subdir} | [{packageName}]({URL}) | [repodata.json]({repo_url}) | CircleCI | "
        comment += f'<details><summary>show</summary>'
        # Conda install examples
        comment += "You may also use `conda` to install:"
        comment += f"`conda install -c {conda_install_url} <package name>`"
        comment +='</details>\n'

    return comment

def compose_gha_comment(artifacts: List[Tuple[str, str]]) -> str:
    nPackages = len(artifacts)

    if nPackages < 1:
        return ""
    
    comment = ""
    # Table of packages and zips
    for URL, artifact in artifacts:
        if not (package_match := re.match(r"^((.+)\/(.+)\/(.+\.conda|.+\.tar\.bz2))$", artifact)):
            continue
        url, basedir, subdir, packageName = package_match.groups()
        comment += f"{subdir} | {packageName} | [{subdir}.zip]({URL}) | GitHub Actions | "
        comment += f'<details><summary>show</summary>'
        # Conda install examples
        comment += "You may also use `conda` to install after downloading and extracting the zip file. "
        comment += "`conda install -c ./packages <package name>`"
        comment +='</details>\n'

    return comment

# Post a comment on a given PR with its artifacts
async def artifact_checker(session: ClientSession, issue_number: int) -> None:
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{issue_number}"
    headers = {
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    pr_info = safe_load(res)

    await make_artifact_comment(session, issue_number, pr_info["head"]["sha"])


# Reposts a quoted message in a given issue/PR if the user isn't a bioconda member
async def comment_reposter(session: ClientSession, user: str, pr: int, message: str) -> None:
    if await is_bioconda_member(session, user):
        log("Not reposting for %s", user)
        return
    log("Reposting for %s", user)
    await send_comment(
        session,
        pr,
        f"Reposting for @{user} to enable pings (courtesy of the BiocondaBot):\n\n> {message}",
    )


# Add the "Please review and merge" label to a PR
async def add_pr_label(session: ClientSession, pr: int) -> None:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/issues/{pr}/labels"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"labels": ["please review & merge"]}
    async with session.post(url, headers=headers, json=payload) as response:
        response.raise_for_status()


async def gitter_message(session: ClientSession, msg: str) -> None:
    token = os.environ["GITTER_TOKEN"]
    room_id = "57f3b80cd73408ce4f2bba26"
    url = f"https://api.gitter.im/v1/rooms/{room_id}/chatMessages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"text": msg}
    log("Sending request to %s", url)
    async with session.post(url, headers=headers, json=payload) as response:
        response.raise_for_status()


async def notify_ready(session: ClientSession, pr: int) -> None:
    try:
        await gitter_message(
            session,
            f"PR ready for review: https://github.com/bioconda/bioconda-recipes/pull/{pr}",
        )
    except Exception:
        logger.exception("Posting to Gitter failed", exc_info=True)
        # Do not die if we can't post to gitter!


# This requires that a JOB_CONTEXT environment variable, which is made with `toJson(github)`
async def main() -> None:
    job_context = await get_job_context()

    sha = await get_sha_for_status_check(job_context)
    if sha:
        # This is a successful status or check_suite event => post artifact lists.
        async with ClientSession() as session:
            for pr in await get_prs_for_sha(session, sha):
                await artifact_checker(session, pr)
        return

    issue_number, original_comment = await get_pr_comment(job_context)
    if issue_number is None or original_comment is None:
        return

    comment = original_comment.lower()
    async with ClientSession() as session:
        if comment.startswith(("@bioconda-bot", "@biocondabot")):
            if "please update" in comment:
                log("This should have been directly invoked via bioconda-bot-update")
                from .update import update_from_master

                await update_from_master(session, issue_number)
            elif " hello" in comment:
                await send_comment(session, issue_number, "Yes?")
            elif " please fetch artifacts" in comment or " please fetch artefacts" in comment:
                await artifact_checker(session, issue_number)
            #elif " please merge" in comment:
            #    await send_comment(session, issue_number, "Sorry, I'm currently disabled")
            #    #log("This should have been directly invoked via bioconda-bot-merge")
            #    #from .merge import request_merge
            #    #await request_merge(session, issue_number)
            elif " please add label" in comment:
                await add_pr_label(session, issue_number)
                await notify_ready(session, issue_number)
            # else:
            #    # Methods in development can go below, flanked by checking who is running them
            #      if job_context["actor"] != "dpryan79":
            #          console.log("skipping")
            #          sys.exit(0)
        elif "@bioconda/" in comment:
            await comment_reposter(
                session, job_context["actor"], issue_number, original_comment
            )
