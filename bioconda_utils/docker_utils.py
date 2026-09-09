"""
To ensure conda packages are built in the most compatible manner, we can use
a docker container. This module supports using a docker container to build
conda packages in the local channel which can later be uploaded to anaconda.

Note that  we cannot simply bind the host's conda-bld directory to the
container's conda-bld directory because during building/testing, symlinks are
made to the conda-pkgs dir which is not necessarily bound. Nor should it be, if
we want to retain isolation from the host when building.

To solve this, we mount the host's conda-bld dir to a temporary directory in
the container. Once the container successfully builds a package, the
corresponding package is copied over to the temporary directory (the host's
conda-bld directory) so that the built package appears on the host.

In the end the workflow is:

    - build a custom docker container (assumed to already have conda installed)
      where the requirements in
      ``bioconda-utils/bioconda-utils_requirements.txt`` have been conda
      installed.

    - mount the host's conda-bld to a read/write temporary dir in the container
      (configured in the RecipeBuilder)

    - in the container, add this directory as a local channel so that all
      previously-built packages can be used as dependencies.

    - mount the host's recipe dir to a read-only dir in the container
      (configured in the RecipeBuilder)

    - build, mount, and run a custom script that conda-builds the mounted
      recipe and if successful copies the built package to the mounted host's
      conda-bld directory.

Other notes:

- Most communication with the host (conda-bld options; host's UID) is via
  environmental variables passed to the container.

- The build script is custom generated each run, providing lots of flexibility.
  Most magic happens here.
"""

import grp
import logging
import os
import os.path
import pwd
import re
import shutil
import subprocess as sp
import tempfile
from importlib.resources import as_file, files
from pathlib import Path
from shlex import quote
from typing import Protocol

from packaging.version import Version

from . import utils
from ._types import (
    ALL_PACKAGE_SUBDIRS,
    ContainerPlatform,
    PkgBuildRef,
    Subdir,
    container_platform_to_package_subdir,
    local_mulled_image_ref,
    native_container_platform,
)

logger = logging.getLogger(__name__)

LOCAL_CHANNEL_SUBDIRS = tuple(
    subdir for subdir in ALL_PACKAGE_SUBDIRS if subdir.startswith("linux-")
) + ("noarch",)
LOCAL_CHANNEL_MKDIRS = "\n  ".join(
    f'mkdir -p "${{local_channel}}"/{subdir}' for subdir in LOCAL_CHANNEL_SUBDIRS
)
LOCAL_CHANNEL_SUBDIR_ARGS = " ".join(quote(subdir) for subdir in LOCAL_CHANNEL_SUBDIRS)


PUBLISH_BUILT_PACKAGES_TEMPLATE = r"""
# Publish only packages produced by this successful conda-build invocation.
# conda-build chooses each output's channel subdirectory, which matters for
# recipes that mix architecture-specific and noarch outputs.
for subdir in {local_channel_subdirs}; do
  output_subdir="${{build_output}}/${{subdir}}"
  test -d "${{output_subdir}}" || continue
  while IFS= read -r -d '' package; do
    destination='{self.container_staging}'/"${{subdir}}/${{package##*/}}"
    cp -- "${{package}}" "${{destination}}"
    chown {self.user_info[uid]}:{self.user_info[gid]} "${{destination}}"
  done < <(
    find "${{output_subdir}}" -maxdepth 1 -type f \
      \( -name '*.tar.bz2' -o -name '*.conda' \) -print0
  )
done
"""


class CondaBuildConfigFile(Protocol):
    arg: str
    path: str


# ----------------------------------------------------------------------------
# BUILD_SCRIPT_TEMPLATE
# ----------------------------------------------------------------------------
#
# The following script is the default that will be regenerated on each call to
# RecipeBuilder.build_recipe() and mounted at container run time when building
# a recipe. It can be overridden by providing a different template when calling
# RecipeBuilder.build_recipe().
#
# It will be filled in using BUILD_SCRIPT_TEMPLATE.format(self=self), so you
# can add additional attributes to the RecipeBuilder instance and have them
# filled in here.
#
BUILD_SCRIPT_TEMPLATE = r"""
#!/bin/bash
set -eo pipefail

# Add the host's mounted conda-bld dir so that we can use its contents as
# dependencies for building this recipe.
#
# Note that if the directory didn't exist on the host, then the staging area
# will exist in the container but will be empty.  Channels expect at least
# Linux and noarch channel subdirectories within that directory, so we make
# sure they exist before adding the channel.
# Also ensure conda-build's local channel directory exists the same way.
for local_channel in '/opt/conda/conda-bld' '{self.container_staging}'; do
  {local_channel_mkdirs}
  conda index "${{local_channel}}"
done
conda config --add channels file://{self.container_staging} 2> >(
    grep -vF "Warning: 'file://{self.container_staging}' already in 'channels' list, moving to the top" >&2
)

# Pass on conda_pkg_format ("2" for .conda instead of .tar.bz2) from host's conda-build config.
#test -n '{self.conda_pkg_format}' && conda config --set conda_build.pkg_format '{self.conda_pkg_format}'

# Build into a clean, container-local output channel. Keeping this separate
# from the mounted staging channel ensures that a failed multi-output build
# cannot publish only some of its packages to the host.
build_output=$(mktemp -d /opt/conda/bioconda-output.XXXXXX)

# The actual building...
# we explicitly point to the meta.yaml, in order to keep
# conda-build from building all subdirectories
conda-build -c file://{self.container_staging} {self.conda_build_args} \
  --output-folder "${{build_output}}" {self.container_recipe}/meta.yaml 2>&1

{publish_built_packages}
conda index {self.container_staging}
"""

# ----------------------------------------------------------------------------
# DOCKERFILE_TEMPLATE
# ----------------------------------------------------------------------------
#
# This template can be used for last-minute changes to the docker image, such
# as adding proxies.
#
# The default image is created automatically for releases using the Dockerfile
# in the bioconda-utils repo.

DOCKERFILE_TEMPLATE = r"""
FROM {docker_base_image}
{proxies}
RUN find /opt/conda \
      \! -group lucky \
      -exec chgrp --no-dereference lucky {{}} + \
      \! -type l \
      -exec chmod g=u {{}} +
"""  # noqa: E122 continuation line missing indentation or outdented


class DockerCalledProcessError(sp.CalledProcessError):
    pass


class RecipeBuilder:
    def __init__(
        self,
        tag: str = "tmp-bioconda-builder",
        container_recipe: str = "/opt/recipe",
        container_staging: str = "/opt/host-conda-bld",
        requirements: str | None = None,
        build_script_template: str = BUILD_SCRIPT_TEMPLATE,
        dockerfile_template: str = DOCKERFILE_TEMPLATE,
        use_host_conda_bld: bool = False,
        pkg_dir: str | None = None,
        keep_image: bool = False,
        build_image: bool = False,
        image_build_dir: str | None = None,
        docker_base_image: str | None = None,
        target_platform: ContainerPlatform | None = None,
        container_pkgs_cache: str | None = None,
    ) -> None:
        """
        Class to handle building a custom docker container that can be used for
        building conda recipes.

        Parameters
        ----------
        tag : str
            Tag to be used for the custom-build docker container. Mostly for
            debugging purposes when you need to inspect the container.

        container_recipe : str
            Directory to which the host's recipe will be exported. Will be
            read-only.

        container_staging : str
            Directory to which the host's conda-bld dir will be mounted so that
            the container can use previously-built packages as dependencies.
            Upon successful building container-built packages will be copied
            over. Mounted as read-write.

        requirements : None or str
            Path to a "requirements.txt" file which will be installed with
            conda in a newly-created container. If None, then use the default
            installed with bioconda_utils.

        build_script_template : str
            Template that will be filled in with .format(self=self) and that
            will be run in the container each time build_recipe() is called. If
            not specified, uses docker_utils.BUILD_SCRIPT_TEMPLATE.

        dockerfile_template : str
            Template that will be filled in with .format(self=self) and that
            will be used to build a custom image. Uses
            docker_utils.DOCKERFILE_TEMPLATE by default.

        use_host_conda_bld : bool
            If True, then use the host's conda-bld directory. This will export
            the host's existing conda-bld directory to the docker container,
            and any recipes successfully built by the container will be added
            here.

            Otherwise, use **pkg_dir** as a common host directory used across
            multiple runs of this RecipeBuilder object.

        pkg_dir : str or None
            Specify where packages should appear on the host.

            If **pkg_dir** is None, then a temporary directory will be
            created once for each `RecipeBuilder` instance and that directory
            will be used for each call to `RecipeBuilder.build_recipe()`. This allows
            subsequent recipes built by the container to see previous built
            recipes without polluting the host's conda-bld directory.

            If **pkg_dir** is a string, then it will be created if needed and
            this directory will be used store all built packages on the host
            instead of the temp dir.

            If the above argument **use_host_conda_bld** is `True`, then the value
            of **pkg_dir** will be ignored and the host's conda-bld directory
            will be used.

            In all cases, **pkg_dir** will be mounted to **container_staging** in
            the container.

        build_image : bool
            Build a local layer on top of the **docker_base_image** layer using
            **dockerfile_template**. This can be used to adjust the versions of
            conda and conda-build in the build container.

        keep_image : bool
            By default, the built docker image will be removed when done,
            freeing up storage space.  Set ``keep_image=True`` to disable this
            behavior.

        image_build_dir : str or None
            If not None, use an existing directory as a docker image context
            instead of a temporary one. For testing purposes only.

        docker_base_image : str or None
            Name of base image that can be used in **dockerfile_template**.

        container_pkgs_cache : str or None
            Host directory bind-mounted at /opt/conda/pkgs in build
            containers, so repodata, shards indexes and downloaded build/host
            env packages persist across the containers of one build run.
            Falls back to the BIOCONDA_UTILS_CONTAINER_PKGS_CACHE
            environment variable when not given.
        """
        self.requirements = requirements
        # Host directory bind-mounted at /opt/conda/pkgs in build containers
        # so repodata/shards and downloaded build/host env packages persist
        # across the containers of one build run. Falls back to the
        self.container_pkgs_cache = container_pkgs_cache or os.environ.get(
            "BIOCONDA_UTILS_CONTAINER_PKGS_CACHE"
        )
        if self.container_pkgs_cache:
            os.makedirs(self.container_pkgs_cache, exist_ok=True)
            # build containers run as a different user (uid 9001 "conda") and
            # conda writes cache state even on cache hits
            os.chmod(self.container_pkgs_cache, 0o777)
        self.conda_build_args = ""
        self.target_platform: ContainerPlatform | None = target_platform
        self.build_script_template: str = build_script_template
        self.dockerfile_template = dockerfile_template
        self.keep_image = keep_image
        self.build_image = build_image
        self.image_build_dir = image_build_dir
        self.docker_base_image = docker_base_image
        self.docker_temp_image = tag

        if not self.build_image:
            self._ensure_base_image()

        # find and store user info
        uid = os.getuid()
        usr = pwd.getpwuid(uid)
        self.user_info = {
            "uid": uid,
            "gid": usr.pw_gid,
            "groupname": grp.getgrgid(usr.pw_gid).gr_name,
            "username": usr.pw_name,
        }

        self.container_recipe = container_recipe
        self.container_staging = container_staging

        conda_build_config = utils.load_conda_build_config()
        # Identify conda-bld directory on the host.
        self.host_conda_bld = conda_build_config.croot
        # Pass on config to choose wheter to build .tar.bz2 or .conda format.
        self.conda_pkg_format = conda_build_config.conda_pkg_format or ""

        if use_host_conda_bld:
            self.pkg_dir = self.host_conda_bld
        else:
            if pkg_dir is None:
                self.pkg_dir = tempfile.mkdtemp()
            else:
                if not os.path.exists(pkg_dir):
                    os.makedirs(pkg_dir)
                self.pkg_dir = pkg_dir

        # Copy the conda build config files to the staging directory that is
        # visible in the container
        for i, config_file in enumerate(utils.get_conda_build_config_files()):
            dst_file = self._get_config_path(self.pkg_dir, i, config_file)
            if not os.path.exists(self.pkg_dir):
                os.makedirs(self.pkg_dir)
            shutil.copyfile(config_file.path, dst_file)
        if self.build_image:
            self._build_image()

    def _ensure_base_image(self) -> None:
        """Ensure the requested build image and platform are available locally."""
        image = self.docker_base_image
        if image is None:
            raise ValueError("docker_base_image is required when build_image is false")

        result = sp.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Os}}/{{.Architecture}}",
                image,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode == 0 and (
            self.target_platform is None
            or result.stdout.strip() == self.target_platform
        ):
            return

        logger.info("Pulling Docker build image %s", image)
        command = ["docker", "pull"]
        if self.target_platform is not None:
            command += ["--platform", self.target_platform]
        command.append(image)
        utils.run(command, live=True)

    def _get_config_path(
        self, staging_prefix: str, i: int, config_file: CondaBuildConfigFile
    ) -> str:
        src_basename = os.path.basename(config_file.path)
        dst_basename = f"conda_build_config_{i}_{config_file.arg}_{src_basename}"
        return os.path.join(staging_prefix, dst_basename)

    def _output_subdir(self, noarch: bool) -> Subdir:
        """Return the legacy ``{arch}`` template value for this build.

        The default build script preserves the subdirectory selected by
        conda-build for every output. Custom templates may still use
        ``{arch}``, so keep it accurate for cross-platform builds.
        """
        if noarch:
            return "noarch"
        target_platform = self.target_platform or native_container_platform()
        return container_platform_to_package_subdir(target_platform)

    def __del__(self) -> None:
        self.cleanup()

    def _find_proxy_settings(self) -> dict[str, str]:
        res: dict[str, str] = {}
        for var in ("http_proxy", "https_proxy"):
            candidates = [
                val
                for val in (os.environ.get(var), os.environ.get(var.upper()))
                if val is not None
            ]
            if len(candidates) == 1:
                res[var] = candidates[0]
            elif len(candidates) > 1:
                raise ValueError(f"{var} and {var.upper()} have different values")
        return res

    def _build_image(self) -> sp.CompletedProcess:
        """
        Builds a new image with requirements installed.
        """

        if self.image_build_dir is None:
            # Create a temporary build directory since we'll be copying the
            # requirements file over
            build_dir = tempfile.mkdtemp()
        else:
            build_dir = self.image_build_dir

        logger.info(
            'DOCKER: Building image "%s" from %s',
            self.docker_temp_image,
            build_dir,
        )
        with open(os.path.join(build_dir, "requirements.txt"), "w") as fout:
            if self.requirements:
                fout.write(Path(self.requirements).read_text())
            else:
                # pkg_resources (deprecated) is replaced with importlib.resources
                with (
                    as_file(
                        files("bioconda_utils") / "bioconda_utils-requirements.txt"
                    ) as req_path,
                    open(req_path, encoding="utf-8") as fh,
                ):
                    fout.write(fh.read())

        proxies = "\n".join(f"ENV {k} {v}" for k, v in self._find_proxy_settings())

        with open(os.path.join(build_dir, "Dockerfile"), "w") as fout:
            fout.write(
                self.dockerfile_template.format(
                    docker_base_image=self.docker_base_image, proxies=proxies
                )
            )

        logger.debug("Dockerfile:\n%s", Path(fout.name).read_text())

        # Check if the installed version of docker supports the --network flag
        # (requires version >= 1.13.0)
        # Parse output of `docker --version` since the format of the
        #  `docker version` command (note the missing dashes) is not consistent
        # between different docker versions. The --version string is the same
        # for docker 1.6.2 and 1.12.6
        try:
            s = sp.check_output(["docker", "--version"]).decode()
        except FileNotFoundError:
            logger.error(
                "DOCKER FAILED: Error checking docker version, is it installed?"
            )
            raise
        except sp.CalledProcessError:
            logger.error("DOCKER FAILED: Error checking docker version.")
            raise
        p = re.compile(
            r"\d+\.\d+\.\d+"
        )  # three groups of at least on digit separated by dots
        version_match = re.search(p, s)
        if version_match is None:
            raise ValueError(f"Unable to parse docker version from {s!r}")
        version_string = version_match.group(0)
        if Version(version_string) >= Version("1.13.0"):
            cmd = [
                "docker",
                "build",
                # xref #5027
                "--network",
                "host",
                "-t",
                self.docker_temp_image,
                build_dir,
            ]
        else:
            # Network flag was added in 1.13.0, do not add it for lower versions. xref #5387
            cmd = ["docker", "build", "-t", self.docker_temp_image, build_dir]
        if self.target_platform:
            cmd[2:2] = ["--platform", self.target_platform]

        try:
            with utils.Progress():
                p = utils.run(cmd)
        except sp.CalledProcessError:
            logger.error(
                "DOCKER FAILED: Error building docker container %s. ",
                self.docker_temp_image,
            )
            raise

        logger.info("DOCKER: Built docker image tag=%s", self.docker_temp_image)
        if self.image_build_dir is None:
            shutil.rmtree(build_dir)
        return p

    def build_recipe(
        self,
        recipe_dir: str,
        build_args: str,
        env: dict[str, str],
        noarch: bool = False,
        live_logs: bool = True,
    ) -> sp.CompletedProcess:
        """
        Build a single recipe.

        Parameters
        ----------

        recipe_dir : str
            Path to recipe that contains meta.yaml

        build_args : str
            Additional arguments to ``conda build``. For example --channel,
            --skip-existing, etc

        env : dict
            Environmental variables

        noarch: bool
            Whether to expose ``noarch`` through the legacy ``{arch}``
            custom-template placeholder. The default template preserves the
            subdirectory conda-build selects independently for every output.

        Note that the binds are set up automatically to match the expectations
        of the build script, and will use the currently-configured
        self.container_staging and self.container_recipe.
        """

        # Attach the build args to self so that it can be filled in by the
        # template.
        if not isinstance(build_args, str):
            raise TypeError("build_args must be str")
        build_args_list = [build_args]
        for i, config_file in enumerate(utils.get_conda_build_config_files()):
            dst_file = self._get_config_path(self.container_staging, i, config_file)
            build_args_list.extend([config_file.arg, quote(dst_file)])
        self.conda_build_args = " ".join(build_args_list)

        # Write build script to tempfile
        build_dir = os.path.realpath(tempfile.mkdtemp())
        publish_built_packages = PUBLISH_BUILT_PACKAGES_TEMPLATE.format_map(
            {
                "self": self,
                "local_channel_subdirs": LOCAL_CHANNEL_SUBDIR_ARGS,
            }
        )
        script = self.build_script_template.format_map(
            {
                "self": self,
                "arch": self._output_subdir(noarch),
                "local_channel_mkdirs": LOCAL_CHANNEL_MKDIRS,
                "publish_built_packages": publish_built_packages,
            }
        )
        with open(os.path.join(build_dir, "build_script.bash"), "w") as fout:
            fout.write(script)
        build_script = fout.name
        logger.debug(
            "DOCKER: Container build script: \n%s", Path(fout.name).read_text()
        )

        # Build the args for env vars. Note can also write these to tempfile
        # and use --env-file arg, but using -e seems clearer in debug output.
        env_list = []
        for k, v in env.items():
            env_list.append("-e")
            env_list.append(f"{k}={v}")

        env_list.append("-e")
        env_list.append("{}={}".format("HOST_USER_ID", self.user_info["uid"]))

        cmd = [
            "docker",
            "run",
            "-t",
            "--net",
            "host",
            "--rm",
        ]
        if self.target_platform:
            cmd += ["--platform", self.target_platform]
        cmd += [
            "-v",
            f"{build_script}:/opt/build_script.bash",
            "-v",
            f"{self.pkg_dir}:{self.container_staging}",
            "-v",
            f"{recipe_dir}:{self.container_recipe}",
        ]
        # Optionally persist the container's conda package cache (repodata,
        # shards indexes, downloaded packages) across the containers of one
        # build run. Containers are ephemeral (--rm); without this, every
        # recipe re-downloads repodata and its build/host env packages.
        # Enabled via --container-pkgs-cache (or the
        # BIOCONDA_UTILS_CONTAINER_PKGS_CACHE environment variable).
        if self.container_pkgs_cache:
            cmd += [
                "-v",
                f"{self.container_pkgs_cache}:/opt/conda/pkgs",
            ]
        cmd += env_list
        image = self.docker_temp_image if self.build_image else self.docker_base_image
        if image is None:
            raise ValueError("docker_base_image is required when build_image is false")
        cmd += [image]
        cmd += ["/bin/bash", "/opt/build_script.bash"]

        logger.debug("DOCKER: cmd: %s", cmd)
        with utils.Progress():
            p = utils.run(cmd, live=live_logs)
        return p

    def cleanup(self) -> None:
        if self.build_image and not self.keep_image:
            cmd = ["docker", "rmi", self.docker_temp_image]
            utils.run(cmd)


def purgeImage(
    img: PkgBuildRef,
    target_platform: ContainerPlatform | None = None,
) -> None:
    """Remove the local mulled image ``mulled-build`` produced for *img*.

    The local image is tagged under the canonical ``biocontainers`` namespace
    by ``pkg_test.build_and_test_mulled_image`` (not the upload target), so the
    ref is derived via :func:`local_mulled_image_ref` -- the same source
    :func:`bioconda_utils.upload.mulled_upload` reads from when copying to the
    registry.
    """
    cmd = ["docker", "rmi", local_mulled_image_ref(img, target_platform)]
    utils.run(cmd)


def pruneStoppedContainers() -> None:
    cmd = ["docker", "container", "prune", "-f"]
    utils.run(cmd)
