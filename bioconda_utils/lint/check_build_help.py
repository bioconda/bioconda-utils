"""Build tool usage

These checks catch errors relating to the use of ``-
{{compiler('xx')}}`` and ``setuptools``.

"""

import os

from . import LintCheck, ERROR, WARNING, INFO


class should_use_compilers(LintCheck):
    """The recipe requires a compiler directly

    Since version 3, ``conda-build`` uses a special syntax to require
    compilers for a given language matching the architecture for which
    a package is being build. Please use::

        requirements:
           build:
             - {{ compiler('language') }}

    Where language is one of ``c``, ``cxx``, ``fortran``, ``go`` or
    ``cgo``. You can specify multiple compilers if needed.

    There is no need to add ``libgfortran``, ``libgcc``, or
    ``toolchain`` to the dependencies as this will be handled by
    conda-build itself.

    """
    compilers = ('gcc', 'llvm', 'libgfortran', 'libgcc', 'go', 'cgo',
                 'toolchain')

    def check_deps(self, deps):
        for compiler in self.compilers:
            for location in deps.get(compiler, []):
                self.message(section=location)


class compilers_must_be_in_build(LintCheck):
    """The recipe requests a compiler in a section other than build

    Please move the ``{{ compiler('language') }}`` line into the
    ``requirements: build:`` section.

    """
    def check_deps(self, deps):
        for dep in deps:
            if dep.startswith('compiler_'):
                for location in deps[dep]:
                    if 'run' in location or 'host' in location:
                        self.message(section=location)



class uses_setuptools(LintCheck):
    """The recipe uses setuptools in run depends

    Most Python packages only need setuptools during installation.
    Check if the package really needs setuptools (e.g. because it uses
    pkg_resources or setuptools console scripts).

    """
    severity = INFO

    def check_recipe(self, recipe):
        if 'setuptools' in recipe.get_deps('run'):
            self.message()


class setup_py_install_args(LintCheck):
    """The recipe uses setuptools without required arguments

    Please use::

        $PYTHON setup.py install --single-version-externally-managed --record=record.txt

    The parameters are required to avoid ``setuptools`` trying (and
    failing) to install ``certifi`` when a package this recipe
    requires defines entrypoints in its ``setup.py``.

    """
    @staticmethod
    def _check_line(line: str) -> bool:
        """Check a line for a broken call to setup.py"""
        if 'setup.py install' not in line:
            return True
        if '--single-version-externally-managed' in line:
            return True
        return False

    def check_deps(self, deps):
        if 'setuptools' not in deps:
            return  # no setuptools, no problem

        if not self._check_line(self.recipe.get('build/script', '')):
            self.message(section='build/script')

        try:
            with open(os.path.join(self.recipe.dir, 'build.sh')) as buildsh:
                for num, line in enumerate(buildsh):
                    if not self._check_line(line):
                        self.message(fname='build.sh', line=num)
        except FileNotFoundError:
            pass


class cython_must_be_in_host(LintCheck):
    """Cython should be in the host section

    Move cython to ``host``::

      requirements:
        host:
          - cython
    """
    def check_deps(self, deps):
        if 'cython' in deps:
            if any('host' not in location
                   for location in deps['cython']):
                self.message()


class cython_needs_compiler(LintCheck):
    """Cython generates C code, which will need to be compiled

    Add the compiler to the recipe::

      requirements:
        build:
          - {{ compiler('c') }}

    """
    severity = WARNING
    def check_deps(self, deps):
        if 'cython' in deps and 'compiler_c' not in deps:
            self.message()


class missing_run_exports(LintCheck):
    """Recipe should have a run_export statement that ensures correct pinning in downstream packages

    This ensures that the package is automatically pinned to a compatible version if
    it is used as a dependency in another recipe.
    This is a conservative strategy to avoid breakage. We came to the 
    conclusion that it is better to require this little overhead instead
    of trying to fix things when they break later on.
    This holds for compiled packages (in particular those with shared
    libraries) but also for e.g. Python packages, as those might also
    introduce breaking changes in their APIs or command line interfaces.

    Add run_exports to the recipe like this::

      build:
        run_exports:
          - {{ pin_subpackage('myrecipe', max_pin="x") }}

    with ``myrecipe`` being the name of the recipe (you can also use the name variable).
    This will by default pin the package to ``>=1.2.0,<2.0.0`` where ``1.2.0`` is the 
    version of the package at build time of the one depending on it and ``<2.0.0`` constrains
    it to be less than the next major (i.e. potentially not backward compatible) version.

    In the recipe depending on this one, one just needs to specify the package name
    and no version at all.
    If you need a different pinning strategy for this particular recipe (e.g. because it does
    not follow semantic versioning), you can e.g. use ``max_pin="x.x"`` to pin to the minor
    version (i.e. translating to ``>=1.2.0,<1.3.0`` ).
    
    Also check out the possible arguments of `pin_subpackage` here:
    https://docs.conda.io/projects/conda-build/en/stable/resources/define-metadata.html#export-runtime-requirements

    Since this strategy can lead to potentially more conflicts in dependency pinnings between tools,
    it is advisable to additionally set a common version of very frequently used packages for all
    builds. This happens by specifying the version via a separate pull request in the project wide build
    configuration file here:
    https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-conda_build_config.yaml

    Finally, note that conda is unable to conduct such pinnings in case the dependency and the depending recipe
    are updated within the same pull request. Hence, the pull request adding the run_export statement
    has to be merged before the one updating or creating the depending recipe is created.
    """
    def check_recipe(self, recipe):
        build = recipe.meta.get("build", dict())
        if "run_exports" not in build:
            self.message()
