#!/bin/bash
# Install the built RPMs and try them, as root in a Fedora container (also a
# fresh one: everything else comes from dnf):
#
#   packaging/rpm/smoke-test.sh RPMDIR [EXPECT_VERSION]
#
# Starts `tavletta --version` (it must say EXPECT_VERSION, if given) and
# renders two example labels to PNG without a display or a printer: one
# with a QR code (segno), one with a Data Matrix code (Zint). Checks the
# print server's unit (not enabled), its user and folders (only with
# tavletta-server), and the udev rule.
set -euo pipefail
RPMS="$(realpath "$1")"
EXPECT="${2:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# as on the CI runners (see build.sh)
export CI=1

# with their documentation (the container images install none: tsflags=nodocs);
# the program first: it makes no user and no server folders
mapfile -t program < <(find "$RPMS" -maxdepth 1 -name '*.rpm' ! -name '*.src.rpm' ! -name 'tavletta-server-*')
dnf install -y -q --setopt=tsflags= "${program[@]}" > /dev/null
if getent passwd tavletta > /dev/null || [ -e /etc/tavletta ]; then
  echo "error: the package tavletta made the server's user or folders" >&2
  exit 1
fi
dnf install -y -q --setopt=tsflags= "$RPMS"/tavletta-server-[0-9]*.noarch.rpm > /dev/null

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
test -f /usr/share/man/man1/tavletta.1.gz
test -f /usr/share/applications/xyz.joshuahirsig.Tavletta.desktop
test -f /usr/share/metainfo/xyz.joshuahirsig.Tavletta.metainfo.xml
# in the package (the container image installs no languages but C: %_install_langs)
rpm -ql tavletta | grep -qx /usr/share/locale/de/LC_MESSAGES/tavletta.mo
# the print server's web page is part of the package (package data)
python3 -c 'from tavletta.server import webui; assert all(webui.asset(n) for n in ("index.html", "app.js", "style.css"))'
# the print server: installed with its user and folders, not enabled
grep -q '^ExecStart=/usr/bin/tavletta serve ' /usr/lib/systemd/system/tavletta-server.service
getent passwd tavletta > /dev/null
test "$(stat -c %U:%a /etc/tavletta /var/lib/tavletta/labels)" = "$(printf 'tavletta:750\ntavletta:750')"
test ! -e /etc/systemd/system/multi-user.target.wants/tavletta-server.service
test -f /usr/lib/udev/rules.d/70-tavletta.rules
echo "smoke test passed"
