"""Syntax checks

These checks verify syntax (schema), in particular for the ``extra``
section that is otherwise free-form.

"""
import re

from . import LintCheck, ERROR, WARNING, INFO


class version_constraints_missing_whitespace(LintCheck):
    """Packages and their version constraints must be space separated

    Example::

        host:
            python >=3

    """
    def check_recipe(self, recipe):
        check_paths = []
        for section in ('build', 'run', 'host'):
            check_paths.append(f'requirements/{section}')

        constraints = re.compile("(.*?)([<=>].*)")
        for path in check_paths:
            for n, spec in enumerate(recipe.get(path, [])):
                has_constraints = constraints.search(spec)
                if has_constraints:
                    space_separated = has_constraints[1].endswith(" ")
                    if not space_separated:
                        self.message(section=f"{path}/{n}", data=True)

    def fix(self, _message, _data):
        check_paths = []
        for section in ('build', 'run', 'host'):
            check_paths.append(f'requirements/{section}')

        constraints = re.compile("(.*?)([<=>].*)")
        for path in check_paths:
            for spec in self.recipe.get(path, []):
                has_constraints = constraints.search(spec)
                if has_constraints:
                    space_separated = has_constraints[1].endswith(" ")
                    if not space_separated:
                        dep, ver = has_constraints.groups()
                        self.recipe.replace(spec, f"{dep} {ver}", within='requirements')
        return True


class extra_identifiers_not_list(LintCheck):
    """The extra/identifiers section must be a list

    Example::

        extra:
           identifiers:
              - doi:123

    """
    def check_recipe(self, recipe):
        identifiers = recipe.get('extra/identifiers', None)
        if identifiers and not isinstance(identifiers, list):
            self.message(section='extra/identifiers')


class extra_identifiers_not_string(LintCheck):
    """Each item in the extra/identifiers section must be a string

    Example::

        extra:
           identifiers:
              - doi:123

    Note that there is no space around the colon

    """
    requires = [extra_identifiers_not_list]

    def check_recipe(self, recipe):
        identifiers = recipe.get('extra/identifiers', [])
        for n, identifier in enumerate(identifiers):
            if not isinstance(identifier, str):
                self.message(section=f'extra/identifiers/{n}')


class extra_identifiers_missing_colon(LintCheck):
    """Each item in the extra/identifiers section must be of form ``type:value``

    Example::

        extra:
           identifiers:
              - doi:123

    """
    requires = [extra_identifiers_not_string]

    def check_recipe(self, recipe):
        identifiers = recipe.get('extra/identifiers', [])
        for n, identifier in enumerate(identifiers):
            if ':' not in identifier:
                self.message(section=f'extra/identifiers/{n}')


class extra_skip_lints_not_list(LintCheck):
    """The extra/skip-lints section must contain a list

    Example::

        extra:
           skip-lints:
              - should_use_compilers

    """
    def check_recipe(self, recipe):
        if not isinstance(recipe.get('extra/skip-lints', []), list):
            self.message(section='extra/skip-lints')

