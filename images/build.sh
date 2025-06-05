#!/bin/bash

# This script builds multi-arch images and a manifest that points to them. The
# manifest can be then pushed to a registry with e.g. podman manifest push.
#
# Usage:
#
#   build.sh <directory>
#
# The only arg directly provided to this script is the image directory,
# containing at least a Dockerfile.
#
# In that directory, if prepare.sh exists it will be sourced. Use prepare.sh to
# create all required env vars and do any image-specific prep work.
#
# Expected env vars to be populated by prepare.sh
# -----------------------------------------------
# TAG: tag to build
# ARCHS: space-separated string of archs to build
# IMAGE_NAME: name of image; created manifest will be IMAGE_NAME:tag
# BUILD_ARGS: array of arguments like ("--build-arg=argument1=the-value", "--build-arg=arg2=a")
#
# Output is:
#   - images for each arch
#   - manifest containing those images
#   - tar files of images ready to upload as artifacts
#   - tar files built by podman loaded into docker

set -xeu

IMAGE_DIR=$1

echo === BUILDING $IMAGE_DIR ===

cd $IMAGE_DIR

[ -e prepare.sh ] && source prepare.sh

# Clean up any manifests before we start.
# IMAGE_NAME and TAG should be created by prepare.sh
buildah manifest rm "${IMAGE_NAME}:${TAG}" || true
buildah manifest create "${IMAGE_NAME}:${TAG}"

# ARCHS should be created by prepare.sh
for arch in $ARCHS; do

  [ "${ONLY_AMD64:-false}" == "true" -a "${arch}" != "amd64" ] && continue

  # This logic is specific to the build-env. We need an arch-specific base
  # image, but the nomenclature is inconsistent. So we directly map arch names
  # to conda-forge base images.
  BASE_IMAGE_BUILD_ARG=""
  if [ "${IS_BUILD_ENV:-false}" == "true" ]; then
    if [ "$arch" == "amd64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=${BASE_IMAGE_CONDAFORGE_AMD64}"
    fi
    if [ "$arch" == "arm64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=${BASE_IMAGE_CONDAFORGE_ARM64}"
    fi
  fi

  # Make arch available as an environment variable and source prepare.sh again
  export CURRENT_ARCH="$arch"
  [ -e prepare.sh ] && source prepare.sh

  # defined in image_config.sh, which is expected to have been sourced prior to
  # running this script.
  env_var_inventory

  # Actual building happens here. We will keep track of the built image in
  # $image_id.
  iidfile="$(mktemp)"
  buildah bud \
    --arch="${arch}" \
    --iidfile="${iidfile}" \
    --file=Dockerfile \
    ${BUILD_ARGS[@]} \
    $BASE_IMAGE_BUILD_ARG
  image_id="$(cat "${iidfile}")"
  rm "${iidfile}"

  # In order for GitHub Actions to inherit container permissions from the repo
  # permissions, we need to add a special label. However `buildah config
  # --label` operates on a container, not an image. So we add the label to
  # a temporary container and then save the resulting image.
  container="$(buildah from "${image_id}")"
  buildah config \
    --label="org.opencontainers.image.source=https://github.com/bioconda/bioconda-utils" \
    "${container}"
  image_id="$(buildah commit "${container}")"
  buildah rm "${container}"

  # Add -$arch suffix to image's tag
  buildah tag "${image_id}" "${IMAGE_NAME}:${TAG}-${arch}"

  # Run basic test in Dockerfile.test
  buildah bud \
    ${TEST_ARGS[@]} \
    --file=Dockerfile.test

  # Save image for storing as artifact or to load into docker
  mkdir -p ../../image-artifacts
  podman save "${IMAGE_NAME}:${TAG}-${arch}" >"../../image-artifacts/${IMAGE_NAME}-${arch}.tar"
  ls ../../image-artifacts

done
