"""
Helpers for invoking skopeo and interpreting OCI registry data.
"""

from __future__ import annotations

import os
import shutil

from .._types import (
    ContainerPlatform,
    OCIImageConfig,
    normalize_container_platform,
)
from ..support.env import env_root
from ..support.subproc import run


def skopeo_bin() -> str:
    """Return the path of the skopeo executable.

    ``skopeo`` is installed into the same environment as ``bioconda-utils``
    itself, so the prefix of the running interpreter is the right place to
    look, independent of ``PATH`` and of any environment that happens to be
    activated in the shell. Installations that bundle ``skopeo`` elsewhere
    fall back to a ``PATH`` lookup.
    """
    packaged = env_root() / "bin" / "skopeo"
    if packaged.exists():
        return str(packaged)
    if found := shutil.which("skopeo"):
        return found
    raise FileNotFoundError(
        f"Unable to find skopeo: expected it at {packaged} (installed "
        "alongside bioconda-utils) or on PATH"
    )


def skopeo_env() -> dict[str, str]:
    """Return an environment dict with SSL_CERT_DIR set for conda's skopeo."""
    env = os.environ.copy()
    ssl_dir = env_root() / "ssl"
    if ssl_dir.is_dir():
        env["SSL_CERT_DIR"] = str(ssl_dir)
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
        [
            skopeo_bin(),
            "inspect",
            "--format",
            "{{.Digest}}",
            *auth_args,
            f"docker://{ref}",
        ],
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
