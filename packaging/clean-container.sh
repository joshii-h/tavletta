#!/bin/bash
# Runs a command in a fresh container, for the package workflows: the
# packages are tried where nothing of their build is installed.
#
#   packaging/clean-container.sh IMAGE DIR COMMAND...
#
# DIR is copied to /work in the container (docker cp: it works where the
# job itself runs in a container and reaches the host's Docker daemon,
# whose paths are not the job's), COMMAND runs there as root, the container
# is removed afterwards (--rm, and by this script). The exit status is
# COMMAND's.
#
# Every such container carries the label tavletta.ci=1 and the time it was
# made (tavletta.ci.created, seconds since 1970), so leftovers of a job that
# was killed can be found (docker ps -a --filter label=tavletta.ci=1) and
# removed: each run first removes those older than STALE_SECONDS (3 hours,
# longer than any job may run; younger ones may belong to a job still
# running, e.g. deb.yml and rpm.yml at the same time; one without the time
# counts as stale).
set -euo pipefail
[ $# -ge 3 ] || { echo "usage: $0 IMAGE DIR COMMAND..." >&2; exit 2; }
image="$1" dir="$2"
shift 2
now="$(date +%s)"

docker ps -a --filter label=tavletta.ci=1 --format '{{.ID}} {{.Label "tavletta.ci.created"}}' |
  while read -r id created; do
    if [ $((now - ${created:-0})) -gt "${STALE_SECONDS:-10800}" ]; then
      echo "removing the leftover container $id"
      docker rm -f "$id" > /dev/null || true
    fi
  done

cid="$(docker create --rm --label tavletta.ci=1 --label "tavletta.ci.created=$now" \
  --workdir /work "$image" "$@")"
trap 'docker rm -f "$cid" > /dev/null 2>&1 || true' EXIT
docker cp "$dir/." "$cid:/work"
docker start --attach "$cid"
