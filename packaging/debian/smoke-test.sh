#!/bin/bash
# Install the built Debian packages and try them, as root in a Debian
# container (also a fresh one: everything comes from apt):
#
#   packaging/debian/smoke-test.sh DEBDIR [EXPECT_VERSION]
#
# Starts `tavletta --version` (it must say EXPECT_VERSION, if given) and
# renders two example labels to PNG without a display or a printer: one
# with a QR code (segno), one with a Data Matrix code (Zint).
set -euo pipefail
DEBS="$(realpath "$1")"
EXPECT="${2:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq "$DEBS"/*.deb > /dev/null

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
echo "smoke test passed"
