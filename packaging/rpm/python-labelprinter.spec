# Tavletta's fork of labelprinter-vc500w (the native protocol of the Brother
# VC-500W), until sgrimee/labelprinter-vc500w#1-#3 are merged. There are no
# releases yet: a snapshot of the commit Tavletta pins in its pyproject.toml.
%global forgeurl https://github.com/joshii-h/labelprinter-vc500w
%global commit a6f2957de323f71f364b3acd6624da8b9885587a
%global shortcommit %(c=%{commit}; echo ${c:0:7})
%global commitdate 20261105

Name:           python-labelprinter
Version:        0.1.0^git%{commitdate}.%{shortcommit}
Release:        1%{?dist}
Summary:        Control the Brother VC-500W label printer

# README: "AGPLv3"; no "or later" stated anywhere
License:        AGPL-3.0-only
URL:            %{forgeurl}
Source:         %{forgeurl}/archive/%{commit}/labelprinter-vc500w-%{shortcommit}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-pytest

%global _description %{expand:
A Python library and command line tools that talk to the Brother VC-500W
colour label printer over its native network protocol: printer status,
configuration and printing of images.}

%description %_description

%package -n     python3-labelprinter
Summary:        %{summary}
# the print queue and the CUPS backend (label-queue*, vc500w-cups-backend)
Suggests:       python3-cups

%description -n python3-labelprinter %_description


%prep
%autosetup -p1 -n labelprinter-vc500w-%{commit}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l labelprinter


%check
# the CUPS backend and print queue need pycups (the optional "cups" extra)
%pyproject_check_import -e labelprinter.cups_backend -e 'labelprinter.queue_*' -e 'labelprinter.test*'
%pytest labelprinter/test --ignore=labelprinter/test/test_cups_backend.py


%files -n python3-labelprinter -f %{pyproject_files}
%doc README.md
%{_bindir}/label-raw
%{_bindir}/label-text
%{_bindir}/label-queue
%{_bindir}/label-queue-setup
%{_bindir}/label-queue-worker
%{_bindir}/vc500w-cups-backend


%changelog
* Tue Nov 17 2026 Joshua Hirsig <joshuahirsig@gmail.com> - 0.1.0^git20261105.a6f2957-1
- Initial package
