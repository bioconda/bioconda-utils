import os
from unittest.mock import Mock

from bioconda_utils import _types, build, docker_utils, pkg_test, upload


def test_mulled_image_metadata_uses_platform_suffix():
    image = build.mulled_image_metadata(
        "samtools=1.3--0", "biocontainers", "linux/arm64"
    )
    assert image.spec == "samtools=1.3--0"
    assert image.target_platform == "linux/arm64"
    assert image.repository == "biocontainers"
    assert image.image_name == "samtools"
    assert image.remote_tag == "quay.io/biocontainers/samtools:1.3--0-arm64"


def test_mulled_image_metadata_keeps_amd64_unsuffixed():
    image = build.mulled_image_metadata(
        "samtools=1.3--0", "biocontainers", "linux/amd64"
    )
    assert image.remote_tag == "quay.io/biocontainers/samtools:1.3--0"


def test_mulled_image_metadata_records_native_platform(monkeypatch):
    monkeypatch.setattr(_types.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        build, "native_container_platform", _types.native_container_platform
    )
    image = build.mulled_image_metadata("samtools=1.3--0", "biocontainers")
    assert image.target_platform == "linux/arm64"


def test_test_package_passes_target_platform(monkeypatch, tmp_path):
    package = tmp_path / "conda-bld" / "linux-64" / "samtools-1.3-0.tar.bz2"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"")

    commands = []
    monkeypatch.setattr(pkg_test, "update_index", lambda _path: None)
    monkeypatch.setattr(pkg_test, "get_tests", lambda _path: "true")
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
    monkeypatch.setattr(
        upload.utils,
        "run",
        lambda cmd, **_kwargs: (
            commands.append(cmd),
            type("R", (), {"stdout": "sha256:" + "a" * 64})(),
        )[1],
    )

    upload.mulled_upload("samtools=1.3--0", "biocontainers", "linux/arm64")

    assert len(commands) > 1
    ref = "quay.io/biocontainers/samtools:1.3--0-arm64"
    assert any(ref in arg for arg in commands[1])


def test_mulled_upload_stages_amd64_under_suffixed_tag(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload, "ensure_quay_repository", lambda *_args: None)
    monkeypatch.setattr(
        upload.utils,
        "run",
        lambda cmd, **_kwargs: (
            commands.append(cmd),
            type("R", (), {"stdout": "sha256:" + "a" * 64})(),
        )[1],
    )

    upload.mulled_upload("samtools=1.3--0", "biocontainers", "linux/amd64")

    assert len(commands) > 1
    assert "quay.io/biocontainers/samtools:1.3--0-amd64" in " ".join(commands[1])


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

    docker_utils.purgeImage("biocontainers", "samtools=1.3--0", "linux/arm64")

    assert commands
    assert "quay.io/biocontainers/samtools:1.3--0-arm64" in " ".join(commands[0])
