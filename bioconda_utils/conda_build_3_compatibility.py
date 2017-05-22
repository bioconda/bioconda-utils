"""
Helpers for supporting conda-build 2 and 3 in the same code base
"""
from conda_build import __version__ as conda_build_version

CONDA3 = False
if conda_build_version.startswith('3'):
    CONDA3 = True


def get_output_file_paths(*args, **kwargs):
    if CONDA3:
        from conda_build.api import get_output_file_paths
        result = get_output_file_paths(*args, **kwargs)
        if len(result) != 1:
            raise NotImplemented(
                "No support in bioconda-utils yet for multiple packages "
                "from the same meta.yaml")

        return result[0]
    else:
        from conda_build.api import get_output_file_path
        return get_output_file_path(*args, **kwargs)
