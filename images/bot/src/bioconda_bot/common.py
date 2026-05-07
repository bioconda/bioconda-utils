import logging
import os
import re
import sys
from asyncio import gather, sleep
from asyncio.subprocess import create_subprocess_exec
from pathlib import Path
from shutil import which
from typing import Any, Dict, List, Optional, Set, Tuple, Mapping
from zipfile import ZipFile

from aiohttp import ClientSession
from yaml import safe_load

logger = logging.getLogger(__name__)
log = logger.info


async def async_exec(
    command: str, *arguments: str, env: Optional[Dict[str, str]] = None
) -> None:
    process = await create_subprocess_exec(command, *arguments, env=env)
    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"Failed to execute {command} {arguments} (return code: {return_code})"
        )


# Post a comment on a given issue/PR with text in message
async def send_comment(session: ClientSession, issue_number: int, message: str) -> None:
    token = os.environ["BOT_TOKEN"]
    url = (
        f"https://api.github.com/repos/bioconda/bioconda-recipes/issues/{issue_number}/comments"
    )
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    payload = {"body": message}
    log("Sending comment: url=%s", url)
    log("Sending comment: payload=%s", payload)
    async with session.post(url, headers=headers, json=payload) as response:
        status_code = response.status
        log("the response code was %d", status_code)
        if status_code < 200 or status_code > 202:
            sys.exit(1)


# Return true if a user is a member of bioconda
async def is_bioconda_member(session: ClientSession, user: str) -> bool:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/orgs/bioconda/members/{user}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    rc = 404
    async with session.get(url, headers=headers) as response:
        try:
            response.raise_for_status()
            rc = response.status
        except:
            # Do nothing, this just prevents things from crashing on 404
            pass

    return rc == 204


# Fetch and return the JSON of a PR
# This can be run to trigger a test merge
async def get_pr_info(session: ClientSession, pr: int) -> Any:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/pulls/{pr}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    pr_info = safe_load(res)
    return pr_info


def list_zip_contents(fname: str) -> [str]:
    f = ZipFile(fname)
    return [
        e.filename
        for e in f.infolist()
        if e.filename.endswith((".tar.gz", ".conda", ".tar.bz2"))
    ]


# Download a zip file from url to zipName.zip and return that path
# Timeout is 30 minutes to compensate for any network issues
async def download_file(session: ClientSession, zipName: str, url: str, headers: Optional[Mapping[str, str]] = None) -> str:
    async with session.get(url, timeout=60*30, headers=headers) as response:
        if response.status == 200:
            ofile = f"{zipName}.zip"
            with open(ofile, 'wb') as fd:
                while True:
                    chunk = await response.content.read(1024*1024*1024)
                    if not chunk:
                        break
                    fd.write(chunk)
            return ofile
    return None


# Find artifact zip files, download them and return their URLs and contents
async def fetch_azure_zip_files(session: ClientSession, buildId: str) -> [(str, str)]:
    artifacts = []

    url = f"https://dev.azure.com/bioconda/bioconda-recipes/_apis/build/builds/{buildId}/artifacts?api-version=4.1"
    log("contacting azure %s", url)
    async with session.get(url) as response:
        # Sometimes we get a 301 error, so there are no longer artifacts available
        if response.status == 301 or response.status == 404:
            return artifacts
        res = await response.text()

    res_object = safe_load(res)
    if res_object['count'] == 0:
        return artifacts

    for artifact in res_object['value']:
        zipName = artifact['name']  # LinuxArtifacts or OSXArtifacts
        zipUrl = artifact['resource']['downloadUrl']
        log(f"zip name is {zipName} url {zipUrl}")
        fname = await download_file(session, zipName, zipUrl)
        if not fname:
            continue
        pkgsImages = list_zip_contents(fname)
        for pkg in pkgsImages:
            artifacts.append((zipUrl, pkg))

    return artifacts


def parse_azure_build_id(url: str) -> str:
    return re.search("buildId=(\d+)", url).group(1)

# Find artifact zip files, download them and return their URLs and contents
async def fetch_circleci_artifacts(session: ClientSession, workflowId: str) -> [(str, str)]:
    artifacts = []

    url_wf = f"https://circleci.com/api/v2/workflow/{workflowId}/job"
    async with session.get(url_wf) as response:
        # Sometimes we get a 301 error, so there are no longer artifacts available
        if response.status == 301:
            return artifacts
        res_wf = await response.text()

    res_wf_object = safe_load(res_wf)

    if len(res_wf_object["items"]) == 0:
        return artifacts
    else:
        for job in res_wf_object["items"]:
            if job["name"].startswith(f"build_and_test-"):
                circleci_job_num = job["job_number"]
                url = f"https://circleci.com/api/v1.1/project/gh/bioconda/bioconda-recipes/{circleci_job_num}/artifacts"

                async with session.get(url) as response:
                    response.raise_for_status()
                    res = await response.text()
                res_object = safe_load(res)
                for artifact in res_object:
                    zipUrl = artifact["url"]
                    pkg = artifact["path"]
                    if zipUrl.endswith((".conda", ".tar.bz2")): # (currently excluding container images) or zipUrl.endswith(".tar.gz"):
                        artifacts.append((zipUrl, pkg))
        return artifacts


# Find artifact zip files, download them and return their URLs and contents
async def fetch_gha_zip_files(session: ClientSession, workflowId: str) -> [(str, str)]:
    artifacts = []
    token = os.environ["BOT_TOKEN"]
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "BiocondaCommentResponder",
    }
    # GitHub Actions uses two different URLs, one for downloading from a browser and another for API downloads
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/actions/runs/{workflowId}/artifacts"
    log("contacting github actions %s", url)
    async with session.get(url, headers=headers) as response:
        # Sometimes we get a 301 error, so there are no longer artifacts available
        if response.status == 301:
            return artifacts
        res = await response.text()

    res_object = safe_load(res)
    if res_object['total_count'] == 0:
        return artifacts

    for artifact in res_object['artifacts']:
        zipName = artifact['name']
        zipUrl = artifact['archive_download_url']
        log(f"zip name is {zipName} url {zipUrl}")
        fname = await download_file(session, zipName, zipUrl, headers)
        if not fname:
            continue
        pkgsImages = list_zip_contents(fname)
        commentZipUrl = f"https://github.com/bioconda/bioconda-recipes/actions/runs/{workflowId}/artifacts/{artifact['id']}"
        for pkg in pkgsImages:
            artifacts.append((commentZipUrl, pkg))

    return artifacts

def parse_gha_build_id(url: str) -> str:
    return re.search("runs/(\d+)/", url).group(1)


# Given a PR and commit sha, fetch a list of the artifact zip files URLs and their contents
async def fetch_pr_sha_artifacts(session: ClientSession, pr: int, sha: str) -> Dict[str, List[Tuple[str, str]]]:
    url = f"https://api.github.com/repos/bioconda/bioconda-recipes/commits/{sha}/check-runs"

    headers = {
        "User-Agent": "BiocondaCommentResponder",
        "Accept": "application/vnd.github.antiope-preview+json",
    }
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        res = await response.text()
    check_runs = safe_load(res)

    artifact_sources = {}
    for check_run in check_runs["check_runs"]:
        if (
            "azure" not in artifact_sources and 
            check_run["app"]["slug"] == "azure-pipelines" and
            check_run["name"].startswith("bioconda.bioconda-recipes (test_")
        ):
            # azure builds
            # The azure build ID is in the details_url as buildId=\d+
            buildID = parse_azure_build_id(check_run["details_url"])
            zipFiles = await fetch_azure_zip_files(session, buildID)
            artifact_sources["azure"] = zipFiles  # We've already fetched all possible artifacts from Azure
        elif (
            "circleci" not in artifact_sources and 
            check_run["app"]["slug"] == "circleci-checks"
        ):
            # Circle CI builds
            workflowId = safe_load(check_run["external_id"])["workflow-id"]
            zipFiles = await fetch_circleci_artifacts(session, workflowId)
            artifact_sources["circleci"] = zipFiles  # We've already fetched all possible artifacts from CircleCI
        elif (
            "github-actions" not in artifact_sources and 
            check_run["app"]["slug"] == "github-actions"
        ):
            # GitHub Actions builds
            buildID = parse_gha_build_id(check_run["details_url"])
            zipFiles = await fetch_gha_zip_files(session, buildID)
            artifact_sources["github-actions"] = zipFiles  # We've already fetched all possible artifacts from GitHub Actions

    return artifact_sources


async def get_sha_for_status(job_context: Dict[str, Any]) -> Optional[str]:
    if job_context["event_name"] != "status":
        return None
    log("Got %s event", "status")
    event = job_context["event"]
    if event["state"] != "success":
        return None
    branches = event.get("branches")
    if not branches:
        return None
    sha: Optional[str] = branches[0]["commit"]["sha"]
    log("Use %s event SHA %s", "status", sha)
    return sha


async def get_sha_for_check_suite_or_workflow(
    job_context: Dict[str, Any], event_name: str
) -> Optional[str]:
    if job_context["event_name"] != event_name:
        return None
    log("Got %s event", event_name)
    event_source = job_context["event"][event_name]
    if event_source["conclusion"] != "success":
        return None
    sha: Optional[str] = event_source.get("head_sha")
    if not sha:
        pull_requests = event_source.get("pull_requests")
        if pull_requests:
            sha = pull_requests[0]["head"]["sha"]
    if not sha:
        return None
    log("Use %s event SHA %s", event_name, sha)
    return sha


async def get_sha_for_check_suite(job_context: Dict[str, Any]) -> Optional[str]:
    return await get_sha_for_check_suite_or_workflow(job_context, "check_suite")


async def get_sha_for_workflow_run(job_context: Dict[str, Any]) -> Optional[str]:
    return await get_sha_for_check_suite_or_workflow(job_context, "workflow_run")


async def get_prs_for_sha(session: ClientSession, sha: str) -> List[int]:
    headers = {
        "User-Agent": "BiocondaCommentResponder",
        "Accept": "application/vnd.github.v3+json",
    }
    pr_numbers: List[int] = []
    per_page = 100
    for page in range(1, 20):
        url = (
            "https://api.github.com/repos/bioconda/bioconda-recipes/pulls"
            f"?per_page={per_page}"
            f"&page={page}"
        )
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            res = await response.text()
        prs = safe_load(res)
        pr_numbers.extend(pr["number"] for pr in prs if pr["head"]["sha"] == sha)
        if len(prs) < per_page:
            break
    return pr_numbers


async def get_sha_for_status_check(job_context: Dict[str, Any]) -> Optional[str]:
    return await get_sha_for_status(job_context) or await get_sha_for_check_suite(job_context)


async def get_job_context() -> Any:
    job_context = safe_load(os.environ["JOB_CONTEXT"])
    log("%s", job_context)
    return job_context


async def get_pr_comment(job_context: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    event = job_context["event"]
    if event["issue"].get("pull_request") is None:
        return None, None
    issue_number = event["issue"]["number"]

    original_comment = event["comment"]["body"]
    log("the comment is: %s", original_comment)
    return issue_number, original_comment
