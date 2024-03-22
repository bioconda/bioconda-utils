# Changelog

## [2.13.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.12.0...v2.13.0) (2024-03-22)


### Features

* add osx-arm64 to platform checks ([#965](https://www.github.com/bioconda/bioconda-utils/issues/965)) ([9f6df10](https://www.github.com/bioconda/bioconda-utils/commit/9f6df10bfecd048956acc80e7bb3d57952585529))

## [2.12.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.11.1...v2.12.0) (2024-03-18)

### Features

* add support for excluding otherwise-selected recipes ([#962](https://www.github.com/bioconda/bioconda-utils/issues/962)) ([3946732](https://www.github.com/bioconda/bioconda-utils/commit/3946732eb6129f6905e53b62d76287e09d4bef36))
* bioconductor improvements ([#944](https://www.github.com/bioconda/bioconda-utils/issues/944)) ([b007d34](https://www.github.com/bioconda/bioconda-utils/commit/b007d34e6c723f7f9d6fcb5a6f58e072d4618cdf))
* Bulk build failure wiki ([#948](https://www.github.com/bioconda/bioconda-utils/issues/948)) ([18f988d](https://www.github.com/bioconda/bioconda-utils/commit/18f988d70966f6f6296170d96cc1ced51ad10392))


### Bug Fixes

* Do not emit cython_needs_compiler if compiler("cxx") is set ([#927](https://www.github.com/bioconda/bioconda-utils/issues/927)) ([8255afd](https://www.github.com/bioconda/bioconda-utils/commit/8255afdd9e5c0fd3cb09cb11269f5ff3397c959e))

### [2.11.1](https://www.github.com/bioconda/bioconda-utils/compare/v2.11.0...v2.11.1) (2023-12-13)


### Bug Fixes

* add local channel for docker builds ([#945](https://www.github.com/bioconda/bioconda-utils/issues/945)) ([de8ce00](https://www.github.com/bioconda/bioconda-utils/commit/de8ce00d1ccf6a395ff6adce97f71b5c6059500f))
* Fix version number check in repodata_patches_no_version_bump() ([#946](https://www.github.com/bioconda/bioconda-utils/issues/946)) ([73e69b2](https://www.github.com/bioconda/bioconda-utils/commit/73e69b2f9aabb06f693518b8ee195c7fa897bc76))

## [2.11.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.10.0...v2.11.0) (2023-11-30)


### Features

* make GITHUB_TOKEN optional for fetch ([#942](https://www.github.com/bioconda/bioconda-utils/issues/942)) ([c0eab1d](https://www.github.com/bioconda/bioconda-utils/commit/c0eab1d7224d6b13ebe399e7933460249e4e9a58))

## [2.10.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.9.0...v2.10.0) (2023-11-26)


### Features

* live logs for mulled build ([#939](https://www.github.com/bioconda/bioconda-utils/issues/939)) ([7f83d7f](https://www.github.com/bioconda/bioconda-utils/commit/7f83d7f66ab81279a5d7c990b9311d493d416d5b))


### Bug Fixes

* specify involucro path when uploading ([#941](https://www.github.com/bioconda/bioconda-utils/issues/941)) ([3086cc0](https://www.github.com/bioconda/bioconda-utils/commit/3086cc083213b9084ba7d0ee5bc12e0d86cebc0b))


### Documentation

* run_export -> run_exports plural in help message ([#928](https://www.github.com/bioconda/bioconda-utils/issues/928)) ([2c5d4ad](https://www.github.com/bioconda/bioconda-utils/commit/2c5d4ad754f7bfa17b90495dc602118c7270d4bc))

## [2.9.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.8.0...v2.9.0) (2023-11-05)


### Features

* use new container version by default (3.0) ([#935](https://www.github.com/bioconda/bioconda-utils/issues/935)) ([11d53db](https://www.github.com/bioconda/bioconda-utils/commit/11d53dbb18d5edf0a6a546c5a53c6d5e942dfc4a))

## [2.8.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.7.0...v2.8.0) (2023-11-02)


### Features

* Enable Live logs and add option to disable ([#930](https://www.github.com/bioconda/bioconda-utils/issues/930)) ([47eaadc](https://www.github.com/bioconda/bioconda-utils/commit/47eaadcd4f0da856733e3fd3170d3451ec9c4b8d))


### Bug Fixes

* try locale C.utf8 ([#931](https://www.github.com/bioconda/bioconda-utils/issues/931)) ([584fcdd](https://www.github.com/bioconda/bioconda-utils/commit/584fcddd45854b88cdf4af72df0a1ad5cc3c9fcc))

## [2.7.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.6.0...v2.7.0) (2023-10-14)


### Features

* add support to run build for recipes with linux-aarch64 additional-platforms ([#923](https://www.github.com/bioconda/bioconda-utils/issues/923)) ([55671f7](https://www.github.com/bioconda/bioconda-utils/commit/55671f77124065fd09bb7d9c4a856cf0e87e48a4))

## [2.6.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.5.0...v2.6.0) (2023-10-08)


### Features

* download aarch64 artifacts from CircleCI ([#921](https://www.github.com/bioconda/bioconda-utils/issues/921)) ([b9cddd4](https://www.github.com/bioconda/bioconda-utils/commit/b9cddd42eb7c45dbbd207cf5d209ea328c02eff1))

## [2.5.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.4.0...v2.5.0) (2023-10-04)


### Features

* add special lints for bioconda-repodata-patches recipe ([#855](https://www.github.com/bioconda/bioconda-utils/issues/855)) ([11c9229](https://www.github.com/bioconda/bioconda-utils/commit/11c92296bbc566fcd481ea45c4d55247b4ba154d))


### Bug Fixes

* adding "vals" to GithubRelease changes for API and expanded assets ([#912](https://www.github.com/bioconda/bioconda-utils/issues/912)) ([9e0e445](https://www.github.com/bioconda/bioconda-utils/commit/9e0e44581cade8d994a5b923964df060122b7519))
* Remove htslib build pinning ([#917](https://www.github.com/bioconda/bioconda-utils/issues/917)) ([c7efb92](https://www.github.com/bioconda/bioconda-utils/commit/c7efb9250312abdcfbdc10be60b5a0fa92e52726))
* Version constraints can start with `!` as well ([#919](https://www.github.com/bioconda/bioconda-utils/issues/919)) ([ee56f6e](https://www.github.com/bioconda/bioconda-utils/commit/ee56f6e1d20aa7c96f150ff79a084faf0521e70b)), closes [#918](https://www.github.com/bioconda/bioconda-utils/issues/918)


### Documentation

* try to clarify some confusion with run_exports. ([#914](https://www.github.com/bioconda/bioconda-utils/issues/914)) ([417e7da](https://www.github.com/bioconda/bioconda-utils/commit/417e7da6524c71ac0aafdcd75244bd001de17efd))

## [2.4.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.3.4...v2.4.0) (2023-08-24)


### Features

* lint missing run exports ([#908](https://www.github.com/bioconda/bioconda-utils/issues/908)) ([84f5c0c](https://www.github.com/bioconda/bioconda-utils/commit/84f5c0c0b4ec4ba4f1756cbe74da50151efa77a3))


### Bug Fixes

* recover --docker-base-image feature ([#906](https://www.github.com/bioconda/bioconda-utils/issues/906)) ([aa08857](https://www.github.com/bioconda/bioconda-utils/commit/aa088572d3721837d08556634961c5c7d86814c1))
* swaps from github API to expanding the assets ([#907](https://www.github.com/bioconda/bioconda-utils/issues/907)) ([2099a40](https://www.github.com/bioconda/bioconda-utils/commit/2099a405f1b888699eb2026146aa0b4f3f070fb0))
* use package name instead of folder for build failure list dag check ([#910](https://www.github.com/bioconda/bioconda-utils/issues/910)) ([11fb14f](https://www.github.com/bioconda/bioconda-utils/commit/11fb14fd1a95c05dca96887d0d9935f6954447e2))

### [2.3.4](https://www.github.com/bioconda/bioconda-utils/compare/v2.3.3...v2.3.4) (2023-07-15)


### Bug Fixes

* update pinning for libxml2 due to conda-forge migration ([#903](https://www.github.com/bioconda/bioconda-utils/issues/903)) ([ee226f8](https://www.github.com/bioconda/bioconda-utils/commit/ee226f8e84a0820430c105412f08714a4de34715))

### [2.3.3](https://www.github.com/bioconda/bioconda-utils/compare/v2.3.2...v2.3.3) (2023-07-06)


### Bug Fixes

* adds alternate lookup if base lookup fails ([#896](https://www.github.com/bioconda/bioconda-utils/issues/896)) ([17a1475](https://www.github.com/bioconda/bioconda-utils/commit/17a147508a64bdafb6031c05a75724b25669ec8d))
* update r-base pinning ([#901](https://www.github.com/bioconda/bioconda-utils/issues/901)) ([54d8702](https://www.github.com/bioconda/bioconda-utils/commit/54d870205d823b6f292df5258b980cbbaa97df77))

### [2.3.2](https://www.github.com/bioconda/bioconda-utils/compare/v2.3.1...v2.3.2) (2023-06-29)


### Bug Fixes

* update to boa 0.15 which supports strict channel priorities ([#887](https://www.github.com/bioconda/bioconda-utils/issues/887)) ([52f5e6d](https://www.github.com/bioconda/bioconda-utils/commit/52f5e6db3262dd36ac88c4db72dbf7085712eb46))

### [2.3.1](https://www.github.com/bioconda/bioconda-utils/compare/v2.3.0...v2.3.1) (2023-06-07)


### Bug Fixes

* improved release workflow ([7abe60d](https://www.github.com/bioconda/bioconda-utils/commit/7abe60dec3e457fa98cff6dd0428327564f4315e))
* improved release workflow ([18aa50b](https://www.github.com/bioconda/bioconda-utils/commit/18aa50bbcf8d11e4229c0b1a0e1b8beaac58eab7))
* improved release workflow ([62b8ebe](https://www.github.com/bioconda/bioconda-utils/commit/62b8ebee298a6aaec5cc5fc75af7a9aeec2afde5))

## [2.3.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.2.1...v2.3.0) (2023-06-07)


### Features

* always add a fallback archive URL to the meta.yaml when using bioconductor-skeleton ([#886](https://www.github.com/bioconda/bioconda-utils/issues/886)) ([d885495](https://www.github.com/bioconda/bioconda-utils/commit/d885495d54b177411863fdca3bdfa35ca781457f))


### Bug Fixes

* various little fixes for build failure records and automatic skiplisting ([#894](https://www.github.com/bioconda/bioconda-utils/issues/894)) ([715efc2](https://www.github.com/bioconda/bioconda-utils/commit/715efc27319afda8b7fe19a0d112a84e8b8569c9))

### [2.2.1](https://www.github.com/bioconda/bioconda-utils/compare/v2.2.0...v2.2.1) (2023-05-22)


### Bug Fixes

* use git cli to obtain recipe commit sha, since there seems to be no fast and correct way to do that with gitpython ([#892](https://www.github.com/bioconda/bioconda-utils/issues/892)) ([a6fd713](https://www.github.com/bioconda/bioconda-utils/commit/a6fd7134d03fe39760654d63a9c279c0bd92afd5))

## [2.2.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.1.0...v2.2.0) (2023-05-22)


### Features

* add subcommand to skiplist a given recipe using the new recipe specific mechanism; in addition, some fixes for the new skiplisting approach, and renaming blacklist into skiplist ([#890](https://www.github.com/bioconda/bioconda-utils/issues/890)) ([da7a912](https://www.github.com/bioconda/bioconda-utils/commit/da7a912c72a3b2d5566804b942ffecd585edd803))

## [2.1.0](https://www.github.com/bioconda/bioconda-utils/compare/v2.0.0...v2.1.0) (2023-05-21)


### Features

* add ability to store build failures as yaml next to recipe, add flag to automatically do so upon build failures, consider such files as blacklisting if they include `blacklist: true`. ([#888](https://www.github.com/bioconda/bioconda-utils/issues/888)) ([e78c120](https://www.github.com/bioconda/bioconda-utils/commit/e78c120a239cf1b845aba44043d51b89f7a27d55))

## [2.0.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.7.1...v2.0.0) (2023-05-18)


### ⚠ BREAKING CHANGES

* remove old unused bot code and simplejson dependency (#883)

### Features

* remove old unused bot code and simplejson dependency ([#883](https://www.github.com/bioconda/bioconda-utils/issues/883)) ([cab3df5](https://www.github.com/bioconda/bioconda-utils/commit/cab3df5de9ba50a181b4f64954dd513c828957d0))

### [1.7.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.7.0...v1.7.1) (2023-05-11)


### Bug Fixes

* support proxy settings via the usual environment variables (HTTPS_PROXY, ...) ([#881](https://www.github.com/bioconda/bioconda-utils/issues/881)) ([5d21b64](https://www.github.com/bioconda/bioconda-utils/commit/5d21b64aced5f8406dfd1de72d58e4c11b98d54f))

## [1.7.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.6.2...v1.7.0) (2023-05-08)


### Performance Improvements

* update to latest conda-forge pinnings and ditch building for Python 3.7 and 3.6 ([#878](https://www.github.com/bioconda/bioconda-utils/issues/878)) ([0704708](https://www.github.com/bioconda/bioconda-utils/commit/0704708238a0793b0f9d7363dc2470418952030d))
* update to latest conda-forge pinnings and ditch building for Python 3.7 and 3.6 ([#878](https://www.github.com/bioconda/bioconda-utils/issues/878)) ([6b5e9f5](https://www.github.com/bioconda/bioconda-utils/commit/6b5e9f53bce9380ff9495dedd2abe45fb9dfcf07))


### Miscellaneous Chores

* release 1.7.0 ([9bf3ba9](https://www.github.com/bioconda/bioconda-utils/commit/9bf3ba91c2a00d890b52121203cc8a2e296fdfaf))

### [1.6.2](https://www.github.com/bioconda/bioconda-utils/compare/v1.6.1...v1.6.2) (2023-05-04)


### Bug Fixes

* fix obtaining LegacyVersion class from pkg_resources ([bceae0f](https://www.github.com/bioconda/bioconda-utils/commit/bceae0f87c5a9008e8a6dd9e9d98a3a1f8313f51))

### [1.6.1](https://www.github.com/bioconda/bioconda-utils/compare/v1.6.0...v1.6.1) (2023-05-04)


### Bug Fixes

* use strict channel priorities in the right order in the container image ([#874](https://www.github.com/bioconda/bioconda-utils/issues/874)) ([6e6c91a](https://www.github.com/bioconda/bioconda-utils/commit/6e6c91a5af5d857f157eb5250f2f87ad56fbc28d))

## [1.6.0](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.7...v1.6.0) (2023-05-02)


### Features

* Add --mulled-conda-image ([#867](https://www.github.com/bioconda/bioconda-utils/issues/867)) ([1923d24](https://www.github.com/bioconda/bioconda-utils/commit/1923d24c4f3cd38740ecfbf240b92d5eb1432e09))
* add Linux aarch64/arm64 support for bioconda-utils ([#866](https://www.github.com/bioconda/bioconda-utils/issues/866)) ([794ec06](https://www.github.com/bioconda/bioconda-utils/commit/794ec068afd3b1eaababb79e2680cf2ad3fdc1a2))


### Bug Fixes

* allow lint for blacklist to see blacklisted recipes ([#863](https://www.github.com/bioconda/bioconda-utils/issues/863)) ([0e63e73](https://www.github.com/bioconda/bioconda-utils/commit/0e63e73c22e3c1160eb5c8ad3f35c34ac4ea6f27))
* fix autobump ([#865](https://www.github.com/bioconda/bioconda-utils/issues/865)) ([b6b674c](https://www.github.com/bioconda/bioconda-utils/commit/b6b674ca81326a6bd6700cb9802b3d7440c08762))


### Performance Improvements

* upgrade to latest conda, conda-build, and boa versions ([#872](https://www.github.com/bioconda/bioconda-utils/issues/872)) ([21a6452](https://www.github.com/bioconda/bioconda-utils/commit/21a6452fcad99b78f976746a2b14339e094327df))

### [1.5.7](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.6...v1.5.7) (2023-03-24)


### Bug Fixes

* uploading of noarch artifacts and other small improvements to artifact uploading ([#860](https://www.github.com/bioconda/bioconda-utils/issues/860)) ([da41c36](https://www.github.com/bioconda/bioconda-utils/commit/da41c365350aaacca0b779ef62068515f1c3c19e))

### [1.5.6](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.5...v1.5.6) (2023-03-23)


### Bug Fixes

* properly handle platforms as list ([0b2cf30](https://www.github.com/bioconda/bioconda-utils/commit/0b2cf301d5a6344541c43e9e333105c5d7c55c12))

### [1.5.5](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.4...v1.5.5) (2023-03-23)


### Bug Fixes

* improved logging for artifact upload, use QUAY_LOGIN env var ([6b7a56d](https://www.github.com/bioconda/bioconda-utils/commit/6b7a56d5fb861dc81c1b48fdc5343d97d50d0725))

### [1.5.4](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.3...v1.5.4) (2023-03-10)


### Bug Fixes

* use correct ssl certs for skopeo upload ([#856](https://www.github.com/bioconda/bioconda-utils/issues/856)) ([a48dcf3](https://www.github.com/bioconda/bioconda-utils/commit/a48dcf3ef5b72829ea2787847e1ec4d24ba04893))

### [1.5.3](https://www.github.com/bioconda/bioconda-utils/compare/v1.5.2...v1.5.3) (2023-03-09)


### Bug Fixes

* load config before instantiating repodata in artifact upload ([#853](https://www.github.com/bioconda/bioconda-utils/issues/853)) ([946442f](https://www.github.com/bioconda/bioconda-utils/commit/946442fd6386f5e8fd61df125c7bbc8d95837e9a))

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
