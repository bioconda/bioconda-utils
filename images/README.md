# Overview 

This is where the various images used by bioconda-utils are created.

Individual images for *each package* are not created here. But those depend on
a base image, and the base image is created here.

See https://bioconda.github.io/developer/dockerfile-inventory.html for context
on the containers themselves and how they relate to each other.

`.github/workflows/build-images.yml` is what orchestrates these image builds on
CI.

`versions.sh` sets env vars that are used to control versions across all
images. It also has some helper functions. It should be sourced before running
`build.sh`.

Then run `build.sh`, providing it an image directory.

When building locally for testing, you need podman installed. Then do the
following:

```bash
source versions.sh

export BUILD_ENV_IMAGE="localhost/${BUILD_ENV_IMAGE_NAME}:${BIOCONDA_IMAGE_TAG}"

bash build.sh base-glibc-busybox-bash
bash build.sh base-glibc-debian-bash
bash build.sh bioconda-utils-build-env-cos7
bash build.sh create-env
```

# Details

Image directories must at least contain the following:

- `prepare.sh` script, where the first line should be `source ../versions.sh`
- `Dockerfile` for building
- `Dockerfile.test` for testing.

`build.sh` sources `<IMAGE DIR>/prepare.sh`, which sources `versions.sh` to
populate the env vars needed for that particular image.
`<IMAGE_DIR>/prepare.sh` should also do any other needed work in preparation
for building.

# How locale is handled

Previously, we were preparing the locale each time in an image and copying that
out to subsequent image. However, we expect the C.utf8 locale to change
infrequently. So now we store it separately in the repo and copy it in. It was
initially prepared with `locale/generate_locale.sh`.
