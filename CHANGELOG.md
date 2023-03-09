# Changelog

### [1.5.2](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.1...v1.5.2) (2023-03-09)


### Bug Fixes

* platform specific artifact upload and fixed file renaming for container image upload ([#851](https://www.github.com/bioconda/bioconda-utils/issues/851)) ([b56e1a4](https://www.github.com/bioconda/bioconda-utils/commit/b56e1a44ab8b10721fd5c530aaa7a8e8b56a8e21))

### [1.5.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.0...v1.5.1) (2023-03-08)


### Bug Fixes

* fix skopeo based upload of container images from build artifacts by removing colons in filenames ([#849](https://www.github.com/bioconda/bioconda-utils/issues/849)) ([4f2eec5](https://www.github.com/bioconda/bioconda-utils/commit/4f2eec5fbc3a44738b25ae601684a29646feacf9))

## [1.5.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.4.0...v1.5.0) (2023-03-02)


### Features

* make use of BIOCONDA_LABEL if specified when calling handle-merged-pr ([#845](https://www.github.com/bioconda/bioconda-utils/issues/845)) ([bc30cb5](https://www.github.com/bioconda/bioconda-utils/commit/bc30cb582cd6aad5062eae770697a32ea4706d81))

## [1.4.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.3.1...v1.4.0) (2023-03-02)


### Features

* bot-free merge handling ([#841](https://www.github.com/bioconda/bioconda-utils/issues/841)) ([1122dd3](https://www.github.com/bioconda/bioconda-utils/commit/1122dd34b5169c6a61d9da00c1af63a5976f2cf0))

### [1.3.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.3.0...v1.3.1) (2023-03-01)


### Bug Fixes

* Change logic for checking if there are missing commits in a branch vs… ([#843](https://www.github.com/bioconda/bioconda-utils/issues/843)) ([836ecee](https://www.github.com/bioconda/bioconda-utils/commit/836ecee662aaf7fd0e3c89a3354fd93712aa6924))


### Documentation

* remove docs ([#839](https://www.github.com/bioconda/bioconda-utils/issues/839)) ([d52810c](https://www.github.com/bioconda/bioconda-utils/commit/d52810c137013e14985c1e7d460cb38e5a49faad))
* support anchors in <details> ([#832](https://www.github.com/bioconda/bioconda-utils/issues/832)) ([2ad8b61](https://www.github.com/bioconda/bioconda-utils/commit/2ad8b61b36a797a289dbc989e2a4d4eae0d95df0))
* update bulk docs to include BioC updates ([#828](https://www.github.com/bioconda/bioconda-utils/issues/828)) ([1301a6c](https://www.github.com/bioconda/bioconda-utils/commit/1301a6c933de8e5db9cdaaf634efb968e3f69175))

## [1.3.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.2.0...v1.3.0) (2022-11-10)


### Features

* Update GLPK pin for BioC 3.16 bulk rebuild ([#825](https://www.github.com/bioconda/bioconda-utils/issues/825)) ([28a4dda](https://www.github.com/bioconda/bioconda-utils/commit/28a4dda0257b436d881da7717a88d75d6bf3067e))

## [1.2.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.5...v1.2.0) (2022-11-02)


### Features

* switch data packages to bioconductor-data-packages ([#822](https://www.github.com/bioconda/bioconda-utils/issues/822)) ([5abba86](https://www.github.com/bioconda/bioconda-utils/commit/5abba8609629145cf9f577cd3773f0b58922594f))
* Update bioconda_utils-conda_build_config.yaml ([#824](https://www.github.com/bioconda/bioconda-utils/issues/824)) ([f8c609a](https://www.github.com/bioconda/bioconda-utils/commit/f8c609a4da02ce24870d01bc47ea74de9021e969))


### Bug Fixes

* autobump cache checking on circleci ([954708e](https://www.github.com/bioconda/bioconda-utils/commit/954708ea0bb06b1b076f0b60f32f21f21644c20a))

### [1.1.5](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.4...v1.1.5) (2022-10-15)


### Bug Fixes

* Use newer mulled conda image ([#819](https://www.github.com/bioconda/bioconda-utils/issues/819)) ([1464d18](https://www.github.com/bioconda/bioconda-utils/commit/1464d180e21b59781bb54c8fe49fa78ffa029430))

### [1.1.4](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.3...v1.1.4) (2022-10-13)


### Bug Fixes

* restore from cache on circleci ([#815](https://www.github.com/bioconda/bioconda-utils/issues/815)) ([001d4f8](https://www.github.com/bioconda/bioconda-utils/commit/001d4f8d5e21b30024b3f62566c112570219ed3c))
* update mamba when performing mulled tests ([#818](https://www.github.com/bioconda/bioconda-utils/issues/818)) ([e39cc48](https://www.github.com/bioconda/bioconda-utils/commit/e39cc4893f58785ba63bc455fe082fe9467898d6))

### [1.1.3](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.2...v1.1.3) (2022-10-11)


### Bug Fixes

* Update pyopenssl pinning ([#814](https://www.github.com/bioconda/bioconda-utils/issues/814)) ([e4950b0](https://www.github.com/bioconda/bioconda-utils/commit/e4950b0bb08c298df6d63dda9eafefabdafdc339))
* Use mamba in mulled-build ([#810](https://www.github.com/bioconda/bioconda-utils/issues/810)) ([554e15b](https://www.github.com/bioconda/bioconda-utils/commit/554e15bba587a4f58ff967934a84d6117832cf2d))


### Documentation

* add cbrueffer to core. ([#811](https://www.github.com/bioconda/bioconda-utils/issues/811)) ([cecf50c](https://www.github.com/bioconda/bioconda-utils/commit/cecf50c2388bea487a6ff4b335067a6d11467358))

### [1.1.2](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.1...v1.1.2) (2022-10-08)


### Bug Fixes

* require a more recent pyopenssl ([#809](https://www.github.com/bioconda/bioconda-utils/issues/809)) ([adaebdc](https://www.github.com/bioconda/bioconda-utils/commit/adaebdc2448698efeccff746f4b76307124f20c4))


### Documentation

* Bioconductor data packages ([#802](https://www.github.com/bioconda/bioconda-utils/issues/802)) ([bb84df2](https://www.github.com/bioconda/bioconda-utils/commit/bb84df2d7c1a282a0a42a7cea81e6b20e077650d))
* reflect Jillians leave of the core team in the docs ([#807](https://www.github.com/bioconda/bioconda-utils/issues/807)) ([3e7c12b](https://www.github.com/bioconda/bioconda-utils/commit/3e7c12bd7dae5ff8b4d7b42efc1551f7397dfbec))

### [1.1.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.1.0...v1.1.1) (2022-09-13)


### Bug Fixes

* autobump uses correct version from common.sh ([#803](https://www.github.com/bioconda/bioconda-utils/issues/803)) ([81ba442](https://www.github.com/bioconda/bioconda-utils/commit/81ba4425dcd85c495518a2071d14e694e393d123))
* circleci yaml syntax ([#806](https://www.github.com/bioconda/bioconda-utils/issues/806)) ([3315057](https://www.github.com/bioconda/bioconda-utils/commit/33150577a97e16ab9e9b4c6443fff37fd456f22f))

## [1.1.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.0.1...v1.1.0) (2022-09-11)


### Features

* linter for missing space in dependency version contraints ([#795](https://www.github.com/bioconda/bioconda-utils/issues/795)) ([85ccb23](https://www.github.com/bioconda/bioconda-utils/commit/85ccb2382066f2b7a6faf3911e52ebe1623dfcc7))
* schedule docs on GitHub actions ([#800](https://www.github.com/bioconda/bioconda-utils/issues/800)) ([dde65d0](https://www.github.com/bioconda/bioconda-utils/commit/dde65d000b6fc978158e49ff875f50996a1bed11))
* Update htslib and conda-forge pinnings ([#797](https://www.github.com/bioconda/bioconda-utils/issues/797)) ([162d597](https://www.github.com/bioconda/bioconda-utils/commit/162d5977eb5acec4c378e1831e6e59c0f4872801))


### Documentation

* developer documentation updates ([#792](https://www.github.com/bioconda/bioconda-utils/issues/792)) ([87c16fe](https://www.github.com/bioconda/bioconda-utils/commit/87c16fe334b2aa5ffbe647d3941b3dfc6ebd53df))

### [1.0.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.0.0...v1.0.1) (2022-08-01)


### Bug Fixes

* Fix pinning and networkx in_degree syntax ([#793](https://www.github.com/bioconda/bioconda-utils/issues/793)) ([208d970](https://www.github.com/bioconda/bioconda-utils/commit/208d9709310776fedda719f1eb8911b7e4b05df8))

## [1.0.0](https://www.github.com/bioconda/bioconda-utils/compare/v0.20.0...v1.0.0) (2022-07-31)


### ⚠ BREAKING CHANGES

* update pinnings to python 3.10 (#790)

### Documentation

* index and faqs update ([#786](https://www.github.com/bioconda/bioconda-utils/issues/786)) ([1c2714c](https://www.github.com/bioconda/bioconda-utils/commit/1c2714ca1174ae715af85504784eac54f974bbb2))
* notes on updating bioconda-utils ([#788](https://www.github.com/bioconda/bioconda-utils/issues/788)) ([9078ae3](https://www.github.com/bioconda/bioconda-utils/commit/9078ae38ddec83e5afe908cd67cd5cf0fa2f960b))


### Miscellaneous Chores

* update pinnings to python 3.10 ([#790](https://www.github.com/bioconda/bioconda-utils/issues/790)) ([e4998a9](https://www.github.com/bioconda/bioconda-utils/commit/e4998a95ccabb5cdc83a58793b645509339ae650))

## [0.20.0](https://www.github.com/bioconda/bioconda-utils/compare/v0.19.4...v0.20.0) (2022-06-23)


### Features

* update mulled-test conda to 4.12 ([#780](https://www.github.com/bioconda/bioconda-utils/issues/780)) ([1934fcb](https://www.github.com/bioconda/bioconda-utils/commit/1934fcb8259b9d33b885b3c9031938f67ac4d1b9))


### Bug Fixes

* create missing config dir ([#777](https://www.github.com/bioconda/bioconda-utils/issues/777)) ([da0a3e4](https://www.github.com/bioconda/bioconda-utils/commit/da0a3e419b765f50f18fd1d6fce58dd2b83e85f1))
* duplicate logging ([#778](https://www.github.com/bioconda/bioconda-utils/issues/778)) ([cf36bf4](https://www.github.com/bioconda/bioconda-utils/commit/cf36bf4e1b097bdefc59aee316e32e7853ba9f18))
* find_best_bioc_version test ([#779](https://www.github.com/bioconda/bioconda-utils/issues/779)) ([c132758](https://www.github.com/bioconda/bioconda-utils/commit/c132758bf9bf43e5ef12636108b8c339652863dd))
* wrong usage of lstrip in ellipsize_recipes ([#737](https://www.github.com/bioconda/bioconda-utils/issues/737)) ([afb4006](https://www.github.com/bioconda/bioconda-utils/commit/afb4006310dc668ba1f14101cceb45bebb054efb))


### Documentation

* minor comment ([#785](https://www.github.com/bioconda/bioconda-utils/issues/785)) ([dadf9ac](https://www.github.com/bioconda/bioconda-utils/commit/dadf9ac2e1ed1339c638ad73210f464c49093a45))
* overhaul front page ([#781](https://www.github.com/bioconda/bioconda-utils/issues/781)) ([5640a66](https://www.github.com/bioconda/bioconda-utils/commit/5640a660f714ca8dd29f4c0e62270519eeaacf25))
* remove bot from api docs ([#774](https://www.github.com/bioconda/bioconda-utils/issues/774)) ([e940def](https://www.github.com/bioconda/bioconda-utils/commit/e940defb21d8fcf997792c1538e063f7d3b69a49))
* update guidelines.rst to mention grayskull ([#771](https://www.github.com/bioconda/bioconda-utils/issues/771)) ([cdc818e](https://www.github.com/bioconda/bioconda-utils/commit/cdc818e509fe71d9fd5158e6b5397121a9a9a0fc))

### [0.19.4](https://www.github.com/bioconda/bioconda-utils/compare/v0.19.3...v0.19.4) (2022-04-30)


### Bug Fixes

* fix problematic docs ([#769](https://www.github.com/bioconda/bioconda-utils/issues/769)) ([44d6d2d](https://www.github.com/bioconda/bioconda-utils/commit/44d6d2d36df13b450684668bc86ce2a85f44a63a))

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
