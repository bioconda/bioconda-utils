from pathlib import Path
from typing import Any

from .._types import RecipePath
import rattler_build as rb
from .rattler_build_bridge import render_rattler_recipe


def get_package_paths(
    recipe: RecipePath, rattler_output_dir: Path, global_variants: rb.VariantConfig
) -> list[Path]:
    result: list[Path] = []
    # get rendered recipe
    variants: list[rb.RenderedVariant] = render_rattler_recipe(
        recipe.path, global_variants
    )

    for variant in variants:
        pass
        name: str = variant.recipe.package.name
        version: str = variant.recipe.package.version
        build_str: str = variant.recipe.build.string
        noarch: Any | None = variant.recipe.build.noarch
        target_platform: str | None = variant.recipe.used_variant.get("target_platform")
        if not target_platform:
            raise ValueError(
                f"Couldn't find target platform for a variant of recipe: {recipe.path.as_posix()}"
            )

        # predict package file names
        # can it also be tar.gz?
        ext: str = "conda"
        file_name: str = f"{name}-{version}-{build_str}.{ext}"

        # predict directory
        target_dir: Path = Path()
        if noarch:
            target_dir = rattler_output_dir / "noarch"
        else:
            target_dir = rattler_output_dir / target_platform
        result.append(target_dir / file_name)

    return result
