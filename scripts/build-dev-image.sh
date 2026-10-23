#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform="${1:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}"
case "$platform" in
linux/amd64) base_image=quay.io/condaforge/linux-anvil-cos7-x86_64 ;;
linux/arm64) base_image=quay.io/condaforge/linux-anvil-aarch64 ;;
*)
    echo "Unsupported build platform: $platform. Use linux/amd64 or linux/arm64." >&2
    exit 1
    ;;
esac

cd -- "$repo_root"
version="$(python -c 'from bioconda_utils import __version__; print(__version__)')"
image="$(python -c 'import sys; from bioconda_utils.cli import extract_stable_version; print("quay.io/bioconda/bioconda-utils-build-env-cos7:" + extract_stable_version(sys.argv[1]))' "$version")"

docker build --platform "$platform" \
    --build-arg "BASE_IMAGE=$base_image" \
    --build-arg "BIOCONDA_UTILS_VERSION=$version" \
    --tag "$image" \
    "$repo_root"
