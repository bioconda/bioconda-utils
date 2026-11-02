# Specify the base image to support multi-arch images, such as
# - 'quay.io/condaforge/linux-anvil-aarch64' for Linux aarch64
# - 'quay.io/condaforge/linux-anvil-cos7-x86_64' for Linux x86_64
ARG BASE_IMAGE=quay.io/condaforge/linux-anvil-cos7-x86_64

FROM ${BASE_IMAGE} AS base

# Conda honors settings exposed as CONDA_<SETTING>. Keep a cache-friendly
# default for CI while allowing callers to request fresher repodata at runtime
# (for example, `docker run -e CONDA_LOCAL_REPODATA_TTL=0 ...`).
ENV CONDA_LOCAL_REPODATA_TTL=3600

# Copy over C.UTF-8 locale from our base image to make it consistently available during build.
COPY --from=quay.io/bioconda/base-glibc-busybox-bash /usr/lib/locale/C.utf8 /usr/lib/locale/C.utf8

# Provide system deps unconditionally until we are able to offer per-recipe installs.
# (Addresses, e.g., "ImportError: libGL.so.1" in tests directly invoked by conda-build.)
# Also install packages that have been installed historically (openssh-client).
RUN yum install -y mesa-libGL-devel \
    && \
    yum install -y openssh-clients \
    && \
    yum clean all && \
    rm -rf /var/cache/yum/*

# This changes root's .condarc which ENTRYPOINT copies to /home/conda/.condarc later.
RUN . /opt/conda/etc/profile.d/conda.sh && \
    conda config \
    --add channels bioconda \
    --add channels conda-forge \
    && \
    conda config \
    --remove channels defaults || true \
    && \
    { conda config --remove repodata_fns current_repodata.json 2> /dev/null || true ; } && \
    conda config --prepend repodata_fns repodata.json && \
    conda config --set channel_priority strict && \
    conda config --set auto_update_conda False

# Only dependency definitions are inputs to these stages. Source edits should
# rebuild the wheel without repeating either environment installation.
FROM base AS requirements
COPY bioconda_utils/bioconda_utils-requirements.txt /opt/bioconda-utils/bioconda_utils-requirements.txt

# BuildKit can prepare the wheel tools and runtime environment independently.
FROM requirements AS build_tools
RUN . /opt/conda/etc/profile.d/conda.sh && conda activate base && \
    # Bootstrap the declared Python and build-tool versions before building the
    # wheel. The base image's interpreter may be older than Requires-Python.
    # Extract the constraints from requirements.txt to keep a single source of truth.
    sed -nE \
    '/^(python|setuptools|setuptools-scm)([><!=~ ].+)?$/p' \
    /opt/bioconda-utils/bioconda_utils-requirements.txt \
    | xargs -r conda install --yes

FROM build_tools AS build
WORKDIR /tmp/repo
COPY . ./
# Worktrees reference Git metadata outside the build context. Local builds can
# supply the version explicitly; ordinary Git checkouts still use setuptools-scm.
ARG BIOCONDA_UTILS_VERSION
RUN . /opt/conda/etc/profile.d/conda.sh && conda activate base && \
    if [ -n "${BIOCONDA_UTILS_VERSION}" ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BIOCONDA_UTILS="${BIOCONDA_UTILS_VERSION}"; \
    fi && \
    pip wheel --no-deps --no-build-isolation . && \
    cp ./bioconda_utils-*.whl /opt/bioconda-utils/ && \
    chgrp -R lucky /opt/bioconda-utils && \
    chmod -R g=u /opt/bioconda-utils

FROM requirements AS runtime
RUN . /opt/conda/etc/profile.d/conda.sh && conda activate base && \
    # Make sure we get the (working) conda we want before installing the rest.
    sed -nE \
    '/^conda([><!=~ ].+)?$/p' \
    /opt/bioconda-utils/bioconda_utils-requirements.txt \
    | xargs -r conda install --yes && \
    conda install --yes --file /opt/bioconda-utils/bioconda_utils-requirements.txt && \
    conda clean --yes --index --tarballs && \
    # Find files that are not already in group "lucky" and change their group and mode.
    find /opt/conda \
    \! -group lucky \
    -exec chgrp --no-dereference lucky {} + \
    \! -type l \
    -exec chmod g=u {} +

# Wait for the wheel only after the runtime dependencies are installed.
FROM runtime AS final
COPY --from=build /opt/bioconda-utils/*.whl /opt/bioconda-utils/
RUN . /opt/conda/etc/profile.d/conda.sh && conda activate base && \
    pip install --no-deps --find-links /opt/bioconda-utils bioconda_utils && \
    # This file previously inherited these permissions from the wheel stage.
    chgrp lucky /opt/bioconda-utils/bioconda_utils-requirements.txt && \
    chmod g=u /opt/bioconda-utils/bioconda_utils-requirements.txt && \
    # Include newly installed files in the inherited entrypoint's group access.
    find /opt/conda \
    \! -group lucky \
    -exec chgrp --no-dereference lucky {} + \
    \! -type l \
    -exec chmod g=u {} +
