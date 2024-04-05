ARG debian_version

FROM "debian:${debian_version}-slim"
RUN [ ! -f /etc/apt/sources.list ] || sed --in-place= --regexp-extended \
      '/ stretch/ { s,-updates,-backports, ; s,/(deb|security)\.,/archive., }' \
      /etc/apt/sources.list \
    && \
    apt-get update -qq \
    && \
    # Add en_US.UTF-8 locale.
    printf '%s\n' 'en_US.UTF-8 UTF-8' \
      >> /etc/locale.gen \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get install --yes --no-install-recommends \
      $( \
        . /etc/os-release \
        && \
        [ "${VERSION_ID-10}" -lt 10 ] \
        && \
        printf '%s\n' \
          libegl1-mesa \
          libgl1-mesa-glx \
        || \
        printf '%s\n' \
          libegl1 \
          libgl1 \
          libglx-mesa0 \
      ) \
      libglvnd0 \
      libopengl0 \
      locales \
      openssh-client \
      procps \
    && \
    # Remove "locales" package, but keep the generated locale.
    sed -i \
      's/\s*rm .*locale-archive$/: &/' \
      /var/lib/dpkg/info/locales.prerm \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get remove --yes \
      locales \
    && \
    # On Debian 10 (and 11) libgl1-mesa-glx pulls in libgl1-mesa-dri (which in
    # turn has more heavy-weight dependencies). We leave these out of the image
    # (by manually removing it from "Depends:" list) like we do with Debian 9.
    sed -i \
      '/^Depends:/ s/, libgl1-mesa-dri\>//g' \
      /var/lib/dpkg/status \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get autoremove --yes \
    && \
    # Remove apt package lists.
    rm -rf /var/lib/apt/lists/* \
    && \
    # Remove contents of /usr/local as downstream images overwrite those.
    find ./usr/local/ \
      -mindepth 1 -depth \
      -delete

RUN dpkg-query --show --showformat \
      '${db:Status-Status} ${Package}\n' \
      | sed -n 's/:/%3a/g ; s/^installed //p' \
      > /.pkg.lst \
    && \
    dpkg-query --show --showformat \
      '${db:Status-Status} ${Package}_${Version}_${Architecture}\n' \
      | sed -n 's/:/%3a/g ; s/$/.deb/ ; s/^installed //p' \
      > /.deb.lst

RUN while IFS=: read _ _ uid gid _ home _ ; do \
      [ -n "${home##/var/run/*}" ] || home="${home#/var}" \
      && \
      [ -d "./${home#/}" ] || [ "${home}" = "/nonexistent" ] && continue ; \
      mkdir -p "./${home#/}" \
      && \
      chown "${uid}:${gid}" "./${home#/}" \
      && \
      chmod 775 "./${home#/}" \
    ; done < ./etc/passwd \
    && \
    pwck --read-only --root "$( pwd )" \
      | { ! grep -v -e 'no changes' -e '/nonexistent' ; } \
    && \
    grpck --read-only --root "$( pwd )" \
    && \
    find \
      -xdev -type f \! -path ./var/\* \! -path ./usr/share/\* \! -name \*.pl \
      | xargs -P0 -n100 sh -c \
        'chroot . ldd -- "${@}" 2> /dev/null | sed -n "/:/h; /not found/{x;p;x;p}"' -- \
        | { ! grep . ; }

# Bash 4.* did not have default key bindings for control-arrow-key key
# combinations. Add some for convenience:
RUN >> /etc/inputrc \
      printf '%s\n' \
      '' \
      '"\e[5C": forward-word' \
      '"\e[5D": backward-word' \
      '"\e\e[C": forward-word' \
      '"\e\e[D": backward-word' \
      '"\e[1;5C": forward-word' \
      '"\e[1;5D": backward-word' \
      ;

# env-activate.sh (+ optionally env-execute) should be overwritten downstream.
# - env-activate.sh:
#    Is sourced (via symlink in /etc/profile.d/) to activate the /usr/local env.
# - env-execute:
#    Is set as the ENTRYPOINT to activate /usr/local before exec'ing CMD.
RUN touch /usr/local/env-activate.sh \
    && \
    touch /usr/local/env-execute \
    && \
    chmod +x /usr/local/env-execute \
    && \
    ln -s \
      /usr/local/env-activate.sh \
      /etc/profile.d/env-activate.sh \
    && \
    printf '%s\n' \
      '#! /bin/bash' \
      ". '/usr/local/env-activate.sh'" \
      'exec "${@}"' \
      > /usr/local/env-execute

ENV LANG=C.UTF-8
ENTRYPOINT [ "/usr/local/env-execute" ]
CMD [ "bash" ]
