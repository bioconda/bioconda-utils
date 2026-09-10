import json
import logging
import os
import subprocess as sp
from unittest.mock import Mock

import pytest

from bioconda_utils import _types, build, cli, docker_utils, pkg_test, upload, utils
from bioconda_utils._types import ContainerPlatform, PackageSubdir, PkgBuildRef

SAMTOOLS_1_3_0 = PkgBuildRef(name="samtools", version="1.3", build_string="0")
BIOCONTAINERS = _types.QuayUploadTarget("biocontainers")


def test_container_platform_maps_to_package_subdir():
    assert (
        _types.container_platform_to_package_subdir(ContainerPlatform.LINUX_AMD64)
        == PackageSubdir.LINUX_64
    )
    assert (
        _types.container_platform_to_package_subdir(ContainerPlatform.LINUX_ARM64)
        == PackageSubdir.LINUX_AARCH64
    )
    assert (
        _types.container_platform_to_package_subdir(ContainerPlatform.LINUX_RISCV64)
        == PackageSubdir.LINUX_RISCV64
    )


def test_osx_package_subdir_has_no_container_platform():
    with pytest.raises(ValueError, match="cannot be installed in Linux"):
        _types.package_subdir_to_container_platform(PackageSubdir.OSX_64)


def test_docker_build_script_creates_supported_linux_channel_subdirs():
    builder = Mock(
        container_staging="/opt/host-conda-bld",
        conda_build_args="",
        container_recipe="/opt/recipe",
        user_info={"uid": 1000, "gid": 100},
    )
    publish_built_packages = docker_utils.PUBLISH_BUILT_PACKAGES_TEMPLATE.format_map(
        {
            "self": builder,
            "local_channel_subdirs": docker_utils.LOCAL_CHANNEL_SUBDIR_ARGS,
        }
    )
    script = docker_utils.BUILD_SCRIPT_TEMPLATE.format_map(
        {
            "self": builder,
            "arch": PackageSubdir.LINUX_RISCV64,
            "local_channel_mkdirs": docker_utils.LOCAL_CHANNEL_MKDIRS,
            "publish_built_packages": publish_built_packages,
        }
    )

    assert 'mkdir -p "${local_channel}"/linux-64' in script
    assert 'mkdir -p "${local_channel}"/linux-aarch64' in script
    assert 'mkdir -p "${local_channel}"/linux-riscv64' in script
    assert 'mkdir -p "${local_channel}"/noarch' in script
    assert "build_output=$(mktemp -d /opt/conda/bioconda-output.XXXXXX)" in script
    assert '--output-folder "${build_output}"' in script
    sp.run(["bash", "-n"], input=script, text=True, check=True)


def test_publish_built_packages_preserves_channel_subdirs(tmp_path):
    build_output = tmp_path / "build-output"
    staging = tmp_path / "staging"
    for subdir in docker_utils.LOCAL_CHANNEL_SUBDIRS:
        (build_output / subdir).mkdir(parents=True)
        (staging / subdir).mkdir(parents=True)

    arm_package = build_output / "linux-aarch64" / "tool-1.0-0.conda"
    noarch_package = build_output / "noarch" / "tool-data-1.0-0.tar.bz2"
    nested_package = build_output / "linux-aarch64" / "work" / "nested.conda"
    unsupported_package = build_output / "osx-arm64" / "unexpected.conda"
    arm_package.write_bytes(b"arm")
    noarch_package.write_bytes(b"noarch")
    nested_package.parent.mkdir()
    nested_package.write_bytes(b"nested")
    unsupported_package.parent.mkdir()
    unsupported_package.write_bytes(b"osx")
    (build_output / "linux-aarch64" / "repodata.json").write_text("{}")

    builder = Mock(
        container_staging=str(staging),
        user_info={"uid": os.getuid(), "gid": os.getgid()},
    )
    publish_script = docker_utils.PUBLISH_BUILT_PACKAGES_TEMPLATE.format_map(
        {
            "self": builder,
            "local_channel_subdirs": docker_utils.LOCAL_CHANNEL_SUBDIR_ARGS,
        }
    )
    sp.run(
        ["bash", "-c", f'build_output="$1"\n{publish_script}', "bash", build_output],
        check=True,
    )

    assert (staging / "linux-aarch64" / arm_package.name).read_bytes() == b"arm"
    assert (staging / "noarch" / noarch_package.name).read_bytes() == b"noarch"
    assert not (staging / "linux-aarch64" / nested_package.name).exists()
    assert not (staging / "osx-arm64").exists()
    assert not (staging / "linux-aarch64" / "repodata.json").exists()


def test_recipe_builder_legacy_arch_uses_target_platform():
    builder = Mock(target_platform=ContainerPlatform.LINUX_ARM64)

    assert (
        docker_utils.RecipeBuilder._output_subdir(builder, noarch=False)
        == PackageSubdir.LINUX_AARCH64
    )
    assert docker_utils.RecipeBuilder._output_subdir(builder, noarch=True) == "noarch"


def test_publish_built_packages_uses_host_uid_and_gid():
    builder = Mock(
        container_staging="/opt/host-conda-bld",
        user_info={"uid": 123, "gid": 456},
    )
    script = docker_utils.PUBLISH_BUILT_PACKAGES_TEMPLATE.format_map(
        {
            "self": builder,
            "local_channel_subdirs": docker_utils.LOCAL_CHANNEL_SUBDIR_ARGS,
        }
    )

    assert 'chown 123:456 "${destination}"' in script


def test_docker_platform_tag_suffix_matches_mulled_build_convention(monkeypatch):
    monkeypatch.setattr(_types.platform, "machine", lambda: "x86_64")
    assert _types.docker_platform_tag_suffix(None) is None
    assert _types.docker_platform_tag_suffix(ContainerPlatform.LINUX_AMD64) is None
    assert _types.docker_platform_tag_suffix(ContainerPlatform.LINUX_ARM64) == "arm64"
    assert (
        _types.docker_platform_tag_suffix(ContainerPlatform.LINUX_RISCV64) == "riscv64"
    )

    monkeypatch.setattr(_types.platform, "machine", lambda: "aarch64")
    assert _types.docker_platform_tag_suffix(None) == "arm64"


def test_handle_merged_pr_linux_fallback_uses_docker(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("channels: []\n", encoding="utf-8")
    build_calls = []

    monkeypatch.setattr(
        cli,
        "upload_pr_artifacts",
        lambda *_args, **_kwargs: cli.UploadResult.NO_ARTIFACTS,
    )
    monkeypatch.setattr(
        cli,
        "build",
        lambda *_args, **kwargs: build_calls.append(kwargs) or True,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.handle_merged_pr(
            recipe_folder=recipe_folder,
            config=config,
            repo="bioconda/bioconda-recipes",
            git_range="base...head",
            package_platform=PackageSubdir.LINUX_AARCH64,
        )

    assert exc_info.value.code == 0
    assert len(build_calls) == 1
    assert build_calls[0]["docker"] is True
    assert build_calls[0]["platform"] == PackageSubdir.LINUX_AARCH64
    assert "container_platform" not in build_calls[0]


def test_handle_merged_pr_native_macos_fallback_uses_host(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("channels: []\n", encoding="utf-8")
    build_calls = []

    monkeypatch.setattr(
        cli.utils.RepoData, "native_subdir", lambda: PackageSubdir.OSX_ARM64
    )
    monkeypatch.setattr(
        cli,
        "upload_pr_artifacts",
        lambda *_args, **_kwargs: cli.UploadResult.NO_ARTIFACTS,
    )
    monkeypatch.setattr(
        cli,
        "build",
        lambda *_args, **kwargs: build_calls.append(kwargs) or True,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.handle_merged_pr(
            recipe_folder=recipe_folder,
            config=config,
            repo="bioconda/bioconda-recipes",
            git_range="base...head",
            package_platform=PackageSubdir.OSX_ARM64,
        )

    assert exc_info.value.code == 0
    assert len(build_calls) == 1
    assert build_calls[0]["docker"] is False
    assert build_calls[0]["platform"] is None
    assert "container_platform" not in build_calls[0]


def test_handle_merged_pr_rejects_foreign_macos_fallback(monkeypatch, tmp_path):
    recipe_folder = tmp_path / "recipes"
    recipe_folder.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("channels: []\n", encoding="utf-8")

    monkeypatch.setattr(
        cli.utils.RepoData, "native_subdir", lambda: PackageSubdir.OSX_64
    )
    monkeypatch.setattr(
        cli,
        "upload_pr_artifacts",
        lambda *_args, **_kwargs: cli.UploadResult.NO_ARTIFACTS,
    )
    monkeypatch.setattr(
        cli,
        "build",
        lambda *_args, **_kwargs: pytest.fail("build should not start"),
    )

    with pytest.raises(ValueError, match="cannot build non-native macOS"):
        cli.handle_merged_pr(
            recipe_folder=recipe_folder,
            config=config,
            repo="bioconda/bioconda-recipes",
            git_range="base...head",
            package_platform=PackageSubdir.OSX_ARM64,
        )


def test_mulled_image_metadata_records_target_platform():
    image = build.mulled_image_metadata(SAMTOOLS_1_3_0, ContainerPlatform.LINUX_ARM64)
    assert image.pkg_ref == SAMTOOLS_1_3_0
    assert image.target_platform == ContainerPlatform.LINUX_ARM64


def test_mulled_image_metadata_records_native_platform(monkeypatch):
    monkeypatch.setattr(_types.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        build, "native_container_platform", _types.native_container_platform
    )
    image = build.mulled_image_metadata(SAMTOOLS_1_3_0)
    assert image.target_platform == ContainerPlatform.LINUX_ARM64


def test_test_package_passes_target_platform(monkeypatch, tmp_path):
    package = tmp_path / "conda-bld" / PackageSubdir.LINUX_64 / "samtools-1.3-0.tar.bz2"
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

    pkg_test.build_and_test_mulled_image(
        str(package),
        target_platform=ContainerPlatform.LINUX_ARM64,
    )

    assert commands
    cmd = commands[0]
    assert cmd[0:2] == ["mulled-build", "build-and-test"]
    assert "--target-platform" in cmd
    assert cmd[cmd.index("--target-platform") + 1] == ContainerPlatform.LINUX_ARM64


def test_recipe_builder_build_image_passes_target_platform(monkeypatch, tmp_path):
    commands = []
    builder = docker_utils.RecipeBuilder.__new__(docker_utils.RecipeBuilder)
    builder.image_build_dir = str(tmp_path)
    builder.requirements = None
    builder.docker_temp_image = "tmp-bioconda-builder"
    builder.docker_base_image = "quay.io/bioconda/build-env:latest"
    builder.dockerfile_template = "FROM {docker_base_image}\n{proxies}\n"
    builder.target_platform = ContainerPlatform.LINUX_ARM64
    builder.build_image = True
    builder.keep_image = True

    monkeypatch.setattr(
        docker_utils.sp,
        "check_output",
        lambda _cmd: b"Docker version 24.0.0, build 0000000",
    )
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    builder._build_image()

    assert commands
    assert commands[0][0:3] == ["docker", "build", "--platform"]
    assert commands[0][3] == ContainerPlatform.LINUX_ARM64


def test_recipe_builder_reuses_matching_local_base_image(monkeypatch):
    builder = docker_utils.RecipeBuilder.__new__(docker_utils.RecipeBuilder)
    builder.docker_base_image = "local-build-env:arm64"
    builder.target_platform = ContainerPlatform.LINUX_ARM64
    builder.build_image = False
    builder.keep_image = False
    pulls = []

    monkeypatch.setattr(
        docker_utils.sp,
        "run",
        lambda *_args, **_kwargs: Mock(returncode=0, stdout="linux/arm64\n"),
    )
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda command, **_kwargs: pulls.append(command),
    )

    builder._ensure_base_image()

    assert pulls == []


def test_recipe_builder_pulls_missing_base_image_for_target(monkeypatch):
    builder = docker_utils.RecipeBuilder.__new__(docker_utils.RecipeBuilder)
    builder.docker_base_image = "quay.io/bioconda/build-env:latest"
    builder.target_platform = ContainerPlatform.LINUX_ARM64
    builder.build_image = False
    builder.keep_image = False
    pulls = []

    monkeypatch.setattr(
        docker_utils.sp,
        "run",
        lambda *_args, **_kwargs: Mock(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda command, **_kwargs: pulls.append(command),
    )

    builder._ensure_base_image()

    assert pulls == [
        [
            "docker",
            "pull",
            "--platform",
            ContainerPlatform.LINUX_ARM64,
            "quay.io/bioconda/build-env:latest",
        ]
    ]


def test_recipe_builder_fails_when_base_image_cannot_be_pulled(monkeypatch):
    builder = docker_utils.RecipeBuilder.__new__(docker_utils.RecipeBuilder)
    builder.docker_base_image = "missing-build-env:arm64"
    builder.target_platform = ContainerPlatform.LINUX_ARM64
    builder.build_image = False
    builder.keep_image = False

    monkeypatch.setattr(
        docker_utils.sp,
        "run",
        lambda *_args, **_kwargs: Mock(returncode=1, stdout=""),
    )

    def fail_pull(command, **_kwargs):
        raise sp.CalledProcessError(1, command)

    monkeypatch.setattr(docker_utils.utils, "run", fail_pull)

    with pytest.raises(sp.CalledProcessError):
        builder._ensure_base_image()


def test_mulled_upload_passes_target_platform(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)

    def run(cmd, **_kwargs):
        commands.append(cmd)
        if "--config" in cmd:
            return type(
                "R",
                (),
                {
                    "stdout": json.dumps(
                        {
                            "os": "linux",
                            "architecture": "arm64",
                            "variant": "v8",
                        }
                    )
                },
            )()
        return type("R", (), {"stdout": "sha256:" + "a" * 64})()

    monkeypatch.setattr(
        upload.utils,
        "run",
        run,
    )

    record = upload.mulled_upload(
        SAMTOOLS_1_3_0, BIOCONTAINERS, ContainerPlatform.LINUX_ARM64
    )

    ref = "quay.io/biocontainers/samtools:1.3--0-arm64"
    assert any(ref in arg for arg in commands[1])
    assert record.platform_ref == ref
    assert record.digest == "sha256:" + "a" * 64


def test_mulled_upload_stages_amd64_under_suffixed_tag(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)

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

    upload.mulled_upload(SAMTOOLS_1_3_0, BIOCONTAINERS, ContainerPlatform.LINUX_AMD64)

    assert "quay.io/biocontainers/samtools:1.3--0-amd64" in " ".join(commands[1])


def test_mulled_upload_rejects_wrong_source_platform(monkeypatch):
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)
    monkeypatch.setattr(
        upload.utils,
        "run",
        lambda _cmd, **_kwargs: type(
            "R", (), {"stdout": json.dumps({"os": "linux", "architecture": "amd64"})}
        )(),
    )

    with pytest.raises(RuntimeError, match="Image platform mismatch"):
        upload.mulled_upload(
            SAMTOOLS_1_3_0, BIOCONTAINERS, ContainerPlatform.LINUX_ARM64
        )


def test_upload_mulled_image_source_records_destination_digest(monkeypatch):
    commands = []
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)

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
        ContainerPlatform.LINUX_ARM64,
    )

    assert commands[1][0:4] == ["skopeo", "--command-timeout", "600s", "copy"]
    assert commands[2][0:4] == ["skopeo", "inspect", "--format", "{{.Digest}}"]
    assert commands[2][-1] == "docker://quay.io/biocontainers/samtools:1.3--0-arm64"
    assert record.digest == "sha256:" + "d" * 64


def test_upload_mulled_image_source_requires_registry_auth_by_default(monkeypatch):
    monkeypatch.delenv("QUAY_LOGIN", raising=False)
    monkeypatch.delenv("QUAY_OAUTH_TOKEN", raising=False)

    with pytest.raises(ValueError, match="--use-existing-auth"):
        upload.upload_mulled_image_source(
            "docker-archive:/tmp/samtools.tar.gz",
            "quay.io/biocontainers/samtools:1.3--0",
            ContainerPlatform.LINUX_ARM64,
        )


def test_upload_mulled_image_source_can_use_ambient_registry_auth(monkeypatch):
    commands = []
    monkeypatch.delenv("QUAY_LOGIN", raising=False)
    monkeypatch.delenv("QUAY_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)

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

    upload.upload_mulled_image_source(
        "docker-archive:/tmp/samtools.tar.gz",
        "quay.io/biocontainers/samtools:1.3--0",
        ContainerPlatform.LINUX_ARM64,
        use_existing_auth=True,
    )

    assert "--dest-creds" not in commands[1]
    assert "--creds" not in commands[2]


def test_purge_image_removes_biocontainers_local_image(monkeypatch):
    commands = []
    monkeypatch.setattr(
        docker_utils.utils,
        "run",
        lambda cmd, **_kwargs: commands.append(cmd),
    )

    # purgeImage must target the canonical biocontainers local image that
    # mulled-build produced -- NOT the upload target namespace. Regression test
    # for a crash where it ran `docker rmi quay.io/<upload-target>/...` against
    # an image that was never tagged locally, raising CalledProcessError and
    # aborting the build after a successful upload.
    docker_utils.purgeImage(SAMTOOLS_1_3_0, ContainerPlatform.LINUX_ARM64)
    docker_utils.purgeImage(SAMTOOLS_1_3_0, ContainerPlatform.LINUX_AMD64)

    assert commands == [
        ["docker", "rmi", "quay.io/biocontainers/samtools:1.3--0-arm64"],
        ["docker", "rmi", "quay.io/biocontainers/samtools:1.3--0"],
    ]


def test_mulled_upload_sources_local_image_from_biocontainers(monkeypatch):
    """mulled_upload must copy from the biocontainers local image regardless of
    the upload target namespace, because mulled-build always tags the local
    image as biocontainers. Guards the same namespace split that broke
    purgeImage: the destination is target-namespaced, but the source is not."""
    monkeypatch.setenv("QUAY_LOGIN", "user:token")
    monkeypatch.setattr(upload.utils, "skopeo_env", dict)

    sources = []

    def run(cmd, **_kwargs):
        if "copy" in cmd:
            sources.append(cmd[cmd.index("copy") + 1])
        if "--config" in cmd:
            return type(
                "R",
                (),
                {"stdout": json.dumps({"os": "linux", "architecture": "amd64"})},
            )()
        return type("R", (), {"stdout": "sha256:" + "a" * 64})()

    monkeypatch.setattr(upload.utils, "run", run)

    # Upload to a NON-biocontainers target: the destination is quay0-namespaced,
    # but the skopeo copy source must still be the biocontainers local image.
    upload.mulled_upload(
        SAMTOOLS_1_3_0,
        _types.QuayUploadTarget("quay0"),
        ContainerPlatform.LINUX_AMD64,
    )

    assert sources == ["docker-daemon:quay.io/biocontainers/samtools:1.3--0"]


def test_utils_run_logs_and_redacts_secrets(caplog):
    with caplog.at_level(logging.INFO):
        utils.run(["echo", "hello", "world"])
        assert "(COMMAND) echo hello world" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO):
        utils.run(["echo", "supersecret123", "public"], secrets=["supersecret123"])
        assert "(COMMAND) echo <hidden> public" in caplog.text
        assert "supersecret123" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO):
        # A bare string is a single secret, not a sequence of characters
        utils.run(["echo", "supersecret123", "public"], secrets="supersecret123")
        assert "(COMMAND) echo <hidden> public" in caplog.text
        assert "supersecret123" not in caplog.text


def test_utils_run_rejects_removed_redacted_secrets_kwarg():
    # The pre-`secrets` kwarg is gone; unknown kwargs are forwarded to Popen,
    # which rejects it instead of silently skipping redaction.
    with pytest.raises(TypeError):
        utils.run(["echo", "hello"], redacted_secrets=["s3cret"])
