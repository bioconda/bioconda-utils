ARG BUSYBOX_IMAGE
FROM ${BUSYBOX_IMAGE} as build

WORKDIR /tmp/work
COPY install-conda print-env-activate create-env ./
RUN arch="$( uname -m )" \
    && \
    wget --quiet -O ./miniconda.sh \
      "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-${arch}.sh"

# Install exact versions of conda/mamba
ARG CONDA_VERSION
ARG MAMBA_VERSION
RUN echo $CONDA_VERSION > requirements.txt && echo $MAMBA_VERSION >> requirements.txt
RUN ./install-conda ./requirements.txt /opt/create-env

ARG BUSYBOX_IMAGE
FROM ${BUSYBOX_IMAGE}
COPY --from=build /opt/create-env /opt/create-env

# Copy (Bioconda-specific) Conda configuration created by the install-conda script.
COPY --from=build /root/.condarc /root/

RUN \
    # Use a per-user config (instead of conda config --sys) for more flexibility.
    cp /root/.condarc /etc/skel/ \
    && \
    # Enable conda shell function for login shells.
    ln -s /opt/create-env/etc/profile.d/conda.sh /etc/profile.d/ \
    && \
    # Enable conda function in interactive Bash (via .bashrc) and POSIX shells (via ENV).
    printf '%s\n' \
      '\. /etc/profile.d/conda.sh' \
      | tee -a /root/.bashrc \
      >>   /etc/skel/.bashrc
ENV ENV=/etc/profile.d/conda.sh

ENTRYPOINT [ "/opt/create-env/bin/tini", "--", "/opt/create-env/env-execute" ]
CMD [ "bash" ]
