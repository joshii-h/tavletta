#!/bin/bash
# Release assets of the package workflows (.forgejo/workflows/deb.yml,
# rpm.yml):
#
#   packaging/release-assets.sh collect DIR SUMS FILE...
#
# Copies FILE... into DIR (new or empty) under names that GitHub keeps as they are (it
# renames others when release-mirror.yml copies them): every character but
# A-Z a-z 0-9 . _ - becomes a dot, e.g. the + of a Debian version and the ^
# of an RPM snapshot. Two files of one name, or a name that starts or ends
# with a dot, are refused. Writes DIR/SUMS, the sha256sum of them all.
#
#   packaging/release-assets.sh upload TAG FILE...
#
# Attaches FILE..., in this order, to the Forgejo release of TAG (the
# checksum file last: release-mirror.yml takes it for the sign that the
# workflow's files are all there), which release.yml creates:
# waits for it up to RELEASE_WAIT_TRIES (30) times RELEASE_WAIT_SECONDS
# (60; frequent polling looks like a crawler to the server's CrowdSec). A
# file attached already with the same content is skipped, one with other
# content (a rebuild) replaced, so re-runs are safe and the checksum file
# always matches the files. Needs FORGEJO_URL, GITHUB_REPOSITORY and TOKEN,
# curl, jq and sha256sum.
set -euo pipefail

collect() {
  local dir="$1" sums="$2" f name names=()
  shift 2
  mkdir -p "$dir"
  if [ -n "$(ls -A "$dir")" ]; then
    echo "error: $dir is not empty" >&2
    return 1
  fi
  for f in "$@"; do
    name="$(basename "$f" | LC_ALL=C sed 's/[^A-Za-z0-9._-]/./g')"
    case "$name" in
      .* | *.) echo "error: asset name $name starts or ends with a dot" >&2; return 1 ;;
    esac
    if [ -e "$dir/$name" ]; then
      echo "error: two assets named $name" >&2
      return 1
    fi
    cp -- "$f" "$dir/$name"
    names+=("$name")
  done
  (cd "$dir" && sha256sum -- "${names[@]}") > "$dir/$sums"
  cat "$dir/$sums"
}

upload() {
  local tag="$1" api release id f name asset url
  shift
  api="$FORGEJO_URL/api/v1/repos/$GITHUB_REPOSITORY"
  local auth=(-H "Authorization: token $TOKEN")
  local tries="${RELEASE_WAIT_TRIES:-30}" i
  for ((i = 1; ; i++)); do
    release="$(curl -sS --fail "${auth[@]}" "$api/releases/tags/$tag")" && break
    if [ "$i" -ge "$tries" ]; then
      echo "::error::no Forgejo release for $tag"
      return 1
    fi
    sleep "${RELEASE_WAIT_SECONDS:-60}"
  done
  id="$(jq -r .id <<< "$release")"
  for f in "$@"; do
    name="$(basename "$f")"
    asset="$(jq -c --arg n "$name" 'first(.assets[]? | select(.name == $n)) // empty' <<< "$release")"
    if [ -n "$asset" ]; then
      url="$(jq -r .browser_download_url <<< "$asset")"
      if [ "$(curl -sS --fail -L "$url" | sha256sum | cut -d' ' -f1)" = \
           "$(sha256sum < "$f" | cut -d' ' -f1)" ]; then
        echo "$name exists"
        continue
      fi
      curl -sS --fail-with-body "${auth[@]}" -X DELETE \
        "$api/releases/$id/assets/$(jq -r .id <<< "$asset")" > /dev/null
      echo "$name replaced:"
    fi
    curl -sS --fail-with-body "${auth[@]}" -F "attachment=@$f" \
      "$api/releases/$id/assets?name=$(jq -rn --arg n "$name" '$n|@uri')" > /dev/null
    echo "$name uploaded"
  done
  echo "$FORGEJO_URL/$GITHUB_REPOSITORY/releases/tag/$tag"
}

case "${1:-}" in
  collect) [ $# -ge 4 ] || { echo "usage: $0 collect DIR SUMS FILE..." >&2; exit 2; }; shift; collect "$@" ;;
  upload) [ $# -ge 3 ] || { echo "usage: $0 upload TAG FILE..." >&2; exit 2; }; shift; upload "$@" ;;
  *) echo "usage: $0 collect DIR SUMS FILE... | upload TAG FILE..." >&2; exit 2 ;;
esac
