#!/bin/bash
# Install the built Debian packages and try them, as root in a Debian
# container (also a fresh one: everything comes from apt):
#
#   packaging/debian/smoke-test.sh DEBDIR [EXPECT_VERSION]
#
# Starts `tavletta --version` (it must say EXPECT_VERSION, if given) and
# renders two example labels to PNG without a display or a printer: one
# with a QR code (segno), one with a Data Matrix code (Zint). Checks that
# only tavletta-server makes the server's user and folders. With the
# source package in DEBDIR/source (build.sh puts it there), then runs its
# autopkgtests (debian/tests) on this system: the program's tests against
# the installed package.
set -euo pipefail
DEBS="$(realpath "$1")"
EXPECT="${2:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export DEBIAN_FRONTEND=noninteractive
# as on the CI runners (see build.sh)
export CI=1

apt-get update -qq
# the program first: it makes no user and no server folders
mapfile -t program < <(find "$DEBS" -maxdepth 1 -name '*.deb' ! -name 'tavletta-server_*')
apt-get install -y -qq "${program[@]}" > /dev/null
if getent passwd tavletta > /dev/null || [ -e /etc/tavletta ]; then
  echo "error: the package tavletta made the server's user or folders" >&2
  exit 1
fi
apt-get install -y -qq "$DEBS"/tavletta-server_*.deb > /dev/null

# no display, no session bus: what a server or a script has
unset DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS
cd "$TMP"
version="$(tavletta --version)"
echo "$version"
if [ -n "$EXPECT" ] && [ "$version" != "tavletta $EXPECT" ]; then
  echo "error: expected tavletta $EXPECT" >&2
  exit 1
fi
cp /usr/share/doc/tavletta/examples/party.tavletta qr.tavletta
sed 's/"symbology": *"qr"/"symbology": "datamatrix"/' qr.tavletta > datamatrix.tavletta
for label in qr datamatrix; do
  tavletta print "$label.tavletta" --dry-run --no-query --no-save-counter --output "$label.png"
  python3 -c 'import sys; from PIL import Image; im = Image.open(sys.argv[1]); print(sys.argv[1], im.size); assert im.width > 100' "$label.png"
done
man -w tavletta > /dev/null 2>&1 || test -f /usr/share/man/man1/tavletta.1.gz
test -f /usr/share/applications/xyz.joshuahirsig.Tavletta.desktop
test -f /usr/share/metainfo/xyz.joshuahirsig.Tavletta.metainfo.xml
test -f /usr/share/locale/de/LC_MESSAGES/tavletta.mo
# the print server: installed with its user and folders, not enabled
grep -q '^ExecStart=/usr/bin/tavletta serve ' /usr/lib/systemd/system/tavletta-server.service
getent passwd tavletta > /dev/null
test "$(stat -c %U:%a /etc/tavletta /var/lib/tavletta/labels)" = "$(printf 'tavletta:750\ntavletta:750')"
test ! -e /etc/systemd/system/multi-user.target.wants/tavletta-server.service
test -f /usr/lib/udev/rules.d/70-tavletta.rules
dsc="$(ls "$DEBS"/source/tavletta_*.dsc 2> /dev/null || true)"
if [ -n "$dsc" ]; then
  apt-get install -y -qq --no-install-recommends autopkgtest > /dev/null
  autopkgtest --output-dir "$TMP/autopkgtest" "$DEBS"/*.deb "$dsc" -- null
else
  echo "(no source package in $DEBS/source: autopkgtests skipped)"
fi
echo "smoke test passed"
