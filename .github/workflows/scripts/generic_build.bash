#!/bin/bash

set -xeu

[ -z $IMAGE_NAME ] && echo "Please set IMAGE_NAME" && exit 1
[ -z $IMAGE_DIR ] && echo "Please set IMAGE_DIR" && exit 1
[ -z $TAGS ] && echo "Please set TAGS" && exit 1
[ -z $ARCHS ] && echo "Please set ARCHS" && exit 1
[ -z $TYPE ] && echo "Please set TYPE: [ base-debian | base-busybox | build-env | create-env ]"

# Dockerfile lives here
cd $IMAGE_DIR

for tag in ${TAGS} ; do
  buildah manifest create "${IMAGE_NAME}:${tag}"
done

# Read space-separated archs input string into an array
read -r -a archs_and_images <<<"$ARCHS"

# ----------------------------------------------------------------------
# Incrementally compose build args, depending on which inputs were
# provided.
BUILD_ARGS=()
if [ "$TYPE" == "base-debian" || "$TYPE" == "base-busybox" ]; then
  [ -z "${DEBIAN_VERSION}" ] && echo "Please set DEBIAN VERSION" && exit 1
  BUILD_ARGS+=("--build-arg=debian_version=$DEBIAN_VERSION")
fi

if [ "$TYPE" == "build-env" || "$TYPE" == "create-env" ]; then

  [ -z "${BIOCONDA_UTILS_VERSION}" ] && echo "Please set BIOCONDA_UTILS_VERSION" && exit 1

  # Due to different nomenclature used by conda-forge and buildah, we
  # need to map archs to base images, so overwrite archs_and_images.
  archs_and_images=(
    "amd64=quay.io/condaforge/linux-anvil-cos7-x86_64"
    "arm64=quay.io/condaforge/linux-anvil-aarch64"
  )

  # FIXME: build-env should export its own conda version immediately after
  # running (or maybe as a label on the image?) so we can just use that as
  # a build arg for create-env.
  #
  # build-env uses bioconda-utils that's local; create-env uses the build-env
  # tagged after this version.
  if [ "$TYPE" == "create-env" ]; then
    BUILD_ARGS+=("--build-arg=bioconda_utils_version=$BIOCONDA_UTILS_VERSION")
  fi
fi

if [ "$TYPE" == "base-busybox" ]; then 
  [ -z "$BUSYBOX_VERSION" ] && echo "Please set BUSYBOX_VERSION" && exit 1
  BUILD_ARGS+=("--build-arg=busybox_version=$BUSYBOX_VERSION")

  # Make a busybox image that we'll use further below. As shown in the
  # Dockerfile.busybox, this uses the build-busybox script which in turn
  # cross-compiles for x86_64 and aarch64, and these execuables are later
  # copied into an arch-specific container.
  #
  # Note that --iidfile (used here and in later commands) prints the built
  # image ID to the specified file so we can refer to the image later.
  iidfile="$( mktemp )"
  buildah bud \
    --iidfile="${iidfile}" \
    --file=Dockerfile.busybox \
    ${BUILD_ARGS[@]}
  busybox_image="$( cat "${iidfile}" )"
  rm "${iidfile}"

  # And then extend the build args with this image.
  BUILD_ARGS+=("--build-arg=busybox_image=${busybox_image}")
fi

# ----------------------------------------------------------------------

# Build each arch's image using the array of archs.
#
for arch_and_image in "${archs_and_images[@]}" ; do
  arch=$(echo $arch_and_image | cut -f1 -d "=")
  base_image=$(echo $arch_and_image | cut -f2 -d "=")

  # build-env is the only one that needs an arch-specific base image from
  # conda-forge; this needs to be set within this loop rather than adding to
  # BUILD_ARGS array.
  BASE_IMAGE_BUILD_ARG=""
  if [ "$TYPE" == "build-env" ]; then
    BASE_IMAGE_BUILD_ARG="--build-arg=base_image="${base_image}""
  fi

  # Actual building happens here.
  iidfile="$( mktemp )"
  buildah bud \
    --arch="${arch}" \
    --iidfile="${iidfile}" \
    ${BUILD_ARGS[@]} \
    $BASE_IMAGE_BUILD_ARG
  image_id="$( cat "${iidfile}" )"
  rm "${iidfile}"

  # Extract various package info and version info, then store that info
  # as labels. Container is removed at the end to avoid e.g. having these
  # commands in the history of the container.
  container="$( buildah from "${image_id}" )"
  run() { buildah run "${container}" "${@}" ; }
  LABELS=()
  LABELS+=("--label=deb-list=$( run cat /.deb.lst | tr '\n' '|' | sed 's/|$//' )")
  LABELS+=("--label=pkg-list=$( run cat /.pkg.lst | tr '\n' '|' | sed 's/|$//' )")
  LABELS+=("--label=glibc=$( run sh -c 'exec "$( find -xdev -name libc.so.6 -print -quit )"' | sed '1!d' )")
  LABELS+=("--label=debian=$( run cat /etc/debian_version | sed '1!d' )")
  LABELS+=("--label=bash=$( run bash --version | sed '1!d' )")
  if [ "$TYPE" == "build-env" ]; then
    bioconda_utils="$(
      run sh -c '. /opt/conda/etc/profile.d/conda.sh && conda activate base && bioconda-utils --version' \
      | rev | cut -f1 -d " " | rev
    )"
    LABELS+=("--label=bioconda-utils=${bioconda_utils}")

    # save conda/mamba versions to install in create-env
    conda_version=$(
      run sh -c '/opt/conda/bin/conda/list --export "^(conda|mamba)$"' \
      | sed -n 's/=[^=]*$//p'
    )
  fi

  if [ ! -z "${BUSYBOX_VERSION}" ]; then
    LABELS+=("--label=busybox-version=${BUSYBOX_VERSION}")
  fi
  buildah rm "${container}"

  # Add labels to a new container...
  container="$( buildah from "${image_id}" )"
  buildah config ${LABELS[@]} "${container}"

  # ...then store the container (now with labels) as a new image. This
  # is what we'll use to eventually upload.
  image_id="$( buildah commit "${container}" )"
  buildah rm "${container}"

  # Add images to manifest. Individual image tags include arch; manifest does not.
  for tag in ${TAGS} ; do
    buildah tag \
      "${image_id}" \
      "${IMAGE_NAME}:${tag}-${arch}"
    buildah manifest add \
      "${IMAGE_NAME}:${tag}" \
      "${image_id}"

    buildah inspect -t image ${image_name}:${tag}-${arch}
  done # tags
done # archs_and_images
buildah inspect -t manifest ${image_name}

# Extract image IDs from the manifest built in the last step
ids="$(
  for tag in ${{ inputs.tags }} ; do
    buildah manifest inspect "${image_name}:${tag}" \
      | jq -r '.manifests[]|.digest' \
      | while read id ; do
          buildah images --format '{{.ID}}{{.Digest}}' \
          | sed -n "s/${id}//p"
        done
  done
  )"

# Run the tests; see Dockerfile.test in the relevant image dir for the
# actual tests run
ids="$( printf %s "${ids}" | sort -u )"
for id in ${ids} ; do
  podman history "${id}"
  buildah bud \
    --build-arg=base="${id}" \
    --file=Dockerfile.test \
    "${IMAGE_DIR}"
done

# Clean up
buildah rmi --prune || true

# TODO: what should be exported here? Image IDs? Manifest? How do we access
# this stuff outside the job?
