import json
import os
from subprocess import run
import warnings

from . import LintCheck, LintMessage, ERROR, WARNING, INFO

class shellcheck(LintCheck):
    """The recipe uses shell scripts with portability/reliability issues

    The shell scripts should be checked and either fixed or annotated with
    # shellcheck disable=<error-code>
    tags
    """

    _SEVERITY_MAP = {
        "error": ERROR,
        "warning": WARNING,
        "info": INFO,
        "style": None
    }

    def _run_shellcheck(self, recipe):
        sh_files = [x for x in os.listdir(recipe.dir) if x.ends_with(".sh") and os.path.isfile(x)]
        if not sh_files:
            return None
        try:
            res = run(["shellcheck", "-f", "json1"] + sh_files, capture_output = True, check = False)
        except FileNotFoundError:
            warnings.warn("Skipping shellcheck linter because it could not be found")
            return None
        if res.returncode == 0:
            return None
        try:
            errors = json.loads(res.stdout)
        except json.JSONDecodeError:
            print("Skipping shellcheck it returned unexpected output: " + res.stdout)
            return None
        return errors["comments"]

    def fix(self, error):
        # shellcheck may provide in error["fix"] suggestions to fix the error, but
        # we don't implement them here. The error message is usually helpful enough
        return False

    @classmethod
    def _shellcheck_msg(cls, recipe, error):
        error_severity = cls._SEVERITY_MAP[error["level"]]
        if error_severity is None:
            return None
        return LintMessage(
            recipe=recipe,
            check=cls,
            severity=error_severity,
            title="shellcheck",
            body=error["message"],
            fname=error["file"],
            start_line=error["line"],
            end_line=error["endLine"],
            canfix=False
    )

    def check_recipe(self, recipe):
        errors = self._run_shellcheck(recipe)
        if errors is None:
            return
        for error in errors:
            if self.try_fix and error.get("fix", None):
                if self.fix(error):
                    continue
            msg = self._shellcheck_msg(recipe, error)
            if msg is not None:
                self.messages.append(msg)
