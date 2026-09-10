"""Publish canonical multi-platform manifests for mulled images."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import utils
from ._types import (
    ALL_CONTAINER_PLATFORMS,
    ContainerPlatform,
    docker_platform_staging_suffix,
    normalize_container_platform,
)
from .utils import (
    skopeo_auth_args,
    skopeo_env,
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


_BUILD_STRING_RE = re.compile(r"^(?:(.*?)h[0-9a-f]{7})?_?(\d+)$")


def _extract_variant_prefix(build_string: str) -> str:
    """Extract the platform-independent variant prefix from a conda build string.

    Conda build strings have the form ``<variant_prefix>?<hash>_<build_number>``.
    The hash is always ``h`` followed by exactly 7 hex characters. Everything
    before the hash is a variant prefix such as ``py310`` or ``r44``; it is
    empty for packages with no variant matrix.

    >>> _extract_variant_prefix("h9dcdb79_1")
    ''
    >>> _extract_variant_prefix("py310h8fb3dee_0")
    'py310'
    >>> _extract_variant_prefix("0")
    ''
    """
    m = _BUILD_STRING_RE.match(build_string)
    if m and m.group(1):
        return m.group(1)
    return ""


def multiarch_ref(canonical_ref: str) -> str:
    """Return the platform-independent multi-arch tag for a canonical ref.

    Strips the conda build hash from the tag, preserving version and any
    variant prefix (e.g. ``py310``). The result is the tag under which a
    multi-architecture OCI index should be published.

    >>> multiarch_ref("quay.io/biocontainers/samtools:1.24--h9dcdb79_1")
    'quay.io/biocontainers/samtools:1.24'
    >>> multiarch_ref("quay.io/biocontainers/htseq:2.1.2--py310h8fb3dee_0")
    'quay.io/biocontainers/htseq:2.1.2--py310'
    >>> multiarch_ref("quay.io/biocontainers/samtools:1.2")
    'quay.io/biocontainers/samtools:1.2'
    """
    repository, separator, tag = canonical_ref.rpartition(":")
    if not separator:
        return canonical_ref
    version, version_sep, build_string = tag.partition("--")
    if not version_sep:
        return canonical_ref
    variant_prefix = _extract_variant_prefix(build_string)
    if variant_prefix:
        return f"{repository}:{version}--{variant_prefix}"
    return f"{repository}:{version}"


def write_image_record(path: str | Path, record: MulledImageRecord) -> None:
    """Write one image record to a uniquely-named JSONL file inside *path*.

    The target directory is created if it does not exist.  Each call produces
    a separate file (timestamp + UUID), so concurrent writers never collide.
    """
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
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


def _is_index(manifest: dict[str, Any]) -> bool:
    """Return whether a raw manifest is a multi-platform index."""
    return manifest.get("mediaType") in INDEX_MEDIA_TYPES or "manifests" in manifest


def _inspect_raw(ref: str, creds: str | None) -> dict[str, Any] | None:
    auth_args, secrets = skopeo_auth_args(creds, option="--creds")
    result = utils.run(
        ["skopeo", "inspect", "--raw", *auth_args, f"docker://{ref}"],
        secrets=secrets,
        env=skopeo_env(),
        check=False,
        quiet_failure=True,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    output = result.stdout.lower()
    if any(
        marker in output
        for marker in (
            "manifest unknown",
            "name unknown",
            "status code 404",
        )
    ):
        return None
    raise RuntimeError(f"Unable to inspect registry ref {ref}: {result.stdout}")


def _inspect_single_image(ref: str, creds: str | None) -> tuple[ContainerPlatform, str]:
    """Return the platform and digest of a non-index image ref."""
    auth_args, secrets = skopeo_auth_args(creds, option="--creds")
    raw = utils.run(
        ["skopeo", "inspect", "--no-tags", *auth_args, f"docker://{ref}"],
        secrets=secrets,
        env=skopeo_env(),
    ).stdout
    inspection = json.loads(raw)
    digest = inspection.get("Digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise RuntimeError(f"Registry returned an invalid digest for {ref}: {digest}")
    platform = normalize_container_platform(
        inspection.get("Os"), inspection.get("Architecture"), ref=ref
    )
    return platform, digest


def _descriptor_platform(descriptor: dict[str, Any]) -> ContainerPlatform:
    platform = descriptor.get("platform") or {}
    return normalize_container_platform(
        platform.get("os"),
        platform.get("architecture"),
        variant=platform.get("variant"),
    )


def _current_descriptors(
    canonical_ref: str, creds: str | None
) -> dict[ContainerPlatform, str] | None:
    manifest = _inspect_raw(canonical_ref, creds)
    if manifest is None:
        return None
    if _is_index(manifest):
        descriptors: dict[ContainerPlatform, str] = {}
        for descriptor in manifest.get("manifests", []):
            platform = _descriptor_platform(descriptor)
            if platform in descriptors:
                raise RuntimeError(f"{canonical_ref} has duplicate {platform} entries")
            descriptors[platform] = descriptor["digest"]
        return descriptors
    # Normal inspection resolves a platform, so only use it after ruling out an
    # index. It returns both values needed for legacy single-image refs in one
    # command, without listing all repository tags.
    platform, digest = _inspect_single_image(canonical_ref, creds)
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

    Returns (env, secrets, tempdir). When creds is None, returns
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
    auth = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
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
    docker_env, secrets, config_dir = _docker_config_env(canonical_ref, creds)
    try:
        utils.run(
            command,
            secrets=secrets,
            live=True,
            env={**os.environ, **(docker_env or {})},
        )
    finally:
        _release_docker_config(config_dir)


def reconcile_manifest(
    canonical_ref: str,
    records: list[MulledImageRecord],
    platforms: Iterable[ContainerPlatform] = ALL_CONTAINER_PLATFORMS,
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
    platforms: Iterable[ContainerPlatform] = ALL_CONTAINER_PLATFORMS,
    *,
    creds: str | None = None,
) -> tuple[int, int]:
    """Reconcile exact build refs and shared multi-arch indexes.

    For each unique ``canonical_ref`` (exact conda build string), an OCI index
    is published at that ref — this preserves the existing per-architecture
    tags.

    Additionally, records are grouped by :func:`multiarch_ref`, which strips
    the platform-specific build hash to produce a shared tag such as
    ``samtools:1.24`` or ``htseq:2.1.2--py310``. A multi-architecture OCI index
    is published at each multi-arch ref, combining all available platforms.
    Variant matrices are handled correctly because different variants (e.g.
    ``py310`` vs ``py312``) produce different multi-arch refs.

    Returns ``(n_changed, n_total)`` for logging/progress reporting.
    """
    records = list(records)
    groups: dict[str, list[MulledImageRecord]] = {}

    # Exact per-build-string tags (existing behavior, preserved as-is).
    for canonical_ref in sorted({record.canonical_ref for record in records}):
        groups[canonical_ref] = [
            record for record in records if record.canonical_ref == canonical_ref
        ]

    # Multi-arch tags (new). Group records by their multiarch ref and add
    # each group that isn't already covered by an exact canonical ref.
    multiarch_groups: dict[str, list[MulledImageRecord]] = {}
    for record in records:
        ref = multiarch_ref(record.canonical_ref)
        if ref != record.canonical_ref:
            multiarch_groups.setdefault(ref, []).append(record)
    for ref in sorted(multiarch_groups):
        ref_records = multiarch_groups[ref]
        if ref in groups:
            continue
        _validate_multiarch_group(ref, ref_records)
        groups[ref] = ref_records

    changed = 0
    for ref, ref_records in groups.items():
        changed += int(reconcile_manifest(ref, ref_records, platforms, creds=creds))
    return changed, len(groups)


def _validate_multiarch_group(ref: str, records: list[MulledImageRecord]) -> None:
    """Reject records with conflicting platforms in a multi-arch group.

    Each platform (e.g. ``linux/amd64``) must appear at most once. Duplicate
    platforms indicate either accumulated stale records or a variant matrix
    that was not separated by the variant prefix — both are unsafe to combine.
    """
    by_platform: dict[ContainerPlatform, MulledImageRecord] = {}
    for record in records:
        previous = by_platform.get(record.platform)
        if previous is not None:
            raise ValueError(
                f"Multiple image records for {ref} on {record.platform}: "
                f"{previous.canonical_ref} and {record.canonical_ref}"
            )
        by_platform[record.platform] = record
