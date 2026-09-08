"""
Helpers for invoking skopeo and interpreting OCI registry data.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .._types import (
    ContainerPlatform,
    OCIImageConfig,
    normalize_container_platform,
)
from ..support.subproc import run


def skopeo_env() -> dict[str, str]:
    """Return an environment dict with SSL_CERT_DIR set for conda's skopeo."""
    env = os.environ.copy()
    skopeo_bin = shutil.which("skopeo")
    if skopeo_bin is None:
        raise FileNotFoundError("Unable to find skopeo on PATH")
    env["SSL_CERT_DIR"] = str(Path(skopeo_bin).parents[1] / "ssl")
    return env


def skopeo_auth_args(creds: str | None, *, option: str) -> tuple[list[str], list[str]]:
    """Build skopeo credential CLI args and redacted secrets list."""
    if not creds:
        return [], []
    return [option, creds], creds.split(":", 1)


def skopeo_inspect_digest(ref: str, creds: str | None) -> str:
    """Inspect a remote image ref and return its registry digest."""
    auth_args, secrets = skopeo_auth_args(creds, option="--creds")
    digest = run(
        ["skopeo", "inspect", "--format", "{{.Digest}}", *auth_args, f"docker://{ref}"],
        secrets=secrets,
        env=skopeo_env(),
    ).stdout.strip()
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"Registry returned an invalid digest for {ref}: {digest}")
    return digest


def parse_oci_config_platform(
    config: OCIImageConfig, *, ref: str = ""
) -> ContainerPlatform:
    """Extract and normalize the Docker platform from a skopeo image config."""
    return normalize_container_platform(
        config.get("os"),
        config.get("architecture"),
        variant=config.get("variant"),
        ref=ref,
    )
