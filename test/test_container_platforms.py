import os

from bioconda_utils import build, docker_utils, pkg_test, upload


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
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        upload.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    upload.mulled_upload("samtools=1.3--0", "biocontainers", "linux/arm64")

    assert commands
    cmd = commands[0]
    assert cmd[0:4] == ["mulled-build", "push", "samtools=1.3--0", "-n"]
    assert "--target-platform" in cmd
    assert cmd[cmd.index("--target-platform") + 1] == "linux/arm64"


def test_purge_image_uses_platform_suffix(monkeypatch):
    commands = []
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    docker_utils.purgeImage("biocontainers", "samtools=1.3--0", "linux/arm64")

    assert commands == [
        ["docker", "rmi", "quay.io/biocontainers/samtools:1.3--0-arm64"]
    ]
