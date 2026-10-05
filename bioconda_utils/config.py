"""
Loading, validation, and normalization of bioconda-utils configuration.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from jsonschema import validate
from ruamel.yaml import YAML

from bioconda_utils._types import DEFAULT_PRIMARY_PLATFORMS, Config, PackageSubdir
from bioconda_utils.conda.repodata import RepoData


def validate_config(config: dict[str, Any]) -> None:
    """Validate a parsed configuration against the packaged schema."""
    # Load packaged schema without pkg_resources (deprecated)
    # files('bioconda_utils') returns a Traversable to the package contents
    with (
        as_file(files("bioconda_utils") / "config.schema.yaml") as schema_path,
        open(schema_path, encoding="utf-8") as fh,
    ):
        schema = YAML(typ="safe").load(fh)

    validate(config, schema)


def normalize_config(config: dict[str, Any], *, base_dir: Path | None = None) -> Config:
    """Validate and apply defaults without mutating parsed configuration data.

    Blacklist entries are file paths and are converted to :class:`Path` here so
    that consumers can rely on the type. Relative entries are resolved against
    **base_dir** when given -- the directory holding the config file -- and
    otherwise left as-is for the caller to interpret relative to the working
    directory.
    """
    if isinstance(config, Config):
        return config

    validate_config(config)

    def get_list(key):
        # always return empty list, also if NoneType is defined in yaml
        value = config.get(key)
        if value is None:
            return []
        return value

    default_config = Config(
        {
            "blacklists": [],
            "channels": ["conda-forge", "bioconda"],
            "requirements": None,
            "upload_channel": "bioconda",
            "primary_platforms": list(DEFAULT_PRIMARY_PLATFORMS),
        }
    )
    default_config.update(config)
    if "blacklists" in config:
        default_config["blacklists"] = [
            base_dir / item if base_dir is not None else Path(item)
            for item in get_list("blacklists")
        ]
    if "channels" in config:
        default_config["channels"] = list(get_list("channels"))
    if "primary_platforms" in config:
        default_config["primary_platforms"] = [
            PackageSubdir(p) for p in get_list("primary_platforms")
        ]

    return default_config


def load_config(path: Path) -> Config:
    """Load and normalize a YAML configuration file."""
    with path.open(encoding="utf-8") as fh:
        config = YAML(typ="safe").load(fh)
    config = normalize_config(config, base_dir=path.parent)
    RepoData.register_config(config)
    return config
