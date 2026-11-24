Name:           python-zint-bindings
Version:        1.2.2
Release:        1%{?dist}
Summary:        Python bindings for the Zint barcode library

License:        Apache-2.0
URL:            https://github.com/bindreams/zint-bindings
Source:         %{pypi_source zint_bindings}
# Build against the system's libzint instead of the copy in external/zint,
# without vcpkg and without network access at configure time (no type
# stubs), without -Werror; build-system pins relaxed to Fedora's versions.
# Not upstream yet.
Patch:          python-zint-bindings-system-zint.patch

BuildRequires:  gcc-c++
BuildRequires:  cmake
BuildRequires:  ninja-build
BuildRequires:  python3-devel
BuildRequires:  pybind11-devel
BuildRequires:  fmt-devel
BuildRequires:  zint-devel

%global _description %{expand:
Python bindings for the Zint library, which encodes over 50 kinds of
barcodes: QR Code, Data Matrix, PDF417, Aztec, Code 128, EAN, UPC, GS1
DataBar and many more.}

%description %_description

%package -n     python3-zint-bindings
Summary:        %{summary}

%description -n python3-zint-bindings %_description


%prep
%autosetup -p1 -n zint_bindings-%{version}
# the bundled copy of Zint: the extension links the system's libzint
rm -r external/zint


%generate_buildrequires
%pyproject_buildrequires


%build
# scikit-build-core strips the module by default: keep the debug information
# (debuginfo package); Fedora's build type, Fedora's flags
%pyproject_wheel -C install.strip=false -C cmake.build-type=RelWithDebInfo


%install
%pyproject_install
# the wheel's licenses/LICENSE.md, marked %%license (-l): the only copy
%pyproject_save_files -l zint


%check
# the sdist has no tests
%pyproject_check_import
%{py3_test_envvars} %{python3} -c 'import zint; s = zint.Symbol(); s.symbology = zint.Symbology.DATAMATRIX; s.encode("Fedora"); assert s.rows > 0'


%files -n python3-zint-bindings -f %{pyproject_files}
%doc README.md


%changelog
* Tue Nov 17 2026 Joshua Hirsig <tavletta@joshuahirsig.xyz> - 1.2.2-1
- Initial package
