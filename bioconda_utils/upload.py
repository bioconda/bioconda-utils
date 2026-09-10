"""
Deploy Artifacts to Anaconda and Quay
"""

import json
import logging
import os
from pathlib import Path
import subprocess as sp

from . import utils
from ._types import (
    ContainerPlatform,
    PkgBuildRef,
    QuayUploadTarget,
    local_mulled_image_ref,
    native_container_platform,
)
from .container_manifests import (
    MulledImageRecord,
    platform_ref,
    resolve_registry_creds,
)
from .utils import (
    parse_oci_config_platform,
    skopeo_auth_args,
    skopeo_env,
    skopeo_inspect_digest,
)

logger = logging.getLogger(__name__)


def anaconda_upload(
    package: str | Path, token: str | None = None, label: str | None = None
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
    package = Path(package)
    if label is not None:
        label_arg = ["--label", label]

    if not package.exists():
        logger.error("UPLOAD ERROR: package %s cannot be found.", package)
        return False

    if token is None:
        token = os.environ.get("ANACONDA_TOKEN")
        if token is None:
            raise ValueError("Env var ANACONDA_TOKEN not found")

    logger.info("UPLOAD uploading package %s", package)
    try:
        cmds = ["anaconda", "-t", token, "upload", package.as_posix()] + label_arg
        utils.run(cmds, secrets=[token])
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
    *,
    use_existing_auth: bool = False,
) -> MulledImageRecord:
    """
    Upload the build Docker images to quay.io with ``mulled-build push``.

    Calls ``mulled-build push <image> -n <quay_target>``

    Args:
      image: package build reference (name, version, build string)
      quary_target: name of image on quay
      target_platform: Docker target platform to pass to mulled-build
      use_existing_auth: Use existing Docker/skopeo registry auth when no
        QUAY_LOGIN or QUAY_OAUTH_TOKEN is configured.

    Returns:
      A manifest publication record for the image uploaded to quay.io.
    """
    target_platform = target_platform or native_container_platform()
    canonical_ref = (
        f"quay.io/{quay_target}/{image.name}:{image.version}--{image.build_string}"
    )
    # mulled-build tags the local image under the canonical biocontainers
    # namespace (see pkg_test.mulled_build_and_test), regardless of the upload
    # target. local_mulled_image_ref is the shared source of truth for that ref;
    # the registry destination (canonical_ref) keeps the requested target namespace.
    local_ref = local_mulled_image_ref(image, target_platform)
    return upload_mulled_image_source(
        f"docker-daemon:{local_ref}",
        canonical_ref,
        target_platform,
        use_existing_auth=use_existing_auth,
    )


def inspect_image_platform(source_ref: str) -> ContainerPlatform:
    """Return the Docker platform recorded in an image source config."""
    raw = utils.run(
        ["skopeo", "inspect", "--config", source_ref],
        env=skopeo_env(),
    ).stdout
    config = json.loads(raw)
    return parse_oci_config_platform(config, ref=source_ref)


def upload_mulled_image_source(
    source_ref: str,
    canonical_ref: str,
    target_platform: ContainerPlatform,
    *,
    timeout: int = 600,
    validate_platform: bool = True,
    use_existing_auth: bool = False,
) -> MulledImageRecord:
    """Upload one mulled image source to its platform staging ref.

    The returned digest is inspected from the destination registry ref after
    upload, so manifest records reflect what Quay actually stores.
    """
    creds = resolve_registry_creds(use_existing_auth=use_existing_auth)
    if validate_platform:
        source_platform = inspect_image_platform(source_ref)
        if source_platform != target_platform:
            raise RuntimeError(
                f"Image platform mismatch for {source_ref}: "
                f"expected {target_platform}, found {source_platform}"
            )
    destination_ref = platform_ref(canonical_ref, target_platform)
    dest_auth_args, secrets = skopeo_auth_args(creds, option="--dest-creds")
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
        secrets=secrets,
        env=skopeo_env(),
    )
    digest = skopeo_inspect_digest(destination_ref, creds)
    return MulledImageRecord(
        canonical_ref=canonical_ref,
        platform=target_platform,
        platform_ref=destination_ref,
        digest=digest,
    )
