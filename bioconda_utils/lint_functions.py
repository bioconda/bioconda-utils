from functools import partial
import glob
import os
import re

import pandas
import numpy as np


def _subset_df(recipe, meta, df):
    """
    Helper function to get the subset of `df` for this recipe.
    """
    if df is None:
        # TODO: this is just a mockup; is there a better way to get the set of
        # expected columns from a channel dump?
        return pandas.DataFrame(
            np.nan, index=[],
            columns=['channel', 'name', 'version', 'build_number'])

    name = meta.get_value('package/name')
    version = meta.get_value('package/version')

    return df[
        (df.name == name) &
        (df.version == version)
    ]


def _get_deps(meta, section=None):
    """
    meta : dict-like
        Parsed meta.yaml file.

    section : str, list, or None
        If None, returns all dependencies. Otherwise can be a string or list of
        options [build, run, test] to return section-specific dependencies.
    """

    get_name = lambda dep: dep.split()[0]

    reqs = meta.get_section('requirements')
    if reqs is None:
        return []
    if section is None:
        sections = ['build', 'run', 'test']
    if isinstance(section, str):
        sections = [section]
    deps = []
    for s in sections:
        dep = reqs.get(s, [])
        if dep:
            deps += [get_name(d) for d in dep]
    return deps


def _has_preprocessing_selector(recipe):
    """
    Does the package have any preprocessing selectors?

    # [osx], # [not py27], etc.
    """
    # regex from
    # https://github.com/conda/conda-build/blob/cce72a95c61b10abc908ab1acf1e07854a236a75/conda_build/metadata.py#L107
    sel_pat = re.compile(r'(.+?)\s*(#.*)?\[([^\[\]]+)\](?(2).*)$')
    for line in open(os.path.join(recipe, 'meta.yaml')):
        line = line.rstrip()
        if line.startswith('#'):
            continue
        if sel_pat.match(line):
            return True


def in_other_channels(recipe, metas, df):
    """
    Does the package exist in any other non-bioconda channels?
    """
    for meta in metas:
        results = _subset_df(recipe, meta, df)
        channels = set(results.channel).difference(['bioconda'])
        if len(channels):
            return {
                'exists_in_channels': channels,
                'fix': 'consider deprecating',
            }


def already_in_bioconda(recipe, metas, df):
    """
    Does the package exist in bioconda?
    """
    for meta in metas:
        results = _subset_df(recipe, meta, df)
        build_section = meta.get_section('build')
        build_number = int(build_section.get('number', 0))
        build_results = results[results.build_number == build_number]
        channels = set(build_results.channel)
        if 'bioconda' in channels:
            return {
                'already_in_bioconda': True,
                'fix': 'bump version or build number'
            }


def missing_home(recipe, metas, df):
    for meta in metas:
        if not meta.get_value('about/home'):
            return {
                'missing_home': True,
                'fix': 'add about:home',
            }


def missing_summary(recipe, metas, df):
    for meta in metas:
        if not  meta.get_value('about/summary'):
            return {
                'missing_summary': True,
                'fix': 'add about:summary',
            }


def missing_license(recipe, metas, df):
    for meta in metas:
        if not  meta.get_value('about/license'):
            return {
                'missing_license': True,
                'fix': 'add about:license'
            }


def missing_tests(recipe, metas, df):
    for meta in metas:
        test_files = ['run_test.py', 'run_test.sh', 'run_test.pl']
        if not meta.get_section('test'):
            if not any([os.path.exists(os.path.join(recipe, f)) for f in
                        test_files]):
                return {
                    'no_tests': True,
                    'fix': 'add basic tests',
                }


def missing_hash(recipe, metas, df):
    for meta in metas:
        # could be a meta-package if no source section or if None
        src = meta.get_section('source')
        if not src:
            continue

        if not any(src.get(checksum)
                   for checksum in ('md5', 'sha1', 'sha256')):
            return {
                'missing_hash': True,
                'fix': 'add md5, sha1, or sha256 hash to "source" section',
            }


def uses_git_url(recipe, metas, df):
    for meta in metas:
        src = meta.get_section('source')
        if not src:
            # metapackage?
            continue

        if 'git_url' in src:
            return {
                'uses_git_url': True,
                'fix': 'use tarballs whenever possible',
            }


def uses_perl_threaded(recipe, metas, df):
    for meta in metas:
        if 'perl-threaded' in _get_deps(meta):
            return {
                'depends_on_perl_threaded': True,
                'fix': 'use "perl" instead of "perl-threaded"',
            }


def uses_javajdk(recipe, metas, df):
    for meta in metas:
        if 'java-jdk' in _get_deps(meta):
            return {
                'depends_on_java-jdk': True,
                'fix': 'use "openjdk" instead of "java-jdk"',
            }


def uses_setuptools(recipe, metas, df):
    for meta in metas:
        if 'setuptools' in _get_deps(meta, 'run'):
            return {
                'depends_on_setuptools': True,
                'fix': ('setuptools might not be a run requirement (unless it uses '
                        'pkg_resources or setuptools console scripts)'),
            }


def has_windows_bat_file(recipe, metas, df):
    if len(glob.glob(os.path.join(recipe, '*.bat'))) > 0:
        return {
            'bat_file': True,
            'fix': 'remove windows .bat files'
        }


def should_be_noarch(recipe, metas, df):
    for meta in metas:
        print(meta.get_value("package/name"))
        deps = _get_deps(meta)
        if (
            ('gcc' not in deps) and
            ('python' in deps) and
            # This will also exclude recipes with skip sections
            # which is a good thing, because noarch also implies independence of
            # the python version.
            not _has_preprocessing_selector(recipe)
        ) and (
            'noarch' not in meta.get_section('build')
        ):
            return {
                'should_be_noarch': True,
                'fix': 'add "build: noarch" section',
            }


def should_not_be_noarch(recipe, metas, df):
    for meta in metas:
        deps = _get_deps(meta)
        if (
            ('gcc' in deps) or
            meta.get_section('build').get('skip', False) in ["true", "True"]
        ) and (
            'noarch' in meta.get_section('build')
        ):
            print("error")
            return {
                'should_not_be_noarch': True,
                'fix': 'remove "build: noarch" section',
            }


def setup_py_install_args(recipe, metas, df):
    for meta in metas:
        if 'setuptools' not in _get_deps(meta, 'build'):
            continue

        err = {
            'needs_setuptools_args': True,
            'fix': ('add "--single-version-externally-managed --record=record.txt" '
                    'to setup.py command'),
        }

        script_line = meta.get_section('build').get('script', '')
        if (
            'setup.py install' in script_line and
            '--single-version-externally-managed' not in script_line
        ):
            return err

        build_sh = os.path.join(recipe, 'build.sh')
        if not os.path.exists(build_sh):
            continue

        contents = open(build_sh).read()
        if (
            'setup.py install' in contents and
            '--single-version-externally-managed' not in contents
        ):
            return err


def invalid_identifiers(recipe, metas, df):
    for meta in metas:
        try:
            identifiers = meta.get_section('extra').get('identifiers', [])
            if not isinstance(identifiers, list):
                return { 'invalid_identifiers': True,
                         'fix': 'extra:identifiers must hold a list of identifiers' }
            if not all(isinstance(i, str) for i in identifiers):
                return { 'invalid_identifiers': True,
                         'fix': 'each identifier must be a string' }
            if not all((':' in i) for i in identifiers):
                return { 'invalid_identifiers': True,
                         'fix': 'each identifier must be of the form '
                                'type:identifier (e.g., doi:123)' }
        except KeyError:
            # no identifier section
            continue


def deprecated_numpy_spec(recipe, metas, df):
    with open(os.path.join(recipe, "meta.yaml")) as recipe:
        if re.search("numpy( )+x\.x", recipe.read()):
            return { 'deprecated_numpy_spec': True,
                     'fix': 'omit x.x as pinning of numpy is now '
                            'handled automatically'}


registry = (
    in_other_channels,

    # disabling for now until we get better per-OS version detection
    # already_in_bioconda,
    missing_tests,
    missing_home,
    missing_license,
    missing_summary,
    missing_hash,
    uses_git_url,
    uses_javajdk,
    uses_perl_threaded,
    # removing setuptools from run requirements should be done cautiously:
    # it breaks packages that use pkg_resources or setuptools console scripts!
    # uses_setuptools,
    has_windows_bat_file,

    # should_be_noarch,
    #
    should_not_be_noarch,
    setup_py_install_args,
    invalid_identifiers,
    deprecated_numpy_spec
)
