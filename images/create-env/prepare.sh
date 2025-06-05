source ../image_config.sh
IMAGE_NAME="${CREATE_ENV_IMAGE_NAME}"
TAG=$BIOCONDA_IMAGE_TAG
BUILD_ARGS=()

# Get the exact versions of mamba and conda that were installed in build-env.
#
# TODO: here we're hard-coding the amd64 on the reasonable assumption that it
# matches the arm64
CONDA_VERSION=$(
        podman run -t ${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG}-amd64 \
                bash -c "/opt/conda/bin/conda list --export '^conda$'| sed -n 's/=[^=]*$//p'"
)
# Remove trailing \r with parameter expansion
export CONDA_VERSION=${CONDA_VERSION%$'\r'}

BUILD_ARGS+=("--build-arg=CONDA_VERSION=$CONDA_VERSION")
BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG}-${CURRENT_ARCH}")

TEST_ARGS=()
TEST_ARGS+=("--build-arg=BUSYBOX_IMAGE=localhost/${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG}-${CURRENT_ARCH}")
