from enum import Enum
import glob
import os
import re
import tempfile
import zipfile
import logging
from typing import Any
from collections.abc import Iterator

import requests
import backoff
import json
from pathlib import Path
from bioconda_utils import utils
from bioconda_utils._types import (
    ContainerPlatform,
    docker_platform_tag_suffix,
)
from bioconda_utils.upload import (
    anaconda_upload,
    inspect_image_platform,
    upload_mulled_image_source,
)
from bioconda_utils.container_manifests import (
    write_image_record,
    registry_creds,
)

logger = logging.getLogger(__name__)

IMAGE_RE = re.compile(r"(.+)(?::|%3A|---)(.+)\.tar\.gz$")
# Exceptions to the {platform}-packages naming convention.
# Most platforms derive their artifact name automatically as
# f"{platform}-packages"; these entries exist only because
# the workflow artifact names don't match that formula.
GHA_ARTIFACT_NAME_EXCEPTIONS = {
    "linux-64": "linux-packages",
    "osx-64": "osx-packages",
    "linux-aarch64": "linux-arm64-packages",
}


def _gha_artifact_names_for_platform(platform: str) -> set[str]:
    return {
        f"{platform}-packages",
        GHA_ARTIFACT_NAME_EXCEPTIONS.get(platform, f"{platform}-packages"),
    }


def _job_platform_from_package_platform(package_platform: str) -> str:
    if package_platform == "linux-64":
        return "linux"
    if package_platform == "osx-64":
        return "osx"
    return package_platform


class UploadResult(Enum):
    SUCCESS = 1
    FAILURE = 2
    NO_ARTIFACTS = 3
    NO_PR = 4


def upload_pr_artifacts(
    config: str,
    repo_name: str,
    git_sha: str,
    dryrun: bool = False,
    mulled_upload_target: str | None = None,
    label: str | None = None,
    artifact_source: str = "azure",
    package_platform: str | None = None,
    container_platforms: list[ContainerPlatform] | None = None,
    mulled_upload_records: Path | None = None,
) -> UploadResult:
    _config = utils.load_config(config)
    if package_platform is None:
        repodata = utils.RepoData()
        package_platform = repodata.platform2subdir(repodata.native_platform())
    job_platform = _job_platform_from_package_platform(package_platform)
    selected_container_platforms = (
        set(container_platforms) if container_platforms is not None else None
    )

    gh = utils.get_github_client()

    repo = gh.get_repo(repo_name)

    commit = repo.get_commit(git_sha)
    prs = commit.get_pulls()
    if not prs or prs.totalCount < 1:
        # no PR found for the commit
        return UploadResult.NO_PR
    pr = prs[0]
    artifacts = set(
        fetch_artifacts(
            pr,
            artifact_source,
            repo,
            job_platform=job_platform,
            package_platform=package_platform,
        )
    )
    if not artifacts:
        # no artifacts found, fail and rebuild packages
        logger.info("No artifacts found.")
        return UploadResult.NO_ARTIFACTS
    else:
        success = []
        for artifact in artifacts:
            with tempfile.TemporaryDirectory() as tmpdir:
                # download the artifact
                if artifact_source == "azure":
                    artifact_path = os.path.join(tmpdir, os.path.basename(artifact))
                    download_artifact(artifact, artifact_path, artifact_source)
                    zipfile.ZipFile(artifact_path).extractall(tmpdir)
                elif artifact_source == "circleci":
                    artifact_dir = os.path.join(tmpdir, *(artifact.split("/")[-4:-1]))
                    artifact_path = os.path.join(
                        tmpdir, artifact_dir, os.path.basename(artifact)
                    )
                    Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                    download_artifact(artifact, artifact_path, artifact_source)
                elif artifact_source == "github-actions":
                    artifact_dir = os.path.join(tmpdir, "artifacts")
                    artifact_path = os.path.join(
                        artifact_dir, os.path.basename(artifact)
                    )
                    Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                    download_artifact(artifact, artifact_path, artifact_source)
                    zipfile.ZipFile(artifact_path).extractall(artifact_dir)

                # get all the contained packages and images and upload them
                platform_patterns = [package_platform]
                if package_platform.startswith("linux"):
                    platform_patterns.append("noarch")

                for platform_pattern in platform_patterns:
                    for ext in (".tar.bz2", ".conda"):
                        pattern = f"{tmpdir}/*/packages/{platform_pattern}/*{ext}"
                        logger.info(f"Checking for packages at {pattern}.")
                        for pkg in glob.glob(pattern):
                            if dryrun:
                                logger.info(f"Would upload {pkg} to anaconda.org.")
                                success.append(True)
                            else:
                                logger.info(f"Uploading {pkg} to anaconda.org.")
                                # upload the package
                                success.append(anaconda_upload(pkg, label=label))

                if mulled_upload_target:
                    quay_login = registry_creds()
                    if not quay_login:
                        raise ValueError("QUAY_LOGIN or QUAY_OAUTH_TOKEN is required")
                    if not selected_container_platforms:
                        raise ValueError(
                            "container_platforms is required for mulled artifact uploads"
                        )
                    if len(selected_container_platforms) != 1:
                        raise ValueError(
                            "Artifact upload requires exactly one container platform"
                        )
                    target_platform = next(iter(selected_container_platforms))

                    pattern = f"{tmpdir}/*/images/*.tar.gz"
                    logger.info(f"Checking for images at {pattern}.")
                    image_seen = False
                    image_matched = False
                    for img in glob.glob(pattern):
                        image_seen = True
                        # Skopeo can't handle a : in the file name
                        fixed_img_name = img.replace(":", "_")
                        os.rename(img, fixed_img_name)
                        source_ref = f"docker-archive:{fixed_img_name}"
                        source_platform = inspect_image_platform(source_ref)
                        if source_platform != target_platform:
                            logger.info(
                                "Skipping image %s for %s; archive platform is %s.",
                                img,
                                target_platform,
                                source_platform,
                            )
                            continue
                        image_matched = True
                        m = IMAGE_RE.match(os.path.basename(img))
                        assert m, f"Could not parse image name from {img}"
                        name, tag = m.groups()
                        suffix = docker_platform_tag_suffix(target_platform)
                        if suffix and tag.endswith(f"-{suffix}"):
                            tag = tag[: -(len(suffix) + 1)]
                        if label:
                            # add label to tag
                            tag = f"{tag}-{label}"
                        canonical_ref = f"quay.io/{mulled_upload_target}/{name}:{tag}"

                        if dryrun:
                            logger.info(
                                "Would upload %s to platform staging ref for %s.",
                                img,
                                canonical_ref,
                            )
                            success.append(True)
                        else:
                            # upload the image
                            logger.info(
                                "Uploading %s to platform staging ref for %s.",
                                img,
                                canonical_ref,
                            )
                            record = upload_mulled_image_source(
                                source_ref,
                                canonical_ref,
                                target_platform,
                                validate_platform=False,
                            )
                            success.append(True)
                            if mulled_upload_records is not None:
                                write_image_record(mulled_upload_records, record)
                    if image_seen and not image_matched:
                        logger.error(
                            "Found mulled image artifacts, but none matched %s.",
                            target_platform,
                        )
                        success.append(False)
        if not success:
            logger.info("No matching artifacts found to upload.")
            return UploadResult.NO_ARTIFACTS
        if all(success):
            return UploadResult.SUCCESS
        else:
            return UploadResult.FAILURE


@backoff.on_exception(backoff.expo, requests.exceptions.RequestException)
def download_artifact(url: str, to_path: str, artifact_source: str) -> None:
    logger.info(f"Downloading artifact {url}.")
    headers = {}
    if artifact_source == "github-actions":
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            logger.critical(
                "GITHUB_TOKEN required to download GitHub Actions artifacts"
            )
            exit(1)
        headers = {"Authorization": f"token {token}"}
    resp = requests.get(url, stream=True, allow_redirects=True, headers=headers)
    resp.raise_for_status()
    with open(to_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)


def fetch_artifacts(
    pr: Any,
    artifact_source: str,
    repo: Any,
    job_platform: str | None = None,
    package_platform: str | None = None,
) -> Iterator[str]:
    """
    Fetch artifacts from a PR.

    Parameters
    ----------
    pr: PR number
    artifact_source: application hosting build artifacts (e.g., Azure or Circle CI)

    Returns
    -------
    """
    commits = pr.get_commits()
    # get the last commit
    commit = commits[commits.totalCount - 1]
    # get the artifacts
    check_runs = commit.get_check_runs()
    if job_platform is None or package_platform is None:
        repodata = utils.RepoData()
        job_platform = job_platform or repodata.native_platform()
        package_platform = package_platform or repodata.platform2subdir(job_platform)
    assert job_platform is not None
    assert package_platform is not None
    for check_run in check_runs:
        if (
            artifact_source == "azure"
            and check_run.app.slug == "azure-pipelines"
            and check_run.name.startswith(
                f"bioconda.bioconda-recipes (test_{job_platform}"
            )
        ):
            # azure builds
            artifact_url = get_azure_artifacts(check_run)
            yield from artifact_url
        elif artifact_source == "circleci" and check_run.app.slug == "circleci-checks":
            # Circle CI builds
            artifact_url = get_circleci_artifacts(check_run, job_platform)
            yield from artifact_url
        elif (
            artifact_source == "github-actions"
            and check_run.app.slug == "github-actions"
        ):
            # GitHubActions builds
            artifact_url = get_gha_artifacts(check_run, package_platform, repo)
            yield from artifact_url


def get_azure_artifacts(check_run: Any) -> Iterator[str]:
    azure_build_id = parse_azure_build_id(check_run.details_url)
    url = f"https://dev.azure.com/bioconda/bioconda-recipes/_apis/build/builds/{azure_build_id}/artifacts?api-version=4.1"
    res = requests.get(url, json=True).json()
    if res["count"] == 0:
        raise ValueError("No artifacts found!")
    else:
        for artifact in res["value"]:
            artifact_url = artifact["resource"]["downloadUrl"]
            yield artifact_url


def parse_azure_build_id(url: str) -> str:
    match = re.search(r"buildId=(\d+)", url)
    if match is None:
        raise ValueError(f"Could not parse Azure build ID from {url}")
    return match.group(1)


def get_circleci_artifacts(check_run: Any, platform: str) -> Iterator[str]:
    circleci_workflow_id = json.loads(check_run.external_id)["workflow-id"]
    # Must use a Personal token for API v2
    token = os.environ.get("CIRCLECI_TOKEN")
    if not token:
        logger.critical("CIRCLECI_TOKEN required to download CircleCI artifacts list.")
        exit(1)
    headers = {"Circle-Token": token}

    # Use API v2 because v1.1 does not have a workflow endpoint
    url_wf = f"https://circleci.com/api/v2/workflow/{circleci_workflow_id}/job"
    res_wf = requests.get(url_wf, headers=headers)
    res_wf.raise_for_status()
    json_wf = json.loads(res_wf.text)

    if len(json_wf["items"]) == 0:
        raise ValueError("No jobs found!")
    else:
        for job in json_wf["items"]:
            if job["name"].startswith(f"build_and_test-{platform}"):
                circleci_job_num = job["job_number"]
                url = f"https://circleci.com/api/v2/project/gh/bioconda/bioconda-recipes/{circleci_job_num}/artifacts"
                res = requests.get(url, headers=headers)
                res.raise_for_status()
                json_job = json.loads(res.text)
                if len(json_job["items"]) == 0:
                    raise ValueError("No artifacts found!")
                else:
                    for artifact in json_job["items"]:
                        artifact_url = artifact["url"]
                        if artifact_url.endswith(
                            (".html", ".json", ".json.bz2", ".json.zst")
                        ):
                            continue
                        else:
                            yield artifact_url


def parse_gha_build_id(url: str) -> str:
    # Get workflow run id from URL
    match = re.search(r"runs/(\d+)/", url)
    if match is None:
        raise ValueError(f"Could not parse GitHub Actions run ID from {url}")
    return match.group(1)


def get_gha_artifacts(check_run: Any, platform: str, repo: Any) -> Iterator[str]:
    gha_workflow_id = parse_gha_build_id(check_run.details_url)
    if gha_workflow_id:
        # The workflow run is different from the check run
        run = repo.get_workflow_run(int(gha_workflow_id))
        artifacts = run.get_artifacts()
        artifact_names = _gha_artifact_names_for_platform(platform)
        for artifact in artifacts:
            if artifact.name not in artifact_names:
                continue
            # This URL is valid for 1 min and requires a token
            artifact_url = artifact.archive_download_url
            yield artifact_url
