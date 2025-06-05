source ../image_config.sh
IMAGE_NAME="${BASE_BUSYBOX_IMAGE_NAME}"
TAG="$BASE_TAG"

# Before building the actual base images (which will happen in build.sh), we
# first build busybox binaries for each arch. Later, the base image Dockerfile
# will extract the arch-appropriate binary.

BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=debian_version=${DEBIAN_VERSION}")
BUILD_ARGS+=("--build-arg=busybox_version=${BUSYBOX_VERSION}")
iidfile="$(mktemp)"
buildah bud \
  --iidfile="${iidfile}" \
  --file=Dockerfile.busybox \
  ${BUILD_ARGS[@]}
busybox_image="$(cat "${iidfile}")"
rm "${iidfile}"

# Override build args for what's needed in main Dockerfile
BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=debian_version=${DEBIAN_VERSION}")
BUILD_ARGS+=("--build-arg=busybox_image=${busybox_image}")

TEST_ARGS=()
TEST_ARGS+=("--build-arg=base=${IMAGE_NAME}:${TAG}-${CURRENT_ARCH}")
