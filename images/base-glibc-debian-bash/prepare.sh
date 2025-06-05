source ../image_config.sh
IMAGE_NAME="${BASE_DEBIAN_IMAGE_NAME}"
TAG="$BASE_TAG"
BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")

TEST_ARGS=()
TEST_ARGS+=("--build-arg=base=${IMAGE_NAME}:${TAG}-${CURRENT_ARCH}")
