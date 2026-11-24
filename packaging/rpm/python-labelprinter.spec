# Tavletta's fork of labelprinter-vc500w (the native protocol of the Brother
# VC-500W), until sgrimee/labelprinter-vc500w#1-#3 are merged. There are no
# releases yet: a snapshot of the commit Tavletta pins in its pyproject.toml.
%global forgeurl https://github.com/joshii-h/labelprinter-vc500w
%global commit a6f2957de323f71f364b3acd6624da8b9885587a
%global shortcommit %(c=%{commit}; echo ${c:0:7})
%global commitdate 20261105

Name:           python-labelprinter
# a snapshot after 0.1.0, as Fedora's guidelines write it
Version:        0.1.0^%{commitdate}git%{shortcommit}
Release:        1%{?dist}
Summary:        Control the Brother VC-500W label printer

# the source files' headers: "either version 3 of the License, or (at your
# option) any later version" (the README just says "AGPLv3")
License:        AGPL-3.0-or-later
URL:            %{forgeurl}
Source:         %{forgeurl}/archive/%{commit}/labelprinter-vc500w-%{shortcommit}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest

%global _description %{expand:
A Python library and command line tools that talk to the Brother VC-500W
color label printer over its native network protocol: printer status,
configuration and printing of images.}

%description %_description

%package -n     python3-labelprinter
Summary:        %{summary}

%description -n python3-labelprinter %_description


%prep
%autosetup -p1 -n labelprinter-vc500w-%{commit}
# modules, not scripts: the commands come from the entry points
sed -i '1{/^#!/d}' labelprinter/*.py labelprinter/test/*.py


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
# the library only, as in Debian: the command line tools (label-text, the
# print queue, the CUPS backend) would take generic command names, and
# Tavletta does not use them; nor the tests (%%exclude below: the wheel's
# file list has them)
rm %{buildroot}%{_bindir}/{label-raw,label-text,label-queue,label-queue-setup,label-queue-worker,vc500w-cups-backend}
%pyproject_save_files -l labelprinter


%check
# the CUPS backend and print queue need pycups (the optional "cups" extra)
%pyproject_check_import -e labelprinter.cups_backend -e 'labelprinter.queue_*' -e 'labelprinter.test*'
%pytest labelprinter/test --ignore=labelprinter/test/test_cups_backend.py


%files -n python3-labelprinter -f %{pyproject_files}
%doc README.md
%exclude %{python3_sitelib}/labelprinter/test/


%changelog
* Tue Nov 17 2026 Joshua Hirsig <tavletta@joshuahirsig.xyz> - 0.1.0^20261105gita6f2957-1
- Initial package
