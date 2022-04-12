# Changelog

### [0.19.3](https://www.github.com/bioconda/bioconda-utils/compare/v0.19.2...v0.19.3) (2022-04-12)


### Bug Fixes

* Don't use conda build --output for finding output file names in the docker container ([#766](https://www.github.com/bioconda/bioconda-utils/issues/766)) ([bdb6c67](https://www.github.com/bioconda/bioconda-utils/commit/bdb6c672f1f2ddd5c423e2448f74cb49283daf86))
* https prompts to password, ssh to the rescue ([#762](https://www.github.com/bioconda/bioconda-utils/issues/762)) ([6282f2d](https://www.github.com/bioconda/bioconda-utils/commit/6282f2dc2a2ef5c8f0929674a1bcf397af13ca53))

### [0.19.2](https://www.github.com/bioconda/bioconda-utils/compare/v0.19.1...v0.19.2) (2022-04-07)


### Bug Fixes

* don't use conda build to get the output file list ([#764](https://www.github.com/bioconda/bioconda-utils/issues/764)) ([f6c7b6f](https://www.github.com/bioconda/bioconda-utils/commit/f6c7b6f2e469bfa6c12e072b3b2f1aa7efa0cc72))
* Use mambabuild for generating the output file list ([43c22aa](https://www.github.com/bioconda/bioconda-utils/commit/43c22aa5c970b3627c0815d50190d51e5aa161e0))

### [0.19.1](https://www.github.com/bioconda/bioconda-utils/compare/v0.19.0...v0.19.1) (2022-03-25)


### Bug Fixes

* loosen jinja2 requirements to enable installation on OSX ([#759](https://www.github.com/bioconda/bioconda-utils/issues/759)) ([7ebe4ae](https://www.github.com/bioconda/bioconda-utils/commit/7ebe4aec07ba0577c9b7726255f09866880b698c))

## [0.19.0](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.6...v0.19.0) (2022-03-23)


### Features

* update dependencies and switch to boa/mambabuild ([#755](https://www.github.com/bioconda/bioconda-utils/issues/755)) ([81a5292](https://www.github.com/bioconda/bioconda-utils/commit/81a529263e8f51f279b6f48d212b4720a7ed3b73))

### [0.18.6](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.5...v0.18.6) (2022-03-20)


### Bug Fixes

* The dependency graph should use the run-time requirements too! ([#756](https://www.github.com/bioconda/bioconda-utils/issues/756)) ([c49b638](https://www.github.com/bioconda/bioconda-utils/commit/c49b6384356f525b4f93a668fc9cd198004ce1bc))

### [0.18.5](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.4...v0.18.5) (2022-02-24)


### Bug Fixes

* Update to the most recent conda-build so git_url works again ([#753](https://www.github.com/bioconda/bioconda-utils/issues/753)) ([4eb01c5](https://www.github.com/bioconda/bioconda-utils/commit/4eb01c569999e1bdafb02ebbd7a3677910da0596))
* Update htslib pinning to 1.15

### [0.18.4](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.3...v0.18.4) (2022-02-21)


### Bug Fixes

* removing tag hard-coding and use tag_name for docker container ([#749](https://www.github.com/bioconda/bioconda-utils/issues/749)) ([a8e750c](https://www.github.com/bioconda/bioconda-utils/commit/a8e750c72a70e63f26f6ed2cb83f1cc9478338d9))

### [0.18.3](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.2...v0.18.3) (2022-02-21)


### Bug Fixes

* hard-code tag to fix docker container ([#747](https://www.github.com/bioconda/bioconda-utils/issues/747)) ([79946cd](https://www.github.com/bioconda/bioconda-utils/commit/79946cdba71fabac40eae60c1f513c878c85d71b))

### [0.18.2](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.1...v0.18.2) (2022-02-21)


### Bug Fixes

* image tag is screwed up ([#745](https://www.github.com/bioconda/bioconda-utils/issues/745)) ([3feb6d0](https://www.github.com/bioconda/bioconda-utils/commit/3feb6d01d6eeb606b77a5eb74b1f2240c5f48fa7))

### [0.18.1](https://www.github.com/bioconda/bioconda-utils/compare/v0.18.0...v0.18.1) (2022-02-21)


### Bug Fixes

* missing container ([#743](https://www.github.com/bioconda/bioconda-utils/issues/743)) ([caf6680](https://www.github.com/bioconda/bioconda-utils/commit/caf6680b5caa4443c80074561d96ff2ac3e072b3))

## 0.18.1

### Bug Fixes

* Sync libdeflate pinning with conda-forge

## [0.18.0](https://www.github.com/bioconda/bioconda-utils/compare/v0.17.10...v0.18.0) (2022-02-20)


### Features

* update conda pinnings and build docs (xref [#736](https://www.github.com/bioconda/bioconda-utils/issues/736)) ([#740](https://www.github.com/bioconda/bioconda-utils/issues/740)) ([53db163](https://www.github.com/bioconda/bioconda-utils/commit/53db1631cdc197922c8b5dd4d038420f4ac0b3c0))


### Bug Fixes

* allow pkg_dir to be empty by creating a real conda channel there ([#738](https://www.github.com/bioconda/bioconda-utils/issues/738)) ([276873d](https://www.github.com/bioconda/bioconda-utils/commit/276873d81a1edd2e7e492cbfc83d0184eee70d07))
