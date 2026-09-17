"""
Subprocess execution and environment handling.

Central place for running external commands with logging and secret
redaction (:func:`run`), for locating conda executables, and for
shaping the environment that child processes (and conda-build's jinja
rendering) get to see.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import os
import queue
import subprocess as sp
from collections import deque
from collections.abc import Sequence
from threading import Thread
from typing import Any

from yaspin import Spinner, yaspin
from yaspin.spinners import Spinners

logger = logging.getLogger(__name__)


ENV_VAR_WHITELIST = [
    "PATH",
    "LC_*",
    "LANG",
    "MACOSX_DEPLOYMENT_TARGET",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
]

# Of those that make it through the whitelist, remove these specific ones
ENV_VAR_BLACKLIST = []

# Of those, also remove these when we're running in a docker container
ENV_VAR_DOCKER_BLACKLIST = [
    "PATH",
]


def allowed_env_var(s: str, docker: bool = False) -> bool:
    for pattern in ENV_VAR_WHITELIST:
        if fnmatch.fnmatch(s, pattern):
            for bpattern in ENV_VAR_BLACKLIST:
                if fnmatch.fnmatch(s, bpattern):
                    return False
            if docker:
                for dpattern in ENV_VAR_DOCKER_BLACKLIST:
                    if fnmatch.fnmatch(s, dpattern):
                        return False
            return True
    return False


def bin_for(name: str = "conda") -> str:
    if "CONDA_ROOT" in os.environ:
        return os.path.join(os.environ["CONDA_ROOT"], "bin", name)
    return name


@contextlib.contextmanager
def temp_env(env):
    """
    Context manager to temporarily set os.environ.

    Used to send values in **env** to processes that only read the os.environ,
    for example when filling in meta.yaml with jinja2 template variables.

    All values are converted to string before sending to os.environ
    """
    env = dict(env)
    orig = os.environ.copy()
    _env = {k: str(v) for k, v in env.items()}
    os.environ.update(_env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(orig)


@contextlib.contextmanager
def sandboxed_env(env):
    """
    Context manager to temporarily set os.environ, only allowing env vars from
    the existing `os.environ` or the provided **env** that match
    ENV_VAR_WHITELIST globs.
    """
    os_environ = os.environ
    orig = os_environ.copy()
    env = dict(env)

    try:
        os_environ.clear()
        os_environ.update({k: v for k, v in orig.items() if allowed_env_var(k)})
        os_environ.update({k: str(v) for k, v in env.items() if allowed_env_var(k)})
        yield
    finally:
        os_environ.clear()
        os_environ.update(orig)


def run(
    cmds: list[str],
    env: dict[str, str] | None = None,
    secrets: Sequence[str] | None = None,
    live: bool = False,
    mylogger: logging.Logger = logger,
    loglevel: int = logging.INFO,
    check: bool = True,
    quiet_failure: bool = False,
    **kwargs: Any,
) -> sp.CompletedProcess:
    """
    Run a command (with logging, masking, etc)

    - Explicitly decodes stdout to avoid UnicodeDecodeErrors that can occur when
      using the ``universal_newlines=True`` argument in the standard
      subprocess.run.
    - Masks secrets when supplied
    - Passes live output to `logging`

    Arguments:
      cmds: List of command and arguments
      env: Optional environment for command, if None, use environment of the parent process
      secrets: Optional sequence of terms to redact (secrets) from logs and
        output. A single term may be passed as a bare string.
      live: Whether output should be sent to log
      check: raise CalledProcessError on failure
      kwargs: Additional arguments to `subprocess.Popen`

    Returns:
      CompletedProcess object

    Raises:
      subprocess.CalledProcessError if the process failed
      FileNotFoundError if the command could not be found
    """
    logq = queue.Queue()
    if isinstance(secrets, str):
        # A single secret passed as a bare string: wrap it so it is redacted
        # as a whole instead of being iterated character by character.
        secrets = [secrets]
    active_secrets = secrets

    def pushqueue(out, pipe):
        """Reads from a pipe and pushes into a queue, pushing "None" to
        indicate closed pipe"""
        for line in iter(pipe.readline, b""):
            out.put((pipe, line))
        out.put(None)  # End-of-data-token

    def redact_secrets(arg: str) -> str:
        """Redacts secrets in **arg**"""
        if active_secrets:
            for mitem in active_secrets:
                if mitem:
                    arg = arg.replace(mitem, "<hidden>")
        return arg

    log_cmd = " ".join(redact_secrets(arg) for arg in cmds)

    mylogger.log(loglevel, "(COMMAND) %s", log_cmd)

    # bufsize=4 result of manual experimentation. Changing it can
    # drop performance drastically.
    with sp.Popen(
        cmds,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
        close_fds=True,
        env=env,
        bufsize=4,
        **kwargs,
    ) as proc:
        # Start threads reading stdout/stderr and pushing it into queue q
        out_thread = Thread(target=pushqueue, args=(logq, proc.stdout))
        err_thread = Thread(target=pushqueue, args=(logq, proc.stderr))
        out_thread.daemon = True  # Do not wait for these threads to terminate
        err_thread.daemon = True
        out_thread.start()
        err_thread.start()

        def handle_output(output_lines):
            try:
                for _ in range(2):  # Run until we've got both `None` tokens
                    for pipe, line in iter(logq.get, None):
                        line = redact_secrets(line.decode(errors="replace").rstrip())
                        output_lines.append(line)
                        # only keep the last 1000 lines to avoid memory issues
                        if len(output_lines) > 1000:
                            output_lines.popleft()
                        if live:
                            if pipe == proc.stdout:
                                prefix = "OUT"
                            else:
                                prefix = "ERR"
                            mylogger.log(loglevel, "(%s) %s", prefix, line)
            except Exception:
                proc.kill()
                proc.wait()
                raise

        output_lines = deque()
        if not live:
            spinner = Spinner(interval=5000, frames=Spinners.dots.frames)
            with yaspin(spinner, text="running", timer=True):
                handle_output(output_lines)
        else:
            handle_output(output_lines)

        output = "\n".join(output_lines)
        masked_cmds = [redact_secrets(c) for c in cmds]

        if proc.poll() is None:
            mylogger.log(loglevel, "Command closed STDOUT/STDERR but is still running")
            waitfor = 30
            waittimes = 5
            for attempt in range(waittimes):
                mylogger.log(
                    loglevel,
                    "Waiting %s seconds (%i/%i)",
                    waitfor,
                    attempt + 1,
                    waittimes,
                )
                try:
                    proc.wait(timeout=waitfor)
                    break
                except sp.TimeoutExpired:
                    pass
            else:
                mylogger.log(loglevel, "Terminating process")
                proc.kill()
                proc.wait()
        returncode = proc.poll()
        assert returncode is not None

        if returncode:
            if not quiet_failure:
                logger.error(
                    "COMMAND FAILED (exited with %s): %s",
                    returncode,
                    " ".join(masked_cmds),
                )
            if not live:
                logger.error("STDOUT+STDERR:\n%s", output)
            if check:
                raise sp.CalledProcessError(returncode, masked_cmds, output=output)

        return sp.CompletedProcess(masked_cmds, returncode, stdout=output)
