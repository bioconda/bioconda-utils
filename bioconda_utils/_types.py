from __future__ import annotations

import platform
import typing
from typing import Any, Literal, NamedTuple, NewType, Protocol, TypeAlias, cast


ContainerPlatform: TypeAlias = Literal["linux/amd64", "linux/arm64", "linux/riscv64"]
# get_args() preserves the runtime values but not their Literal type.
CONTAINER_PLATFORMS: tuple[ContainerPlatform, ...] = cast(
    tuple[ContainerPlatform, ...],
    typing.get_args(ContainerPlatform),
)
PackagePlatform: TypeAlias = Literal["linux-64", "linux-aarch64", "osx-64", "osx-arm64"]
PACKAGE_PLATFORMS: tuple[PackagePlatform, ...] = cast(
    tuple[PackagePlatform, ...],
    typing.get_args(PackagePlatform),
)
QuayUploadTarget = NewType("QuayUploadTarget", str)


def parse_quay_upload_target(value: str | None) -> QuayUploadTarget | None:
    """Validate the quay.io namespace used for mulled image uploads."""
    if value is None:
        return None
    if not value or value != value.strip() or "/" in value or ":" in value:
        raise ValueError(
            f"--quay-upload-target must be a single quay.io namespace, not {value!r}"
        )
    return QuayUploadTarget(value)


def docker_platform_tag_suffix(target_platform: ContainerPlatform | None) -> str | None:
    """Suffix mulled-build appends to an image tag, mirroring galaxy's rule.

    MUST stay in sync with
    ``galaxy.tool_util.deps.mulled.mulled_build.docker_platform_tag_suffix``
    (and ``apply_platform_tag_suffix``) in the ``galaxy-tool-util`` package:
    amd64 is left unsuffixed, every other architecture gets ``-<arch>``.
    ``mulled_upload`` relies on this so its ``docker-daemon:`` source ref
    matches the tag mulled-build actually produces locally. If galaxy ever
    changes its suffix rule, this function must change with it.
    """
    if target_platform is None or target_platform == "linux/amd64":
        return None
    return target_platform.removeprefix("linux/").replace("/", "-")


def docker_platform_staging_suffix(target_platform: ContainerPlatform) -> str:
    """Return the *always-suffixed* tag used for per-arch registry staging.

    Unlike :func:`docker_platform_tag_suffix` (which mirrors mulled-build and
    leaves amd64 unsuffixed), this always appends a suffix -- including
    ``-amd64`` -- so every architecture gets a distinct registry tag that
    ``docker buildx imagetools create`` can assemble into a multi-platform
    manifest at the unsuffixed canonical tag. Nothing in ``galaxy-tool-util``
    produces these tags; they are a bioconda-utils convention used only by
    :func:`bioconda_utils.container_manifests.platform_ref`.
    """
    return target_platform.removeprefix("linux/").replace("/", "-")


def native_container_platform() -> ContainerPlatform:
    """Return the supported Linux container platform matching this host."""
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "linux/amd64"
    if arch in ("aarch64", "arm64"):
        return "linux/arm64"
    if arch == "riscv64":
        return "linux/riscv64"
    raise ValueError(f"Unsupported native container architecture: {arch}")


def container_platform_is_native(target_platform: ContainerPlatform | None) -> bool:
    """Return True if target_platform matches the host's native architecture.

    The pre-solved mulled test path runs ``conda create --dry-run`` on the host,
    resolving packages for the host's architecture. It can only be safely used
    when the target platform matches the host (or is unspecified).
    """
    if target_platform is None:
        return True
    return target_platform == native_container_platform()


class PkgBuildRef(NamedTuple):
    """A built package identity: name, version, build string.

    Produced from a ``.tar.bz2`` or ``.conda`` package filename by
    extracting the name, version, and build string.  Can be stringified
    back to the standard ``name=version--build_string`` form via ``str()``.
    """

    name: str
    version: str
    build_string: str

    def __str__(self) -> str:
        return f"{self.name}={self.version}--{self.build_string}"


class RecipeMetaLike(Protocol):
    def get_value(self, key: str, default: Any = None) -> Any: ...
