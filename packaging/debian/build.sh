#!/bin/bash
# Build Tavletta's Debian packages, and the two it needs that Debian does
# not have (python3-zint-bindings, python3-labelprinter), in a Debian
# container as root:
#
#   packaging/debian/build.sh [--tarball FILE] OUTDIR
#
# --tarball: the upstream source as `git archive --prefix=tavletta-X.Y.Z/`
# writes it (or Forgejo's tag archive); by default it is made from the
# checkout's HEAD with git archive, so an untagged commit builds too. The
# version comes from tavletta/__init__.py and must be debian/changelog's
# (a pre-release 1.2.0-rc.1 there is 1.2.0~rc.1 here, which sorts before
# 1.2.0); tests/test_packaging.py checks that too.
#
# Installs what the builds need with apt (Build-Depends via mk-build-deps),
# builds source and binary packages with dpkg-buildpackage, runs lintian
# (an error fails the build; warnings and notes are shown) and copies the
# .deb files to OUTDIR, the source packages to OUTDIR/source.
#
# Uses the network to download the two dependencies' sources (checked
# against the SHA-256 below) and the build tools; the builds themselves are
# offline. packaging/debian/smoke-test.sh tests the packages.
set -euo pipefail

# python3-zint-bindings: PyPI sdist, built against Debian's libzint
ZINT_VERSION=1.2.2
ZINT_URL=https://files.pythonhosted.org/packages/source/z/zint-bindings/zint_bindings-$ZINT_VERSION.tar.gz
ZINT_SHA256=e28287040c34831b5919c6587bd3eb46d100571a187963d027afc62e3301998b
# python3-labelprinter: the commit of the fork pyproject.toml pins
LABELPRINTER_COMMIT=a6f2957de323f71f364b3acd6624da8b9885587a
LABELPRINTER_VERSION=0.1.0+git20261105.a6f2957
LABELPRINTER_URL=https://github.com/joshii-h/labelprinter-vc500w/archive/$LABELPRINTER_COMMIT.tar.gz
LABELPRINTER_SHA256=63086cc6a1306f1e08437452f60dc4dc0c91443c76d4e397e1f9daa411450a9a

TARBALL=
if [ "${1:-}" = --tarball ]; then
  TARBALL="$(realpath "$2")"
  shift 2
fi
[ $# = 1 ] || { echo "usage: $0 [--tarball FILE] OUTDIR" >&2; exit 2; }
mkdir -p "$1"
OUT="$(realpath "$1")"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export DEBIAN_FRONTEND=noninteractive

grep -q "$LABELPRINTER_COMMIT" "$SRC/pyproject.toml" || {
  echo "error: pyproject.toml pins another labelprinter commit; update LABELPRINTER_* in $0" >&2
  exit 1
}

echo "== build tools"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  build-essential ca-certificates curl devscripts dpkg-dev equivs fakeroot git lintian \
  quilt > /dev/null

download() {  # URL SHA256 FILE
  curl -sSfL --retry 3 -o "$3" "$1"
  echo "$2  $3" | sha256sum -c --quiet -
}

build() {  # DIR (the unpacked source with debian/): build, lint, install the debs
  local dir="$1"
  (
    cd "$dir"
    mk-build-deps -i -r -t "apt-get -y -qq --no-install-recommends" debian/control > /dev/null
    rm -f ./*-build-deps_* ../*-build-deps_*  # what mk-build-deps leaves behind
    dpkg-buildpackage -us -uc -sa
  )
  local changes
  changes="$(ls "$(dirname "$dir")"/*.changes)"
  echo "== lintian $(basename "$changes")"
  lintian --fail-on error --display-info --pedantic --tag-display-limit 0 "$changes"
  local debs
  debs="$(sed -n 's/^ [0-9a-f]\{64\} [0-9]* \(.*\.deb\)$/\1/p' "$changes")"
  (cd "$(dirname "$dir")" && apt-get install -y -qq --no-install-recommends $(printf './%s ' $debs) > /dev/null)
  mkdir -p "$OUT/source"
  (
    cd "$(dirname "$dir")"
    cp $debs "$OUT/"
    cp ./*.dsc ./*.debian.tar.* ./*.orig.tar.* ./*.changes ./*.buildinfo "$OUT/source/"
  )
}

echo "== python3-zint-bindings $ZINT_VERSION"
mkdir "$WORK/zint"
download "$ZINT_URL" "$ZINT_SHA256" "$WORK/zint/zint_bindings-$ZINT_VERSION.tar.gz"
deb="$SRC/packaging/debian-deps/python-zint-bindings/debian"
# Files-Excluded in debian/copyright: the copy of Zint goes
(cd "$WORK/zint" && mk-origtargz --package python-zint-bindings --version "$ZINT_VERSION" \
  --repack --repack-suffix +ds --compression xz --copyright-file "$deb/copyright" \
  --directory . "zint_bindings-$ZINT_VERSION.tar.gz" > /dev/null)
mkdir "$WORK/zint/src"
tar -xf "$WORK/zint/python-zint-bindings_$ZINT_VERSION+ds.orig.tar.xz" -C "$WORK/zint/src" --strip-components=1
cp -r "$deb" "$WORK/zint/src/debian"
build "$WORK/zint/src"

echo "== python3-labelprinter $LABELPRINTER_VERSION"
mkdir -p "$WORK/labelprinter/src"
orig="$WORK/labelprinter/python-labelprinter_$LABELPRINTER_VERSION.orig.tar.gz"
download "$LABELPRINTER_URL" "$LABELPRINTER_SHA256" "$orig"
tar -xf "$orig" -C "$WORK/labelprinter/src" --strip-components=1
cp -r "$SRC/packaging/debian-deps/python-labelprinter/debian" "$WORK/labelprinter/src/debian"
build "$WORK/labelprinter/src"

VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$SRC/tavletta/__init__.py")"
[ -n "$VERSION" ] || { echo "error: no __version__ in tavletta/__init__.py" >&2; exit 1; }
UPSTREAM="${VERSION/-/\~}"  # the Debian upstream version: 1.2.0-rc.1 -> 1.2.0~rc.1
packaged="$(dpkg-parsechangelog -l "$SRC/packaging/debian/changelog" -S Version)"
if [ "$packaged" != "$UPSTREAM-1" ]; then
  echo "error: packaging/debian/changelog is at $packaged, tavletta/__init__.py at $VERSION" >&2
  echo "(add a changelog entry for $UPSTREAM-1)" >&2
  exit 1
fi
echo "== tavletta $VERSION"
mkdir -p "$WORK/tavletta/src"
orig="$WORK/tavletta/tavletta_$UPSTREAM.orig.tar.gz"
if [ -n "$TARBALL" ]; then
  cp "$TARBALL" "$orig"
else
  git -C "$SRC" archive --format=tar.gz --prefix="tavletta-$VERSION/" -o "$orig" HEAD
fi
tar -xf "$orig" -C "$WORK/tavletta/src" --strip-components=1
grep -q "^__version__ = \"$VERSION\"$" "$WORK/tavletta/src/tavletta/__init__.py" || {
  echo "error: the tarball is not version $VERSION" >&2
  exit 1
}
cp -r "$SRC/packaging/debian" "$WORK/tavletta/src/debian"
# build.sh and smoke-test.sh are tools of the repository, not the package's
rm -f "$WORK/tavletta/src/debian/build.sh" "$WORK/tavletta/src/debian/smoke-test.sh"
build "$WORK/tavletta/src"

echo "== built"
ls -l "$OUT"
