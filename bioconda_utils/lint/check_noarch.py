"""Use of ``noarch`` and ``skip``

When to use ``noarch`` and when to use ``skip`` or pin the interpreter
is non-intuitive and idiosynractic due to ``conda`` legacy
behavior. These checks aim at getting the right settings.

"""

import re

from . import LintCheck, ERROR, WARNING, INFO

from logging import getLogger

logger = getLogger(__name__)


# Noarch or not checks:
#
# - Python packages that use no compiler should be
#   a) Marked ``noarch: python``
#   b) Not use ``skip: True  # [...]`` except for osx/linux,
#      but use ``- python [<>]3``
# - Python packages that use a compiler should be
#   a) NOT marked ``noarch: python``
#   b) Not use ``- python [<>]3``,
#      but use ``skip: True  # [py[23]k]``


class should_be_noarch_python(LintCheck):
    """The recipe should be build as ``noarch``

    Please add::

        build:
          noarch: python

    Python packages that don't require a compiler to build are
    normally architecture independent and go into the ``noarch``
    subset of packages.

    """
    def check_deps(self, deps):
        if not self.recipe.is_noarch(python=True) and \
           self.recipe.has_dep('python', section='host') and \
           not self.recipe.has_compiler() and \
           not self.recipe.has_selector():
            self.message(section='build', data=True)

    def fix(self, _message, _data):
        logger.warning("Lint fix: setting build/noarch=python")
        self.recipe.set('build/noarch', 'python')
        return True


class should_be_noarch_generic(LintCheck):
    """The recipe should be build as ``noarch``

    Please add::

        build:
          noarch: generic

    Packages that don't require a compiler to build are normally
    architecture independent and go into the ``noarch`` subset of
    packages.

    """
    requires = ['should_be_noarch_python']
    def check_deps(self, deps):
        if not self.recipe.is_noarch(python=False) and \
           not self.recipe.has_dep('python', section='host') and \
           not self.recipe.has_compiler() and \
           not self.recipe.has_selector():
            logger.error("here")
            logger.error(self.recipe.has_selector())
            self.message(section='build', data=True)

    def fix(self, _message, _data):
        logger.warning("Lint fix: setting build/noarch=generic")
        self.recipe.set('build/noarch', 'generic')
        return True


class should_not_be_noarch_compiler(LintCheck):
    """The recipe uses a compiler but is marked noarch

    Recipes using a compiler should not be marked noarch.

    Please remove the ``build: noarch:`` section.

    """
    def check_deps(self, deps):
        if self.recipe.is_noarch() and self.recipe.has_compiler():
            self.message(section='build/noarch')


class should_not_be_noarch_skip(LintCheck):
    """The recipe uses ``skip: True`` but is marked noarch

    Recipes marked as ``noarch`` cannot use skip.

    """
    def check_recipe(self, recipe):
        if self.recipe.is_noarch() and \
           self.recipe.get('build/skip', False) is not False:
            self.message(section='build/noarch')


class should_not_be_noarch_selector(LintCheck):
    """The recipe uses ``# [cond]`` but is marked noarch

    Recipes using conditional lines cannot be noarch.

    """
    requires = ['should_use_compilers',
                'should_not_be_noarch_skip',
                'should_not_be_noarch_source']
    def check_recipe(self, recipe):
        if self.recipe.is_noarch() and self.recipe.has_selector():
            self.message(section='build/noarch')


class should_not_use_skip_python(LintCheck):
    """The recipe should be noarch and not use python based skipping

    Please use::

       requirements:
          build:
            - python >3  # or <3
          run:
            - python >3  # or <3

    The ``build: skip: True`` feature only works as expected for
    packages built specifically for each "platform" (i.e. Python
    version and OS). This package should be ``noarch`` and not use
    skips.

    """
    bad_skip_terms = ('py2k', 'py3k', 'python')

    def check_deps(self, deps):
        if self.recipe.has_dep('python') and \
           not self.recipe.has_compiler() and \
           self.recipe.get('build/skip', None) is not None and \
           any(term in self.bad_skip_terms
               for term in self.recipe.get_raw('build/skip')):
            self.message(section='build/skip')


class should_not_be_noarch_source(LintCheck):
    """The recipe uses per platform sources and cannot be noarch

    You are downloading different upstream sources for each
    platform. Remove the noarch section or use just one source for all
    platforms.
    """
    def check_source(self, source, section):
        if self.recipe.is_noarch() and self.recipe.has_selector(section):
            self.message(section)
