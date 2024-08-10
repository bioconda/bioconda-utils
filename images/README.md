The intended use is to run `build.sh`, providing it an image directory.


Image directories must at least contain the following:

- `prepare.sh` script, where the first line should be `source ../versions.sh`
- `Dockerfile` for building
- `Dockerfile.test` for testing.

`build.sh` sources `<IMAGE DIR>/prepare.sh`, which sources `versions.sh` to
populate the env vars needed for that particular image.
`<IMAGE_DIR>/prepare.sh` should also do any other needed work in preparation
for building.

A note on locale: we prepare the C.utf8 locale ahead of time in
`locale/generate_locale.sh`. This can be copied over to image dirs if/when
needed by `prepare.sh`. Previously, we were preparing the locale each time in
an image and copying that out to subsequent image. Since this is expected to
change infrequently, storing it separately like this in the repo allows us to
remove the dependency of building that first image.

E.g.,

```
bash build.sh base-glibc-busybox-bash
```
