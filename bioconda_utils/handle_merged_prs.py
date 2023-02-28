

import glob
import os
import re
import tempfile
import zipfile

import requests
from bioconda_utils import utils
from bioconda_utils.upload import anaconda_upload


def handle_merged_pr():
    sha = os.environ["GITHUB_SHA"]

    gh = utils.get_github_client()

    repo = gh.get_repo(os.environ["GITHUB_REPOSITORY"])
    
    assert repo is not None, (
        "No github repository found in $GITHUB_REPOSITORY. "
        "The current action is meant to be run from within a github action job!"
    )

    commit = repo.get_commit(sha)
    prs = commit.get_pulls()
    if not prs:
        # no PR found for the commit
        return
    pr = prs[0]
    artifacts = list(fetch_artifacts(pr))
    if not artifacts:
        raise ValueError("No artifacts found!")
    else:
        for artifact in artifacts:
            with tempfile.TemporaryDirectory() as tmpdir:
                # download the artifact
                artifact_path = os.path.join(tmpdir, os.path.basename(artifact))
                requests.get(artifact, stream=True, allow_redirects=True).raw.save(artifact_path)
                zipfile.ZipFile(artifact_path).extractall(tmpdir)
                # get all the contained packages
                for pkg in glob.glob("*/packages/*/*.tar.bz2"):
                    # upload the artifact
                    anaconda_upload(pkg)


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
    commit = commits[len(commits) - 1]
    # get the artifacts
    check_runs = commit.get_check_runs()
    # get the artifact with the name "artifacts"
    for check_run in check_runs:
        if check_run.name.startswith("bioconda.bioconda-recipes (test_"):
            # azure builds
            artifact_url = get_azure_artifact(check_run)
            yield artifact_url

def get_azure_artifact(check_run):
    azure_build_id = parse_azure_build_id(check_run.details_url)
    url = f"https://dev.azure.com/bioconda/bioconda-recipes/_apis/build/builds/{azure_build_id}/artifacts?api-version=4.1"
    res = requests.get(url, json=True)
    if res["count"] == 0:
        raise ValueError("No artifacts found!")
    elif res["count"] > 1:
        raise ValueError("More than one artifact found!")
    else:
        artifact = res["value"][0]
        artifact_url = artifact["resource"]["downloadUrl"]
        return artifact_url


def parse_azure_build_id(url: str) -> str:
    return re.search("buildId=(\d+)", url).group(1)