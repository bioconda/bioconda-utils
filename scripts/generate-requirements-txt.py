#!/usr/bin/env python3
"""Generate bioconda_utils/bioconda_utils-requirements.txt from pixi.toml.

This script reads the [dependencies] section of pixi.toml and writes
the conda-compatible requirements file that ships with the package
and is used at runtime by docker_utils.py and Dockerfiles.

Usage:
    python scripts/generate-requirements-txt.py
    python scripts/generate-requirements-txt.py --check
"""

import argparse
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PIXI_TOML = REPO_ROOT / "pixi.toml"
REQUIREMENTS_TXT = REPO_ROOT / "bioconda_utils" / "bioconda_utils-requirements.txt"
SUPPORTED_DICT_KEYS = {"version", "build"}
SPEC_OPERATORS = ("==", ">=", "<=", ">", "<", "~=", "!=")


def read_pixi_deps():
    if not PIXI_TOML.exists():
        raise FileNotFoundError(f"{PIXI_TOML} does not exist")

    with open(PIXI_TOML, "rb") as f:
        data = tomllib.load(f)

    deps = data.get("dependencies")
    if not isinstance(deps, dict) or not deps:
        raise ValueError(f"{PIXI_TOML} must define a non-empty [dependencies] table")

    # Preserve insertion order from the TOML file. This intentionally exports
    # only runtime dependencies, not feature/dev/test dependencies or lockfile
    # resolved transitive packages.
    return deps


def format_dep(pkg, ver):
    """Convert a pixi dependency entry to conda --file format.

    Handles both simple strings (``python = "3.13.*"``) and
    the expanded form with build-string constraints
    (``libblas = { version = "*", build = "*openblas" }``).
    """
    if not isinstance(pkg, str) or not pkg:
        raise ValueError(f"Invalid dependency name: {pkg!r}")

    if isinstance(ver, dict):
        unknown_keys = set(ver) - SUPPORTED_DICT_KEYS
        if unknown_keys:
            keys = ", ".join(sorted(unknown_keys))
            raise ValueError(
                f"Unsupported pixi dependency keys for {pkg!r}: {keys}. "
                "Only 'version' and 'build' can be exported to conda --file format."
            )

        v = ver.get("version", "*")
        b = ver.get("build")
        if not isinstance(v, str) or not v:
            raise ValueError(f"Unsupported version value for {pkg!r}: {v!r}")
        if b is not None and (not isinstance(b, str) or not b):
            raise ValueError(f"Unsupported build value for {pkg!r}: {b!r}")
        if b:
            return f"{pkg}={v}={b}"
        return f"{pkg}={v}" if v != "*" else pkg

    if not isinstance(ver, str):
        raise ValueError(f"Unsupported dependency specification for {pkg!r}: {ver!r}")

    ver = str(ver)
    if ver == "*":
        return pkg
    sep = "" if ver.startswith(SPEC_OPERATORS) else "="
    return f"{pkg}{sep}{ver}"


def generate():
    deps = read_pixi_deps()

    lines = [
        "# auto-generated from pixi.toml -- do not edit directly",
        "# runtime [dependencies] only; feature/dev/test dependencies are excluded",
        "# See https://github.com/bioconda/bioconda-utils/blob/main/pixi.toml",
        "",
    ]

    for pkg in deps:
        lines.append(format_dep(pkg, deps[pkg]))

    lines.append("")
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the generated requirements file is out of date",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    content = generate()
    if args.check:
        existing = REQUIREMENTS_TXT.read_text()
        if existing != content:
            print(
                f"ERROR: {REQUIREMENTS_TXT} is out of date. "
                "Run `pixi run regenerate-requirements` to update."
            )
            raise SystemExit(1)
        print(f"{REQUIREMENTS_TXT} is up to date")
    else:
        REQUIREMENTS_TXT.write_text(content)
        print(f"Regenerated {REQUIREMENTS_TXT}")


if __name__ == "__main__":
    main()
