"""
Deploy Artifacts to Anaconda and Quay
"""

import json
import os
import subprocess as sp
import logging
import requests
import backoff
from . import utils
from .utils import (
    skopeo_env,
    skopeo_auth_args,
    skopeo_inspect_digest,
    parse_skopeo_config_platform,
)
from ._types import (
    ContainerPlatform,
    PkgBuildRef,
    QuayUploadTarget,
    docker_platform_tag_suffix,
    native_container_platform,
)
from .container_manifests import (
    MulledImageRecord,
    platform_ref,
    registry_creds,
)

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
            "QUAY_OAUTH_TOKEN is not set; cannot verify visibility for %s/%s. "
            "If the repository does not exist, skopeo may auto-create it as private.",
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
            logger.warning(
                "Repository %s/%s is private; changing visibility to public",
                namespace,
                repository,
            )
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
        utils.run(cmds, redacted_secrets=[token])
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
    image: PkgBuildRef,
    quay_target: QuayUploadTarget,
    target_platform: ContainerPlatform | None = None,
) -> MulledImageRecord:
    """
    Upload the build Docker images to quay.io with ``mulled-build push``.

    Calls ``mulled-build push <image> -n <quay_target>``

    Args:
      image: package build reference (name, version, build string)
      quary_target: name of image on quay
      target_platform: Docker target platform to pass to mulled-build

    Returns:
      A manifest publication record for the image uploaded to quay.io.
    """
    target_platform = target_platform or native_container_platform()
    canonical_ref = (
        f"quay.io/{quay_target}/{image.name}:{image.version}--{image.build_string}"
    )
    local_ref = canonical_ref
    if suffix := docker_platform_tag_suffix(target_platform):
        local_ref = f"{canonical_ref}-{suffix}"
    return upload_mulled_image_source(
        f"docker-daemon:{local_ref}",
        canonical_ref,
        target_platform,
    )


def inspect_image_platform(source_ref: str) -> ContainerPlatform:
    """Return the Docker platform recorded in an image source config."""
    raw = utils.run(
        ["skopeo", "inspect", "--config", source_ref],
        redacted_secrets=False,
        env=skopeo_env(),
    ).stdout
    config = json.loads(raw)
    return parse_skopeo_config_platform(config, ref=source_ref)


def _quay_namespace_and_repository(canonical_ref: str) -> tuple[str, str]:
    repository, separator, _tag = canonical_ref.rpartition(":")
    if not separator:
        raise ValueError(f"Expected tagged image ref: {canonical_ref}")
    parts = repository.split("/")
    if len(parts) != 3 or parts[0] != "quay.io":
        raise ValueError(f"Expected quay.io namespace/repository ref: {canonical_ref}")
    return parts[1], parts[2]


def upload_mulled_image_source(
    source_ref: str,
    canonical_ref: str,
    target_platform: ContainerPlatform,
    *,
    timeout: int = 600,
    validate_platform: bool = True,
) -> MulledImageRecord:
    """Upload one mulled image source to its platform staging ref.

    The returned digest is inspected from the destination registry ref after
    upload, so manifest records reflect what Quay actually stores.
    """
    creds = registry_creds()
    if not creds:
        raise ValueError("QUAY_LOGIN or QUAY_OAUTH_TOKEN is required")
    if validate_platform:
        source_platform = inspect_image_platform(source_ref)
        if source_platform != target_platform:
            raise RuntimeError(
                f"Image platform mismatch for {source_ref}: "
                f"expected {target_platform}, found {source_platform}"
            )
    namespace, repository = _quay_namespace_and_repository(canonical_ref)
    ensure_quay_repository(namespace, repository)
    destination_ref = platform_ref(canonical_ref, target_platform)
    dest_auth_args, redacted_secrets = skopeo_auth_args(creds, option="--dest-creds")
    utils.run(
        [
            "skopeo",
            "--command-timeout",
            f"{timeout}s",
            "copy",
            source_ref,
            f"docker://{destination_ref}",
            *dest_auth_args,
        ],
        redacted_secrets=redacted_secrets,
        env=skopeo_env(),
    )
    digest = skopeo_inspect_digest(destination_ref, creds)
    return MulledImageRecord(
        canonical_ref=canonical_ref,
        platform=target_platform,
        platform_ref=destination_ref,
        digest=digest,
    )
