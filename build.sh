#!/bin/bash

# create-env depends on base-busybox and build-env (which in turn also depends
# on base-busybox). base-debian is independent.
#
# This can be run locally for testing, and can be used as a template for CI.
#
#  base-busybox    base-debian
#   |        |
# build-env  |
#   \        |
#    \       |
#    create-env

set -euo

# Used for build-env. bioconda-utils will be cloned to this folder inside the
# image dir (where the Dockerfile is) and the version will be checked out.
export BIOCONDA_UTILS_FOLDER=bioconda-utils
export BIOCONDA_UTILS_VERSION=v2.11.1

export DEBIAN_VERSION="12.2"
export BUSYBOX_VERSION="1.36.1"

# Use same tags for base-busybox and base-debian
export BASE_TAGS="0.1.1 0.1 latest"
export WARN_IF_MISSING=false

# Store as separate vars so we can use these for dependencies.
BUILD_ENV_IMAGE_NAME=tmp-build-env
CREATE_ENV_IMAGE_NAME=tmp-create-env
BASE_DEBIAN_IMAGE_NAME=tmp-debian
BASE_BUSYBOX_IMAGE_NAME=tmp-busybox

# # Build base-busybox------------------------------------------------------------
IMAGE_NAME=$BASE_BUSYBOX_IMAGE_NAME \
IMAGE_DIR=images/base-glibc-busybox-bash \
ARCHS="arm64" \
TYPE="base-busybox" \
TAGS=$BASE_TAGS \
./generic_build.bash

# Build base-debian-------------------------------------------------------------
IMAGE_NAME=$BASE_DEBIAN_IMAGE_NAME \
IMAGE_DIR=images/base-glibc-debian-bash \
ARCHS="amd64" \
TYPE="base-debian" \
TAGS=$BASE_TAGS \
./generic_build.bash

# Build build-env---------------------------------------------------------------

 # Clone bioconda-utils into same directory as Dockerfile
 if [ ! -e "images/bioconda-utils-build-env-cos7/bioconda-utils" ]; then
         git clone https://github.com/bioconda/bioconda-utils images/bioconda-utils-build-env-cos7/bioconda-utils
 else
         (cd images/bioconda-utils-build-env-cos7/bioconda-utils && git fetch)
 fi

 IMAGE_NAME=$BUILD_ENV_IMAGE_NAME \
 IMAGE_DIR=images/bioconda-utils-build-env-cos7 \
 ARCHS="amd64" \
 TYPE="build-env" \
 BUSYBOX_IMAGE=localhost/$BASE_BUSYBOX_IMAGE_NAME \
 ./generic_build.bash

# # Build create-env--------------------------------------------------------------
# Get the exact versions of mamba and conda that were installed in build-env.
CONDA_VERSION=$(
        podman run -t localhost/${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_UTILS_VERSION} \
        bash -c "/opt/conda/bin/conda list --export '^conda$'| sed -n 's/=[^=]*$//p'"
)
MAMBA_VERSION=$(
        podman run -t localhost/${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_UTILS_VERSION} \
        bash -c "/opt/conda/bin/conda list --export '^mamba$'| sed -n 's/=[^=]*$//p'"
)
# Remove trailing \r with parameter expansion
export CONDA_VERSION=${CONDA_VERSION%$'\r'}
export MAMBA_VERSION=${MAMBA_VERSION%$'\r'}

IMAGE_NAME=$CREATE_ENV_IMAGE_NAME \
IMAGE_DIR=images/create-env \
ARCHS="arm64" \
TYPE="create-env" \
BUSYBOX_IMAGE=localhost/$BASE_BUSYBOX_IMAGE_NAME \
./generic_build.bash
