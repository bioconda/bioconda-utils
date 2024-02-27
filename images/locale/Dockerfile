FROM "debian:12.1-slim"
RUN apt-get update -qq \
    && \
    # Add en_US.UTF-8 locale.
    printf '%s\n' 'en_US.UTF-8 UTF-8' \
      >> /etc/locale.gen \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get install --yes --no-install-recommends \
      locales \
    && \
    # Remove "locales" package, but keep the generated locale.
    sed -i \
      's/\s*rm .*locale-archive$/: &/' \
      /var/lib/dpkg/info/locales.prerm \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get remove --yes \
      locales
