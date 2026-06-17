#!/usr/bin/env python3
"""Generate bioconda_utils/bioconda_utils-requirements.txt from pixi.toml.

This script reads the [dependencies] section of pixi.toml and writes
the conda-compatible requirements file that ships with the package
and is used at runtime by docker_utils.py and Dockerfiles.

Usage:
    python scripts/generate-requirements-txt.py
    python scripts/generate-requirements-txt.py --check
"""
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PIXI_TOML = REPO_ROOT / "pixi.toml"
REQUIREMENTS_TXT = REPO_ROOT / "bioconda_utils" / "bioconda_utils-requirements.txt"

# Ordering group headers to insert before certain packages for readability.
ORDERED_GROUPS = [
    ("conda-forge-pinning", "\n# pinnings"),
    ("python", "\n# basics"),
    ("argh", ""),
    ("anaconda-client", ""),
    ("regex", "\n# hosters - special regex not supported by RE"),
    ("aiohttp", "\n# asyncio"),
    ("gitpython", "\n# client API wrappers"),
    ("beautifulsoup4", "\n# bioconductor-skeleton"),
    ("requests", ""),
    ("pygithub", "\n# merge handling"),
    ("diskcache", "\n# caching"),
    ("tabulate", "\n# build failure output"),
    ("psutil", "\n# resource reporting for builds"),
]

def make_group_map():
    """Build dict: package → header comment."""
    m = {}
    for pkg, header in ORDERED_GROUPS:
        m[pkg] = header
    return m


def read_pixi_deps():
    with open(PIXI_TOML, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("dependencies", {})
    # Preserve insertion order from the TOML file
    return deps


def format_dep(pkg, ver):
    """Convert a pixi dependency entry to conda --file format.

    Handles both simple strings (``python = "3.13.*"``) and
    the expanded form with build-string constraints
    (``libblas = { version = "*", build = "*openblas" }``).
    """
    if isinstance(ver, dict):
        v = ver.get("version", "*")
        b = ver.get("build")
        if b:
            return f"{pkg}={v}={b}"
        return f"{pkg}={v}" if v != "*" else pkg

    ver = str(ver)
    if ver == "*":
        return pkg
    sep = "" if ver.startswith(("==", ">=", "<=", ">", "<", "~=", "!=")) else "="
    return f"{pkg}{sep}{ver}"


def generate():
    deps = read_pixi_deps()
    group_map = make_group_map()

    lines = [
        "# auto-generated from pixi.toml -- do not edit directly",
        "# See https://github.com/bioconda/bioconda-utils/blob/main/pixi.toml",
        "",
    ]

    pkg_order = list(deps.keys())

    for i, pkg in enumerate(pkg_order):
        header = group_map.get(pkg)
        if header:
            lines.append(header)
        lines.append(format_dep(pkg, deps[pkg]))

    lines.append("")
    return "\n".join(lines)


def main():
    content = generate()
    if "--check" in sys.argv:
        existing = REQUIREMENTS_TXT.read_text()
        if existing != content:
            print(
                f"ERROR: {REQUIREMENTS_TXT} is out of date. "
                "Run `pixi run regenerate-requirements` to update."
            )
            sys.exit(1)
        print(f"{REQUIREMENTS_TXT} is up to date")
    else:
        REQUIREMENTS_TXT.write_text(content)
        print(f"Regenerated {REQUIREMENTS_TXT}")


if __name__ == "__main__":
    main()
