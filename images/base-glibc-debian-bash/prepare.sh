source ../versions.sh
IMAGE_NAME="${BASE_DEBIAN_IMAGE_NAME}"
TAG="$BASE_TAG"
BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")