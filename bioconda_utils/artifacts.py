

import glob
import json
import os
import re
import tempfile
import zipfile

import requests
from bioconda_utils import utils
from bioconda_utils.upload import anaconda_upload, mulled_upload


def upload_pr_artifacts(repo, git_sha, dryrun=False, mulled_upload_target=None, label=None) -> bool:
    gh = utils.get_github_client()

    repo = gh.get_repo(repo)

    commit = repo.get_commit(git_sha)
    prs = commit.get_pulls()
    if not prs:
        # no PR found for the commit
        return True
    pr = prs[0]
    artifacts = set(fetch_artifacts(pr))
    if not artifacts:
        # no artifacts found, fail and rebuild packages
        return False
    else:
        for artifact in artifacts:
            with tempfile.TemporaryDirectory() as tmpdir:
                # download the artifact
                artifact_path = os.path.join(tmpdir, os.path.basename(artifact))
                resp = requests.get(artifact, stream=True, allow_redirects=True)
                resp.raise_for_status()
                with open(artifact_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024):
                        if chunk:
                            f.write(chunk)
                zipfile.ZipFile(artifact_path).extractall(tmpdir)
                # get all the contained packages and images
                for pkg in glob.glob(f"{tmpdir}/*/packages/*/*.tar.bz2"):
                    if dryrun:
                        print(f"Would upload {pkg} to anaconda.org.")
                    else:
                        # upload the artifact
                        anaconda_upload(pkg, label=label)
                if mulled_upload_target:
                    for img in glob.glob(f"{tmpdir}/*/images/*.tar.gz"):
                        if dryrun:
                            print(f"Would upload {img} to quay.io/{mulled_upload_target}.")
                        else:
                            # upload the artifact
                            mulled_upload(img, mulled_upload_target)
        return True


def fetch_artifacts(pr):
    """
    Fetch artifacts from a PR.

    Parameters
    ----------
    pr: PR number

    Returns
    -------
    """
    commits = pr.get_commits()
    # get the last commit
    commit = commits[commits.totalCount - 1]
    # get the artifacts
    check_runs = commit.get_check_runs()
    # get the artifact with the name "artifacts"
    for check_run in check_runs:
        if check_run.name.startswith("bioconda.bioconda-recipes (test_"):
            # azure builds
            artifact_url = get_azure_artifacts(check_run)
            yield from artifact_url

def get_azure_artifacts(check_run):
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
    return re.search("buildId=(\d+)", url).group(1)