import os
import json
from unittest.mock import Mock

import pytest

from bioconda_utils import _types, build, docker_utils, pkg_test, upload
from bioconda_utils._types import PkgBuildRef

SAMTOOLS_1_3_0 = PkgBuildRef(name="samtools", version="1.3", build_string="0")
BIOCONTAINERS = _types.QuayUploadTarget("biocontainers")


def test_mulled_image_metadata_records_target_platform():
    image = build.mulled_image_metadata(SAMTOOLS_1_3_0, "linux/arm64")
    assert image.pkg_ref == SAMTOOLS_1_3_0
    assert image.target_platform == "linux/arm64"


def test_mulled_image_metadata_records_native_platform(monkeypatch):
    monkeypatch.setattr(_types.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        build, "native_container_platform", _types.native_container_platform
    )
    image = build.mulled_image_metadata(SAMTOOLS_1_3_0)
    assert image.target_platform == "linux/arm64"


def test_test_package_passes_target_platform(monkeypatch, tmp_path):
    package = tmp_path / "conda-bld" / "linux-64" / "samtools-1.3-0.tar.bz2"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"")

    commands = []
    monkeypatch.setattr(pkg_test, "update_index", lambda _path: None)
    monkeypatch.setattr(pkg_test, "get_test_command", lambda _path: "true")
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        pkg_test.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    pkg_test.test_package(
        str(package),
        presolved=False,
        target_platform="linux/arm64",
    )

    assert commands
    cmd = commands[0]
    assert cmd[0:2] == ["mulled-build", "build-and-test"]
    assert "--target-platform" in cmd
    assert cmd[cmd.index("--target-platform") + 1] == "linux/arm64"


def test_mulled_upload_passes_target_platform(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload, "ensure_quay_repository", lambda *_args: None)
    monkeypatch.setattr(upload.utils, "skopeo_env", lambda: {})

    def run(cmd, **_kwargs):
        commands.append(cmd)
        if "--config" in cmd:
            return type(
                "R",
                (),
                {"stdout": json.dumps({"os": "linux", "architecture": "arm64"})},
            )()
        return type("R", (), {"stdout": "sha256:" + "a" * 64})()

    monkeypatch.setattr(
        upload.utils,
        "run",
        run,
    )

    record = upload.mulled_upload(SAMTOOLS_1_3_0, BIOCONTAINERS, "linux/arm64")

    ref = "quay.io/biocontainers/samtools:1.3--0-arm64"
    assert any(ref in arg for arg in commands[1])
    assert record.platform_ref == ref
    assert record.digest == "sha256:" + "a" * 64


def test_mulled_upload_stages_amd64_under_suffixed_tag(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload, "ensure_quay_repository", lambda *_args: None)
    monkeypatch.setattr(upload.utils, "skopeo_env", lambda: {})

    def run(cmd, **_kwargs):
        commands.append(cmd)
        if "--config" in cmd:
            return type(
                "R",
                (),
                {"stdout": json.dumps({"os": "linux", "architecture": "amd64"})},
            )()
        return type("R", (), {"stdout": "sha256:" + "a" * 64})()

    monkeypatch.setattr(
        upload.utils,
        "run",
        run,
    )

    upload.mulled_upload(SAMTOOLS_1_3_0, BIOCONTAINERS, "linux/amd64")

    assert "quay.io/biocontainers/samtools:1.3--0-amd64" in " ".join(commands[1])


def test_mulled_upload_rejects_wrong_source_platform(monkeypatch):
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload, "ensure_quay_repository", lambda *_args: None)
    monkeypatch.setattr(upload.utils, "skopeo_env", lambda: {})
    monkeypatch.setattr(
        upload.utils,
        "run",
        lambda _cmd, **_kwargs: type(
            "R", (), {"stdout": json.dumps({"os": "linux", "architecture": "amd64"})}
        )(),
    )

    with pytest.raises(RuntimeError, match="Image platform mismatch"):
        upload.mulled_upload(SAMTOOLS_1_3_0, BIOCONTAINERS, "linux/arm64")


def test_upload_mulled_image_source_records_destination_digest(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload, "ensure_quay_repository", lambda *_args: None)
    monkeypatch.setattr(upload.utils, "skopeo_env", lambda: {})

    def run(cmd, **_kwargs):
        commands.append(cmd)
        if "--config" in cmd:
            return type(
                "R",
                (),
                {"stdout": json.dumps({"os": "linux", "architecture": "arm64"})},
            )()
        if "--format" in cmd:
            return type("R", (), {"stdout": "sha256:" + "d" * 64})()
        return type("R", (), {"stdout": ""})()

    monkeypatch.setattr(upload.utils, "run", run)

    record = upload.upload_mulled_image_source(
        "docker-archive:/tmp/samtools.tar.gz",
        "quay.io/biocontainers/samtools:1.3--0",
        "linux/arm64",
    )

    assert commands[1][0:4] == ["skopeo", "--command-timeout", "600s", "copy"]
    assert commands[2][0:4] == ["skopeo", "inspect", "--format", "{{.Digest}}"]
    assert commands[2][-1] == "docker://quay.io/biocontainers/samtools:1.3--0-arm64"
    assert record.digest == "sha256:" + "d" * 64


def test_ensure_quay_repository_creates_public_repository(monkeypatch):
    upload._QUAY_REPOSITORIES_READY.clear()
    monkeypatch.setenv("QUAY_OAUTH_TOKEN", "token")
    not_found = Mock(status_code=404)
    created = Mock(status_code=201)
    get = Mock(return_value=not_found)
    post = Mock(return_value=created)
    monkeypatch.setattr(upload.requests, "get", get)
    monkeypatch.setattr(upload.requests, "post", post)

    upload.ensure_quay_repository("biocontainers", "samtools")

    post.assert_called_once()
    assert post.call_args.kwargs["json"]["visibility"] == "public"


def test_ensure_quay_repository_makes_private_repository_public(monkeypatch):
    upload._QUAY_REPOSITORIES_READY.clear()
    monkeypatch.setenv("QUAY_OAUTH_TOKEN", "token")
    private = Mock(status_code=200)
    private.json.return_value = {"is_public": False}
    changed = Mock(status_code=200)
    get = Mock(return_value=private)
    post = Mock(return_value=changed)
    monkeypatch.setattr(upload.requests, "get", get)
    monkeypatch.setattr(upload.requests, "post", post)

    upload.ensure_quay_repository("biocontainers", "samtools")

    assert post.call_args.args[0].endswith("/samtools/changevisibility")
    assert post.call_args.kwargs["json"] == {"visibility": "public"}


def test_purge_image_uses_platform_suffix(monkeypatch):
    commands = []
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    docker_utils.purgeImage(BIOCONTAINERS, SAMTOOLS_1_3_0, "linux/arm64")

    assert commands
    assert "quay.io/biocontainers/samtools:1.3--0-arm64" in " ".join(commands[0])
