#!/bin/bash

# Configures various versions to be used throughout infrastructure.
#
# Anything with GITHUB_ENV at the end of the line will be exported to $GITHUB_ENV during GitHub Actions jobs.
ARCHS="amd64 arm64"                                                       # GITHUB_ENV
DEBIAN_VERSION=12.5                                                       # GITHUB_ENV
BUSYBOX_VERSION=1.36.1                                                    # GITHUB_ENV
BASE_DEBIAN_IMAGE_NAME="tmp-base-debian"                                  # GITHUB_ENV
BASE_BUSYBOX_IMAGE_NAME="tmp-base-busybox"                                # GITHUB_ENV
BUILD_ENV_IMAGE_NAME="tmp-build-env"                                      # GITHUB_ENV
CREATE_ENV_IMAGE_NAME="tmp-create-env"                                    # GITHUB_ENV
BASE_TAG="0.2"                                                            # GITHUB_ENV
BASE_IMAGE_CONDAFORGE_AMD64="quay.io/condaforge/linux-anvil-x86_64:cos7"  # GITHUB_ENV
BASE_IMAGE_CONDAFORGE_ARM64="quay.io/condaforge/linux-anvil-aarch64:cos7" # GITHUB_ENV
CURRENT_ARCH=${CURRENT_ARCH:-""}                                          # GITHUB_ENV
function export_github_env() {
  HERE=$1
  GITHUB_ENV=${GITHUB_ENV:-/dev/null}
  for var in $(grep "# GITHUB_ENV$" $HERE | cut -f1 -d "="); do
    echo "$var=\"${!var}\"" >>$GITHUB_ENV
  done
}

# Inspect this repo to get the currently-checked-out version, but if
# BIOCONDA_UTILS_VERSION was set outside this script, use that instead.
BIOCONDA_UTILS_VERSION=${BIOCONDA_UTILS_VERSION:-$(git describe --tags --dirty --always)} # GITHUB_ENV

# This will be used as the tag for create-env and build-env images, which
# depend on bioconda-utils
BIOCONDA_IMAGE_TAG=${BIOCONDA_UTILS_VERSION}_base${BASE_TAG} # GITHUB_ENV

# FUNCTIONS --------------------------------------------------------------------

function tag_exists() {
  # Returns 0 if the tag for the image exists on quay.io, otherwise returns 1.
  # Skips "latest" tags (likely they will always be present)
  # $1: image name
  # $2: tags
  local IMAGE_NAME="$1"
  local TAGS="$2"

  response="$(curl -sL "https://quay.io/api/v1/repository/bioconda/${IMAGE_NAME}/tag/")"

  # Images can be set to expire; the jq query selects only non-expired images.
  existing_tags="$(
    printf %s "${response}" |
      jq -r '.tags[]|select(.end_ts == null or .end_ts >= now)|.name'
  )" ||
    {
      printf %s\\n \
        'Could not get list of image tags.' \
        'Does the repository exist on Quay.io?' \
        'Quay.io REST API response was:' \
        "${response}" >&2
      return 1
    }
  for tag in $TAGS; do
    case "${tag}" in
    "latest") ;;
    *)
      if printf %s "${existing_tags}" | grep -qxF "${tag}"; then
        printf 'Tag %s already exists for %s on quay.io!\n' "${tag}" "${IMAGE_NAME}" >&2
        echo "exists"
      fi
      ;;
    esac
  done
}

function build_and_push_manifest() {
  # Builds a local manifest containing multiple archs for an image/tag, and
  # pushes to a registry. E.g.,
  #
  #   build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
  #
  # or
  #
  #   build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} quay.io/bioconda
  #
  local image=$1
  local registry=$2

  buildah manifest rm "local_${image}" || true

  # Locally-named manifest to which we'll add the different archs.
  buildah manifest create "local_${image}"

  # Expects images for archs to be built already; here we add them to local
  # manifest.
  for arch in $ARCHS; do
    imgid=$(buildah pull --arch=$arch "${image}-${arch}")
    buildah manifest add "local_${image}" "${imgid}"
  done

  if [ "$registry" == "docker://localhost:5000" ]; then 
    if ! curl -X GET http://localhost:5000/v2/_catalog; then
      echo "Local docker registry does not appear to be running on localhost:5000!"
      return 1
    fi
    # To avoid setting up TLS certs for local registry which seems like overkill
    additional_args="--tls-verify=false"
  else
    additional_args=""
  fi

  podman manifest push --all $additional_args "local_${image}" "$registry/${image}"
}
