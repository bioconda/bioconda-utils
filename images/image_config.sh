#!/bin/bash

#----------------------------VERSIONS-------------------------------------------
# Configures various versions to be used throughout infrastructure.
ARCHS="amd64 arm64"
DEBIAN_VERSION="12.5"
BUSYBOX_VERSION="1.36.1"
BASE_DEBIAN_IMAGE_NAME="tmp-base-debian"
BASE_BUSYBOX_IMAGE_NAME="tmp-base-busybox"
BUILD_ENV_IMAGE_NAME="tmp-build-env"
CREATE_ENV_IMAGE_NAME="tmp-create-env"
BOT_IMAGE_NAME="tmp-bot"
ISSUE_RESPONDER_IMAGE_NAME="tmp-issue-responder"
BASE_TAG="0.2"
BASE_IMAGE_CONDAFORGE_AMD64="quay.io/condaforge/linux-anvil-x86_64:cos7"
BASE_IMAGE_CONDAFORGE_ARM64="quay.io/condaforge/linux-anvil-aarch64:cos7"
CURRENT_ARCH=${CURRENT_ARCH:-""}

# Inspect this repo to get the currently-checked-out version, which matches
# what versioneer.py does --  but if BIOCONDA_UTILS_VERSION_OVERRIDE was set
# outside this script, use that instead.
BIOCONDA_UTILS_VERSION=${BIOCONDA_UTILS_VERSION_OVERRIDE:-$(git describe --tags --dirty --always)}

# This will be used as the tag for create-env and build-env images, which
# depend on bioconda-utils. The base images do not depend on bioconda-utils and
# will only have the base tag.
BIOCONDA_IMAGE_TAG=${BIOCONDA_UTILS_VERSION}_base${BASE_TAG}
#-------------------------------------------------------------------------------



#-------------------------------FUNCTIONS---------------------------------------

function tag_exists() {
  # Check to see if the image and tag exists on quay.io.
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
  # Creates a local manifest, adds containers for multiple archs, and pushes to
  # a registry.
  #
  # Typical usage:
  #   build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
  #   build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} quay.io/bioconda
  #
  local image=$1
  local registry=$2

  buildah manifest rm "local_${image}" || true

  # Locally-named manifest to which we'll add the different archs.
  buildah manifest create "local_${image}"

  # Expects images for archs to be built already by buildah/podman. Here we add
  # them to local manifest.
  for arch in $ARCHS; do
    [ "${ONLY_AMD64:-false}" == "true" -a "${arch}" != "amd64" ] && continue
    imgid=$(buildah pull --arch=$arch "${image}-${arch}")
    buildah manifest add "local_${image}" "${imgid}"
  done

  # In order for docker to use manifests, they must come from a registry (in
  # contrast to podman, which can use local manifests). When testing, a local
  # docker registry is expected to be running; when pushing final images, the
  # registry will be a public registry like quay.io.
  if [ "$registry" == "docker://localhost:5000" ]; then
    if ! curl -X GET http://localhost:5000/v2/_catalog; then
      echo "Local docker registry does not appear to be running on localhost:5000!"
      return 1
    fi
    # This avoids needing to set up TLS certs for the local registry
    additional_args="--tls-verify=false"
  else
    additional_args=""
  fi

  # Note that --all is required to actually push the images, too
  podman manifest push --all $additional_args "local_${image}" "$registry/${image}"
}


function env_var_inventory () {
  # There are a lot of environment variables used here; this function describes
  # them and reports their values at call time.

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
  BOT_IMAGE_IMAGE_NAME "name for bot image"
  ISSUE_RESPONDER_IMAGE_NAME "name for issue-responder image"
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
}
