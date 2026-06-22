"""
Deploy Artifacts to Anaconda and Quay
"""

import json
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
    QuayUploadTarget,
    docker_platform_tag_suffix,
    native_container_platform,
)
from .container_manifests import MulledImageRecord, platform_ref, registry_creds

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
    quay_target: QuayUploadTarget,
    target_platform: ContainerPlatform | None = None,
) -> MulledImageRecord:
    """
    Upload the build Docker images to quay.io with ``mulled-build push``.

    Calls ``mulled-build push <image> -n <quay_target>``

    Args:
      image: name of image to push
      quary_target: name of image on quay
      target_platform: Docker target platform to pass to mulled-build

    Returns:
      A manifest publication record for the image uploaded to quay.io.
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
    return upload_mulled_image_source(
        f"docker-daemon:{local_ref}",
        canonical_ref,
        target_platform,
    )


def _skopeo_env() -> dict[str, str]:
    env = os.environ.copy()
    skopeo_bin = shutil.which("skopeo")
    if skopeo_bin is None:
        raise FileNotFoundError("Unable to find skopeo on PATH")
    env["SSL_CERT_DIR"] = str(Path(skopeo_bin).parents[1] / "ssl")
    return env


def _skopeo_auth_args(creds: str | None, *, option: str) -> tuple[list[str], list[str]]:
    if not creds:
        return [], []
    return [option, creds], creds.split(":", 1)


def inspect_image_platform(source_ref: str) -> str:
    """Return the Docker platform recorded in an image source config."""
    raw = utils.run(
        ["skopeo", "inspect", "--config", source_ref],
        mask=False,
        env=_skopeo_env(),
    ).stdout
    config = json.loads(raw)
    os_name = config.get("os")
    architecture = config.get("architecture")
    variant = config.get("variant")
    if not os_name or not architecture:
        raise RuntimeError(f"Image config for {source_ref} has no OS/architecture")
    platform = f"{os_name}/{architecture}"
    if variant:
        platform += f"/{variant}"
    return platform


def inspect_remote_digest(ref: str, creds: str | None) -> str:
    """Inspect a remote image ref and return its registry digest."""
    auth_args, mask = _skopeo_auth_args(creds, option="--creds")
    digest = utils.run(
        [
            "skopeo",
            "inspect",
            "--format",
            "{{.Digest}}",
            *auth_args,
            f"docker://{ref}",
        ],
        mask=mask,
        env=_skopeo_env(),
    ).stdout.strip()
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"Registry returned an invalid digest for {ref}: {digest}")
    return digest


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
    dest_auth_args, mask = _skopeo_auth_args(creds, option="--dest-creds")
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
        mask=mask,
        env=_skopeo_env(),
    )
    digest = inspect_remote_digest(destination_ref, creds)
    return MulledImageRecord(
        canonical_ref=canonical_ref,
        platform=target_platform,
        platform_ref=destination_ref,
        digest=digest,
    )
