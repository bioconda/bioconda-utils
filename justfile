format:
    ruff format .

deps:
    conda install --file bioconda_utils/bioconda_utils-requirements.txt -c conda-forge -c bioconda

check:
    ruff check .
    ty check .

install:
    python setup.py install

test:
    pytest
