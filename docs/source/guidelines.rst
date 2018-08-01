.. _guidelines:

Guidelines for ``bioconda`` recipes
===================================

bioconda recipe checklist
-------------------------
- Source URL is stable (:ref:`details <stable-url>`)
- sha256 or md5 hash included for source download (:ref:`details <hashes>`)
- Appropriate build number (:ref:`details <buildnum>`)
- `.bat` file for Windows removed (:ref:`details <bat-files>`)
- Remove unnecessary comments (:ref:`details <comments-in-meta>`)
- Adequate tests included (:ref:`details <tests>`)
- Files created by the recipe follow the FSH (:ref:`details <fsh-section>`)
- License allows redistribution and license is indicated in ``meta.yaml``
- Package does not already exist in the `defaults`, `r`, or `conda-forge`
  channels with some exceptions (:ref:`details <channel-exceptions>`)
- Package is appropriate for bioconda (:ref:`details <appropriate-for-bioconda>`)
- If the recipe installs custom wrapper scripts, usage notes should be added to
  ``extra -> notes`` in the ``meta.yaml``.
- **Update 7 Feb 2018**: Previously we had recommended that if the recipe is
  a pure Python package, it should be marked as a `"noarch"
  <https://www.continuum.io/blog/developer-blog/condas-new-noarch-packages>`_
  package (:ref:`details <noarch>`). However due to technical incompatibilies
  we can't do this -- so please DO NOT use ``"noarch"`` for now.
- **Update 7 Mar 2018**: When patching a recipe, please provide details on how
  you tried to address the problem upstream (:ref:`details <patching>`)

.. _stable-url:

Stable urls
~~~~~~~~~~~
While supported by conda, `git_url` and `git_rev` are not as stable as a git
tarball. Ideally a github repo should have tagged releases that are accessible
as tarballs from the "releases" section of the github repo.

TODO: additional info on the various R and bioconductor URLs

.. _hashes:

Hashes
~~~~~~
We support either sha256 or md5 checksums to verify the integrity of the source
package. If a checksum is provided alongside the source package, then use that.
Otherwise we prefer sha256 over md5.

Use ``shasum -a 256 ...`` (or ``sha256sum``  or ``openssl sha256`` etc) on a
file to compute its sha256 hash, and copy this into the recipe, for example:

.. code-block:: bash

    wget -O- $URL | shasum -a 256

Likewise use `md5sum` (Linux) or `md5` (OSX) on a file to compute its md5 hash,
and copy this into the recipe.

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
aren't already in the `conda-forge` channel can be added to bioconda.

If uploading of an unreleased version is necessary, please follow the
versioning scheme of conda for pre- and post-releases (e.g. using a, b, rc, and
dev suffixes, see `here
<https://github.com/conda/conda/blob/d1348cf3eca0f78093c7c46157989509572e9c25/conda/version.py#L30>`_).


.. _noarch:

"Noarch" packages
~~~~~~~~~~~~~~~~~
**Update 7 Feb 2018** For now please DO NOT use ``noarch`` until technical
compatibility issues are resolved.

.. Deprecated advice:
.. A ``noarch`` package can be created for pure Python packages, data packages, or
   packages that do not require compilation. This single ``noarch`` package can be
   used across multiple platforms, which saves on build time and saves on storage
   space on the bioconda channel.

.. For pure Python packages, add ``noarch: python`` to the ``build`` section.

.. For other generic packages (like a data package), add ``noarch: generic`` to
   the ``build`` section.

.. See `here
   <https://www.continuum.io/blog/developer-blog/condas-new-noarch-packages>`_ for
   more details.

Dependencies
~~~~~~~~~~~~

There is currently no mechanism to define, in the `meta.yaml` file, that
a particular dependency should come from a particular channel. This means that
a recipe must have its dependencies in one of the following:

- as-yet-unbuilt recipes in the repo but that will be included in the PR
- `bioconda` channel
- `conda-forge` channel
- default Anaconda channel

Otherwise, you will have to write the recipes for those dependencies and
include them in the PR. One shortcut is to use `anaconda search -t conda
<dependency name>` to look for other packages built by others. Inspecting those
recipes can give some clues into building a version of the dependency for
bioconda.

.. _patching:

Patching
~~~~~~~~
Some recipes require small patches to get the tests to pass, for example,
fixing hard-coded shebang lines (as described at
:ref:`perl-or-python-not-found`). Other patches are more extensive. When
patching a recipe, please first make an effort to fix the issue upstream and
document that effort in your pull request by either linking to the relevant
upstream PR or indicating that you have contacted the author. The goal is not
to block merging your PR until upstream is fixed, but rather to make sure
upstream authors know there's an issue that other users (including non-bioconda
users) might be having. Ideally, upstream would fix the issue quickly and the
PR could be modified, but it's fine to merge with the patches and if/when
upstream fixes, a separate bioconda PR could be opened that pulls in those
upstream changes.


Python
------

.. note::

    If you have conda-build 3 installed locally and use ``conda skeleton``,
    please see :ref:`cb3-recipes-in-cb2`.

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

.. note::

    If you have conda-build 3 installed locally and use ``conda skeleton``,
    please see :ref:`cb3-recipes-in-cb2`.

.. note::

    Using the ``conda skeleton cran`` method results in a recipe intended to be
    built for Windows as well, with lines like:

    .. code-block:: yaml

         {% set posix = 'm2-' if win else '' %}
         {% set native = 'm2w64-' if win else '' %}

    and

    .. code-block:: yaml

        test:
          commands:
            - $R -e "library('RNeXML')"  # [not win]
            - "\"%R%\" -e \"library('RNeXML')\""  # [win]

    The bioconda channel does not build for Windows. To keep recipes
    streamlined, please remove the "set posix" and "set native" lines described
    above and convert the `test:commands:` block to only:

    .. code-block:: yaml

        test:
          commands:
            - $R -e "library('RNeXML')"

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

Use the ``bioconda-utils bioconductor-skeleton`` tool to build a Bioconductor
skeleton. After using the :ref:`bootstrap` method to set up a testing
environment and activating that environment (which will ensure the correct
versions of bioconda-utils and conda-build), from the top level of the
``bioconda-recipes`` repository run::

    bioconda-utils bioconductor-skeleton recipes config.yml DESeq2

Note that the provided package name is a case-sensitive package available on
Bioconductor. The output recipe name will have a ``bioconductor-`` prefix and
will be converted to lowercase.  Data packages will be detected automatically,
and a post-link script (see https://github.com/bioconda/bioconda-utils/pull/169
for details). Typically the resulting recipe can be used without modification,
though dependencies may also need recipes. Recipes for dependencies with an
``r-`` prefix should be created using ``conda skeleton cran``; see above.

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
specified in the build requirements. Compilers are handled via a special macro.
E.g., `{{ compiler('c')}}` ensures that the correct version of `gcc` is used.
For the C++ variant `g++`, you need to use `{{ compiler('cxx') }}`.
These rules apply for both Linux and macOS.

Conda distinguishes between dependencies needed for building (the `build` section),
and dependencies needed during build time (the `host` section).
For example, the following


.. code:: yaml

    requirements:
      build:
        - {{ compiler('c') }}
      host:
        - zlib
      run:
        - zlib

specifies that a recipe needs the C compiler to build, and zlib present during
building and running.

For two examples see:

- example requiring ``autoconf``: `srprism
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/srprism>`_
- simple example: `samtools
  <https://github.com/bioconda/bioconda-recipes/tree/master/recipes/samtools>`_

If the package uses ``zlib``, then please see the :ref:`troubleshooting section on zlib <zlib>`.

If your package links dynamically against a particular library, it is
often necessary to pin the version against which it was compiled, in
order to avoid ABI incompatibilities. Instead of hardcoding a particular
version in the recipe, we rely on conda doing this automatically.
We use globally defined configurations, namely `this for dependencies from conda-forge <https://github.com/conda-forge/conda-forge-pinning-feedstock/blob/master/recipe/conda_build_config.yaml>`_
and `this for dependencies in bioconda <https://github.com/bioconda/bioconda-utils/blob/master/bioconda_utils/bioconda_utils-conda_build_config.yaml>`_.
If you need to pin another library, please notify @bioconda/core, and we will extend these lists.

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

Haskell
-------

Bioconda has a small number of haskell tools. Most often they are built with
``stack`` (which is available on `conda-forge
<https://github.com/conda-forge/stack-feedstock>`__). `NGLess
<https://github.com/bioconda/bioconda-recipes/blob/master/recipes/ngless/build.sh>`__
provides an example of how to call ``stack``. Here are a few notes:

- ``LD_LIBRARY_PATH``/``LIBRARY_PATH`` are set to include both
  ``${PREFIX}/lib`` and the system paths (otherwise, ``stack setup`` will
  fail).
- Create a directory (called ``fake-home`` in this example) and set it as
  ``$HOME``, further setting ``$STACK_ROOT`` to use a subdirectory of this
  ``$HOME``.

Mac OS X support is generally missing (any help is appreciated, see `#6607
<https://github.com/bioconda/bioconda-recipes/issues/6607>`__).

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
since it may timeout the build system on CircleCI. We especially want to avoid
including any kind of test data in the repository.

Note that a test must return an exit code of 0. The test can be in the ``test``
field of ``meta.yaml``, or can be a separate script (see the `relevant conda
docs <http://conda.pydata.org/docs/building/meta-yaml.html#test-section>`_ for
testing).

It is recommended to pipe unneeded stdout/stderr to /dev/null to avoid
cluttering the output in the CircleCI build environment.

Link and unlink scripts (pre- and post- install hooks)
------------------------------------------------------
It is possible to include `scripts
<https://conda.io/docs/user-guide/tasks/build-packages/link-scripts.html>`_ that are
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
