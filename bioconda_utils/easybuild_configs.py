#!/usr/bin/env python

"""
Bioconda creates easybuild configs to deploy modules with ease.

https://easybuild.readthedocs.io/en/latest/

##################################################################
## Add Bioconda Easybuild Configs
## These are custom configs outside of the Easybuild Main
##################################################################
mkdir -p $HOME/.eb/custom_repos
cd $HOME/.eb/custom_repos
git clone https://github.com/bioconda/bioconda-easybuild-easyconfigs
cd

##################################################################
## Add Bioconda Easybuild Configs to the Robot Path
## Robot will tell EB to automatically pull in deps
## Robot-path will tell EB where to look for configs
##################################################################
export ROBOT=$HOME/.eb/custom_repos/bioconda-easybuild-easyconfigs/

eb --dry-run  --robot --robot-paths=$ROBOT   multiqc-1.0.eb
"""

# ----------------------------------------------------------------------------
# EASYBUILD_EASYCONFIG_TEMPLATE
# ----------------------------------------------------------------------------
#
# Easybuild requires an easyconfig to deploy modules
#
EASYBUILD_EASYCONFIG_TEMPLATE = \
"""
##
# This is an easyconfig file for EasyBuild, see https://github.com/easybuilders/easybuild
#
# Copyright:: Copyright 2017 Bioconda Core Team
# License::   3-clause BSD
##

easyblock = 'Conda'

name = \"{{ name }}\"
version = \"{{ version }}\"
variant = "Linux-x86_64"

homepage = 'https://github.com/miyagawa/cpanminus'
description = \""" {{ summary }} \"""

toolchain = {'name': 'dummy', 'version': ''}

requirements = "%(name)s=%(version)s"
channels = ['defaults', 'conda-forge', 'bioconda']

builddependencies = [('Anaconda{{ PYV }}', '4.0.0')]

sanity_check_paths = {
    'dirs': ['lib']
}

moduleclass = 'tools'
"""

import os
import shutil
import subprocess as sp
import tempfile
import pwd
import grp
from textwrap import dedent
import pkg_resources
import re
from distutils.version import LooseVersion
from jinja2 import Environment, BaseLoader
from conda_build.metadata import MetaData

import logging
logger = logging.getLogger(__name__)

def easybuild_easyconfig_gen(pkg, name, recipe_dir):
    ## For now we are only building the Linux Packages
    if 'osx-64' in pkg:
        return

    rtemplate = Environment(loader=BaseLoader()).from_string(EASYBUILD_EASYCONFIG_TEMPLATE)
    config_file = os.path.join(recipe_dir, 'meta.yaml');
    config_data = read_recipe(config_file)

    #If package depends on a specific version of python ensure it depends on
    #correct python
    #Otherwise the default is Anaconda3
    if 'py2' in pkg:
        data = rtemplate.render(name=name, version=config_data['version'], summary=config_data['summary'], PYV=2)
    elif 'py3' in pkg:
        data = rtemplate.render(name=name, version=config_data['version'], summary=config_data['summary'], PYV=3)
    else:
        data = rtemplate.render(name=name, version=config_data['version'], summary=config_data['summary'], PYV=3)

    eb_dir = prepare_dirs(name)

    eb_file = os.path.join(eb_dir, '{}-{}.eb'.format(name, version))
    f = open(eb_file, 'w')

    f.write(data)
    f.close()

    logger.info('Wrote eb config file file {}'.format(eb_file))

def read_recipe(config_file):

    data = MetaData(config_file)
    summary = data.meta['about']['summary']

    version = data.meta.get('package').get('version')

    return {'summary' : summary, 'version': version}

def prepare_dirs(name):
    tmpdir = tempfile.mkdtemp()
    os.chdir(tmpdir)

    # utils.run(["git", "clone", "EASYBUILD_EASYCONFIG_REPO"])
    # p = utils.run(cmd, env=os.environ)
    git_dir = os.path.join(tmpdir, 'EASYBUILD_EASYCONFIGS_REPO')
    os.makedirs(git_dir, exist_ok=True)

    os.chdir(git_dir)

    eb_dir = os.path.join(git_dir, 'easybuild', 'easyconfigs', name[0])
    os.makedirs(eb_dir, exist_ok=True)

    logger.info('Cloning bioconda easyconfigs complete')
    return eb_dir

def commit_files(name, version):
    """
    Commit and push the files back to github
    """

    # utils.run(["git", "add", "-A"])
    # utils.run(["git", "commit", "-m" "commiting {} {}".format(name, version)])
    # utils.run(["git", "push", "origin", "master"])
