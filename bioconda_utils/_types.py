from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias


ContainerPlatform: TypeAlias = Literal["linux/amd64", "linux/arm64"]


class RecipeMetaLike(Protocol):
    def get_value(self, key: str, default: Any = None) -> Any: ...
