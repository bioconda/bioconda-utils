#!/bin/bash

# This single script builds the following containers depending on the value of
# the env var TYPE:
#
# - build-env: contains conda + conda-build + bioconda-utils, used for building
#    package
# - create-env: contains the exact version of conda from build-env (which is
#    expected to have been built beforehand). Used for creating env from
#    package + depdendencies
# - base-busybox: the minimal container into which created conda envs are
#     copied. This is the container uploaded to quay.io
# - base-debian: an extended version of the busybox container for special cases
#
# Built containers are added to a manifest. If multiple architectures are
# provided, they will all be added to a manifest which can be subsequently
# uploaded to a registry.

USAGE='
Builds various containers.

Set env vars immediately before running.

REQUIRED ARGS FOR ALL TYPES
===========================
TYPE: base-busybox | base-debian | build-env | create-env
IMAGE_DIR: Location of Dockerfile.
IMAGE_NAME: Image name to upload.
ARCHS: Space-separated architectures e.g. "amd64 arm64"

REQUIRED for base-busybox
-------------------------
  TAGS: Space-separated tags.
  DEBIAN_VERSION
  BUSYBOX_VERSION

REQUIRED for base-debian
------------------------
  TAGS: Space-separated tags.
  DEBIAN_VERSION

REQUIRED for build-env
----------------------
  BIOCONDA_UTILS_VERSION
  BIOCONDA_UTILS_FOLDER: relative to the Dockerfile

REQUIRED for create-env
-----------------------
  BIOCONDA_UTILS_VERSION
  BIOCONDA_UTILS_FOLDER: relative to the Dockerfile
  CONDA_VERSION: conda version to install, typically of the form "conda=x.y.z" extracted from build-env
  MAMBA_VERSION: mamba version to install, typically of the form "mamba=x.y.z" extracted from build-env
  BUSYBOX_IMAGE: the image to use as a base; typically this will be the results
    of building base-busybox in a previous run of this script.

EXAMPLE USAGE
=============

  IMAGE_NAME=base-glibc-debian-bash \
  IMAGE_DIR=../../../images/base-glibc-debian-bash \
  TYPE="base-debian" \
  TAGS="0.1.1 0.1" \
  ARCHS="arm64 amd64" \
  DEBIAN_VERSION="12.2" \
  ./generic_build.bash

'
# ------------------------------------------------------------------------------
# Handle required env vars
[ -z "$IMAGE_NAME" ] && echo -e "$USAGE error: please set IMAGE_NAME" && exit 1
[ -z "$IMAGE_DIR" ] && echo "error: please set IMAGE_DIR, where Dockerfile is found." && exit 1
[ -z "$TYPE" ] && echo "error: please set TYPE: [ base-debian | base-busybox | build-env | create-env ]" && exit 1
[ -z "$ARCHS" ] && echo "error: please set ARCHS" && exit 1

if [ "$TYPE" == "build-env" ] || [ "$TYPE" == "create-env" ]; then
  [ -n "$TAGS" ] && echo "error: TAGS should not be set for build-env or create-env; use BIOCONDA_UTILS_VERSION instead" && exit 1
  [ -z "$BIOCONDA_UTILS_VERSION" ] && echo "error: please set BIOCONDA_UTILS_VERSION for build-env and create-env" && exit 1

  TAGS="$BIOCONDA_UTILS_VERSION"  # Set TAGS to BIOCONDA_UTILS_VERSION from here on

  if [ "$TYPE" == "build-env" ]; then
    [ -z "$BIOCONDA_UTILS_FOLDER" ] && echo "error: please set BIOCONDA_UTILS_FOLDER for build-env" && exit 1
    [ -z "$BUSYBOX_IMAGE" ] && echo "error: please set BUSYBOX_IMAGE for create-env" && exit 1
  fi

  if [ "$TEYPE" == "create-env" ]; then
    [ -z "$BUSYBOX_IMAGE" ] && echo "error: please set BUSYBOX_IMAGE for create-env" && exit 1
    [ -z "$CONDA_VERSION" ] && echo "error: please set CONDA_VERSION for create-env" && exit 1
    [ -z "$MAMBA_VERSION" ] && echo "error: please set MAMBA_VERSION for create-env" && exit 1
  fi
fi

if [ "$TYPE" == "base-debian" ] || [ "$TYPE" == "base-busybox" ]; then
  [ -z "${DEBIAN_VERSION}" ] && echo "error: please set DEBIAN VERSION" && exit 1
fi

if [ "$TYPE" == "base-busybox" ]; then
  [ -z "$BUSYBOX_VERSION" ] && echo "error: please set BUSYBOX_VERSION" && exit 1
fi
# ------------------------------------------------------------------------------

set -xeu

# Dockerfile lives here
cd $IMAGE_DIR

# One manifest per tag
for tag in ${TAGS} ; do
  buildah manifest create "${IMAGE_NAME}:${tag}"
done

# Read space-separated archs input string into an array
read -r -a archs_and_images <<<"$ARCHS"

# ------------------------------------------------------------------------------
# BUILD_ARGS: Incrementally compose build args array, depending on which inputs
# were provided. This will eventually be provided to buildah bud.
#
BUILD_ARGS=()
if [ "$TYPE" == "base-debian" ]; then
  BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")  # version of debian to use as base
fi

if [ "$TYPE" == "build-env" ] || [ "$TYPE" == "create-env" ]; then

  if [ "$TYPE" == "create-env" ]; then
    BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")  # which image to use as base
    BUILD_ARGS+=("--build-arg=CONDA_VERSION=$CONDA_VERSION")  # conda version to install
    BUILD_ARGS+=("--build-arg=MAMBA_VERSION=$MAMBA_VERSION")  # mamba version to install
  fi

  if [ "$TYPE" == "build-env" ]; then
    BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")  # which image to use as base
    BUILD_ARGS+=("--build-arg=BIOCONDA_UTILS_FOLDER=$BIOCONDA_UTILS_FOLDER")  # git clone, relative to Dockerfile
    BUILD_ARGS+=("--build-arg=bioconda_utils_version=$BIOCONDA_UTILS_VERSION")  # specify version to checkout and install, also used as tag
  fi
fi

if [ "$TYPE" == "base-busybox" ]; then
  BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")  # version of debian to use as base for building busybox
  BUILD_ARGS+=("--build-arg=busybox_version=$BUSYBOX_VERSION")  # busybox version to build and use

  # Make a busybox image that we'll use further below. As shown in the
  # Dockerfile.busybox, this uses the build-busybox script which in turn
  # cross-compiles for x86_64 and aarch64, and these execuables are later
  # copied into an arch-specific container.
  #
  # Note that --iidfile (used here and in later commands) prints the built
  # image ID to the specified file so we can refer to the image later.
  iidfile="$( mktemp )"
  echo $BUILD_ARGS
  buildah bud \
    --iidfile="${iidfile}" \
    --file=Dockerfile.busybox \
    ${BUILD_ARGS[@]}
  busybox_image="$( cat "${iidfile}" )"
  rm "${iidfile}"

  BUILD_ARGS+=("--build-arg=busybox_image=${busybox_image}")  # just-built image from which busybox executable will be copied
fi

# ------------------------------------------------------------------------------
# BUILDING:
# - Build each arch's image.
# - Extract info
# - Add info as labels
# - Add tags to image
# - Add image to manifest
#
for arch in $ARCHS; do

  # For build-env, need to use different base image from upstream conda-forge
  # depending on arch.
  BASE_IMAGE_BUILD_ARG=""
  if [ "$TYPE" == "build-env" ]; then
    if [ "$arch" == "amd64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=quay.io/condaforge/linux-anvil-cos7-x86_64"
    fi
    if [ "$arch" == "arm64" ]; then
      BASE_IMAGE_BUILD_ARG="--build-arg=base_image=quay.io/condaforge/linux-anvil-aarch64"
    fi
  fi

  # Actual building happens here.
  iidfile="$( mktemp )"
  buildah bud \
    --arch="${arch}" \
    --iidfile="${iidfile}" \
    ${BUILD_ARGS[@]} \
    $BASE_IMAGE_BUILD_ARG
  image_id="$( cat "${iidfile}" )"
  rm "${iidfile}"

  # Extract various package info and version info, then store that info
  # as labels. Container is removed at the end to avoid e.g. having these
  # commands in the history of the container.
  container="$( buildah from "${image_id}" )"
  run() { buildah run "${container}" "${@}" ; }
  LABELS=()
  LABELS+=("--label=deb-list=$( run cat /.deb.lst | tr '\n' '|' | sed 's/|$//' )")
  LABELS+=("--label=pkg-list=$( run cat /.pkg.lst | tr '\n' '|' | sed 's/|$//' )")
  LABELS+=("--label=glibc=$( run sh -c 'exec "$( find -xdev -name libc.so.6 -print -quit )"' | sed '1!d' )")
  LABELS+=("--label=debian=$( run cat /etc/debian_version | sed '1!d' )")
  LABELS+=("--label=bash=$( run bash --version | sed '1!d' )")
  if [ "$TYPE" == "build-env" ]; then
    bioconda_utils="$(
      run sh -c '. /opt/conda/etc/profile.d/conda.sh && conda activate base && bioconda-utils --version' \
      | rev | cut -f1 -d " " | rev
    )"
    LABELS+=("--label=bioconda-utils=${bioconda_utils}")
  fi

  if [ "$TYPE" == "base-busybox" ]; then
    LABELS+=("--label=busybox-version=${BUSYBOX_VERSION}")
  fi
  buildah rm "${container}"

  # Add labels to a new container...
  container="$( buildah from "${image_id}" )"
  buildah config "${LABELS[@]}" "${container}"

  # ...then store the container (now with labels) as a new image.
  # This is what we'll use to eventually upload.
  image_id="$( buildah commit "${container}" )"
  buildah rm "${container}"

  # Add images to manifest. Note that individual image tags include arch;
  # manifest does not.
  for tag in ${TAGS} ; do
    buildah tag \
      "${image_id}" \
      "${IMAGE_NAME}:${tag}-${arch}"
    buildah manifest add \
      "${IMAGE_NAME}:${tag}" \
      "${image_id}"

    buildah inspect -t image ${IMAGE_NAME}:${tag}-${arch}
  done # tags
done # archs_and_images

for tag in ${TAGS}; do
  buildah inspect -t manifest ${IMAGE_NAME}:${tag}
done

# Extract image IDs from the manifest built in the last step
ids="$(
  for tag in $TAGS ; do
    buildah manifest inspect "${IMAGE_NAME}:${tag}" \
      | jq -r '.manifests[]|.digest' \
      | while read id ; do
          buildah images --format '{{.ID}}{{.Digest}}' \
          | sed -n "s/${id}//p"
        done
  done
  )"

# Run the tests; see Dockerfile.test in the relevant image dir for the
# actual tests run
#
# N.B. need to unique since one image can have multiple tags
ids="$( printf %s "${ids}" | sort -u )"
for id in ${ids} ; do
  podman history "${id}"
  buildah bud \
    --build-arg=base="${id}" \
    --file=Dockerfile.test
done

# Clean up
buildah rmi --prune || true
