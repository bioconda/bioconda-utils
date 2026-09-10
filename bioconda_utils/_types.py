from __future__ import annotations

import platform
from enum import StrEnum
from typing import (
    Any,
    Literal,
    NamedTuple,
    NewType,
    Protocol,
    TypeAlias,
    TypedDict,
)


class Config(dict[str, Any]):
    """Validated Bioconda configuration with defaults applied."""


#: Docker/OCI platform notation used by Docker, buildx, skopeo, registry
#: manifests, and mulled-build's ``--target-platform``.
class ContainerPlatform(StrEnum):
    """Supported Docker/OCI platforms."""

    LINUX_AMD64 = "linux/amd64"
    LINUX_ARM64 = "linux/arm64"
    LINUX_RISCV64 = "linux/riscv64"


ALL_CONTAINER_PLATFORMS: tuple[ContainerPlatform, ...] = tuple(ContainerPlatform)


#: Conda package subdir notation for *built* per-architecture packages. This
#: is the directory under a conda channel, for example ``linux-64`` or
#: ``linux-aarch64``. ``noarch`` is excluded because noarch packages have no
#: per-architecture artifact.
class PackageSubdir(StrEnum):
    """Conda subdirectories containing architecture-specific packages."""

    LINUX_64 = "linux-64"
    LINUX_AARCH64 = "linux-aarch64"
    LINUX_RISCV64 = "linux-riscv64"
    OSX_64 = "osx-64"
    OSX_ARM64 = "osx-arm64"


ALL_PACKAGE_SUBDIRS: tuple[PackageSubdir, ...] = tuple(PackageSubdir)
DEFAULT_PRIMARY_PLATFORMS: tuple[PackageSubdir, ...] = (
    PackageSubdir.LINUX_64,
    PackageSubdir.OSX_64,
)
#: Conda repodata subdir notation, including ``noarch``.
Subdir: TypeAlias = PackageSubdir | Literal["noarch"]
#: A two-part OS label -- the form conda-build's ``config.platform`` and its
#: ``DEFAULT_COMPILERS`` table require (see ``conda_build.variants``). This is
#: *not* a subdir: ``"linux-64"`` is not a valid ``OsLabel``.
OsLabel: TypeAlias = Literal["linux", "osx"]
QuayUploadTarget = NewType("QuayUploadTarget", str)

#: Architecture-equivalent Linux package/container platforms. Keep this as the
#: single source for converting between conda channel subdirs and Docker/OCI
#: platform strings. macOS package subdirs intentionally have no container
#: platform because mulled containers are Linux images.
PACKAGE_SUBDIR_TO_CONTAINER_PLATFORM: dict[PackageSubdir, ContainerPlatform] = {
    PackageSubdir.LINUX_64: ContainerPlatform.LINUX_AMD64,
    PackageSubdir.LINUX_AARCH64: ContainerPlatform.LINUX_ARM64,
    PackageSubdir.LINUX_RISCV64: ContainerPlatform.LINUX_RISCV64,
}
ALL_LINUX_PACKAGE_SUBDIRS: tuple[PackageSubdir, ...] = tuple(
    PACKAGE_SUBDIR_TO_CONTAINER_PLATFORM.keys()
)
CONTAINER_PLATFORM_TO_PACKAGE_SUBDIR: dict[ContainerPlatform, PackageSubdir] = {
    container_platform: package_subdir
    for package_subdir, container_platform in PACKAGE_SUBDIR_TO_CONTAINER_PLATFORM.items()
}


def package_subdir_to_container_platform(
    package_subdir: PackageSubdir,
) -> ContainerPlatform:
    """Return the Linux container platform that matches a conda package subdir."""
    try:
        return PACKAGE_SUBDIR_TO_CONTAINER_PLATFORM[package_subdir]
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"{package_subdir} packages cannot be installed in Linux mulled containers"
        ) from exc


def container_platform_to_package_subdir(
    container_platform: ContainerPlatform,
) -> PackageSubdir:
    """Return the conda package subdir matching a Linux container platform."""
    return CONTAINER_PLATFORM_TO_PACKAGE_SUBDIR[container_platform]


def normalize_container_platform(
    os_name: object,
    architecture: object,
    *,
    variant: object = None,
    ref: str = "",
) -> ContainerPlatform:
    """Return the supported Docker platform, ignoring OCI variant metadata.

    Registry descriptors and image configs may include a CPU variant field, for
    example ``linux/arm64/v8``. Internally bioconda-utils keys container images
    by Docker's two-part platform values from :data:`ContainerPlatform`.
    """
    if not isinstance(os_name, str) or not isinstance(architecture, str):
        location = f" for {ref}" if ref else ""
        raise TypeError(f"Image platform{location} has no OS/architecture")
    platform_value = f"{os_name}/{architecture}"
    try:
        return ContainerPlatform(platform_value)
    except ValueError as exc:
        suffix = f"/{variant}" if variant else ""
        location = f" for {ref}" if ref else ""
        raise RuntimeError(
            f"Unsupported container platform{location}: {platform_value}{suffix}"
        ) from exc


def parse_quay_upload_target(value: str | None) -> QuayUploadTarget | None:
    """Validate the quay.io namespace used for mulled image uploads."""
    if value is None:
        return None
    if not value or value != value.strip() or "/" in value or ":" in value:
        raise ValueError(
            f"--container-upload-target must be a single quay.io namespace, not {value!r}"
        )
    return QuayUploadTarget(value)


def docker_platform_tag_suffix(target_platform: ContainerPlatform | None) -> str | None:
    """Suffix mulled-build appends to a local image tag for a target platform.

    This mirrors Galaxy/mulled-build's current convention: amd64 is left
    unsuffixed, every other architecture gets ``-<arch>``, and ``None`` means
    the native Docker platform. ``mulled_upload`` relies on this so its
    ``docker-daemon:`` source ref matches the tag mulled-build actually
    produces locally. If Galaxy changes this convention, this function and its
    tests must change with it.
    """
    target_platform = target_platform or native_container_platform()
    if target_platform is ContainerPlatform.LINUX_AMD64:
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


#: Namespace under which bioconda-utils tells ``mulled-build`` to tag local
#: images (the ``-n`` argument). Local mulled images are always built under
#: this canonical namespace regardless of the upload target, so they match
#: production naming; :func:`bioconda_utils.upload.mulled_upload` re-namespaces
#: them on push. Every consumer of the local image ref -- ``pkg_test``'s
#: ``-n`` value, :func:`local_mulled_image_ref`, and the cleanup in
#: :func:`bioconda_utils.docker_utils.purgeImage` -- must agree on this.
MULLED_LOCAL_NAMESPACE = "biocontainers"


def local_mulled_image_ref(
    image: PkgBuildRef, target_platform: ContainerPlatform | None = None
) -> str:
    """Return the local Docker ref ``mulled-build`` produces for *image*.

    ``mulled-build build-and-test -n biocontainers`` tags the local image as
    ``quay.io/biocontainers/<name>:<version>--<build>`` for the native arch and
    appends ``-<arch>`` for every other target platform (see
    :func:`docker_platform_tag_suffix`). This is the single source of truth for
    that ref: the upload source (:func:`bioconda_utils.upload.mulled_upload`)
    and the post-upload cleanup (:func:`bioconda_utils.docker_utils.purgeImage`)
    both build it here so they can never disagree on the namespace.
    """
    tag = f"{image.version}--{image.build_string}"
    if suffix := docker_platform_tag_suffix(target_platform):
        tag = f"{tag}-{suffix}"
    return f"quay.io/{MULLED_LOCAL_NAMESPACE}/{image.name}:{tag}"


def native_container_platform() -> ContainerPlatform:
    """Return the supported Linux container platform matching this host."""
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return ContainerPlatform.LINUX_AMD64
    if arch in ("aarch64", "arm64"):
        return ContainerPlatform.LINUX_ARM64
    if arch == "riscv64":
        return ContainerPlatform.LINUX_RISCV64
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


class OCIImageConfig(TypedDict, total=False):
    """OCI image config JSON – the output of ``skopeo inspect --config``.

    Only the platform fields that bioconda-utils reads are declared here;
    the full spec (``created``, ``rootfs``, ``history``, …) is wider.
    See https://github.com/opencontainers/image-spec/blob/main/config.md
    """

    architecture: str
    os: str
    variant: str


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
