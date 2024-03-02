#!/bin/bash

# This script builds multi-arch images and a manifest that points to them. The
# manifest can be then pushed to a registry with e.g. podman manifest push.
#
# Usage:
#
#   build.sh <directory>
#
# The only arg directly provided to this script is the image directory,
# containing at least a Dockerfile. In that directory, if prepare.sh exists it
# will be sourced to get all other env vars used here, as well as do any
# image-specific prep work.
#
# Expected env vars populated by prepare.sh
# -----------------------------------------
# TAG: tag to build
# ARCHS: space-separated string of archs to build
# IMAGE_NAME: name of image; created manifest will be IMAGE_NAME:tag
# BUILD_ARGS: array of arguments like ("--build-arg=argument1=the-value", "--build-arg=arg2=a")

set -xeu

IMAGE_DIR=$1

cd $IMAGE_DIR

[ -e prepare.sh ] && source prepare.sh

buildah manifest rm "${IMAGE_NAME}:${TAG}" || true
buildah manifest create "${IMAGE_NAME}:${TAG}"

for arch in $ARCHS; do

  # This is specific to the build-env: we need to decide on the base image
  # depending on the arch.
  BASE_IMAGE_BUILD_ARG=""
  if [ "${IS_BUILD_ENV:-false}" == "true" ]; then
    if [ "$arch" == "amd64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=quay.io/condaforge/linux-anvil-cos7-x86_64"
    fi
    if [ "$arch" == "arm64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=quay.io/condaforge/linux-anvil-aarch64"
    fi
  fi

  # Actual building happens here. Keep track of the built image in $iidfile.
  iidfile="$( mktemp )"
  buildah bud \
    --arch="${arch}" \
    --iidfile="${iidfile}" \
    --file=Dockerfile \
    ${BUILD_ARGS[@]} \
    $BASE_IMAGE_BUILD_ARG
  image_id="$( cat "${iidfile}" )"
  rm "${iidfile}"

  # Add a label needed for GitHub Actions to inherit container permissions from
  # repo permissions. Must be set on container, not image. Then save resulting
  # image.
  container="$( buildah from "${image_id}" )"
  buildah config \
    --label="org.opencontainers.image.source=https://github.com/bioconda/bioconda-utils" \
    "${container}"
  image_id="$( buildah commit "${container}" )"
  buildah rm "${container}"

  # Add image to manifest
  buildah tag "${image_id}" "${IMAGE_NAME}:${TAG}-${arch}"
  buildah manifest add "${IMAGE_NAME}:${TAG}" "${image_id}"
done
