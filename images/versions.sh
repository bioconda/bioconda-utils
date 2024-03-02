#!/bin/bash

# Configures various versions to be used throughout infrastructure
ARCHS="amd64 arm64"
DEBIAN_VERSION=12.2
BUSYBOX_VERSION=1.36.1
BASE_DEBIAN_IMAGE_NAME="tmp-debian"
BASE_BUSYBOX_IMAGE_NAME="tmp-busybox"
BUILD_ENV_IMAGE_NAME="tmp-build-env"
CREATE_ENV_IMAGE_NAME="tmp-create-env"
BASE_TAG="0.1"

# This assumes you've already checked out whatever branch/commit to use.
#
# Respects setting outside this script, if e.g. you want GitHub Actions to
# handle naming based on branch.
BIOCONDA_UTILS_VERSION=${BIOCONDA_UTILS_VERSION:-$(git describe --tags --dirty --always)}

# Used as the tag for create-env and build-env, which depend on bioconda-utils
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

function push_to_ghcr () {
  buildah manifest push localhost/${1}:${2} ghcr.io/bioconda/${1}:${2}
}

function move_from_ghcr_to_quay () {
  local image_name=$1
  local tag=$2
  buildah manifest create "local_${image_name}:${tag}"
  for arch in $ARCHS; do
    imgid=$(buildah pull --arch=$arch "ghcr.io/bioconda/${image_name}:${tag}")
    buildah manifest add "local_${image_name}:${tag}" "${imgid}"
  done
  buildah manifest push "local_${image_name}:${tag}" "quay.io/bioconda/${image_name}:${tag}"
}
