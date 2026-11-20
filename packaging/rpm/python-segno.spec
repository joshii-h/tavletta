Name:           python-segno
Version:        1.6.6
Release:        1%{?dist}
Summary:        QR Code and Micro QR Code generator

License:        BSD-3-Clause
URL:            https://github.com/heuer/segno
Source:         %{pypi_source segno}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest
# the tests read the PNG output back
BuildRequires:  python3-pypng

%global _description %{expand:
Segno generates QR Codes and Micro QR Codes according to ISO/IEC 18004:2015
with no dependencies, and writes them as SVG, PNG, EPS, PDF, text and more.}

%description %_description

%package -n     python3-segno
Summary:        %{summary}

%description -n python3-segno %_description


%prep
%autosetup -p1 -n segno-%{version}
# a module, not a script: the command comes from the entry point
sed -i '1{/^#!/d}' segno/cli.py


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l segno


%check
%pyproject_check_import
# needs the qrcode-artist plugin (to_pil), not in Fedora
%pytest --deselect tests/test_plugin.py::test_plugin


%files -n python3-segno -f %{pyproject_files}
%doc README.rst CHANGES.rst
%{_bindir}/segno
%{_mandir}/man1/segno.1*


%changelog
* Tue Nov 17 2026 Joshua Hirsig <joshuahirsig@gmail.com> - 1.6.6-1
- Initial package
