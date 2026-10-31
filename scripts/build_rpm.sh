#!/usr/bin/env bash
# ==============================================================================
# build_rpm.sh - Build native Fedora RPM and SRPM packages for UniWiFi AutoLogin
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

SPEC_FILE="packaging/uniwifi.spec"
BUILD_DIR="dist/rpmbuild"

print_usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -s, --srpm       Build Source RPM (SRPM) only
  -b, --binary     Build both binary RPM and SRPM (default)
  -l, --lint       Run rpmlint on the spec file if available
  -h, --help       Show this help message and exit

Examples:
  ./scripts/build_rpm.sh --srpm
  ./scripts/build_rpm.sh --binary --lint
EOF
}

BUILD_MODE="binary"
RUN_LINT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--srpm)
            BUILD_MODE="srpm"
            shift
            ;;
        -b|--binary)
            BUILD_MODE="binary"
            shift
            ;;
        -l|--lint)
            RUN_LINT=1
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            print_usage >&2
            exit 1
            ;;
    esac
done

if [ ! -f "$SPEC_FILE" ]; then
    echo "Error: Spec file $SPEC_FILE not found!" >&2
    exit 1
fi

# Extract version from spec file
VERSION=$(grep -E '^Version:' "$SPEC_FILE" | awk '{print $2}')
NAME=$(grep -E '^Name:' "$SPEC_FILE" | awk '{print $2}')
TARBALL_NAME="${NAME}-${VERSION}.tar.gz"

echo "==> Preparing build environment for ${NAME} version ${VERSION}..."

if [ "$RUN_LINT" -eq 1 ]; then
    if command -v rpmlint >/dev/null 2>&1; then
        echo "==> Running rpmlint on $SPEC_FILE..."
        rpmlint "$SPEC_FILE" || true
    else
        echo "==> Note: rpmlint not installed, skipping lint."
    fi
fi

# Setup clean rpmbuild directory structure
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# Create source tarball
echo "==> Generating source tarball: $TARBALL_NAME..."
mkdir -p dist
tar --exclude-vcs \
    --exclude='.venv*' \
    --exclude='dist' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --transform "s,^\.,${NAME}-${VERSION}," \
    -czf "$BUILD_DIR/SOURCES/$TARBALL_NAME" .

cp "$SPEC_FILE" "$BUILD_DIR/SPECS/"

if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "==> Note: rpmbuild is not installed on this system."
    echo "    To build RPMs natively on Fedora, install: sudo dnf install -y rpm-build"
    echo "    Source archive prepared at: $BUILD_DIR/SOURCES/$TARBALL_NAME"
    exit 0
fi

if [ "$BUILD_MODE" = "srpm" ]; then
    echo "==> Building Source RPM (SRPM)..."
    rpmbuild --define "_topdir $PROJECT_ROOT/$BUILD_DIR" -bs "$BUILD_DIR/SPECS/uniwifi.spec"
    echo "==> SRPM built successfully:"
    find "$BUILD_DIR/SRPMS" -name "*.src.rpm"
else
    echo "==> Building Binary and Source RPMs..."
    rpmbuild --define "_topdir $PROJECT_ROOT/$BUILD_DIR" -ba "$BUILD_DIR/SPECS/uniwifi.spec"
    echo "==> RPM packages built successfully:"
    find "$BUILD_DIR/RPMS" -name "*.rpm"
    find "$BUILD_DIR/SRPMS" -name "*.src.rpm"
fi
