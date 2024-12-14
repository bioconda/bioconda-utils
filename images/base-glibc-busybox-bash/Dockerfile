# Don't use Debian's busybox package since it only provides a smaller subset of
# BusyBox's functions (e.g., no administrative tools like adduser etc.).
# Since we create a glibc image anyway, we can also use a the slightly smaller
# dynamically linked binary.

ARG debian_version
FROM "debian:${debian_version}-slim" AS build_base
RUN [ ! -f /etc/apt/sources.list ] || sed --in-place= --regexp-extended \
      '/ stretch/ { s,-updates,-backports, ; s,/(deb|security)\.,/archive., }' \
      /etc/apt/sources.list


FROM build_base AS rootfs_builder

ARG busybox_image
COPY --from="${busybox_image}" /build /build
WORKDIR /busybox-rootfs
RUN arch="$( uname -m )" \
    && \
    mkdir -p ./bin ./sbin ./usr/bin ./usr/sbin \
    && \
    cp -al "/build/busybox.${arch}" ./bin/busybox \
    && \
    ldd ./bin/busybox \
      | grep --only-matching --extended-regexp '/lib\S+' \
      | xargs -n1 sh -xc 'mkdir -p ".${1%/*}" && cp -aL "${1}" ".${1%/*}"' -- \
    && \
    chroot . /bin/busybox --install \
    && \
    rm -rf ./lib*

WORKDIR /rootfs

RUN mkdir -p ./etc ./home ./opt ./root ./run /tmp ./usr ./var/log \
     && \
    for dir in bin lib sbin ; do \
      mkdir "./usr/${dir}" \
      && \
      if [ -L "/bin" ] ; then \
        ln -s "usr/${dir}" "./${dir}" ; \
      else \
        mkdir "./${dir}" ; \
      fi ; \
    done

RUN find /busybox-rootfs -type f \
    -exec sh -c 'cp -al -- "${1}" "./${1#/busybox-rootfs/}"' -- '{}' ';'

# Install helper tools used by install-pkgs.
RUN apt-get update -qq \
    && \
    DEBIAN_FRONTEND=noninteractive \
      apt-get install --yes --no-install-recommends \
      patchelf

COPY install-pkgs /usr/local/bin
RUN install-pkgs "$( pwd )" /tmp/work \
      bash \
      base-passwd \
      libc-bin \
      login \
      ncurses-base \
    && \
    # Remove contents of /usr/local as downstream images overwrite those.
    find ./usr/local/ \
      -mindepth 1 -depth \
      -delete

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

# env-activate.sh (+ optionally env-execute) should be overwritten downstream.
# - env-activate.sh:
#    Is sourced (via symlink in /etc/profile.d/) to activate the /usr/local env.
# - env-execute:
#    Is set as the ENTRYPOINT to activate /usr/local before exec'ing CMD.
RUN touch ./usr/local/env-activate.sh \
    && \
    touch ./usr/local/env-execute \
    && \
    chmod +x ./usr/local/env-execute \
    && \
    ln -s \
      /usr/local/env-activate.sh \
      ./etc/profile.d/env-activate.sh \
    && \
    printf '%s\n' \
      '#! /bin/bash' \
      ". '/usr/local/env-activate.sh'" \
      'exec "${@}"' \
      > ./usr/local/env-execute

FROM scratch
COPY --from=rootfs_builder /rootfs /
ENV LANG=C.UTF-8
ENTRYPOINT [ "/usr/local/env-execute" ]
CMD [ "bash" ]
