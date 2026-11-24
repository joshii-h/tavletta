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
# always matches the files.
#
#   packaging/release-assets.sh notes TAG LINE
#
# Adds LINE to the release notes, under **Downloads** (made if missing),
# unless they have it: a workflow names its files only once they are
# attached, and a re-run adds nothing twice. Another workflow may change
# the notes at the same moment and drop the line again: it is checked again
# after NOTES_RECHECK_SECONDS (30) and added again if missing.
#
# Both wait for the release as upload does. They need FORGEJO_URL,
# GITHUB_REPOSITORY and TOKEN, curl, jq and sha256sum.
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

api() { echo "$FORGEJO_URL/api/v1/repos/$GITHUB_REPOSITORY"; }
auth=(-H "Authorization: token ${TOKEN:-}")
# no request hangs a workflow: API calls a minute, files 10 minutes
quick=(--connect-timeout 20 --max-time 60)
slow=(--connect-timeout 20 --max-time 600)

release_of() {  # TAG: the release's JSON, waiting for it; fails without it
  local tries="${RELEASE_WAIT_TRIES:-30}" i
  for ((i = 1; ; i++)); do
    curl -sS --fail "${quick[@]}" "${auth[@]}" "$(api)/releases/tags/$1" && return
    [ "$i" -lt "$tries" ] || return 1
    sleep "${RELEASE_WAIT_SECONDS:-60}"
  done
}

upload() {
  local tag="$1" release id f name asset url
  shift
  release="$(release_of "$tag")" || { echo "::error::no Forgejo release for $tag"; return 1; }
  id="$(jq -r .id <<< "$release")"
  for f in "$@"; do
    name="$(basename "$f")"
    asset="$(jq -c --arg n "$name" 'first(.assets[]? | select(.name == $n)) // empty' <<< "$release")"
    if [ -n "$asset" ]; then
      url="$(jq -r .browser_download_url <<< "$asset")"
      if [ "$(curl -sS --fail -L "${slow[@]}" "$url" | sha256sum | cut -d' ' -f1)" = \
           "$(sha256sum < "$f" | cut -d' ' -f1)" ]; then
        echo "$name exists"
        continue
      fi
      curl -sS --fail-with-body "${quick[@]}" "${auth[@]}" -X DELETE \
        "$(api)/releases/$id/assets/$(jq -r .id <<< "$asset")" > /dev/null
      echo "$name replaced:"
    fi
    curl -sS --fail-with-body "${slow[@]}" "${auth[@]}" -F "attachment=@$f" \
      "$(api)/releases/$id/assets?name=$(jq -rn --arg n "$name" '$n|@uri')" > /dev/null
    echo "$name uploaded"
  done
  echo "$FORGEJO_URL/$GITHUB_REPOSITORY/releases/tag/$tag"
}

notes() {
  local tag="$1" line="$2" release body checked=0 i
  for ((i = 0; i < 4; i++)); do
    release="$(release_of "$tag")" || { echo "::error::no Forgejo release for $tag"; return 1; }
    body="$(jq -r '.body // ""' <<< "$release")"
    if grep -qxF -- "$line" <<< "$body"; then
      if [ "$checked" = 1 ]; then
        echo "release notes: $line"
        return 0
      fi
      checked=1
      sleep "${NOTES_RECHECK_SECONDS:-30}"
      continue
    fi
    grep -qxF -- "**Downloads**" <<< "$body" || body="${body%$'\n'}"$'\n\n'"**Downloads**"$'\n\n'
    body="${body%$'\n'}"$'\n'"$line"
    curl -sS --fail-with-body "${quick[@]}" "${auth[@]}" -X PATCH -H "Content-Type: application/json" \
      --data "$(jq -n --arg b "$body" '{body: $b}')" \
      "$(api)/releases/$(jq -r .id <<< "$release")" > /dev/null
    checked=0
  done
  echo "::error::the release notes of $tag keep losing: $line"
  return 1
}

case "${1:-}" in
  collect) [ $# -ge 4 ] || { echo "usage: $0 collect DIR SUMS FILE..." >&2; exit 2; }; shift; collect "$@" ;;
  upload) [ $# -ge 3 ] || { echo "usage: $0 upload TAG FILE..." >&2; exit 2; }; shift; upload "$@" ;;
  notes) [ $# = 3 ] || { echo "usage: $0 notes TAG LINE" >&2; exit 2; }; shift; notes "$@" ;;
  *) echo "usage: $0 collect DIR SUMS FILE... | upload TAG FILE... | notes TAG LINE" >&2; exit 2 ;;
esac
