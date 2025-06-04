source ../image_config.sh
IMAGE_NAME="${BOT_IMAGE_NAME}"

# Depends on create-env, which in turn depends on bioconda-utils
TAG=$BIOCONDA_IMAGE_TAG

# Signal to build.sh that we only need an amd64 version
ONLY_AMD64=true

BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=create_env=${CREATE_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG}-${CURRENT_ARCH}")
BUILD_ARGS+=("--build-arg=base=localhost/${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG}-${CURRENT_ARCH}")
