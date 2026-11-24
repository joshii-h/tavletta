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

# PyGObject with GTK 4, libadwaita, Pango and PangoCairo (typelibs), pycairo
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       pango
Requires:       hicolor-icon-theme
Requires:       shared-mime-info
# shipping labels from PDF files (images work without it)
Recommends:     poppler-glib
# every symbology but QR Code, Code 128 and EAN-13 (without it, such codes
# show "code unavailable"); compiled, so not on every architecture
Recommends:     python3dist(zint-bindings)
# resolve printers' *.local names
Recommends:     nss-mdns

%description
Tavletta designs labels with text, images, symbols and codes, shows them
exactly as they will be printed and prints them over the network or USB: on
label printers with endless tape or die-cut labels (Brother VC-500W, QL and
P-touch, cab, NIIMBOT, ZPL, EPL, TSPL, ESC/POS and DYMO printers), and on
office printers through CUPS or IPP, one label per page or on label sheets.
It also prints label files from the command line and runs a print server
with a REST API and MCP.


%prep
%autosetup -p1 -n %{name}
# the VC-500W protocol: a direct git URL in pyproject.toml (pip only),
# here the python3-labelprinter package
sed -i 's|"labelprinter @ git+[^"]*"|"labelprinter"|' pyproject.toml
# optional (Recommends above): the other symbologies
sed -i '/"zint-bindings[^"]*",/d' pyproject.toml
grep -q '"labelprinter"' pyproject.toml


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l tavletta
%find_lang %{name}


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


%files -f %{pyproject_files} -f %{name}.lang
%doc README.md CREDITS.md
%license THIRD_PARTY_NOTICES.md
%{_bindir}/tavletta
%{_datadir}/applications/%{app_id}.desktop
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_datadir}/icons/hicolor/symbolic/apps/%{app_id}-symbolic.svg
%{_datadir}/icons/hicolor/*x*/apps/%{app_id}.svg
%{_datadir}/mime/packages/%{app_id}.xml


%changelog
* Mon Nov 23 2026 Joshua Hirsig <joshuahirsig@gmail.com> - 1.1.2-1
- Initial package
