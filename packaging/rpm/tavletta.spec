%global app_id xyz.joshuahirsig.Tavletta

Name:           tavletta
Version:        1.1.2
Release:        1%{?dist}
Summary:        Design and print labels on label printers

# AGPL-3.0-or-later: Tavletta
# MIT: the label sheets from gLabels (tavletta/sheets/templates.json) and
#      the shipping label detection after ThermalPrint (tavletta/shipping/detect.py)
# Apache-2.0: the symbols from Material Design Icons (tavletta/symbols/mdi.json.gz)
License:        AGPL-3.0-or-later AND MIT AND Apache-2.0
URL:            https://git.joshuahirsig.xyz/joshii/tavletta
# a pre-release v1.2.0-rc.1 is Version 1.2.0~rc.1 (sorts before 1.2.0)
Source:         %{url}/archive/v%{version_no_tilde}.tar.gz#/%{name}-%{version_no_tilde}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
# setup.py compiles the translations
BuildRequires:  gettext
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
# the tests: rendering through Pango and Cairo, codes read back with zxing-cpp
BuildRequires:  python3-pytest
BuildRequires:  python3-hypothesis
BuildRequires:  python3-pyyaml
BuildRequires:  python3dist(zxing-cpp)
BuildRequires:  python3-gobject
BuildRequires:  pango
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  dejavu-sans-fonts
# tools/icons.py's tests: librsvg's GObject introspection (Rsvg 2.0)
BuildRequires:  librsvg2
# the print server's unit and user (tavletta-server), the udev rule
BuildRequires:  systemd-rpm-macros

# PyGObject with GTK 4, libadwaita, Pango and PangoCairo (typelibs), pycairo
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       pango
Requires:       hicolor-icon-theme
Requires:       shared-mime-info
# shipping labels from PDF files (images work without it)
Recommends:     poppler-glib
# finding printers on the network and resolving their *.local names: through
# the Avahi daemon on D-Bus; other programs resolve *.local with nss-mdns
Recommends:     avahi
Suggests:       nss-mdns
# The Python dependencies (Pillow, segno, python-barcode, zint-bindings,
# labelprinter) come from pyproject.toml, as BuildRequires
# (%%generate_buildrequires, so the tests use them) and as Requires; Zint is
# one of them as in the Debian package: without it most symbologies show
# "code unavailable".

%description
Tavletta designs labels with text, images, symbols and codes, shows them
exactly as they will be printed and prints them over the network or USB: on
label printers with endless tape or die-cut labels (Brother VC-500W, QL and
P-touch, cab, NIIMBOT, ZPL, EPL, TSPL, ESC/POS and DYMO printers), and on
office printers through CUPS or IPP, one label per page or on label sheets.
It also prints label files from the command line and runs a print server
with a REST API, MCP and a web page.

A device rule lets the user at the computer use the USB label printers of
Brother, cab, DYMO, TSC, Zebra and NIIMBOT without joining further groups;
printers of other makers need the printer group. The print server as a
system service is in tavletta-server.


%package        server
Summary:        Print server of the Tavletta label designer, as a system service
Requires:       %{name} = %{version}-%{release}
# signing in to the web page with OpenID Connect (--oidc): the extra "oidc"
Requires:       python3-jwt+crypto
%{?systemd_requires}

%description    server
The print server of Tavletta, with a REST API, MCP and a web page, as the
system service tavletta-server: as a user of its own (made by this package),
in a sandbox, on the local address 127.0.0.1, port 8790, with its printers,
access tokens and labels in folders of its own. It is installed but not
enabled; an upgrade restarts it if it runs.


%prep
%autosetup -p1 -n %{name}
# the VC-500W protocol: a direct git URL in pyproject.toml (pip only),
# here the python3-labelprinter package
sed -i 's|"labelprinter @ git+[^"]*"|"labelprinter"|' pyproject.toml
grep -q '"labelprinter"' pyproject.toml


%generate_buildrequires
# the extra "oidc" (PyJWT with cryptography) too: its tests run
%pyproject_buildrequires -x oidc


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l tavletta
%find_lang %{name}
install -Dpm 0644 data/tavletta.1 %{buildroot}%{_mandir}/man1/tavletta.1
# the print server as a system service, with the package's program
install -Dpm 0644 packaging/systemd/tavletta-server.service %{buildroot}%{_unitdir}/tavletta-server.service
sed -i 's|^ExecStart=/opt/tavletta/bin/tavletta |ExecStart=%{_bindir}/tavletta |' \
    %{buildroot}%{_unitdir}/tavletta-server.service
grep -q '^ExecStart=%{_bindir}/tavletta serve ' %{buildroot}%{_unitdir}/tavletta-server.service
install -Dpm 0644 packaging/systemd/tavletta.sysusers.conf %{buildroot}%{_sysusersdir}/tavletta.conf
install -dm 0750 %{buildroot}%{_sysconfdir}/tavletta %{buildroot}%{_sharedstatedir}/tavletta/labels
install -Dpm 0644 packaging/udev/70-tavletta.rules %{buildroot}%{_udevrulesdir}/70-tavletta.rules


%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
# the GTK window needs a display: the modules of the window are not imported
%pyproject_check_import -e 'tavletta.app' -e 'tavletta.ui.*'
%{py3_test_envvars} %{python3} -m tavletta.launch --version
# without a display, the window's tests skip (marker gui). test_packaging.py
# checks the repository's packaging files (Flatpak, container, CI), not the
# program.
%pytest -m 'not gui' -p no:cacheprovider --ignore=tests/test_packaging.py


%post server
# not enabled: the preset disables it unless the system's presets say otherwise
%systemd_post tavletta-server.service

%preun server
%systemd_preun tavletta-server.service

%postun server
%systemd_postun_with_restart tavletta-server.service


%files -f %{pyproject_files} -f %{name}.lang
# LICENSE, THIRD_PARTY_NOTICES.md, CREDITS.md and the data licences are
# the package's license files (pyproject.toml), %%license through -l above
%doc README.md
# the example labels, as in the Debian package
%doc docs/examples
%{_bindir}/tavletta
%{_mandir}/man1/tavletta.1*
%{_datadir}/applications/%{app_id}.desktop
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_datadir}/icons/hicolor/symbolic/apps/%{app_id}-symbolic.svg
%{_datadir}/icons/hicolor/*x*/apps/%{app_id}.svg
%{_datadir}/mime/packages/%{app_id}.xml
%{_udevrulesdir}/70-tavletta.rules

%files server
%{_unitdir}/tavletta-server.service
%{_sysusersdir}/tavletta.conf
%dir %attr(0750,tavletta,tavletta) %{_sysconfdir}/tavletta
%dir %attr(0750,tavletta,tavletta) %{_sharedstatedir}/tavletta
%dir %attr(0750,tavletta,tavletta) %{_sharedstatedir}/tavletta/labels


%changelog
* Mon Nov 23 2026 Joshua Hirsig <tavletta@joshuahirsig.xyz> - 1.1.2-1
- Initial package
