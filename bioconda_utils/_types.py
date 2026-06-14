from __future__ import annotations

import typing
from typing import Any, Literal, Protocol, TypeAlias


ContainerPlatform: TypeAlias = Literal["linux/amd64", "linux/arm64", "linux/riscv64"]
CONTAINER_PLATFORMS: list[str] = list(typing.get_args(ContainerPlatform))


class RecipeMetaLike(Protocol):
    def get_value(self, key: str, default: Any = None) -> Any: ...
