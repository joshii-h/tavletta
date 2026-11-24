#!/bin/bash
# Runs a command in a fresh container, for the package workflows: the
# packages are tried where nothing of their build is installed.
#
#   packaging/clean-container.sh IMAGE DIR COMMAND...
#
# DIR is copied to /work in the container (docker cp: it works where the
# job itself runs in a container and reaches the host's Docker daemon,
# whose paths are not the job's), COMMAND runs there as root, the container
# is removed afterwards. The exit status is COMMAND's.
set -euo pipefail
[ $# -ge 3 ] || { echo "usage: $0 IMAGE DIR COMMAND..." >&2; exit 2; }
image="$1" dir="$2"
shift 2
cid="$(docker create --workdir /work "$image" "$@")"
trap 'docker rm -f "$cid" > /dev/null' EXIT
docker cp "$dir/." "$cid:/work"
docker start --attach "$cid"
