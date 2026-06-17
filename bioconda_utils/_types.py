from __future__ import annotations

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


class RecipeMetaLike(Protocol):
    def get_value(self, key: str, default: Any = None) -> Any: ...
