from __future__ import annotations

import platform
import typing
from typing import Any, Literal, Protocol, TypeAlias, cast


ContainerPlatform: TypeAlias = Literal["linux/amd64", "linux/arm64", "linux/riscv64"]
# get_args() preserves the runtime values but not their Literal type.
CONTAINER_PLATFORMS: tuple[ContainerPlatform, ...] = cast(
    tuple[ContainerPlatform, ...],
    typing.get_args(ContainerPlatform),
)


def docker_platform_tag_suffix(target_platform: ContainerPlatform | None) -> str | None:
    if target_platform in (None, "linux/amd64"):
        return None
    return target_platform.removeprefix("linux/").replace("/", "-")


def container_platform_is_native(target_platform: ContainerPlatform | None) -> bool:
    """Return True if target_platform matches the host's native architecture.

    The pre-solved mulled test path runs ``conda create --dry-run`` on the host,
    resolving packages for the host's architecture. It can only be safely used
    when the target platform matches the host (or is unspecified).
    """
    if target_platform is None:
        return True
    arch = platform.machine()
    if target_platform == "linux/amd64":
        return arch in ("x86_64", "amd64")
    if target_platform == "linux/arm64":
        return arch in ("aarch64", "arm64")
    if target_platform == "linux/riscv64":
        return arch == "riscv64"
    return False


class RecipeMetaLike(Protocol):
    def get_value(self, key: str, default: Any = None) -> Any: ...
