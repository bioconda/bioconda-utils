import logging
from bioconda_utils import utils


logger = logging.getLogger(__name__)


def check_branch():
    branch = utils.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], mask=False).stdout
    if branch != "bulk":
        logger.error(
            "bulk-trigger-ci has to be executed on a checkout of the bulk branch"
        )
        exit(1)


def commit(message=None):
    check_branch()
    utils.run(["git", "commit", "-a", "-m", f"[ci skip] {message}"], mask=False)


def trigger_ci():
    check_branch()
    utils.run(
        ["git", "commit", "--allow-empty", "-m", "[ci run] trigger bulk run"],
        mask=False,
    )
    utils.run(["git", "push"], mask=False)
