#!/bin/bash

# There are a lot of environment variables used here. This script aims to
# document them as well as provide a mechanism (e.g., in CI workflows) to show
# their current values.
#
# Typical usage:
#
#   source versions.sh
#   source env_var_inventory.sh
#
echo "--------BEGIN ENVIRONMENT VARIABLE INVENTORY ---------------------------------"
while read -r name description; do
    description="${description//\"/}"
    value="${!name:-}"
    echo -e "${name}\t${value}\t$description"
done <<'EOF' | column -t -s $'\t'
ARCHS "architectures to build images for"
DEBIAN_VERSION "version of debian for extended base image"
BUSYBOX_VERSION "version of busybox for base image"
BIOCONDA_UTILS_VERSION "version of bioconda-utils to use"
BASE_DEBIAN_IMAGE_NAME "name for debian image"
BASE_BUSYBOX_IMAGE_NAME "name for busybox image"
BUILD_ENV_IMAGE_NAME "name for build image"
CREATE_ENV_IMAGE_NAME "name for create image"
CURRENT_ARCH "Arch for current iteration of loop"
BASE_TAG "the base version tag to add to image tags"
BIOCONDA_IMAGE_TAG "full bioconda + image version"
BUILD_ENV_REGISTRY "where the build image should come from (used in CI)"
CREATE_ENV_REGISTRY "where the create image should come from (used in CI)"
DEFAULT_BASE_IMAGE "where the busybox image should come from (used in CI and mulled-build)"
DEST_BASE_REGISTRY "registry where the busybox image should go to (used in CI)"
DEST_BASE_IMAGE "fully qualified busybox image destination (used in CI)"
DEFAULT_EXTENDED_BASE_IMAGE "where the debian image should come from (used in CI and mulled-build)"
DEST_EXTENDED_BASE_REGISTRY "where the debian image should go to (used in CI)"
BUILD_ENV_IMAGE "fully qualified image for building (used in CI)"
CREATE_ENV_IMAGE "fully qualified image for creating (used in CI)"
BUILD_ARGS "full build arguments passed to podman, typically created by <image>/prepare.sh"
busybox_image "initial busybox image, created by images/base-glibc-busybox-bash/prepare.sh"
IMAGE_NAME "image name as determined by <image>/prepare.sh"
TAG "tag as determined by <image>prepare.sh"
BASE_IMAGE_CONDAFORGE_AMD64 "x86_64 base image for building"
BASE_IMAGE_CONDAFORGE_ARM64 "ARM64 base image for building"
BASE_IMAGE_BUILD_ARG "unique to build image, this determines the upstream conda-forge image to use"
arch "current architecture"
EOF

echo "--------END ENVIRONMENT VARIABLE INVENTORY ---------------------------------"
