"""
Mulled Tests
"""

import json
import subprocess as sp
import tempfile
import os
import shlex
import logging

from . import utils

from conda_build.metadata import MetaData
from conda_index.index import update_index
from conda_package_streaming.package_streaming import stream_conda_info

logger = logging.getLogger(__name__)

MULLED_CONDA_IMAGE = "quay.io/bioconda/create-env:latest"


def get_tests(path):
    "Extract tests from a built package"
    tmp = tempfile.mkdtemp()
    for tar, member in stream_conda_info(path):
        if member.name.startswith("info/recipe/"):
            tar.extract(member, tmp)
    input_dir = os.path.join(tmp, "info", "recipe")

    tests = [
        "/usr/local/env-execute true",
        ". /usr/local/env-activate.sh",
    ]
    recipe_meta = MetaData(input_dir)

    tests_commands = recipe_meta.get_value("test/commands")
    tests_imports = recipe_meta.get_value("test/imports")
    requirements = recipe_meta.get_value("requirements/run")

    if tests_imports or tests_commands:
        if tests_commands:
            tests.append(" && ".join(tests_commands))
        if tests_imports and "python" in requirements:
            tests.append(
                " && ".join('python -c "import %s"' % imp for imp in tests_imports)
            )
        elif tests_imports and (
            "perl" in requirements or "perl-threaded" in requirements
        ):
            tests.append(
                " && ".join('''perl -e "use %s;"''' % imp for imp in tests_imports)
            )

    tests = " && ".join(tests)
    tests = tests.replace("$R ", "Rscript ")
    # this is specific to involucro, the way how we build our containers
    tests = tests.replace("$PREFIX", "/usr/local")
    tests = tests.replace("${PREFIX}", "/usr/local")

    return f"bash -c {shlex.quote(tests)}"


def get_image_name(path):
    """
    Returns name of generated docker image.

    Parameters
    ----------

    path : str
        Path to .tar.bz2 or .conda package build by conda-build

    """
    if path.endswith(".tar.bz2"):
        ext = ".tar.bz2"
    elif path.endswith(".conda"):
        ext = ".conda"
    else:
        raise ValueError()

    pkg = os.path.basename(path).removesuffix(ext)
    toks = pkg.split("-")
    build_string = toks[-1]
    version = toks[-2]
    name = "-".join(toks[:-2])

    spec = "%s=%s--%s" % (name, version, build_string)
    return spec


def _generate_explicit_spec(spec, channels, conda_bld_dir, tmpdir):
    """Generate an @EXPLICIT spec file by dry-running conda create.

    Parameters
    ----------
    spec : str
        Package spec in name=version--build format
    channels : list
        List of resolved channel URLs (no "local")
    conda_bld_dir : str
        Path to local conda-bld directory
    tmpdir : str
        Directory to write the spec file into

    Returns
    -------
    str or None
        Path to explicit spec file, or None on failure
    """
    # Convert spec from mulled format (name=version--build) to conda format (name=version=build)
    pkg_spec = spec.replace("--", "=")

    channel_args = []
    for ch in channels:
        channel_args += ["-c", ch]

    cmd = (
        [
            "conda",
            "create",
            "--dry-run",
            "--json",
            "--override-channels",
        ]
        + channel_args
        + [pkg_spec]
    )

    logger.debug("Generating explicit spec: %s", cmd)
    try:
        result = sp.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.debug("conda create --dry-run failed: %s", result.stderr)
            return None

        data = json.loads(result.stdout)
        actions = data.get("actions", {})
        link_actions = actions.get("LINK", [])
        if not link_actions:
            logger.debug("No LINK actions in dry-run output")
            return None

        # Build @EXPLICIT spec file
        spec_path = os.path.join(tmpdir, "explicit_spec.txt")
        with open(spec_path, "w") as f:
            f.write("@EXPLICIT\n")
            for pkg in link_actions:
                url = pkg.get("url")
                if not url:
                    # Build URL from channel + subdir + fn
                    channel = pkg.get("channel", "")
                    subdir = pkg.get("platform", pkg.get("subdir", ""))
                    fn = pkg.get("dist_name", "")
                    if fn and not fn.endswith((".tar.bz2", ".conda")):
                        fn += ".conda"
                    url = f"{channel}/{subdir}/{fn}"
                md5 = pkg.get("md5", "")
                line = url
                if md5:
                    line += f"#{md5}"
                f.write(line + "\n")

        logger.debug("Generated explicit spec with %d packages", len(link_actions))
        return spec_path

    except (sp.TimeoutExpired, json.JSONDecodeError, KeyError) as exc:
        logger.debug("Failed to generate explicit spec: %s", exc)
        return None


def _test_with_explicit_spec(
    spec_path, tests, base_image, conda_image, conda_bld_dir, live_logs
):
    """Run mulled test using a pre-solved explicit spec file.

    Parameters
    ----------
    spec_path : str
        Path to @EXPLICIT spec file
    tests : str
        Test commands to run
    base_image : str
        Base image for the test container
    conda_image : str
        Conda image used to install packages
    conda_bld_dir : str
        Path to local conda-bld directory (needed for file:// URLs)
    live_logs : bool
        Enable live log output
    """
    # Build a test script that:
    # 1. Creates env from explicit spec
    # 2. Runs create-env post-processing (activation/entrypoint scripts)
    # 3. Runs the test commands
    test_script = f"""#!/bin/bash
set -eo pipefail

# Install from pre-solved explicit spec (no solver needed)
conda create --name test --file /opt/explicit_spec.txt --yes --quiet

# Run create-env to set up activation/entrypoint scripts
# This replicates the POSTINSTALL step from involucro
create-env --conda=: /usr/local

# Run tests
{tests}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "test_script.bash")
        with open(script_path, "w") as f:
            f.write(test_script)

        cmd = [
            "docker",
            "run",
            "-t",
            "--net",
            "host",
            "--rm",
            "-v",
            f"{script_path}:/opt/test_script.bash:ro",
            "-v",
            f"{spec_path}:/opt/explicit_spec.txt:ro",
            "-v",
            f"{conda_bld_dir}:{conda_bld_dir}:ro",
        ]

        env_args = []
        if base_image is not None:
            env_args += ["-e", f"DEST_BASE_IMAGE={base_image}"]

        cmd += env_args
        cmd += [conda_image]
        cmd += ["/bin/bash", "/opt/test_script.bash"]

        logger.debug("Pre-solved mulled test command: %s", cmd)
        with utils.Progress():
            p = utils.run(cmd, mask=False, live=live_logs)
        return p


def test_package(
    path,
    name_override=None,
    channels=("conda-forge", "local", "bioconda"),
    mulled_args="",
    base_image=None,
    conda_image=MULLED_CONDA_IMAGE,
    live_logs=True,
    presolved=True,
):
    """
    Tests a built package in a minimal docker container.

    Parameters
    ----------
    path : str
        Path to a .tar.bz2 or .conda package built by conda-build

    name_override : str
        Passed as the --name-override argument to mulled-build

    channels : list
        List of Conda channels to use. Must include an entry "local" for the
        local build channel.

    mulled_args : str
        Mechanism for passing arguments to the mulled-build command. They will
        be split with shlex.split and passed to the mulled-build command. E.g.,
        mulled_args="--dry-run --involucro-path /opt/involucro"

    base_image : None | str
        Specify custom base image. Busybox is used in the default case.

    conda_image : None | str
        Conda Docker image to install the package with during the mulled based
        tests.

    live_logs : True | bool
        If True, enable live logging during the build process

    presolved : bool
        If True, attempt to pre-solve the test environment on the host and
        pass an @EXPLICIT spec file to the container, avoiding a redundant
        solver run. Falls back to the original mulled-build path on failure.
    """

    assert path.endswith((".tar.bz2", ".conda")), "Unrecognized path {0}".format(path)
    # assert os.path.exists(path), '{0} does not exist'.format(path)

    conda_bld_dir = os.path.abspath(os.path.dirname(os.path.dirname(path)))

    update_index(conda_bld_dir)

    spec = get_image_name(path)

    if "local" not in channels:
        raise ValueError('"local" must be in channel list')

    resolved_channels = [
        "file://{0}".format(conda_bld_dir) if channel == "local" else channel
        for channel in channels
    ]

    tests = get_tests(path)
    logger.debug("Tests to run: %s", tests)

    # Try the pre-solved path first for faster testing
    if presolved:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                spec_path = _generate_explicit_spec(
                    spec,
                    resolved_channels,
                    conda_bld_dir,
                    tmpdir,
                )
                if spec_path is not None:
                    logger.info("Using pre-solved explicit spec for mulled test")
                    return _test_with_explicit_spec(
                        spec_path,
                        tests,
                        base_image,
                        conda_image,
                        conda_bld_dir,
                        live_logs,
                    )
                else:
                    logger.info("Pre-solve failed, falling back to mulled-build")
        except Exception as exc:
            logger.info(
                "Pre-solved test failed (%s), falling back to mulled-build", exc
            )

    # Original mulled-build path
    channel_args = ["--channels", ",".join(resolved_channels)]

    cmd = [
        "mulled-build",
        "build-and-test",
        spec,
        "-n",
        "biocontainers",
        "--test",
        tests,
    ]
    if name_override:
        cmd += ["--name-override", name_override]
    cmd += channel_args
    cmd += shlex.split(mulled_args)

    # galaxy-lib always downloads involucro, unless it's in cwd or its path is explicitly given.
    # We inject a POSTINSTALL to the involucro command with a small wrapper to
    # create activation / entrypoint scripts for the container.
    involucro_path = os.path.join(os.path.dirname(__file__), "involucro")
    if not os.path.exists(involucro_path):
        raise RuntimeError("internal involucro wrapper missing")
    cmd += ["--involucro-path", involucro_path]

    logger.debug("mulled-build command: %s" % cmd)

    env = os.environ.copy()
    if base_image is not None:
        env["DEST_BASE_IMAGE"] = base_image
    env["CONDA_IMAGE"] = conda_image
    with tempfile.TemporaryDirectory() as d:
        with utils.Progress():
            p = utils.run(cmd, env=env, cwd=d, mask=False, live=live_logs)

    return p
