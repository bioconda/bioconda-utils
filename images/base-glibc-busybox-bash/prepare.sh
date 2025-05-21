source ../versions.sh
IMAGE_NAME="${BASE_BUSYBOX_IMAGE_NAME}"
TAG="$BASE_TAG"

# Build busybox binaries for each arch.
#
# The respective busybox base containers for each arch will later extract the
# relevant binary from this image.

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
