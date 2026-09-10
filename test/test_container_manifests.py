import base64
import json
import subprocess as sp
from pathlib import Path

import pytest

from bioconda_utils import container_manifests
from bioconda_utils._types import ContainerPlatform
from bioconda_utils.container_manifests import (
    ManifestDescriptor,
    MulledImageRecord,
)


def test_platform_ref_uses_staging_suffix_for_every_architecture():
    canonical = "quay.io/biocontainers/samtools:1.20--h50ea8bc_0"
    assert (
        container_manifests.platform_ref(canonical, ContainerPlatform.LINUX_AMD64)
        == f"{canonical}-amd64"
    )
    assert (
        container_manifests.platform_ref(canonical, ContainerPlatform.LINUX_ARM64)
        == f"{canonical}-arm64"
    )


@pytest.mark.parametrize(
    "ref",
    [
        "samtools:latest",
        "quay.io/biocontainers/samtools",
        "not-an-image",
    ],
)
def test_platform_ref_rejects_noncanonical_refs(ref):
    with pytest.raises(ValueError, match="fully-qualified tagged"):
        container_manifests.platform_ref(ref, ContainerPlatform.LINUX_AMD64)


def test_record_roundtrip_and_deduplication(tmp_path):
    record = MulledImageRecord(
        canonical_ref="quay.io/biocontainers/samtools:1.20--0",
        platform=ContainerPlatform.LINUX_ARM64,
        platform_ref="quay.io/biocontainers/samtools:1.20--0-arm64",
        digest="sha256:" + "a" * 64,
    )
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    container_manifests.write_image_record(str(dir_a), record)
    container_manifests.write_image_record(str(dir_b), record)

    assert container_manifests.load_image_records([str(tmp_path)]) == [record]


def test_multiarch_ref_strips_build_hash():
    assert (
        container_manifests.multiarch_ref(
            "quay.io/biocontainers/samtools:1.24--h9dcdb79_1"
        )
        == "quay.io/biocontainers/samtools:1.24"
    )


def test_multiarch_ref_preserves_variant_prefix():
    assert (
        container_manifests.multiarch_ref(
            "quay.io/biocontainers/htseq:2.1.2--py310h8fb3dee_0"
        )
        == "quay.io/biocontainers/htseq:2.1.2--py310"
    )


def test_multiarch_ref_passes_through_version_only_tags():
    assert (
        container_manifests.multiarch_ref("quay.io/biocontainers/samtools:1.2")
        == "quay.io/biocontainers/samtools:1.2"
    )


def test_multiarch_ref_handles_old_style_build_number():
    assert (
        container_manifests.multiarch_ref("quay.io/biocontainers/samtools:1.2--0")
        == "quay.io/biocontainers/samtools:1.2"
    )


def test_reconcile_manifests_creates_multiarch_index_from_different_build_hashes(
    monkeypatch,
):
    """Two architectures with different build strings combine under one multi-arch tag."""
    records = [
        MulledImageRecord(
            canonical_ref=f"quay.io/biocontainers/samtools:1.24--{build}",
            platform=platform,
            platform_ref=f"quay.io/biocontainers/samtools:1.24--{build}-{suffix}",
            digest="sha256:" + digest * 64,
        )
        for build, platform, suffix, digest in (
            ("h9dcdb79_1", ContainerPlatform.LINUX_AMD64, "amd64", "a"),
            ("h391949c_1", ContainerPlatform.LINUX_ARM64, "arm64", "b"),
        )
    ]
    calls = []
    monkeypatch.setattr(
        container_manifests,
        "reconcile_manifest",
        lambda ref, grouped, *_args, **_kwargs: calls.append((ref, grouped)) or True,
    )

    assert container_manifests.reconcile_manifests(records) == (3, 3)
    refs = {ref for ref, _ in calls}
    # Two exact per-arch tags + one multi-arch tag
    assert refs == {
        "quay.io/biocontainers/samtools:1.24--h9dcdb79_1",
        "quay.io/biocontainers/samtools:1.24--h391949c_1",
        "quay.io/biocontainers/samtools:1.24",
    }
    # The multi-arch call gets both records
    multiarch_call = next(
        c for c in calls if c[0] == "quay.io/biocontainers/samtools:1.24"
    )
    assert len(multiarch_call[1]) == 2


def test_reconcile_manifests_separates_variant_matrix_into_distinct_multiarch_tags(
    monkeypatch,
):
    """Different python variants produce different multi-arch tags."""
    records = [
        MulledImageRecord(
            canonical_ref=f"quay.io/biocontainers/htseq:2.1.2--{build}",
            platform=platform,
            platform_ref=f"quay.io/biocontainers/htseq:2.1.2--{build}-{suffix}",
            digest="sha256:" + digest * 64,
        )
        for build, platform, suffix, digest in (
            ("py310h8fb3dee_0", ContainerPlatform.LINUX_AMD64, "amd64", "a"),
            ("py310h1b7e08d_0", ContainerPlatform.LINUX_ARM64, "arm64", "b"),
            ("py311hb6b0eea_0", ContainerPlatform.LINUX_AMD64, "amd64", "c"),
            ("py311hda4d338_0", ContainerPlatform.LINUX_ARM64, "arm64", "d"),
        )
    ]
    calls = []
    monkeypatch.setattr(
        container_manifests,
        "reconcile_manifest",
        lambda ref, grouped, *_args, **_kwargs: calls.append((ref, grouped)) or True,
    )

    # 4 exact + 2 multi-arch = 6 groups
    assert container_manifests.reconcile_manifests(records) == (6, 6)
    multiarch_refs = {
        ref for ref, _ in calls if ref not in {r.canonical_ref for r in records}
    }
    assert multiarch_refs == {
        "quay.io/biocontainers/htseq:2.1.2--py310",
        "quay.io/biocontainers/htseq:2.1.2--py311",
    }
    # Each multi-arch group has one record per platform
    for ref in multiarch_refs:
        group = next(g for r, g in calls if r == ref)
        assert len(group) == 2
        assert {r.platform for r in group} == {
            ContainerPlatform.LINUX_AMD64,
            ContainerPlatform.LINUX_ARM64,
        }


def test_reconcile_manifests_rejects_duplicate_platform_in_multiarch_group():
    """Two records for the same platform under one multi-arch ref is an error."""
    records = [
        MulledImageRecord(
            canonical_ref=f"quay.io/biocontainers/samtools:1.24--{build}",
            platform=ContainerPlatform.LINUX_AMD64,
            platform_ref=f"quay.io/biocontainers/samtools:1.24--{build}-amd64",
            digest="sha256:" + digest * 64,
        )
        for build, digest in (("h1111111_1", "a"), ("h2222222_1", "b"))
    ]
    with pytest.raises(ValueError, match="Multiple image records"):
        container_manifests.reconcile_manifests(records)


def test_write_image_record_creates_unique_file(tmp_path):
    record = MulledImageRecord(
        canonical_ref="quay.io/biocontainers/samtools:1.20--0",
        platform=ContainerPlatform.LINUX_ARM64,
        platform_ref="quay.io/biocontainers/samtools:1.20--0-arm64",
        digest="sha256:" + "a" * 64,
    )
    container_manifests.write_image_record(str(tmp_path), record)
    assert tmp_path.is_dir()
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".jsonl"
    assert files[0].name.startswith("20")
    assert container_manifests.load_image_records([str(tmp_path)]) == [record]


def test_load_records_from_directory_ignores_non_jsonl_files(tmp_path):
    record = MulledImageRecord(
        canonical_ref="quay.io/biocontainers/samtools:1.20--0",
        platform=ContainerPlatform.LINUX_ARM64,
        platform_ref="quay.io/biocontainers/samtools:1.20--0-arm64",
        digest="sha256:" + "a" * 64,
    )
    container_manifests.write_image_record(str(tmp_path), record)
    (tmp_path / "README.md").write_text("not json\n", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")

    assert container_manifests.load_image_records([str(tmp_path)]) == [record]


def test_load_records_rejects_mismatched_platform_ref(tmp_path):
    path = tmp_path / "images.jsonl"
    path.write_text(
        json.dumps(
            {
                "canonical_ref": "quay.io/biocontainers/samtools:1.20--0",
                "platform": ContainerPlatform.LINUX_ARM64,
                "platform_ref": "quay.io/biocontainers/samtools:wrong",
                "digest": "sha256:" + "a" * 64,
            }
        )
    )
    with pytest.raises(ValueError, match="Unexpected platform ref"):
        container_manifests.load_image_records([str(path)])


def test_load_records_rejects_invalid_digest(tmp_path):
    path = tmp_path / "images.jsonl"
    path.write_text(
        json.dumps(
            {
                "canonical_ref": "quay.io/biocontainers/samtools:1.20--0",
                "platform": ContainerPlatform.LINUX_ARM64,
                "platform_ref": "quay.io/biocontainers/samtools:1.20--0-arm64",
                "digest": "not-a-digest",
            }
        )
    )
    with pytest.raises(ValueError, match="Invalid digest"):
        container_manifests.load_image_records([str(path)])


def test_reconcile_is_idempotent(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_AMD64,
            f"{canonical}-amd64",
            "sha256:" + "a" * 64,
        ),
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: {
            ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
            ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
        },
    )
    publish = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda *args: publish.append(args),
    )

    assert (
        container_manifests.reconcile_manifest(
            canonical,
            records,
            [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
        )
        is False
    )
    assert publish == []


def test_current_descriptors_inspects_index_once(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    digest = "sha256:" + "a" * 64
    manifest = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "digest": digest,
                "platform": {"os": "linux", "architecture": "amd64"},
            }
        ],
    }
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return sp.CompletedProcess(command, 0, stdout=json.dumps(manifest))

    monkeypatch.setattr(container_manifests.utils, "run", run)
    monkeypatch.setattr(container_manifests, "skopeo_env", dict)

    assert container_manifests._current_descriptors(canonical, None) == {
        ContainerPlatform.LINUX_AMD64: digest
    }
    assert len(calls) == 1
    assert calls[0][0] == ["skopeo", "inspect", "--raw", f"docker://{canonical}"]
    assert calls[0][1]["check"] is False


def test_current_descriptors_inspects_single_image_twice(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    digest = "sha256:" + "a" * 64
    manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json"}
    inspection = {
        "Digest": digest,
        "Os": "linux",
        "Architecture": "arm64",
    }
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = manifest if "--raw" in command else inspection
        return sp.CompletedProcess(command, 0, stdout=json.dumps(output))

    monkeypatch.setattr(container_manifests.utils, "run", run)
    monkeypatch.setattr(container_manifests, "skopeo_env", dict)

    assert container_manifests._current_descriptors(canonical, None) == {
        ContainerPlatform.LINUX_ARM64: digest
    }
    assert [call[0] for call in calls] == [
        ["skopeo", "inspect", "--raw", f"docker://{canonical}"],
        ["skopeo", "inspect", "--no-tags", f"docker://{canonical}"],
    ]


@pytest.mark.parametrize("message", ["manifest unknown", "status code 404"])
def test_inspect_raw_returns_none_for_missing_ref(monkeypatch, message):
    def run(command, **_kwargs):
        return sp.CompletedProcess(command, 1, stdout=message)

    monkeypatch.setattr(container_manifests.utils, "run", run)
    monkeypatch.setattr(container_manifests, "skopeo_env", dict)

    assert container_manifests._inspect_raw("quay.io/example/missing:tag", None) is None


def test_inspect_raw_raises_for_unexpected_failure(monkeypatch):
    def run(command, **_kwargs):
        return sp.CompletedProcess(command, 1, stdout="connection refused")

    monkeypatch.setattr(container_manifests.utils, "run", run)
    monkeypatch.setattr(container_manifests, "skopeo_env", dict)

    with pytest.raises(RuntimeError, match="connection refused"):
        container_manifests._inspect_raw("quay.io/example/image:tag", None)


def test_current_descriptors_normalizes_registry_platform_variants(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    manifest = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "digest": "sha256:" + "a" * 64,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "digest": "sha256:" + "b" * 64,
                "platform": {
                    "os": "linux",
                    "architecture": "arm64",
                    "variant": "v8",
                },
            },
        ],
    }
    monkeypatch.setattr(
        container_manifests,
        "_inspect_raw",
        lambda *_args: manifest,
    )

    assert container_manifests._current_descriptors(canonical, None) == {
        ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
        ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
    }


def test_reconcile_is_idempotent_with_registry_platform_variant(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_AMD64,
            f"{canonical}-amd64",
            "sha256:" + "a" * 64,
        ),
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    manifest = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "digest": "sha256:" + "a" * 64,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "digest": "sha256:" + "b" * 64,
                "platform": {
                    "os": "linux",
                    "architecture": "arm64",
                    "variant": "v8",
                },
            },
        ],
    }
    monkeypatch.setattr(
        container_manifests,
        "_inspect_raw",
        lambda *_args: manifest,
    )
    publish = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda *args: publish.append(args),
    )

    assert (
        container_manifests.reconcile_manifest(
            canonical,
            records,
            [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
        )
        is False
    )
    assert publish == []


def test_reconcile_publishes_and_verifies(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_AMD64,
            f"{canonical}-amd64",
            "sha256:" + "a" * 64,
        ),
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    current = iter(
        [
            {ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64},
            {
                ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
                ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
            },
        ]
    )
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: next(current),
    )
    published = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda ref, descriptors, **_kwargs: published.append((ref, descriptors)),
    )

    assert container_manifests.reconcile_manifest(
        canonical,
        records,
        [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
    )
    assert published[0][0] == canonical
    assert {item.platform for item in published[0][1]} == {
        ContainerPlatform.LINUX_AMD64,
        ContainerPlatform.LINUX_ARM64,
    }


def test_reconcile_preserves_existing_arm64_when_only_amd64_is_updated(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_AMD64,
            f"{canonical}-amd64",
            "sha256:" + "a" * 64,
        ),
    ]
    desired = {
        ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
        ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
    }
    current = iter(
        [
            {
                ContainerPlatform.LINUX_AMD64: "sha256:" + "0" * 64,
                ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
            },
            desired,
        ]
    )
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: next(current),
    )
    published = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda ref, descriptors, **_kwargs: published.append((ref, descriptors)),
    )

    assert container_manifests.reconcile_manifest(
        canonical,
        records,
        [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
    )
    assert {item.platform: item.digest for item in published[0][1]} == desired
    assert {item.platform: item.source_ref for item in published[0][1]} == {
        ContainerPlatform.LINUX_AMD64: f"{canonical}-amd64",
        ContainerPlatform.LINUX_ARM64: canonical,
    }


def test_reconcile_preserves_existing_amd64_when_only_arm64_is_updated(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    desired = {
        ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
        ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
    }
    current = iter(
        [
            {
                ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
                ContainerPlatform.LINUX_ARM64: "sha256:" + "0" * 64,
            },
            desired,
        ]
    )
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: next(current),
    )
    published = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda ref, descriptors, **_kwargs: published.append((ref, descriptors)),
    )

    assert container_manifests.reconcile_manifest(
        canonical,
        records,
        [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
    )
    assert {item.platform: item.digest for item in published[0][1]} == desired
    assert {item.platform: item.source_ref for item in published[0][1]} == {
        ContainerPlatform.LINUX_AMD64: canonical,
        ContainerPlatform.LINUX_ARM64: f"{canonical}-arm64",
    }


def test_reconcile_publishes_arm64_only_manifest(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    desired = {
        ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
    }
    current = iter([None, desired])
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: next(current),
    )
    published = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda ref, descriptors, **_kwargs: published.append((ref, descriptors)),
    )

    assert container_manifests.reconcile_manifest(
        canonical,
        records,
        [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
    )
    assert {item.platform: item.digest for item in published[0][1]} == desired


def test_reconcile_rejects_manifest_with_no_available_images(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="No images are available"):
        container_manifests.reconcile_manifest(
            canonical,
            [],
            [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
        )


def test_initial_publish_succeeds_when_no_manifest_exists(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_AMD64,
            f"{canonical}-amd64",
            "sha256:" + "a" * 64,
        ),
        MulledImageRecord(
            canonical,
            ContainerPlatform.LINUX_ARM64,
            f"{canonical}-arm64",
            "sha256:" + "b" * 64,
        ),
    ]
    desired = {
        ContainerPlatform.LINUX_AMD64: "sha256:" + "a" * 64,
        ContainerPlatform.LINUX_ARM64: "sha256:" + "b" * 64,
    }
    current = iter([None, desired])
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: next(current),
    )
    published = []
    monkeypatch.setattr(
        container_manifests,
        "_publish_manifest",
        lambda ref, descriptors, **_kwargs: published.append((ref, descriptors)),
    )

    assert container_manifests.reconcile_manifest(
        canonical,
        records,
        [ContainerPlatform.LINUX_AMD64, ContainerPlatform.LINUX_ARM64],
    )
    assert len(published) == 1


def test_publish_single_platform_creates_index(monkeypatch):
    commands = []
    monkeypatch.setattr(
        container_manifests.utils,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0", [descriptor]
    )

    assert commands
    assert "--prefer-index=false" not in commands[0]
    assert any(f"@{descriptor.digest}" in arg for arg in commands[0])


def test_publish_manifest_injects_docker_config_when_creds_provided(monkeypatch):
    captured: dict = {}
    config_paths: list = []

    def fake_run(command, **_kwargs):
        captured["command"] = command
        captured["env"] = _kwargs.get("env")
        captured["secrets"] = _kwargs.get("secrets")
        captured["live"] = _kwargs.get("live")
        docker_config = _kwargs["env"]["DOCKER_CONFIG"]
        config_paths.append(Path(docker_config))

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
        creds="alice:s3cret",
    )

    assert len(config_paths) == 1
    config_path = config_paths[0]
    assert not config_path.exists(), "temp DOCKER_CONFIG should be removed"

    assert captured["secrets"] == ["s3cret"]
    assert captured["live"] is True
    assert captured["env"]["DOCKER_CONFIG"]


def test_publish_manifest_cleans_up_docker_config_on_failure(monkeypatch):
    config_paths: list = []

    def fake_run(command, **_kwargs):
        docker_config = _kwargs["env"]["DOCKER_CONFIG"]
        config_paths.append(Path(docker_config))
        raise RuntimeError("buildx failed")

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    with pytest.raises(RuntimeError, match="buildx failed"):
        container_manifests._publish_manifest(
            "quay.io/biocontainers/samtools:1.20--0",
            [descriptor],
            creds="alice:s3cret",
        )

    assert not config_paths[0].exists(), "temp DOCKER_CONFIG should be cleaned up"


def test_publish_manifest_handles_oauth_token_format(monkeypatch):
    captured: dict = {}

    def fake_run(command, **_kwargs):
        docker_config = Path(_kwargs["env"]["DOCKER_CONFIG"])
        captured["config"] = json.loads((docker_config / "config.json").read_text())

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
        creds="$oauthtoken:TOKEN",
    )

    assert captured["config"]["auths"]["quay.io"]["auth"] == base64.b64encode(
        b"$oauthtoken:TOKEN"
    ).decode("ascii")


def test_publish_manifest_rejects_malformed_creds(monkeypatch):
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )
    with pytest.raises(ValueError, match="Cannot parse credentials"):
        container_manifests._publish_manifest(
            "quay.io/biocontainers/samtools:1.20--0",
            [descriptor],
            creds="no-colon-here",
        )


def test_publish_manifest_without_creds_omits_docker_config(monkeypatch):
    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["env"] = _kwargs.get("env")
        captured["secrets"] = _kwargs.get("secrets")

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
    )

    assert captured["secrets"] is None
    assert "DOCKER_CONFIG" not in captured["env"]


def test_publish_manifest_preserves_user_docker_config(monkeypatch, tmp_path):
    source_dir = tmp_path / "docker-config"
    source_dir.mkdir()
    user_config = {
        "auths": {
            "ghcr.io": {"auth": base64.b64encode(b"gh-user:gh-token").decode("ascii")}
        },
        "credsStore": "desktop",
        "credHelpers": {"registry.example.org": "secretservice"},
        "experimental": "enabled",
    }
    (source_dir / "config.json").write_text(json.dumps(user_config))
    monkeypatch.setenv("DOCKER_CONFIG", str(source_dir))

    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["config"] = json.loads(
            (Path(_kwargs["env"]["DOCKER_CONFIG"]) / "config.json").read_text()
        )

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
        creds="alice:s3cret",
    )

    assert captured["config"]["credsStore"] == "desktop"
    assert captured["config"]["credHelpers"] == {
        "registry.example.org": "secretservice"
    }
    assert captured["config"]["experimental"] == "enabled"
    assert captured["config"]["auths"]["ghcr.io"] == user_config["auths"]["ghcr.io"]
    assert captured["config"]["auths"]["quay.io"]["auth"] == base64.b64encode(
        b"alice:s3cret"
    ).decode("ascii")


def test_publish_manifest_overrides_existing_auth_for_target_host(
    monkeypatch, tmp_path
):
    source_dir = tmp_path / "docker-config"
    source_dir.mkdir()
    (source_dir / "config.json").write_text(
        json.dumps(
            {
                "auths": {
                    "quay.io": {
                        "auth": base64.b64encode(b"old-user:old-token").decode("ascii")
                    }
                }
            }
        )
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(source_dir))

    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["config"] = json.loads(
            (Path(_kwargs["env"]["DOCKER_CONFIG"]) / "config.json").read_text()
        )

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
        creds="new-user:new-token",
    )

    assert captured["config"]["auths"]["quay.io"]["auth"] == base64.b64encode(
        b"new-user:new-token"
    ).decode("ascii")


def test_publish_manifest_handles_missing_user_docker_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "does-not-exist"))

    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["config"] = json.loads(
            (Path(_kwargs["env"]["DOCKER_CONFIG"]) / "config.json").read_text()
        )

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
        creds="alice:s3cret",
    )

    assert captured["config"] == {
        "auths": {
            "quay.io": {"auth": base64.b64encode(b"alice:s3cret").decode("ascii")}
        }
    }


def test_publish_manifest_warns_and_proceeds_on_malformed_user_config(
    monkeypatch, tmp_path, caplog
):
    source_dir = tmp_path / "docker-config"
    source_dir.mkdir()
    (source_dir / "config.json").write_text("{not valid json")
    monkeypatch.setenv("DOCKER_CONFIG", str(source_dir))

    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["config"] = json.loads(
            (Path(_kwargs["env"]["DOCKER_CONFIG"]) / "config.json").read_text()
        )

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    with caplog.at_level("WARNING", logger="bioconda_utils.container_manifests"):
        container_manifests._publish_manifest(
            "quay.io/biocontainers/samtools:1.20--0",
            [descriptor],
            creds="alice:s3cret",
        )

    assert "Could not read existing docker config" in caplog.text
    assert captured["config"] == {
        "auths": {
            "quay.io": {"auth": base64.b64encode(b"alice:s3cret").decode("ascii")}
        }
    }


def test_publish_manifest_ignores_non_object_user_config(monkeypatch, tmp_path, caplog):
    source_dir = tmp_path / "docker-config"
    source_dir.mkdir()
    (source_dir / "config.json").write_text(json.dumps(["not", "an", "object"]))
    monkeypatch.setenv("DOCKER_CONFIG", str(source_dir))

    captured: dict = {}

    def fake_run(command, **_kwargs):
        captured["config"] = json.loads(
            (Path(_kwargs["env"]["DOCKER_CONFIG"]) / "config.json").read_text()
        )

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        ContainerPlatform.LINUX_AMD64,
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    with caplog.at_level("WARNING", logger="bioconda_utils.container_manifests"):
        container_manifests._publish_manifest(
            "quay.io/biocontainers/samtools:1.20--0",
            [descriptor],
            creds="alice:s3cret",
        )

    assert "is not a JSON object" in caplog.text
    assert captured["config"]["auths"]["quay.io"]["auth"] == base64.b64encode(
        b"alice:s3cret"
    ).decode("ascii")
