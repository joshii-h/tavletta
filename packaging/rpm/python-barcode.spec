# The PyPI name is python-barcode: the source package keeps it (not
# python-python-barcode), the binary package is python3-barcode as in Debian.
Name:           python-barcode
Version:        0.16.1
Release:        1%{?dist}
Summary:        Create standard barcodes with Python

License:        MIT
URL:            https://github.com/WhyNotHugo/python-barcode
Source:         %{pypi_source python_barcode}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest
BuildRequires:  python3-pillow
BuildRequires:  dejavu-sans-mono-fonts

%global _description %{expand:
python-barcode creates standard barcodes (EAN-8, EAN-13, EAN-14, UPC-A,
JAN, ISBN, ISSN, Code 39, Code 128, GS1-128, PZN, ITF, Codabar) and writes
them as SVG or, with Pillow, as images.}

%description %_description

%package -n     python3-barcode
Summary:        %{summary}
# the writers' font comes from the system (unbundled in %%install)
Requires:       dejavu-sans-mono-fonts

%description -n python3-barcode %_description


%prep
%autosetup -p1 -n python_barcode-%{version}
# coverage options for the tests
sed -i '/--cov/d;/--no-cov-on-fail/d' setup.cfg


%generate_buildrequires
%pyproject_buildrequires -x images


%build
%pyproject_wheel


%install
%pyproject_install
# the wheel also has its docs/ and tests/ as top-level packages
rm -rf %{buildroot}%{python3_sitelib}/{docs,tests}
%pyproject_save_files -l barcode
# the bundled DejaVu Sans Mono: the system's font instead, by a relative link
ln -srf %{buildroot}%{_datadir}/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf \
    %{buildroot}%{python3_sitelib}/barcode/fonts/DejaVuSansMono.ttf


%check
%pyproject_check_import
%pytest


%files -n python3-barcode -f %{pyproject_files}
%doc README.rst
%{_bindir}/python-barcode


%changelog
* Tue Nov 17 2026 Joshua Hirsig <tavletta@joshuahirsig.xyz> - 0.16.1-1
- Initial package
