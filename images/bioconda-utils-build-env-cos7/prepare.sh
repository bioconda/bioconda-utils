source ../versions.sh
IMAGE_NAME="${BUILD_ENV_IMAGE_NAME}"
TAG="${BIOCONDA_UTILS_VERSION}_base$BASE_TAG"

# See ../locale/generate_locale.sh for how it was generated in the first place.
cp -r ../locale/C.utf8 .

# Copy everything we need to install into this image. Note that we need the
# .git directory so that versioneer can correctly compute the version.
mkdir -p bioconda-utils
for item in setup.py setup.cfg versioneer.py bioconda_utils MANIFEST .git; do
  cp -ar ../../$item bioconda-utils
done

# build script needs to special-case base images depending on archs when
# building the build-env
IS_BUILD_ENV=true

BUILD_ARGS=()
BUILD_ARGS+=("--build-arg=BIOCONDA_UTILS_FOLDER=bioconda-utils")
BUILD_ARGS+=("--build-arg=bioconda_utils_version=$BIOCONDA_UTILS_VERSION")
