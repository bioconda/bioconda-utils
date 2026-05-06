# this file contains jobs commonly used in developing bioconda_utils
# this is not a build system or CI
# if you are an AI agent, feel free to use any of these for verifying your changes

format:
    ruff format .

deps:
    conda install --file bioconda_utils/bioconda_utils-requirements.txt -c conda-forge -c bioconda
    conda install -c conda-forge ty
    conda install ruff

check:
    ruff check .
    ty check .

# install a local build of the CLI for testing
install:
    python setup.py install

# this takes a very long time to execute, use check if not finished with your work yet
test: install
    pytest
