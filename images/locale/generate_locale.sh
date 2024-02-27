#!/bin/bash

iidfile="$( mktemp )"
buildah bud --iidfile="${iidfile}" --file=Dockerfile
podman run -v $(pwd):/tmp "$( cat $iidfile )" cp -r /usr/lib/locale/C.utf8 /tmp
