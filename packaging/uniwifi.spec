Name:           uniwifi-autologin
Version:        0.1.0
Release:        1%{?dist}
Summary:        Zero-touch university captive portal auto-login background daemon for Fedora

License:        MIT
URL:            https://github.com/Vishwa-Srinath/UniWiFi-AutoLogin-for-Fedora
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-setuptools >= 68.0
BuildRequires:  python3-wheel

Requires:       python3-dbus-fast >= 2.21
Requires:       python3-httpx >= 0.27
Requires:       python3-beautifulsoup4 >= 4.12
Requires:       python3-lxml >= 5.0
Requires:       python3-keyring >= 25.0
Requires:       python3-typer >= 0.12
Requires:       libnotify
Requires:       NetworkManager
Requires:       gnome-keyring
Requires:       systemd

Suggests:       python3-playwright >= 1.45

Provides:       uniwifi = %{version}-%{release}

%description
UniWiFi AutoLogin is an autonomous background service engineered for Fedora Linux
that detects university Wi-Fi captive portals upon connection, network flap, or
systemd-logind sleep/resume, and automatically authenticates using credentials
stored securely in the OS keyring.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files uniwifi

# Install systemd user service unit
install -D -p -m 0644 packaging/uniwifi.service %{buildroot}%{_userunitdir}/uniwifi.service

# Install example config to datadir
install -D -p -m 0644 config.example.toml %{buildroot}%{_datadir}/uniwifi/config.example.toml

# Install manual pages if present
if [ -f packaging/man/uniwifi.1 ]; then
    install -D -p -m 0644 packaging/man/uniwifi.1 %{buildroot}%{_mandir}/man1/uniwifi.1
fi
if [ -f packaging/man/uniwifi.toml.5 ]; then
    install -D -p -m 0644 packaging/man/uniwifi.toml.5 %{buildroot}%{_mandir}/man5/uniwifi.toml.5
fi

%post
%systemd_user_post uniwifi.service

%preun
%systemd_user_preun uniwifi.service

%postun
%systemd_user_postun_with_restart uniwifi.service

%files -f %{pyproject_files}
%license LICENSE
%doc README.md config.example.toml
%{_bindir}/uniwifi
%{_userunitdir}/uniwifi.service
%{_datadir}/uniwifi/
%ghost %{_mandir}/man1/uniwifi.1*
%ghost %{_mandir}/man5/uniwifi.toml.5*

%changelog
* Sat Oct 31 2026 Vishwa Srinath <vishwa@fedoraproject.org> - 0.1.0-1
- Initial packaging release of UniWiFi AutoLogin for Fedora 40/41
- Systemd user service unit and Secret Service keyring integration
- Defense-in-depth HTTPS enforcement and rogue AP host pinning
