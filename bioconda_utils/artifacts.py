

import glob
import os
import re
import tempfile
import zipfile
import logging

import requests
import backoff
from bioconda_utils import utils
from bioconda_utils.upload import anaconda_upload, skopeo_upload

logger = logging.getLogger(__name__)


IMAGE_RE = re.compile(r"(.+)(?::|%3A)(.+)\.tar\.gz$")


def upload_pr_artifacts(config, repo, git_sha, dryrun=False, mulled_upload_target=None, label=None) -> bool:
    _config = utils.load_config(config)
    repodata = utils.RepoData()

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
                download_artifact(artifact, artifact_path)
                zipfile.ZipFile(artifact_path).extractall(tmpdir)

                # get all the contained packages and images and upload them
                platform_patterns = [repodata.platform2subdir(repodata.native_platform())]
                if repodata.native_platform() == "linux":
                    platform_patterns.append("noarch")

                for platform_pattern in platform_patterns:
                    pattern = f"{tmpdir}/*/packages/{platform_pattern}/*.tar.bz2"
                    logger.info(f"Checking for packages at {pattern}.")
                    for pkg in glob.glob(pattern):
                        if dryrun:
                            logger.info(f"Would upload {pkg} to anaconda.org.")
                        else:
                            logger.info(f"Uploading {pkg} to anaconda.org.")
                            # upload the package
                            anaconda_upload(pkg, label=label)

                if mulled_upload_target:
                    quay_login = os.environ['QUAY_LOGIN']

                    pattern = f"{tmpdir}/*/images/*.tar.gz"
                    logger.info(f"Checking for images at {pattern}.")
                    for img in glob.glob(pattern):
                        m = IMAGE_RE.match(os.path.basename(img))
                        assert m, f"Could not parse image name from {img}"
                        name, tag = m.groups()
                        if label:
                            # add label to tag
                            tag = f"{tag}-{label}"
                        target = f"{mulled_upload_target}/{name}:{tag}"
                        # Skopeo can't handle a : in the file name
                        fixed_img_name = img.replace(":", "_")
                        os.rename(img, fixed_img_name)
                        if dryrun:
                            logger.info(f"Would upload {img} to {target}.")
                        else:
                            # upload the image
                            logger.info(f"Uploading {img} to {target}.")
                            skopeo_upload(fixed_img_name, target, creds=quay_login)
        return True


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException
)
def download_artifact(url, to_path):
    logger.info(f"Downloading artifact {url}.")
    resp = requests.get(url, stream=True, allow_redirects=True)
    resp.raise_for_status()
    with open(to_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)


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
    repodata = utils.RepoData()
    platform = repodata.native_platform()
    # get the artifact with the name "artifacts"
    for check_run in check_runs:
        if check_run.name.startswith(f"bioconda.bioconda-recipes (test_{platform}"):
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
