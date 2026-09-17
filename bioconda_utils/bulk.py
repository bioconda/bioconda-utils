import logging
import sys

from bioconda_utils.support.subproc import run

logger = logging.getLogger(__name__)


def check_branch() -> None:
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout
    if branch != "bulk":
        logger.error(
            "bulk-trigger-ci has to be executed on a checkout of the bulk branch"
        )
        sys.exit(1)


def commit(message: str | None = None) -> None:
    check_branch()
    run(["git", "commit", "-a", "-m", f"[ci skip] {message}"])


def trigger_ci() -> None:
    check_branch()
    run(["git", "commit", "--allow-empty", "-m", "[ci run] trigger bulk run"])
    run(["git", "push"])
