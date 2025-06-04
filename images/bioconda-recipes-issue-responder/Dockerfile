ARG base=
ARG create_env=

FROM ${create_env} as build
RUN /opt/create-env/env-execute \
      create-env \
        --strip-files=\* \
        --remove-paths=\*.a \
        --remove-paths=\*.pyc \
        /usr/local \
        aiohttp \
        anaconda-client \
        ca-certificates \
        git \
        openssh \
        python=3.8 \
        pyyaml \
        skopeo \
    && \
    # Workaround for https://github.com/conda/conda/issues/10490
    export CONDA_REPODATA_THREADS=1 && \
    # We don't need Perl (used by Git for some functionalities).
    # => Remove perl package to reduce image size.
    /opt/create-env/env-execute \
      conda remove --yes \
        --prefix=/usr/local \
        --force-remove \
        perl

FROM "${base}"
COPY --from=build /usr/local /usr/local
COPY ./issue-responder /usr/local/bin/

# Used environment variables:
#  - JOB_CONTEXT
#  - BOT_TOKEN
#  - GITTER_TOKEN
#  - ANACONDA_TOKEN
#  - QUAY_OAUTH_TOKEN
#  - QUAY_LOGIN
