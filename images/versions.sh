#!/bin/bash

# Configures various versions to be used throughout infrastructure
ARCHS="amd64 arm64"
DEBIAN_VERSION=12.5
BUSYBOX_VERSION=1.36.1
BASE_DEBIAN_IMAGE_NAME="tmp_base-debian"
BASE_BUSYBOX_IMAGE_NAME="tmp_base-busybox"
BUILD_ENV_IMAGE_NAME="tmp_build-env"
CREATE_ENV_IMAGE_NAME="tmp_create-env"
BASE_TAG="0.2"
BASE_IMAGE_CONDAFORGE_AMD64="quay.io/condaforge/linux-anvil-x86_64:cos7"
BASE_IMAGE_CONDAFORGE_ARM64="quay.io/condaforge/linux-anvil-aarch64:cos7"


# Inspect this repo to get the currently-checked-out version, but if
# BIOCONDA_UTILS_VERSION was set outside this script, use that instead.
BIOCONDA_UTILS_VERSION=${BIOCONDA_UTILS_VERSION:-$(git describe --tags --dirty --always)}

# This will be used as the tag for create-env and build-env images, which
# depend on bioconda-utils
BIOCONDA_IMAGE_TAG=${BIOCONDA_UTILS_VERSION}_base${BASE_TAG}

# FUNCTIONS --------------------------------------------------------------------

function tag_exists () {
  # Returns 0 if the tag for the image exists on quay.io, otherwise returns 1.
  # Skips "latest" tags (likely they will always be present)
  # $1: image name
  # $2: tags
  local IMAGE_NAME="$1"
  local TAGS="$2"

  response="$(curl -sL "https://quay.io/api/v1/repository/bioconda/${IMAGE_NAME}/tag/")"

  # Images can be set to expire; the jq query selects only non-expired images.
  existing_tags="$(
    printf %s "${response}" \
      | jq -r '.tags[]|select(.end_ts == null or .end_ts >= now)|.name'
    )" \
    || {
      printf %s\\n \
        'Could not get list of image tags.' \
        'Does the repository exist on Quay.io?' \
        'Quay.io REST API response was:' \
        "${response}" >&2
      return 1
    }
  for tag in $TAGS ; do
    case "${tag}" in
      "latest" ) ;;
      * )
        if printf %s "${existing_tags}" | grep -qxF "${tag}" ; then
          printf 'Tag %s already exists for %s on quay.io!\n' "${tag}" "${IMAGE_NAME}" >&2
          echo "exists"
        fi
    esac
  done
}

# Helper function to push a just-built image to GitHub Container
# Respository, which is used as a temporary storage mechanism.
function push_to_ghcr () {
  podman manifest push localhost/${1}:${2} ghcr.io/bioconda/${1}:${2}
}

# Helper function to move an image from gchr to quay.io for public use.
function move_from_ghcr_to_quay () {
  local image_name=$1
  local tag=$2

  # Locally-named manifest to which we'll add the different archs.
  buildah manifest create "local_${image_name}:${tag}"

  # Expects images for archs to be built already; add them to local manifest.
  for arch in $ARCHS; do
    imgid=$(buildah pull --arch=$arch "ghcr.io/bioconda/${image_name}:${tag}")
    buildah manifest add "local_${image_name}:${tag}" "${imgid}"
  done

  # Publish
  podman manifest push "local_${image_name}:${tag}" "quay.io/bioconda/${image_name}:${tag}"
}
