"""Completeness

Verify that the recipe is not missing anything essential.
"""

import os
from . import LintCheck, ERROR, WARNING, INFO, _recipe


class missing_build_number(LintCheck):
    """The recipe is missing a build number

    Please add::

        build:
            number: 0
    """

    def check_recipe(self, recipe):
        if not recipe.get("build/number", ""):
            self.message(section="build")


class missing_home(LintCheck):
    """The recipe is missing a homepage URL

    Please add::

       about:
          home: <URL to homepage>

    """

    def check_recipe(self, recipe):
        if not recipe.get("about/home", ""):
            self.message(section="about")


class missing_summary(LintCheck):
    """The recipe is missing a summary

    Please add::

       about:
         summary: One line briefly describing package

    """

    def check_recipe(self, recipe):
        if not recipe.get("about/summary", ""):
            self.message(section="about")


class missing_license(LintCheck):
    """The recipe is missing the ``about/license`` key.

    Please add::

        about:
           license: <name of license>

    """

    def check_recipe(self, recipe):
        if not recipe.get("about/license", ""):
            self.message(section="about")


class missing_tests(LintCheck):
    """The recipe is missing tests.

    Please add::

        test:
            commands:
               - some_command

    and/or::

        test:
            imports:
               - some_module


    and/or any file named ``run_test.py`, ``run_test.sh`` or
    ``run_test.pl`` executing tests.

    You can either add the test section on the top level of
    your meta.yaml file; or if you use an ``outputs:`` section
    to specify multiple outputs, add a test to each of your
    ``outputs:`` entries.
    """

    test_files = ["run_test.py", "run_test.sh", "run_test.pl"]

    def check_recipe(self, recipe):
        if any(os.path.exists(os.path.join(recipe.dir, f)) for f in self.test_files):
            return
        # if multiple `outputs:` are specified, we check that
        # all subpackages have `test:` specified, but ignore
        # top-level tests, as top-level package outputs will
        # become incompatible with multiple `outputs:` in the
        # future, see:
        # https://conda.org/learn/ceps/cep-0014/#outputs-section
        if recipe.get("outputs", ""):
            packages = recipe.get("outputs")
        else:
            packages = [
                recipe,
            ]
        # can't use the Recipe.get function, as we might have plain dicts
        # here; so we need to go one level of the resulting dicts at
        # a time and check that all of them have a test specified
        tests_specified = [
            t.get("commands", "") or t.get("imports", "")
            for t in [p.get("test", {}) for p in packages]
        ]
        if all(tests_specified):
            return
        for i in range(len(packages)):
            if not tests_specified[i]:
                if not isinstance(packages[i], _recipe.Recipe) and packages[i].get(
                    f"outputs/{i}/test", ""
                ):
                    self.message(section=f"outputs/{i}/test")
                elif packages[i].get("test", ""):
                    self.message(section="test")
                else:
                    self.message()


class missing_hash(LintCheck):
    """The recipe is missing a checksum for a source file

    Please add::

       source:
         sha256: checksum-value

    """

    checksum_names = ("md5", "sha1", "sha256")

    def check_source(self, source, section):
        if not any(source.get(chk) for chk in self.checksum_names):
            self.message(section=section)
