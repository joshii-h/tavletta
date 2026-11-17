#!/bin/bash
# Build Tavletta's RPMs, and those of the Python packages it needs that
# Fedora does not have, in a Fedora system or container:
#
#   packaging/rpm/build.sh OUTDIR [TARBALL]
#
# TARBALL is Tavletta's source as Forgejo serves it for a tag (top folder
# tavletta/). Without it the source is taken from this checkout: `git
# archive HEAD` (committed files only), or, where git cannot read the
# checkout (a worktree mounted into a container), the folder as it is.
#
# Order: python-segno, python-barcode, python-labelprinter,
# python-zint-bindings, then tavletta; each package is installed after its
# build so the next one's build requirements resolve. Sources of the
# dependencies are downloaded (spectool), so this needs the network.
# rpmlint checks every SRPM and RPM: an error fails the build, warnings are
# shown. OUTDIR gets the SRPMs and RPMs.
#
# As root, the build tools and build requirements are installed with dnf;
# otherwise they must be there and sudo is used to install.
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: $0 OUTDIR [TARBALL]" >&2
    exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
mkdir -p "$1"
OUT="$(cd "$1" && pwd)"
TARBALL="${2:+$(realpath "$2")}"
DEPS=(python-segno python-barcode python-labelprinter python-zint-bindings)

SUDO=()
[ "$(id -u)" = 0 ] || SUDO=(sudo)
dnf_install() { "${SUDO[@]}" dnf install -y -q --setopt=install_weak_deps=False "$@"; }
dnf_install rpm-build rpmdevtools rpmlint python3-rpm-macros pyproject-rpm-macros git-core

# Tavletta's version: the spec must be at the version of the source
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/tavletta/__init__.py")"
spec_version="$(rpmspec -q --srpm --qf '%{version}' "$HERE/tavletta.spec")"
if [ "$version" != "$spec_version" ]; then
    echo "error: tavletta.spec has Version $spec_version, tavletta/__init__.py $version" >&2
    echo "(bump the spec's Version and add a %changelog entry)" >&2
    exit 1
fi

TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{SOURCES,SPECS,SRPMS,RPMS,BUILD}
cp "$HERE"/*.spec "$HERE"/*.patch "$TOP/SPECS/"
cp "$HERE"/*.patch "$TOP/SOURCES/"
RPMBUILD=(rpmbuild --define "_topdir $TOP")

# Tavletta's source tarball, named as the spec's Source
src="$TOP/SOURCES/tavletta-$version.tar.gz"
if [ -n "$TARBALL" ]; then
    cp "$TARBALL" "$src"
elif git -C "$ROOT" rev-parse --git-dir > /dev/null 2>&1; then
    git -C "$ROOT" archive --format=tar.gz --prefix=tavletta/ -o "$src" HEAD
else
    echo "note: no git checkout, packing the folder as it is"
    tar -C "$ROOT/.." --transform "s|^$(basename "$ROOT")|tavletta|" \
        --exclude=.git --exclude=.venv --exclude=build --exclude=build-dir \
        --exclude=.flatpak-builder --exclude=__pycache__ --exclude='*.egg-info' \
        -czf "$src" "$(basename "$ROOT")"
fi

build() {
    local spec="$TOP/SPECS/$1.spec" status
    echo "== $1"
    [ "$1" = tavletta ] || spectool -g -C "$TOP/SOURCES" "$spec"
    # the build requirements: the spec's own, then those %generate_buildrequires
    # finds (rpmbuild -br stops with 11 and writes them into a .buildreqs.nosrc.rpm)
    "${SUDO[@]}" dnf builddep -y -q "$spec"
    for _ in 1 2 3 4 5; do
        rm -f "$TOP"/SRPMS/*.buildreqs.nosrc.rpm
        status=0
        "${RPMBUILD[@]}" -br "$spec" > "$TOP/br.log" 2>&1 || status=$?
        [ "$status" = 0 ] && break
        if [ "$status" != 11 ]; then
            cat "$TOP/br.log"; return 1
        fi
        "${SUDO[@]}" dnf builddep -y -q "$TOP"/SRPMS/*.buildreqs.nosrc.rpm
    done
    [ "$status" = 0 ] || { cat "$TOP/br.log"; return 1; }
    rm -f "$TOP"/SRPMS/*.buildreqs.nosrc.rpm
    "${RPMBUILD[@]}" -ba "$spec"
}

for name in "${DEPS[@]}"; do
    build "$name"
    # the next builds (and tavletta's) need it
    dnf_install "$TOP"/RPMS/*/python3-"${name#python-}"-[0-9]*.rpm
done
build tavletta

find "$TOP/SRPMS" "$TOP/RPMS" -name '*.rpm' ! -name '*debuginfo*' ! -name '*debugsource*' \
    -exec cp -t "$OUT" {} +
echo "== rpmlint"
# exit code 64 on errors; warnings are shown and do not fail
rpmlint -r "$HERE/tavletta.rpmlintrc" "$OUT"/*.rpm
ls -l "$OUT"
