# Overview 

This is where the various images used by bioconda-utils are created.

Individual images for *each created conda package* are not created here (those
happen in pull requests to bioconda-recipes) but those depend on a base image
-- and the base image is created here.

See https://bioconda.github.io/developer/dockerfile-inventory.html for context
on the containers themselves and how they relate to each other.

Here are the relevant components:

- `image_config.sh` configures versions and defines some useful functions. It
  should be sourced before doing anything else.
- There is a directory for each of the images to build; each has a `prepare.sh`
  and a `Dockerfile` in it.
- `build.sh`, when given an image directory, will build that image.

## Building locally

Building locally has the following requirements:

- podman installed
- docker installed
- docker registry running on localhost:5000
    - i.e. with `docker run -p 5000:5000 --rm --name registry registry`
- bioconda-utils installed, along with test dependencies
    - i.e. with `conda create -p ./env --file bioconda_utils/bioconda_utils-requirements.txt --file test-requirements.txt -y`
    - followed by `conda activate ./env && pip install -e .`

Use the following commands to build and test locally:

```bash

cd images
source image_config.sh

export BUILD_ENV_REGISTRY="localhost"

time bash build.sh base-glibc-busybox-bash
time bash build.sh base-glibc-debian-bash
time bash build.sh build-env
time bash build.sh create-env
time bash build.sh bot
time bash build.sh bioconda-recipes-issue-responder



build_and_push_manifest ${BASE_DEBIAN_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
build_and_push_manifest ${BASE_BUSYBOX_IMAGE_NAME}:${BASE_TAG} docker://localhost:5000
build_and_push_manifest ${CREATE_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000
build_and_push_manifest ${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000
ONLY_AMD64=true build_and_push_manifest ${BOT_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000
ONLY_AMD64=true build_and_push_manifest ${ISSUE_RESPONDER_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG} docker://localhost:5000

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

## Details

Image directories must at least contain the following:

- `prepare.sh` script, where the first line should be `source
  ../image_config.sh`. It should also do any other work needed in preparation
  for building.
- `Dockerfile` for building
- `Dockerfile.test` for testing.

`build.sh` sources `<IMAGE DIR>/prepare.sh`, which sources `image_config.sh` to
populate the env vars needed for that particular image.

## How locale is handled

Previously, we were preparing the locale each time in an image and copying that
out to subsequent image. However, we expect the C.utf8 locale to change
infrequently. So now we store it separately in the repo and copy it in. It was
initially prepared with `locale/generate_locale.sh` and stored in
`locale/C.utf8`; if the locale needs updating then this script and output
should be updated.
