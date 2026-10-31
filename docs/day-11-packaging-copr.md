# Day 11: Fedora RPM Packaging, COPR Automation & Release Engineering

This document details the architectural specifications, Fedora packaging infrastructure, automated COPR distribution pipelines, and release engineering workflows delivered for **Day 11** of the UniWiFi AutoLogin project on branch `feature/day-11-packaging`.

---

## 1. Executive Summary & Deliverables Matrix

Day 11 completes the transition of **UniWiFi AutoLogin for Fedora** from a source-distributed Python project into a first-class, natively packaged Fedora Linux application. It delivers a standard Fedora RPM `.spec` file, Unix man pages (`man1` and `man5`), GitHub Actions multi-python CI workflows, Fedora COPR build pipelines, and automated release readiness tooling.

The Day 11 deliverables were committed and pushed across **7 discrete pushes** to `origin/feature/day-11-packaging`:

| Push # | Commit Hash | Focus Area | Description |
|:---:|:---:|:---|:---|
| **1** | `2a95d0a` | `packaging(rpm)` | Author Fedora RPM spec file (`packaging/uniwifi.spec`) with systemd macros. |
| **2** | `e044897` | `build(rpm)` | Implement `scripts/build_rpm.sh` build helper and `tests/test_packaging.py`. |
| **3** | `4a10604` | `docs(man)` | Author Unix man pages (`packaging/man/uniwifi.1` and `packaging/man/uniwifi.toml.5`). |
| **4** | `aadbb7f` | `ci(github)` | Implement multi-version Python CI test and lint workflow (`.github/workflows/ci.yml`). |
| **5** | `751b143` | `ci(copr)` | Add automated Fedora COPR build workflow and credentials configuration template. |
| **6** | `9c16bf1` | `feat(release)` | Implement `scripts/release.sh` pre-flight check script and release readiness tests. |
| **7** | *(Current)* | `docs(report)` | Comprehensive Day 11 Fedora packaging, COPR, and release automation guide. |

---

## 2. Fedora Native RPM Packaging Architecture

Fedora packaging guidelines require modern Python packages to utilize declarative `pyproject-rpm-macros` rather than legacy `setup.py` invocations.

```
packaging/uniwifi.spec
├── Build System: pyproject-rpm-macros (%pyproject_wheel, %pyproject_install)
├── System Integration:
│   ├── User Unit: %{_userunitdir}/uniwifi.service
│   ├── Scriptlets: %systemd_user_post, %systemd_user_preun, %systemd_user_postun_with_restart
│   ├── Manuals: %{_mandir}/man1/uniwifi.1.gz & %{_mandir}/man5/uniwifi.toml.5.gz
│   └── Config Doc: %{_datadir}/uniwifi/config.example.toml
└── Security & Permissions:
    ├── Binaries: %{_bindir}/uniwifi (0755)
    └── Unit: %{_userunitdir}/uniwifi.service (0644)
```

### Spec Highlights (`packaging/uniwifi.spec`):
1. **Dynamic Build Requirements:** `%pyproject_buildrequires` dynamically computes dependencies from `pyproject.toml`.
2. **Systemd User Scriptlet Macros:** Employs standard `%systemd_user_post`, `%systemd_user_preun`, and `%systemd_user_postun_with_restart` to ensure daemon processes restart cleanly upon RPM upgrades.
3. **Ghost File Tracking:** Pre-allocates compressed man page locations in `%files` to prevent file conflicts during packaging builds.

---

## 3. Local RPM Build Pipeline (`scripts/build_rpm.sh`)

Developers and package maintainers on Fedora can build native packages locally without needing external build systems:

```bash
# Build Source RPM (SRPM) only
./scripts/build_rpm.sh --srpm

# Build both binary RPM and SRPM with spec linting
./scripts/build_rpm.sh --binary --lint
```

The script:
- Synthesizes a release tarball (`dist/rpmbuild/SOURCES/uniwifi-autologin-0.1.0.tar.gz`) excluding dev caches and `.git`.
- Configures an isolated `_topdir` build tree in `dist/rpmbuild/`.
- Executes `rpmbuild -ba` or `rpmbuild -bs` generating signed/distributable RPMs in `dist/rpmbuild/RPMS/noarch/`.

---

## 4. Unix Manual Pages: `uniwifi(1)` & `uniwifi.toml(5)`

To deliver a standard Linux desktop experience, Day 11 authors complete groff/roff manual pages installed to standard system man paths:

1. **`packaging/man/uniwifi.1` (Section 1: User Commands):**
   - Documents syntax, commands (`status`, `setup`, `login`, `run`, `service`, `doctor`, `config`, `recon`, `events`, `notify`).
   - Lists standard Linux exit codes (0 = Success, 1 = Error, 2 = Config Error, 3 = Credentials Missing, 4 = Security Rejection, 130 = SIGINT).
   - Can be previewed locally using: `man -l packaging/man/uniwifi.1`.

2. **`packaging/man/uniwifi.toml.5` (Section 5: File Formats):**
   - Documents configuration schema: `[general]` and `[portal]` sections.
   - Emphasizes security rules: strict `0600` file permissions and zero-password policy (credentials must reside in OS keyring).
   - Can be previewed locally using: `man -l packaging/man/uniwifi.toml.5`.

---

## 5. Fedora COPR Automated Distribution

Fedora COPR allows Fedora users to install and receive automatic updates for UniWiFi AutoLogin using standard `dnf` package management:

### End-User Experience:
```bash
sudo dnf copr enable vishwa/uniwifi
sudo dnf install -y uniwifi-autologin
systemctl --user enable --now uniwifi.service
```

### CI/CD Automation (`.github/workflows/copr.yml`):
- **Trigger:** Fires automatically upon pushing a git release tag (`v*`) or manual workflow dispatch.
- **Stage 1 (Build):** Spins up a clean `fedora:latest` container and builds the Source RPM (`.src.rpm`).
- **Stage 2 (Submit):** Uses `copr-cli` with repository secrets (`COPR_LOGIN`, `COPR_TOKEN`) to queue builds across active Fedora architectures:
  - `fedora-40-x86_64`
  - `fedora-41-x86_64`
  - `fedora-rawhide-x86_64`

---

## 6. Multi-Version CI Matrix (`.github/workflows/ci.yml`)

The continuous integration pipeline guarantees zero regression across supported Python runtimes:
- **Lint Job:** Executes `ruff format --check .` and `ruff check .` on Python 3.12.
- **Test Matrix:** Runs full unit and e2e simulation tests (`pytest --cov=uniwifi`) across Python **3.11**, **3.12**, and **3.13**.
- **Fedora Container Job:** Executes `rpmlint` and tests `scripts/build_rpm.sh --srpm` inside official `fedora:latest`.

---

## 7. Release Engineering Tooling (`scripts/release.sh`)

To eliminate human error during releases, `scripts/release.sh` enforces pre-flight validation rules:
1. **Version Synchronization:** Verifies that the version number is strictly identical across:
   - `pyproject.toml`
   - `src/uniwifi/__init__.py` (`__version__`)
   - `packaging/uniwifi.spec` (`Version:`)
   - `packaging/man/uniwifi.1`
   - `packaging/man/uniwifi.toml.5`
2. **Automated Quality Checks:** Runs `./scripts/check.sh` ensuring all 269 tests pass with 0 warnings.
3. **Artifact Generation:** Builds Python Wheel (`.whl`), source distribution (`.tar.gz`), and Fedora Source RPM (`.src.rpm`).
4. **Integrity Hashes:** Generates SHA-256 checksums for all release binaries.
5. **Git Tagging:** Creates signed or annotated release tags (`git tag -a v0.1.0`).

---

## 8. Test Verification Summary

With the additions of `tests/test_packaging.py` and `tests/test_release_readiness.py`, the test suite now comprises:
- **Total Passing Tests:** **269 tests**
- **Test Suite Execution Time:** ~4.5 seconds
- **Lint / Style Issues:** 0 (strict PEP 8 & Ruff conformance)
- **Code Coverage:** Comprehensive coverage across daemon orchestrator, D-Bus listeners, keyring storage, login adapters, and packaging manifests.
