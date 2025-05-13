set -e

source ../versions.sh
IMAGE_NAME="${BUILD_ENV_IMAGE_NAME}"
TAG="${BIOCONDA_UTILS_VERSION}_base$BASE_TAG"

# See ../locale/generate_locale.sh for how it was generated in the first place.
cp -r ../locale/C.utf8 .

# We are aiming to get the entire repo into the container so that versioneer
# can correctly determine the version. But the direc
if [ -e "./bioconda-utils" ]; then
  rm -rf "./bioconda-utils"
fi
git clone ../../ ./bioconda-utils

# The build script needs to special-case base images depending on archs when
# building the build-env
IS_BUILD_ENV=true

BUILD_ARGS=()

# Where to find the copied-over bioconda-utils
BUILD_ARGS+=("--build-arg=BIOCONDA_UTILS_FOLDER=bioconda-utils")
BUILD_ARGS+=("--build-arg=bioconda_utils_version=$BIOCONDA_UTILS_VERSION")
