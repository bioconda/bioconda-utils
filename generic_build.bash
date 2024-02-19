#!/bin/bash

# This single script builds the following images depending on the value of the
# env var TYPE:
#
# - build-env: contains conda + conda-build + bioconda-utils, used for building
#    package
# - create-env: contains the exact version of conda from build-env (which is
#    expected to have been built beforehand). Used for creating env from
#    package + depdendencies
# - base-busybox: the minimal container into which created conda envs are
#     copied. This is the image uploaded to quay.io
# - base-debian: an extended version of the busybox image for special cases
#
# Built images are added to a manifest. If multiple architectures are provided,
# they will all be added to a manifest which can be subsequently uploaded to
# a registry.
#
# After images are built, they are tested.
#
# This script does NOT upload anything, that must be handled separately.

USAGE='
Builds various containers.

Set env vars immediately before running.

REQUIRED ARGS FOR ALL TYPES
===========================
  TYPE: base-busybox | base-debian | build-env | create-env
  IMAGE_DIR: Location of Dockerfile.
  IMAGE_NAME: Image name to upload.
  ARCHS: Space-separated architectures e.g. "amd64 arm64"
  TAG: image tag

REQUIRED for base-busybox
-------------------------
  DEBIAN_VERSION
  BUSYBOX_VERSION

REQUIRED for base-debian
------------------------
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

OPTIONAL args
-------------

  WARN_IF_MISSING: true | false
    If true (default), will exit if there is no remote repository yet. Set to
    false when testing with custom image names.

  LOG: filename
    Write info here so other jobs can read from it. Defaults to $TYPE.log


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
# HANDLE REQUIRED ENV VARS
[ -z "$IMAGE_NAME" ] && echo -e "$USAGE error: please set IMAGE_NAME" && exit 1
[ -z "$IMAGE_DIR" ] && echo "error: please set IMAGE_DIR, where Dockerfile is found." && exit 1
[ -z "$TYPE" ] && echo "error: please set TYPE: [ base-debian | base-busybox | build-env | create-env ]" && exit 1
[ -z "$ARCHS" ] && echo "error: please set ARCHS" && exit 1
[ -z "$TAG" ] && echo "error: please set TAG" && exit 1

if [ "$TYPE" == "build-env" ] || [ "$TYPE" == "create-env" ]; then
  [ -z "$BIOCONDA_UTILS_VERSION" ] && echo "error: please set BIOCONDA_UTILS_VERSION for build-env and create-env" && exit 1

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

LOG=${LOG:="${TYPE}.log"}
touch $LOG

# Also add "latest" tag.
TAGS="$TAG latest"

# ------------------------------------------------------------------------------


# ------------------------------------------------------------------------------
# CHECK FOR EXISTING TAGS. This is because quay.io does not support immutable
# images and we don't want to clobber existing. `latest` will likely always be
# present though, so don't consider that existing. If you know that the
# repository doesn't exist (e.g., you're testing using different names) then
# set ERROR_IF_MISSING=false.
response="$(curl -sL "https://quay.io/api/v1/repository/bioconda/${IMAGE_NAME}/tag/")"

# Images can be set to expire; the jq query selects only non-expired images.
existing_tags="$(
  printf %s "${response}" \
    | jq -r '.tags[]|select(.end_ts == null or .end_ts >= now)|.name'
  )" \
  || {
    if [ ${ERROR_IF_MISSING:-true} == "true" ]; then
      printf %s\\n \
        'Could not get list of image tags.' \
        'Does the repository exist on Quay.io?' \
        'Quay.io REST API response was:' \
        "${response}"
      exit 1
    fi
  }
for tag in $TAGS ; do
  case "${tag}" in
    "latest" ) ;;
    * )
      if printf %s "${existing_tags}" | grep -qxF "${tag}" ; then
        printf 'Tag %s already exists for %s on quay.io! Logging, and exiting with code 64\n' "${tag}" "${IMAGE_NAME}" >&2
        echo "TAG_EXISTS_${TYPE}=true" >> $LOG
        exit 64
      fi
  esac
done

echo "TAG_EXISTS_${TYPE}=false"

#-------------------------------------------------------------------------------
# SETUP

set -xeu

# Dockerfile lives here
cd $IMAGE_DIR

# One manifest per tag; multiple archs will go in the same manifest.
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

if [ "$TYPE" == "create-env" ]; then
  BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")  # which image to use as base
  BUILD_ARGS+=("--build-arg=CONDA_VERSION=$CONDA_VERSION")  # conda version to install
  BUILD_ARGS+=("--build-arg=MAMBA_VERSION=$MAMBA_VERSION")  # mamba version to install
fi

if [ "$TYPE" == "build-env" ]; then
  BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")  # which image to use as base
  BUILD_ARGS+=("--build-arg=BIOCONDA_UTILS_FOLDER=$BIOCONDA_UTILS_FOLDER")  # git clone, relative to Dockerfile
  BUILD_ARGS+=("--build-arg=bioconda_utils_version=$BIOCONDA_UTILS_VERSION")  # specify version to checkout and install, also used as part of tag
fi

if [ "$TYPE" == "base-busybox" ]; then
  BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")  # version of debian to use as base for building busybox
  BUILD_ARGS+=("--build-arg=busybox_version=$BUSYBOX_VERSION")  # busybox version to build and use

  # Make a busybox image that we'll use further below. As shown in the
  # Dockerfile.busybox, this uses the build-busybox script which in turn
  # cross-compiles for x86_64 and aarch64, and these executables are later
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
    --file=Dockerfile \
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
  # This is what we'll eventually upload.
  image_id="$( buildah commit "${container}" )"
  buildah rm "${container}"

  # Add images to manifest. Note that individual **image** tags include arch;
  # manifest does not.
  for tag in ${TAGS} ; do
    buildah tag \
      "${image_id}" \
      "${IMAGE_NAME}:${tag}-${arch}"
    buildah manifest add \
      "${IMAGE_NAME}:${tag}" \
      "${image_id}"

    # Inspect image details, but remove the most verbose (like history) and
    # redundant (just need one of Docker or OCIv1) fields.
    buildah inspect -t image ${IMAGE_NAME}:${tag}-$arch} \
      | jq 'del(
          .History,
          .OCIv1.history,
          .Config,
          .Manifest,
          .Docker,
          .NamespaceOptions)'

  done # tags
done # archs_and_images

for tag in ${TAGS}; do
  buildah inspect -t manifest ${IMAGE_NAME}:${tag}
done

# ------------------------------------------------------------------------------
# TESTING
#
# Args to be used specifically when testing with Dockerfile.test
TEST_BUILD_ARGS=()
if [ "$TYPE" == "create-env" ]; then
  TEST_BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")
fi

# Turns out that buildah cannot use --arch and and provide an image ID as the
# `base` build-arg at the same time, because we get the error:
#
#   "error creating build container: pull policy is always but image has been
#   referred to by ID".
#
# This happens even when using --pull-never. This may be fixed in later
# versions, in which case we can use the code below in the "EXTRA" section.
#
# Since the rest of this script builds a single image and assigns possibly
# multiple tags, we just use the first tag to use as the `base` build-arg.

tag=$(echo $TAGS | cut -f1 -d " ")
for arch in $ARCHS; do
  echo "[LOG] Starting test for ${IMAGE_NAME}:${tag}, $arch."
  buildah bud \
    --arch="$arch" \
    --build-arg=base="localhost/${IMAGE_NAME}:${tag}" \
    ${TEST_BUILD_ARGS[@]} \
    --file=Dockerfile.test
done


# EXTRA ------------------------------------------------------------------------
# The following demonstrates how to extract images from corresponding manifest
# digests. This may be a better approach in the future, but as noted above we
# cannot use FROM <IMAGE_ID> and --arch and instead use name:tag.
#
# It may be useful in the future but it is disabled for now.
#
if [ "" ] ; then
  # Manifests provide a digest; we then need to look up the corresponding image
  # name for that digest.
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

  # N.B. need to unique since one image can have multiple tags. In general,
  # this should be one image for each arch, no matter how many tags.
  ids="$( printf %s "${ids}" | sort -u )"

  # Run the tests; see Dockerfile.test in the relevant image dir for the
  # actual tests that are run.
  for id in ${ids} ; do

    podman history "${id}"

    # Make sure we're explicit with the arch so that the right image is pulled
    # from the respective container.
    arch=$(buildah inspect "${id}" | jq -r '.OCIv1.architecture' | sort -u)

    buildah bud \
      --arch="$arch" \
      --build-arg=base="localhost/${IMAGE_NAME}" \
      ${TEST_BUILD_ARGS[@]} \
      --file=Dockerfile.test
  done
fi
# -------------------------------------------------------------------------------

podman manifest push --all localhost/${IMAGE_NAME} docker-daemon:${IMAGE_NAME}
docker run ${IMAGE_NAME} ls -l

# Clean up
buildah rmi --prune || true
