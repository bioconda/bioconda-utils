"""
Deploy Artifacts to Anaconda and Quay
"""

import os
from pathlib import Path
import shutil
import subprocess as sp
import logging
import requests
import backoff
from . import utils
from ._types import (
    ContainerPlatform,
    docker_platform_tag_suffix,
    native_container_platform,
)
from .container_manifests import platform_ref, registry_creds

logger = logging.getLogger(__name__)
_QUAY_REPOSITORIES_READY: set[tuple[str, str]] = set()


@backoff.on_exception(
    backoff.expo,
    requests.RequestException,
    max_tries=5,
    max_time=60,
)
def ensure_quay_repository(namespace: str, repository: str) -> None:
    """Ensure a Quay repository exists and is public before pushing."""
    key = (namespace, repository)
    if key in _QUAY_REPOSITORIES_READY:
        return
    token = os.environ.get("QUAY_OAUTH_TOKEN")
    if not token:
        # Existing public repositories can still be pushed with registry creds.
        # New repositories require the API token to enforce public visibility.
        logger.warning(
            "QUAY_OAUTH_TOKEN is not set; cannot verify visibility for %s/%s",
            namespace,
            repository,
        )
        return
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://quay.io/api/v1/repository/{namespace}/{repository}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        response = requests.post(
            "https://quay.io/api/v1/repository",
            headers=headers,
            json={
                "repository": repository,
                "namespace": namespace,
                "description": "",
                "visibility": "public",
            },
            timeout=30,
        )
        if response.status_code not in (200, 201, 409):
            response.raise_for_status()
    elif response.status_code != 200:
        response.raise_for_status()
    else:
        data = response.json()
        if data.get("is_public") is False:
            response = requests.post(
                f"{url}/changevisibility",
                headers=headers,
                json={"visibility": "public"},
                timeout=30,
            )
            response.raise_for_status()
    _QUAY_REPOSITORIES_READY.add(key)


def anaconda_upload(
    package: str, token: str | None = None, label: str | None = None
) -> bool:
    """
    Upload a package to anaconda.

    Args:
      package: Filename to built package
      token: If None, use the environment variable ``ANACONDA_TOKEN``,
             otherwise, use this as the token for authenticating the
             anaconda client.
      label: Optional label to add
    Returns:
      True if the operation succeeded, False if it cannot succeed,
      None if it should be retried
    Raises:
      ValueError
    """
    label_arg = []
    if label is not None:
        label_arg = ["--label", label]

    if not os.path.exists(package):
        logger.error("UPLOAD ERROR: package %s cannot be found.", package)
        return False

    if token is None:
        token = os.environ.get("ANACONDA_TOKEN")
        if token is None:
            raise ValueError("Env var ANACONDA_TOKEN not found")

    logger.info("UPLOAD uploading package %s", package)
    try:
        cmds = ["anaconda", "-t", token, "upload", package] + label_arg
        utils.run(cmds, mask=[token])
        logger.info("UPLOAD SUCCESS: uploaded package %s", package)
        return True

    except sp.CalledProcessError as e:
        if "already exists" in e.stdout:
            # ignore error assuming that it is caused by
            # existing package
            logger.warning(
                "UPLOAD WARNING: tried to upload package, got:\n %s", e.stdout
            )
            return True
        elif "Gateway Timeout" in e.stdout:
            logger.warning("UPLOAD TEMP FAILURE: Gateway timeout")
            return False
        else:
            logger.error("UPLOAD ERROR: command: %s", e.cmd)
            logger.error("UPLOAD ERROR: stdout+stderr: %s", e.stdout)
            return False


def mulled_upload(
    image: str,
    quay_target: str,
    target_platform: ContainerPlatform | None = None,
) -> str:
    """
    Upload the build Docker images to quay.io with ``mulled-build push``.

    Calls ``mulled-build push <image> -n <quay_target>``

    Args:
      image: name of image to push
      quary_target: name of image on quay
      target_platform: Docker target platform to pass to mulled-build

    Returns:
      The digest of the pushed image (captured before push from the local
      Docker daemon).
    """
    target_platform = target_platform or native_container_platform()
    pkg_name_and_version, pkg_build_string = image.rsplit("--", 1)
    pkg_name, pkg_version = pkg_name_and_version.rsplit("=", 1)
    canonical_ref = (
        f"quay.io/{quay_target}/{pkg_name}:{pkg_version}--{pkg_build_string}"
    )
    local_ref = canonical_ref
    if suffix := docker_platform_tag_suffix(target_platform):
        local_ref = f"{canonical_ref}-{suffix}"
    destination_ref = platform_ref(canonical_ref, target_platform)
    creds = registry_creds()
    if not creds:
        raise ValueError("QUAY_LOGIN or QUAY_OAUTH_TOKEN is required")
    ensure_quay_repository(quay_target, pkg_name)

    digest = utils.run(
        ["skopeo", "inspect", "--format", "{{.Digest}}", f"docker-daemon:{local_ref}"],
        mask=False,
    ).stdout.strip()
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"Invalid digest for {local_ref}: {digest}")

    utils.run(
        [
            "skopeo",
            "copy",
            "--dest-creds",
            creds,
            f"docker-daemon:{local_ref}",
            f"docker://{destination_ref}",
        ],
        mask=creds.split(":", 1),
        env=os.environ.copy(),
    )
    return digest


def skopeo_upload(
    image_file: str,
    target: str,
    creds: str,
    registry: str = "quay.io",
    timeout: int = 600,
) -> bool:
    """
    Upload an image to docker registy

    Uses ``skopeo`` to upload tar archives of docker images as created
    with e.g.``docker save`` to a docker registry.

    The image name and tag are read from the archive.

    Args:
      image_file: path to the file to be uploaded (may be gzip'ed). NOTE: may not contain a colon!
      target: namespace/repo for the image
      creds: login credentials (``USER:PASS``)
      registry: url of the registry. defaults to "quay.io"
      timeout: timeout in seconds
    """
    cmd = [
        "skopeo",
        "--command-timeout",
        f"{timeout}s",
        "copy",
        f"docker-archive:{image_file}",
        f"docker://{registry}/{target}",
        "--dest-creds",
        creds,
    ]
    env = os.environ.copy()
    skopeo_bin = shutil.which("skopeo")
    if skopeo_bin is None:
        raise FileNotFoundError("Unable to find skopeo on PATH")
    env["SSL_CERT_DIR"] = str(Path(skopeo_bin).parents[1] / "ssl")
    try:
        utils.run(cmd, mask=creds.split(":"), env=env)
        return True
    except sp.CalledProcessError as exc:
        logger.error("Failed to upload %s to %s", image_file, target)
        for line in exc.stdout.splitlines():
            logger.error("> %s", line)
        return False
