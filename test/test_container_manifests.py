import base64
import json
from pathlib import Path

import pytest

from bioconda_utils import container_manifests
from bioconda_utils.container_manifests import (
    ManifestDescriptor,
    MulledImageRecord,
)


def test_platform_ref_uses_staging_suffix_for_every_architecture():
    canonical = "quay.io/biocontainers/samtools:1.20--h50ea8bc_0"
    assert (
        container_manifests.platform_ref(canonical, "linux/amd64")
        == f"{canonical}-amd64"
    )
    assert (
        container_manifests.platform_ref(canonical, "linux/arm64")
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
        container_manifests.platform_ref(ref, "linux/amd64")


def test_record_roundtrip_and_deduplication(tmp_path):
    record = MulledImageRecord(
        canonical_ref="quay.io/biocontainers/samtools:1.20--0",
        platform="linux/arm64",
        platform_ref="quay.io/biocontainers/samtools:1.20--0-arm64",
        digest="sha256:" + "a" * 64,
    )
    first = tmp_path / "first" / "images.jsonl"
    second = tmp_path / "second.jsonl"
    container_manifests.write_image_record(str(first), record)
    container_manifests.write_image_record(str(second), record)

    assert container_manifests.load_image_records([str(tmp_path)]) == [record]


def test_load_records_rejects_mismatched_platform_ref(tmp_path):
    path = tmp_path / "images.jsonl"
    path.write_text(
        json.dumps(
            {
                "canonical_ref": "quay.io/biocontainers/samtools:1.20--0",
                "platform": "linux/arm64",
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
                "platform": "linux/arm64",
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
            canonical, "linux/amd64", f"{canonical}-amd64", "sha256:" + "a" * 64
        ),
        MulledImageRecord(
            canonical, "linux/arm64", f"{canonical}-arm64", "sha256:" + "b" * 64
        ),
    ]
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: {
            "linux/amd64": "sha256:" + "a" * 64,
            "linux/arm64": "sha256:" + "b" * 64,
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
            canonical, records, ["linux/amd64", "linux/arm64"]
        )
        is False
    )
    assert publish == []


def test_reconcile_publishes_and_verifies(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical, "linux/amd64", f"{canonical}-amd64", "sha256:" + "a" * 64
        ),
        MulledImageRecord(
            canonical, "linux/arm64", f"{canonical}-arm64", "sha256:" + "b" * 64
        ),
    ]
    current = iter(
        [
            {"linux/amd64": "sha256:" + "a" * 64},
            {
                "linux/amd64": "sha256:" + "a" * 64,
                "linux/arm64": "sha256:" + "b" * 64,
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
        canonical, records, ["linux/amd64", "linux/arm64"]
    )
    assert published[0][0] == canonical
    assert {item.platform for item in published[0][1]} == {
        "linux/amd64",
        "linux/arm64",
    }


def test_reconcile_preserves_existing_arm64_when_only_amd64_is_updated(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical, "linux/amd64", f"{canonical}-amd64", "sha256:" + "a" * 64
        ),
    ]
    desired = {
        "linux/amd64": "sha256:" + "a" * 64,
        "linux/arm64": "sha256:" + "b" * 64,
    }
    current = iter(
        [
            {
                "linux/amd64": "sha256:" + "0" * 64,
                "linux/arm64": "sha256:" + "b" * 64,
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
        canonical, records, ["linux/amd64", "linux/arm64"]
    )
    assert {item.platform: item.digest for item in published[0][1]} == desired
    assert {item.platform: item.source_ref for item in published[0][1]} == {
        "linux/amd64": f"{canonical}-amd64",
        "linux/arm64": canonical,
    }


def test_reconcile_preserves_existing_amd64_when_only_arm64_is_updated(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical, "linux/arm64", f"{canonical}-arm64", "sha256:" + "b" * 64
        ),
    ]
    desired = {
        "linux/amd64": "sha256:" + "a" * 64,
        "linux/arm64": "sha256:" + "b" * 64,
    }
    current = iter(
        [
            {
                "linux/amd64": "sha256:" + "a" * 64,
                "linux/arm64": "sha256:" + "0" * 64,
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
        canonical, records, ["linux/amd64", "linux/arm64"]
    )
    assert {item.platform: item.digest for item in published[0][1]} == desired
    assert {item.platform: item.source_ref for item in published[0][1]} == {
        "linux/amd64": canonical,
        "linux/arm64": f"{canonical}-arm64",
    }


def test_reconcile_requires_amd64_for_new_manifest(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical, "linux/arm64", f"{canonical}-arm64", "sha256:" + "b" * 64
        ),
    ]
    monkeypatch.setattr(
        container_manifests,
        "_current_descriptors",
        lambda *_args: None,
    )
    with pytest.raises(RuntimeError, match="No amd64 image"):
        container_manifests.reconcile_manifest(
            canonical, records, ["linux/amd64", "linux/arm64"]
        )


def test_initial_publish_succeeds_when_no_manifest_exists(monkeypatch):
    canonical = "quay.io/biocontainers/samtools:1.20--0"
    records = [
        MulledImageRecord(
            canonical, "linux/amd64", f"{canonical}-amd64", "sha256:" + "a" * 64
        ),
        MulledImageRecord(
            canonical, "linux/arm64", f"{canonical}-arm64", "sha256:" + "b" * 64
        ),
    ]
    desired = {
        "linux/amd64": "sha256:" + "a" * 64,
        "linux/arm64": "sha256:" + "b" * 64,
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
        canonical, records, ["linux/amd64", "linux/arm64"]
    )
    assert len(published) == 1


def test_publish_single_platform_preserves_single_manifest(monkeypatch):
    commands = []
    monkeypatch.setattr(
        container_manifests.utils,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )
    descriptor = ManifestDescriptor(
        "linux/amd64",
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0", [descriptor]
    )

    assert commands
    assert any(f"@{descriptor.digest}" in arg for arg in commands[0])


def test_publish_manifest_injects_docker_config_when_creds_provided(monkeypatch):
    captured: dict = {}
    config_paths: list = []

    def fake_run(command, **_kwargs):
        captured["command"] = command
        captured["env"] = _kwargs.get("env")
        captured["redacted_secrets"] = _kwargs.get("redacted_secrets")
        captured["live"] = _kwargs.get("live")
        docker_config = _kwargs["env"]["DOCKER_CONFIG"]
        config_paths.append(Path(docker_config))
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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

    assert captured["redacted_secrets"] == ["s3cret"]
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
        "linux/amd64",
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
        "linux/amd64",
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
        captured["redacted_secrets"] = _kwargs.get("redacted_secrets")
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
        "sha256:" + "a" * 64,
        "quay.io/biocontainers/samtools:1.20--0-amd64",
    )

    container_manifests._publish_manifest(
        "quay.io/biocontainers/samtools:1.20--0",
        [descriptor],
    )

    assert captured["redacted_secrets"] is False
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
        return None

    monkeypatch.setattr(container_manifests.utils, "run", fake_run)
    descriptor = ManifestDescriptor(
        "linux/amd64",
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
