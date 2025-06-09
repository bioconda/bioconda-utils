# Overview 

This is where the various images used by bioconda-utils are created.

Here, we build the base containers (busybox and debian), the build container
(has bioconda-utils and conda), the create container (only conda), and the bot
(includes autobump and responding to PRs and issues).

See https://bioconda.github.io/developer/dockerfile-inventory.html for context
on the containers themselves and how they relate to each other.

The primary components are:

- `image_config.sh` configures versions and defines some useful functions. It
  should be sourced before doing anything else.
- Each image has a separate directory and has at least:
  - `prepare.sh`, which sources the `image_config.sh` described above.
    `prepare.sh` is responsible for setting image-specific env vars needed for
    the build
  - `Dockerfile` creates the image
  - `Dockerfile.test` tests the image.
- `build.sh` is provided with one of these image directories, and will source
  the respective `prepare.sh` to set env vars for the image.

## GitHub Actions

See `.github/workflows/build-images.yml` for how this is configured to run on
GitHub Actions, which largely follows the method described below for building
locally.

## Building locally

Building locally has the following requirements:

- podman installed
- docker installed
- docker registry running on localhost:5000
    - i.e. with `docker run -p 5000:5000 --rm --name registry registry`,
      optionally with `-d` to run in detached mode
- bioconda-utils installed, along with test dependencies
    - i.e. with `conda create -p ./env --file bioconda_utils/bioconda_utils-requirements.txt --file test-requirements.txt -y`
    - followed by `conda activate ./env && pip install -e .`

Use the following commands to build and test locally:

```bash

cd images
source image_config.sh

time bash build.sh base-glibc-busybox-bash
time bash build.sh base-glibc-debian-bash
time bash build.sh build-env
time bash build.sh create-env
time bash build.sh bot

build_and_push_manifest ${BASE_DEBIAN_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
build_and_push_manifest ${CREATE_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000
build_and_push_manifest ${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000
ONLY_AMD64=true build_and_push_manifest ${BOT_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000

# Run bioconda-utils tests
export DEFAULT_BASE_IMAGE="localhost:5000/${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG}"
export DEFAULT_EXTENDED_BASE_IMAGE="localhost:5000/${BASE_DEBIAN_IMAGE_NAME}:${BASE_TAG}"
export BUILD_ENV_IMAGE="localhost:5000/${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG}"
export CREATE_ENV_IMAGE="localhost:5000/${CREATE_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG}"

docker pull $DEFAULT_BASE_IMAGE
docker pull $DEFAULT_EXTENDED_BASE_IMAGE
docker pull $BUILD_ENV_IMAGE
docker pull $CREATE_ENV_IMAGE

cd ../
py.test --durations=0 test/ -v --log-level=DEBUG -k "docker" --tb=native
```


## How locale is handled

Previously, we were preparing the locale each time in an image and copying that
out to subsequent image. However, we expect the C.utf8 locale to change
infrequently. So now we store it separately in the repo and copy it in. It was
initially prepared with `locale/generate_locale.sh` and stored in
`locale/C.utf8`; if the locale needs updating then this script and output
should be updated.
