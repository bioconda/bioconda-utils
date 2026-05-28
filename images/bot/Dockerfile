ARG base=quay.io/bioconda/base-glibc-busybox-bash:latest

FROM quay.io/bioconda/create-env as build
## If gettext pulls in libxml2, use one that doesn't bloat the image with ICU.
#RUN . /opt/create-env/env-activate.sh \
#    && \
#    mamba install --yes curl conda-build patch \
#    && \
#    curl -L \
#      https://github.com/conda-forge/libxml2-feedstock/archive/master.tar.gz \
#      | tar -xzf- \
#    && \
#    cd libxml2-feedstock* \
#    && \
#    sed -i s/--with-icu/--without-icu/ recipe/build.sh \
#    && \
#    sed -i '/- icu\>/d' recipe/meta.yaml \
#    && \
#    conda-build -m .ci_support/linux_64_.yaml recipe/
ARG packages=
ARG prefix=/usr/local
RUN . /opt/create-env/env-activate.sh && \
    export CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY=0 \
    && \
    create-env \
        --strip-files=\* \
        --remove-paths=\*.a \
        --remove-paths=\*.c \
        --remove-paths=\*.pyc \
        --remove-paths=\*.pyi \
        --remove-paths=\*.pyx \
        --remove-paths=\*.pyx \
        --remove-paths=include/\* \
        --remove-paths=share/doc/\* \
        --remove-paths=share/man/\* \
        --remove-paths='share/terminfo/[!x]/*' \
        --remove-paths=share/locale/\* \
        --remove-paths=lib/python*/ensurepip/\* \
        "${prefix}" \
        --channel=local \
        --channel=conda-forge \
        --override-channels \
        pip wheel setuptools \
        python\>=3.8 \
        aiohttp \
        ca-certificates \
        pyyaml \
        ${packages} \
    && \
    # Remove tk since no tkinter & co. are needed.
    conda remove \
        --yes \
        --force-remove \
        --prefix="${prefix}" \
        tk \
    && \
    # Get rid of Perl pulled in by Git.
    # (Bot only uses non-Perl Git functionality => remove baggage.)
    if conda list --prefix="${prefix}" | grep -q '^perl\s' ; then \
        conda remove \
            --yes \
            --force-remove \
            --prefix="${prefix}" \
            perl \
    ; fi
# Install bioconda_bot.
WORKDIR /tmp/bot
COPY . ./
RUN . "${prefix}/env-activate.sh" && \
    pip wheel --no-deps . \
    && \
    pip install --no-deps --find-links . bioconda_bot

FROM "${base}"
COPY --from=build /usr/local /usr/local
