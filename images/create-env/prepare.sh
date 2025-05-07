source ../versions.sh
IMAGE_NAME="${CREATE_ENV_IMAGE_NAME}"
TAG=$BIOCONDA_IMAGE_TAG
BUILD_ARGS=()



# Get the exact versions of mamba and conda that were installed in build-env.
#
# If this tag exists on quay.io (that is, this create-env is being built in
# a subsequent run), then use that. Otherwise, we assume this tag has already
# been built and pushed to GitHub Container Registry (and the GitHub Actions job
# dependency should reflect this)
BUILD_ENV_REGISTRY=${BUILD_ENV_REGISTRY:="ghcr.io/bioconda"}
if [ $(tag_exists $BUILD_ENV_IMAGE_NAME $BIOCONDA_IMAGE_TAG) ]; then
  BUILD_ENV_REGISTRY=quay.io/bioconda
fi

CONDA_VERSION=$(
        podman run -t $BUILD_ENV_REGISTRY/${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} \
        bash -c "/opt/conda/bin/conda list --export '^conda$'| sed -n 's/=[^=]*$//p'"
)
# Remove trailing \r with parameter expansion
export CONDA_VERSION=${CONDA_VERSION%$'\r'}

BUILD_ARGS+=("--build-arg=CONDA_VERSION=$CONDA_VERSION")

# Needs busybox image to copy some items over
BASE_BUSYBOX_REGISTRY=${BASE_BUSYBOX_REGISTRY:="ghcr.io/bioconda"}
if [ $(tag_exists $BASE_BUSYBOX_IMAGE_NAME $BASE_TAG) ]; then
  BASE_BUSYBOX_REGISTRY=quay.io/bioconda
fi

BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=${BASE_BUSYBOX_REGISTRY}/${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG}")

# TEST_BUILD_ARGS=()
# TEST_BUILD_ARGS+=("--build-arg=BUSYBOX_IMAGE=$BUSYBOX_IMAGE")
