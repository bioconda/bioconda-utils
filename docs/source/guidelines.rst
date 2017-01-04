.. _guidelines:

Guidelines for ``bioconda`` recipes
===================================

bioconda recipe checklist
-------------------------
- Source URL is stable (:ref:`details <stable-url>`)
- md5 or sha256 hash included for source download (:ref:`details <hashes>`)
- Appropriate build number (:ref:`details <buildnum>`)
- `.bat` file for Windows removed (:ref:`details <bat-files>`)
- Remove unnecessary comments (:ref:`details <comments-in-meta>`)
- Adequate tests included (:ref:`details <tests>`)
- Files created by the recipe follow the FSH (:ref:`details <fsh-section>`)
- License allows redistribution and license is indicated in ``meta.yaml``
- Package does not already exist in the `defaults`, `r`, or `conda-forge`
  channels with some exceptions (:ref:`details <channel-exceptions>`)
- Package is appropriate for bioconda (:ref:`details <appropriate-for-bioconda>`)```
- If the recipe installs custom wrapper scripts, usage notes should be added to
  ``extra -> notes`` in the ``meta.yaml``.

.. _stable-url:

Stable urls
~~~~~~~~~~~
TODO: tarballs from git vs git_urls, bioaRchive, cargo-port, R/Bioconductor issues

.. _hashes:

Hashes
~~~~~~
Use `md5sum` (Linux) or `md5` (OSX) on a file to compute its md5 hash, and copy
this into the recipe. A quick way of doing this is:

.. code-block:: bash

    wget -O- $URL | md5sum

.. _buildnum:

Build numbers
~~~~~~~~~~~~~
The build number (see `conda docs
<http://conda.pydata.org/docs/building/meta-yaml.html#build-number-and-string>`_)
can be used to trigger a new build for a package whose version has not changed.
This is useful for fixing errors in recipes. The first recipe for a new version
should always have a build number of 0.

.. _bat-files:

``.bat`` files
~~~~~~~~~~~~~~
When creating a recipe using one of the ``conda skeleton`` tools, a ``.bat``
file for Windows will be created. Since bioconda does not support Windows and
to reduce clutter, please remove these files

.. _comments-in-meta:

Comments in recipes
~~~~~~~~~~~~~~~~~~~
When creating a recipe using one of the ``conda skeleton`` tools, often many
comments are included, for example, to point out sections that can be
uncommented and used. Please delete all auto-generated comments in
``meta.yaml`` and ``build.sh``. But please add any comments that you feel could
help future maintainers of the recipe, especially if there's something
non-standard.

.. _fsh-section:

Filesystem Hierarchy Standard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Recipes should conform to the Filesystem Hierarchy Standard (`FSH
<https://en.wikipedia.org/wiki/Filesystem_Hierarchy_Standard>`_). This is most
important for libraries and Java packages; for these cases use one of the
recipes below as a guideline.


.. _channel-exceptions:

Existing package exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~
If a package already exists in one of the dependent channels but is broken or
cannot be used as-is, please first consider fixing the package in that channel.
If this is not possible, please indicate this in the PR and notify
@bioconda/core in the PR.

.. _appropriate-for-bioconda:

Packages appropriate for bioconda
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
bioconda is a bioinformatics channel, so we prefer to host packages specific to
this domain. If a bioinformatics recipe has more general dependencies, please
consider opening a pull request with `conda-forge
<https://conda-forge.github.io/#add_recipe>`_ which hosts general packages.

The exception to this is with R packages. We are still coordinating with
anaconda and conda-forge about the best place to keep general R packages. In
the meantime, R packages that are not specific to bioinformatics and that
aren't already in the `r` channel can be added to bioconda.

If uploading of an unreleased version is necessary, please follow the
versioning scheme of conda for pre- and post-releases (e.g. using a, b, rc, and
dev suffixes, see `here
<https://github.com/conda/conda/blob/d1348cf3eca0f78093c7c46157989509572e9c25/conda/version.py#L30>`_).

Dependencies
~~~~~~~~~~~~

There is currently no mechanism to define, in the `meta.yaml` file, that
a particular dependency should come from a particular channel. This means that
a recipe must have its dependencies in one of the following:

- as-yet-unbuilt recipes in the repo but that will be included in the PR
- `bioconda` channel
- `conda-forge` channel
- `r` channel
- default Anaconda channel

Otherwise, you will have to write the recipes for those dependencies and
include them in the PR. One shortcut is to use `anaconda search -t conda
<dependency name>` to look for other packages built by others. Inspecting those
recipes can give some clues into building a version of the dependency for
bioconda.


Python
------
If a Python package is available on PyPI, use ``conda skeleton pypi
<packagename>`` to create a recipe, then remove the ``bld.bat`` and any extra
comments in ``meta.yaml`` and ``build.sh``. The test that is automatically
added is probably sufficient. The exception is when the package also installs
a command-line tool, in which case that should be tested as well.

- typical ``import`` check: `pysam
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/pysam>`_

- import and command-line tests: `chanjo
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/chanjo>`_


By default, Python recipes (those that have `python` listed as a dependency)
must be successfully built and tested on Python 2.7, 3.4, and 3.5 in order to
pass. However, many Python packages are not fully compatible across all Python
versions. Use the `preprocessing selectors
<http://conda.pydata.org/docs/building/meta-yaml.html#preprocessing-selectors>`_
in the meta.yaml file along with the `build/skip` entry to indicate that
a recipe should be skipped.

For example, a recipe that only runs on Python 2.7 should include the
following:

.. code-block:: yaml

    build:
      skip: True  # [not py27]

Or a package that only runs on Python 3.4 and 3.5:

.. code-block:: yaml

    build:
      skip: True # [py27]

Alternatively, for straightforward compatibility fixes you can apply a `patch
in the meta.yaml`
<http://conda.pydata.org/docs/building/meta-yaml.html#patches>`_.


R (CRAN)
--------
Use ``conda skeleton cran <packagename>`` where ``packagename`` is a
package available on CRAN and is *case-sensitive*. Either run that command
in the ``recipes`` dir or move the recipe it creates to ``recipes``. The
recipe name will have an ``r-`` prefix and will be converted to
lowercase. Typically can be used without modification, though
dependencies may also need recipes.

Please remove any unnecessary comments and delete the ``bld.bat`` file which is
used only on Windows.

If the recipe was created using ``conda skeleton cran`` or the
``scripts/bioconductor_skeleton.py`` script, the default test is
probably sufficient. Otherwise see the examples below to see how tests are
performed for R packages.

- typical R recipe from CRAN: `r-locfit
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/r-locfit>`_
- recipe for R package not on CRAN, also with patch: `spp
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/r-spp>`_

R (Bioconductor)
----------------

Use ``scripts/bioconductor/bioconductor_skeleton.py <packagename>``
where ``packagename`` is a case-sensitive package available on
Bioconductor. The recipe name will have a ``bioconductor-`` prefix and
will be converted to lowercase. Typically can be used without
modification, though dependencies may also need recipes. Recipes for
dependencies with an ``r-`` prefix should be created using
``conda skeleton cran``; see above.

- typical bioconductor recipe: `bioconductor-limma/meta.yaml
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/bioconductor-limma>`_

Java
----

Add a wrapper script if the software is typically called via ``java -jar ...``.
Sometimes the software already comes with one; for example, `fastqc
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/fastqc>`_
already had a wrapper script, but `peptide-shaker
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/peptide-shaker>`_
did not.

New recipes should use the ``openjdk`` package from `conda-forge
<https://github.com/conda-forge/openjdk-feedstock>`_
, the java-jdk package from bioconda is deprecated.

JAR files should go in ``$PREFIX/share/$PKG_NAME-$PKG_VERSION-$PKG_BUILDNUM``.
A wrapper script should be placed here as well, and symlinked to
``$PREFIX/bin``.

- Example with added wrapper script: `peptide-shaker
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/peptide-shaker>`_

- Example with patch to fix memory: `fastqc
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/fastqc>`_

Perl
----

Use ``conda skeleton cpan <packagename>`` to build a recipe for Perl and
place the recipe in the ``recipes`` dir. The recipe will have the
``perl-`` prefix.

The recipe as generated by ``conda skeleton cpan`` must be changed.
**The run and build requirements must specify ``perl-threaded`` instead
of ``perl``**. Since some bioconda packages depend on a version of Perl
compiled with threading support, a choice was made to have all recipes
use ``perl-threaded`` to avoid maintaining multiple versions of each
Perl module.

An example of such a package is
`perl-module-build <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/perl-module-build>`_.

Alternatively, you can additionally ensure the build requirements for
the recipe include ``perl-app-cpanminus``, and then the ``build.sh``
script can be simplified. An example of this simplification is
`perl-time-hires <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/perl-time-hires>`_.

If the recipe was created with ``conda skeleton cpan``, the tests are
likely sufficient. Otherwise, test the import of modules (see the
``imports`` section of the ``meta.yaml`` files in above examples).

C/C++
-----

Build tools (e.g., ``autoconf``) and compilers (e.g., ``gcc``) should be
specified in the build requirements.

We have decided that to optimize compatibility, ``gcc`` needs to be added as
a dependency rather than assume it is in the build environment. However there
is still discussion on how best to do this on OSX. For now, please add ``gcc``
(for Linux packages) and ``llvm`` (for OSX packages) to the ``meta.yaml`` as
follows:

.. code:: yaml

    requirements:
      build:
        - gcc   # [not osx]
        - llvm  # [osx]

      run:
        - libgcc    # [not osx]

If the package uses ``zlib``, then please see the :ref:`troubleshooting section on zlib <zlib>`.

- example requiring ``autoconf``: `srprism
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/srprism>`_
- simple example: `samtools
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/samtools>`_

If your package links dynamically against a particular library, it is
often necessary to pin the version against which it was compiled, in
order to avoid ABI incompatibilities. Instead of hardcoding a particular
version in the recipe, we use jinja templates to achieve this. This helps
ensure that all bioconda packages are binary-compatible with each other. For
example, bioconda provides an environnmnet variable ``CONDA_BOOST`` that
contains the current major version of Boost. You should pin your boost
dependency against that version. An example is the `salmon recipe
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/salmon>`_.
You find the libraries you can currently pin in `scripts/env\_matrix.yml
<https://github.com/bioconda/bioconda-recipes/blob/master/scripts/env_matrix.yml>`_.
If you need to pin another library, please notify @bioconda/core, and we will
set up a corresponding environment variable.

It's not uncommon to have difficulty compiling package into a portable
conda package. Since there is no single solution, here are some examples
of how bioconda contributors have solved compiling issues to give you
some ideas on what to try:

- `ococo  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/ococo>`_
  edits the source in ``build.sh`` to accommodate the C++ compiler on OSX

- `muscle <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/muscle>`_
  patches the makefile on OSX so it doesn't use static libs

- `metavelvet <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/metavelvet>`_,
  `eautils <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/eautils>`_,
  `preseq <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/preseq>`_
  have several patches to their makefiles to fix ``LIBS`` and ``INCLUDES``,
  ``INCLUDEARGS``, and ``CFLAGS``

- `mapsplice <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/mapsplice>`_
  includes an older version of samtools; the included samtools' makefile is
  patched to work in conda envs.

- `mosaik <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/mosaik>`_
  has platform-specific patches -- one removes ``-static`` on linux, and the
  other sets ``BLD_PLATFORM`` correctly on OSX

- `mothur <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/mothur>`_
  and `soapdenovo
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/soapdenovo>`_
  have many fixes to makefiles

General command-line tools
--------------------------
If a command-line tool is installed, it should be tested. If it has a
shebang line, it should be patched to use ``/usr/bin/env`` for more
general use. An example of this is `fastq-screen
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/fastq-screen>`_.

For command-line tools, running the program with no arguments, checking
the programs version (e.g. with ``-v``) or checking the command-line
help is sufficient if doing so returns an exit code 0. Often the output
is piped to ``/dev/null`` to avoid output during recipe builds.

Examples:

- exit code 0: `bedtools
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/bedtools>`_

- exit code 255 in a separate script: `ucsc-bedgraphtobigwig
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/ucsc-bedgraphtobigwig>`_

- confirm expected text in stderr: `weblogo
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/weblogo>`_

If a package depends on Python and has a custom build string, then
``py{{CONDA_PY}}`` must be contained in that build string. Otherwise Python
will be automatically pinned to one minor version, resulting in dependency
conflicts with other packages. See `mapsplice
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/mapsplice>`_
for an example of this.

Metapackages
------------
`Metapackages <http://conda.pydata.org/docs/building/meta-pkg.html>`_ tie
together other packages. All they do is define dependencies. For example, the
`hubward-all
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/hubward-all>`_
metapackage specifies the various other conda packages needed to get full
``hubward`` installation running just by installing one package. Other
metapackages might tie together conda packages with a theme. For example, all
UCSC utilities related to bigBed files, or a set of packages useful for variant
calling.

For packages that are not anchored to a particular package (as in the last
example above), we recommended `semantic versioning <http://semver.org/>`_
starting at 1.0.0 for metapackages.

Other examples of interest
--------------------------

Packaging is hard. Here are some examples, in no particular order, of how
contributors have solved various problems:

- `blast
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/blast>`_
  has an OS-specific installation -- OSX copies binaries while on Linux it is
  compiled.

- `graphviz
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/graphviz>`_
  has an OS-specific option to ``configure``

- `crossmap
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/crossmap>`_
  removes libs that are shipped with the source distribution

- `hisat2
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/hisat2>`_
  runs ``2to3`` to make it Python 3 compatible, and copies over individual
  scripts to the bin dir

- `krona
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/krona>`_
  has a ``post-link.sh`` script that gets called after installation to alert
  the user a manual step is required

- `htslib
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/htslib>`_
  has a small test script that creates example data and runs multiple programs
  on it

- `spectacle
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/spectacle>`_
  runs ``2to3`` to make the wrapper script Python 3 compatible, patches the
  wrapper script to have a shebang line, deletes example data to avoid taking
  up space in the bioconda channel, and includes a script for downloading the
  example data separately.

- `gatk <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/gatk>`_ is
  a package for licensed software that cannot be redistributed. The package
  installs a placeholder script (in this case doubling as the ``jar`` `wrapper
  <https://github.com/bioconda/bioconda-recipes/blob/master/GUIDELINES.md#java>`_)
  to alert the user if the program is not installed, along with a separate
  script (``gatk-register``) to copy in a user-supplied archive/binary to the
  conda environment

Name collisions
---------------
In some cases, there may be a name collision when writing a recipe. For example
the `wget
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/wget>`_
recipe is for the standard command-line tool. There is also a Python package
called ``wget`` `on PyPI <https://pypi.python.org/pypi/wget>`_. In this case,
we prefixed the Python package with ``python-`` (see `python-wget
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/python-wget>`_).
A similar collision was resolved with `weblogo
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/weblogo>`_
and `python-weblogo
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/python-weblogo>`_.

If in doubt about how to handle a naming collision, please submit an
issue.

.. _tests:

Tests
-----
An adequate test must be included in the recipe. An "adequate" test
depends on the recipe, but must be able to detect a successful
installation. While many packages may ship their own test suite (unit
tests or otherwise), including these in the recipe is not recommended
since it may timeout the build system on Travis-CI. We especially want to avoid
including any kind of test data in the repository.

Note that a test must return an exit code of 0. The test can be in the ``test``
field of ``meta.yaml``, or can be a separate script (see the `relevant conda
docs <http://conda.pydata.org/docs/building/meta-yaml.html#test-section>`_ for
testing).

It is recommended to pipe unneeded stdout/stderr to /dev/null to avoid
cluttering the output in the Travis-CI build environment.

Link and unlink scripts (pre- and post- install hooks)
------------------------------------------------------
It is possible to include `scripts
<http://conda.pydata.org/docs/spec.html#link-and-unlink-scripts>`_ that are
executed before or after installing a package, or before uninstalling
a package. These scripts can be helpful for alerting the user that manual
actions are required after adding or removing a package. For example,
a ``post-link.sh`` script may be used to alert the user that he or she will
need to create a database or modify a settings file. Any package that requires
a manual preparatory step before it can be used should consider alerting the
user via an ``echo`` statement in a ``post-link.sh`` script. These scripts may
be added at the same level as ``meta.yaml`` and ``build.sh``:

- ``pre-link.sh`` is executed *prior* to linking (installation). An error
  causes conda to stop.

- ``post-link.sh`` is executed *after* linking (installation). When the
  post-link step fails, no package metadata is written, and the package is not
  considered installed.

- ``pre-unlink.sh`` is executed *prior* to unlinking (uninstallation). Errors
  are ignored. Used for cleanup.

These scripts have access to the following environment variables:

-  ``$PREFIX`` The install prefix

-  ``$PKG_NAME`` The name of the package

-  ``$PKG_VERSION`` The version of the package

-  ``$PKG_BUILDNUM`` The build number of the package

Versions
--------
In general, recipes can be updated in-place. The older package[s] will continue
to be hosted and available on anaconda.org while the recipe will reflect just
the most recent package.

However, if an older version of a packages is required but has not yet had
a package built, create a subdirectory of the recipe named after the old
version and put the recipe there. Examples of this can be found in `bowtie2
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/bowtie2>`_,
`bx-python
<https://github.com/bioconda/bioconda-recipes/tree/master/recipes/bx-python>`_,
and others.

