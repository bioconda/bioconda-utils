"""Publish canonical multi-platform manifests for mulled images."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from . import utils
from ._types import (
    CONTAINER_PLATFORMS,
    ContainerPlatform,
    docker_platform_staging_suffix,
)

logger = logging.getLogger(__name__)

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
    """Return the architecture-specific staging ref for a canonical image ref."""
    repository, separator, tag = canonical_ref.rpartition(":")
    if not separator or "/" not in repository:
        raise ValueError(
            f"Expected a fully-qualified tagged image ref: {canonical_ref}"
        )
    return f"{repository}:{tag}-{docker_platform_staging_suffix(platform)}"


def write_image_record(path: str | Path, record: MulledImageRecord) -> None:
    """Append one image record as JSONL, creating parent directories as needed."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        json.dump(asdict(record), handle, sort_keys=True)
        handle.write("\n")


def record_mulled_upload(
    output_path: Path | None,
    canonical_ref: str,
    platform: ContainerPlatform,
    platform_ref: str,
    digest: str,
) -> None:
    """Append an upload record to *output_path* (JSONL), if configured."""
    if output_path is None:
        return
    write_image_record(
        output_path,
        MulledImageRecord(
            canonical_ref=canonical_ref,
            platform=platform,
            platform_ref=platform_ref,
            digest=digest,
        ),
    )


def load_image_records(paths: Iterable[str]) -> list[MulledImageRecord]:
    """Load and de-duplicate JSONL records from files or directories."""
    records: set[MulledImageRecord] = set()
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
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


def _skopeo_args(creds: str | None) -> tuple[list[str], list[str]]:
    if not creds:
        return [], []
    return ["--creds", creds], creds.split(":", 1)


def _inspect_raw(ref: str, creds: str | None) -> tuple[dict[str, Any], str]:
    auth_args, mask = _skopeo_args(creds)
    raw = utils.run(
        ["skopeo", "inspect", "--raw", *auth_args, f"docker://{ref}"],
        mask=mask,
    ).stdout
    manifest = json.loads(raw)
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
    ).stdout.strip()
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"Registry returned an invalid digest for {ref}: {digest}")
    return manifest, digest


def _inspect_config_platform(ref: str, creds: str | None) -> str:
    auth_args, mask = _skopeo_args(creds)
    raw = utils.run(
        ["skopeo", "inspect", "--config", *auth_args, f"docker://{ref}"],
        mask=mask,
    ).stdout
    config = json.loads(raw)
    os_name = config.get("os")
    architecture = config.get("architecture")
    variant = config.get("variant")
    if not os_name or not architecture:
        raise RuntimeError(f"Image config for {ref} has no OS/architecture")
    result = f"{os_name}/{architecture}"
    if variant:
        result += f"/{variant}"
    return result


def _descriptor_platform(descriptor: dict[str, Any]) -> str:
    platform = descriptor.get("platform") or {}
    result = f"{platform.get('os')}/{platform.get('architecture')}"
    if variant := platform.get("variant"):
        result += f"/{variant}"
    return result


def _ref_exists(ref: str, creds: str | None) -> bool:
    auth_args, mask = _skopeo_args(creds)
    result = utils.run(
        ["skopeo", "inspect", *auth_args, f"docker://{ref}"],
        mask=mask,
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
) -> dict[str, str] | None:
    if not _ref_exists(canonical_ref, creds):
        return None
    manifest, digest = _inspect_raw(canonical_ref, creds)
    if manifest.get("mediaType") in INDEX_MEDIA_TYPES or "manifests" in manifest:
        descriptors: dict[str, str] = {}
        for descriptor in manifest.get("manifests", []):
            platform = _descriptor_platform(descriptor)
            if platform in descriptors:
                raise RuntimeError(f"{canonical_ref} has duplicate {platform} entries")
            descriptors[platform] = descriptor["digest"]
        return descriptors
    platform = _inspect_config_platform(f"{canonical_ref}@{digest}", creds)
    return {platform: digest}


def _publish_manifest(
    canonical_ref: str, descriptors: list[ManifestDescriptor]
) -> None:
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
    if len(descriptors) == 1:
        command += ["--prefer-index=false"]
    command += ["--tag", canonical_ref, *sources]
    utils.run(
        command,
        mask=False,
        live=True,
    )


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

    if not any(d.platform == "linux/amd64" for d in descriptors):
        raise RuntimeError(f"No amd64 image is available for {canonical_ref}")

    desired = {d.platform: d.digest for d in descriptors}
    if current == desired:
        logger.info("Manifest already current: %s", canonical_ref)
        return False

    _publish_manifest(canonical_ref, descriptors)

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
    """Reconcile each canonical ref represented by uploaded image records."""
    records = list(records)
    canonical_refs = sorted({record.canonical_ref for record in records})
    changed = 0
    for canonical_ref in canonical_refs:
        ref_records = [r for r in records if r.canonical_ref == canonical_ref]
        changed += int(
            reconcile_manifest(canonical_ref, ref_records, platforms, creds=creds)
        )
    return changed, len(canonical_refs)
