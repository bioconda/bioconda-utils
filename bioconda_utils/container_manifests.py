"""Publish canonical multi-platform manifests for mulled images."""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import utils
from .utils import (
    skopeo_env,
    skopeo_auth_args,
    skopeo_inspect_digest,
    parse_oci_config_platform,
)
from ._types import (
    CONTAINER_PLATFORMS,
    ContainerPlatform,
    docker_platform_staging_suffix,
    normalize_container_platform,
)

logger = logging.getLogger(__name__)

DEFAULT_MULLED_RECORDS_DIR = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "bioconda-utils"
    / "mulled-records"
)

INDEX_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}


@dataclass(frozen=True)
class MulledImageRecord:
    """Registry coordinates for one architecture-specific mulled image."""

    canonical_ref: str
    platform: ContainerPlatform
    platform_ref: str
    digest: str


@dataclass(frozen=True)
class ManifestDescriptor:
    platform: ContainerPlatform
    digest: str
    source_ref: str


def platform_ref(canonical_ref: str, platform: ContainerPlatform) -> str:
    """Return the architecture-specific staging ref for a canonical image ref.

    >>> platform_ref("quay.io/biocontainers/samtools:1.20--0", "linux/arm64")
    'quay.io/biocontainers/samtools:1.20--0-arm64'
    """
    image_ref, separator, tag = canonical_ref.rpartition(":")
    if not separator or "/" not in image_ref:
        raise ValueError(
            f"Expected a fully-qualified tagged image ref: {canonical_ref}"
        )
    return f"{image_ref}:{tag}-{docker_platform_staging_suffix(platform)}"


def write_image_record(path: str | Path, record: MulledImageRecord) -> None:
    """Write one image record to a uniquely-named JSONL file inside *path*.

    The target directory is created if it does not exist.  Each call produces
    a separate file (timestamp + UUID), so concurrent writers never collide.
    """
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    file_path = output / f"{timestamp}_{uuid.uuid4().hex}.jsonl"
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(record), handle, sort_keys=True)
        handle.write("\n")


def load_image_records(paths: Iterable[str | Path]) -> list[MulledImageRecord]:
    """Load and de-duplicate JSONL records from files or directories.

    Directory inputs are treated as record directories written by
    :func:`write_image_record`, so only ``*.jsonl`` files are read. Explicit
    file inputs are always read, even if their suffix differs, so callers still
    get a clear validation error for a mistyped record path.
    """
    records: set[MulledImageRecord] = set()
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    record = MulledImageRecord(**data)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid mulled image record in {path}:{line_number}"
                    ) from exc
                expected_ref = platform_ref(record.canonical_ref, record.platform)
                if record.platform_ref != expected_ref:
                    raise ValueError(
                        f"Unexpected platform ref in {path}:{line_number}: "
                        f"{record.platform_ref} != {expected_ref}"
                    )
                if not record.digest.startswith("sha256:"):
                    raise ValueError(
                        f"Invalid digest in {path}:{line_number}: {record.digest}"
                    )
                records.add(record)
    return sorted(records, key=lambda record: (record.canonical_ref, record.platform))


def registry_creds() -> str | None:
    """Return credentials accepted by skopeo, if configured."""
    if quay_login := os.environ.get("QUAY_LOGIN"):
        return quay_login
    if token := os.environ.get("QUAY_OAUTH_TOKEN"):
        return f"$oauthtoken:{token}"
    return None


def resolve_registry_creds(*, use_existing_auth: bool = False) -> str | None:
    """Return explicit registry credentials or validate ambient-auth opt-in."""
    creds = registry_creds()
    if creds:
        return creds
    if use_existing_auth:
        logger.warning(
            "QUAY_LOGIN and QUAY_OAUTH_TOKEN are not set; using existing "
            "Docker/skopeo registry authentication. New Quay repositories "
            "cannot be created or made public without QUAY_OAUTH_TOKEN."
        )
        return None
    raise ValueError(
        "QUAY_LOGIN or QUAY_OAUTH_TOKEN is required unless --use-existing-auth "
        "is specified"
    )


def _inspect_raw(ref: str, creds: str | None) -> tuple[dict[str, Any], str]:
    auth_args, redacted_secrets = skopeo_auth_args(creds, option="--creds")
    raw = utils.run(
        ["skopeo", "inspect", "--raw", *auth_args, f"docker://{ref}"],
        redacted_secrets=redacted_secrets,
        env=skopeo_env(),
    ).stdout
    manifest = json.loads(raw)
    digest = skopeo_inspect_digest(ref, creds)
    return manifest, digest


def _inspect_config_platform(ref: str, creds: str | None) -> ContainerPlatform:
    auth_args, redacted_secrets = skopeo_auth_args(creds, option="--creds")
    raw = utils.run(
        ["skopeo", "inspect", "--config", *auth_args, f"docker://{ref}"],
        redacted_secrets=redacted_secrets,
        env=skopeo_env(),
    ).stdout
    config = json.loads(raw)
    return parse_oci_config_platform(config, ref=ref)


def _descriptor_platform(descriptor: dict[str, Any]) -> ContainerPlatform:
    platform = descriptor.get("platform") or {}
    return normalize_container_platform(
        platform.get("os"),
        platform.get("architecture"),
        variant=platform.get("variant"),
    )


def _ref_exists(ref: str, creds: str | None) -> bool:
    auth_args, redacted_secrets = skopeo_auth_args(creds, option="--creds")
    result = utils.run(
        ["skopeo", "inspect", *auth_args, f"docker://{ref}"],
        redacted_secrets=redacted_secrets,
        env=skopeo_env(),
        check=False,
        quiet_failure=True,
    )
    if result.returncode == 0:
        return True
    output = result.stdout.lower()
    if any(
        marker in output
        for marker in (
            "manifest unknown",
            "name unknown",
            "not found",
            "status code 404",
        )
    ):
        return False
    raise RuntimeError(f"Unable to inspect registry ref {ref}: {result.stdout}")


def _current_descriptors(
    canonical_ref: str, creds: str | None
) -> dict[ContainerPlatform, str] | None:
    if not _ref_exists(canonical_ref, creds):
        return None
    manifest, digest = _inspect_raw(canonical_ref, creds)
    if manifest.get("mediaType") in INDEX_MEDIA_TYPES or "manifests" in manifest:
        descriptors: dict[ContainerPlatform, str] = {}
        for descriptor in manifest.get("manifests", []):
            platform = _descriptor_platform(descriptor)
            if platform in descriptors:
                raise RuntimeError(f"{canonical_ref} has duplicate {platform} entries")
            descriptors[platform] = descriptor["digest"]
        return descriptors
    platform = _inspect_config_platform(f"{canonical_ref}@{digest}", creds)
    return {platform: digest}


def _docker_config_env(
    canonical_ref: str, creds: str | None
) -> tuple[dict[str, str] | None, list[str] | None, Path | None]:
    """Build a DOCKER_CONFIG env for buildx when credentials are supplied.

    The user's existing ``config.json`` (resolved via ``$DOCKER_CONFIG`` or
    ``~/.docker/config.json``) is loaded and merged with an ``auths[host].auth``
    entry so settings like ``credsStore``, ``credHelpers``, and registry mirrors
    are preserved. The merged file is written to a tempdir that the caller must
    remove via ``_release_docker_config``.

    Returns (env, redacted_secrets, tempdir). When creds is None, returns
    (None, None, None) so callers can fall back to the ambient
    ``~/.docker/config.json``.
    """
    if not creds:
        return None, None, None
    host, separator, _ = canonical_ref.partition("/")
    if not separator:
        raise ValueError(f"Expected a fully-qualified image ref: {canonical_ref}")
    user, separator, password = creds.partition(":")
    if not separator or not password:
        raise ValueError(
            f"Cannot parse credentials for docker login to {host}: "
            "expected 'user:password' or '$oauthtoken:token'"
        )
    auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    source_dir = Path(os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker")
    source_config = source_dir / "config.json"
    config: dict[str, Any] = {}
    if source_config.is_file():
        try:
            config = json.loads(source_config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not read existing docker config at %s (%s); "
                "proceeding without merged settings",
                source_config,
                exc,
            )
    if not isinstance(config, dict):
        logger.warning(
            "Existing docker config at %s is not a JSON object; ignoring it",
            source_config,
        )
        config = {}
    config.setdefault("auths", {})[host] = {"auth": auth}
    config_dir = Path(tempfile.mkdtemp(prefix="bioconda-docker-"))
    (config_dir / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
    return {"DOCKER_CONFIG": str(config_dir)}, [password], config_dir


def _release_docker_config(config_dir: Path | None) -> None:
    if config_dir is None:
        return
    shutil.rmtree(config_dir, ignore_errors=True)


def _publish_manifest(
    canonical_ref: str,
    descriptors: list[ManifestDescriptor],
    *,
    creds: str | None = None,
) -> None:
    """Publish the canonical mulled image ref as an OCI index.

    Even single-platform publishes use an index so canonical mulled refs have a
    consistent media type. :func:`_current_descriptors` still accepts single
    image manifests when inspecting existing refs, because registry state may
    predate this convention or be created manually.
    """
    sources = [
        f"{descriptor.source_ref}@{descriptor.digest}" for descriptor in descriptors
    ]
    command = [
        "docker",
        "buildx",
        "imagetools",
        "create",
        "--progress",
        "plain",
    ]
    command += ["--tag", canonical_ref, *sources]
    docker_env, redacted_secrets, config_dir = _docker_config_env(canonical_ref, creds)
    try:
        utils.run(
            command,
            redacted_secrets=redacted_secrets
            if redacted_secrets is not None
            else False,
            live=True,
            env={**os.environ, **(docker_env or {})},
        )
    finally:
        _release_docker_config(config_dir)


def reconcile_manifest(
    canonical_ref: str,
    records: list[MulledImageRecord],
    platforms: Iterable[ContainerPlatform] = CONTAINER_PLATFORMS,
    *,
    creds: str | None = None,
) -> bool:
    """Publish one canonical manifest from uploaded image records.

    Digests are captured at push time and carried in the records,
    so no registry polling or staging-tag bootstrap is needed.
    The manifest may contain any non-empty subset of requested platforms.

    Returns True when a manifest was changed and False when it was already current.
    """
    requested = tuple(dict.fromkeys(platforms))
    record_map = {r.platform: r for r in records}
    current = _current_descriptors(canonical_ref, creds)

    descriptors: list[ManifestDescriptor] = []
    for platform in requested:
        record = record_map.get(platform)
        if record is not None:
            descriptors.append(
                ManifestDescriptor(
                    platform=platform,
                    digest=record.digest,
                    source_ref=record.platform_ref,
                )
            )
        elif current and platform in current:
            descriptors.append(
                ManifestDescriptor(
                    platform=platform,
                    digest=current[platform],
                    source_ref=canonical_ref,
                )
            )

    if not descriptors:
        raise RuntimeError(f"No images are available for {canonical_ref}")

    desired = {d.platform: d.digest for d in descriptors}
    if current == desired:
        logger.info("Manifest already current: %s", canonical_ref)
        return False

    _publish_manifest(canonical_ref, descriptors, creds=creds)

    current = _current_descriptors(canonical_ref, creds)
    if current != desired:
        raise RuntimeError(
            f"Manifest verification failed for {canonical_ref}: "
            f"expected {desired}, found {current}"
        )
    logger.info(
        "Published %s with platforms %s",
        canonical_ref,
        ", ".join(sorted(desired)),
    )
    return True


def reconcile_manifests(
    records: Iterable[MulledImageRecord],
    platforms: Iterable[ContainerPlatform] = CONTAINER_PLATFORMS,
    *,
    creds: str | None = None,
) -> tuple[int, int]:
    """Reconcile each canonical ref represented by uploaded image records.
    Returns (n_changed, n_total) for logging/progress reporting.
    """
    records = list(records)
    canonical_refs = sorted({record.canonical_ref for record in records})
    changed = 0
    for canonical_ref in canonical_refs:
        ref_records = [r for r in records if r.canonical_ref == canonical_ref]
        changed += int(
            reconcile_manifest(canonical_ref, ref_records, platforms, creds=creds)
        )
    return changed, len(canonical_refs)
