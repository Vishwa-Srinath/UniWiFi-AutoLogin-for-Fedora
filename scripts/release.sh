#!/usr/bin/env bash
# ==============================================================================
# release.sh - Release validation, artifact generation, and tagging helper
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

print_usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -c, --check       Perform dry-run release readiness checks only
  -b, --build       Build all release artifacts (Wheel, sdist, SRPM)
  -t, --tag VERSION Create annotated git release tag (e.g. v0.1.0)
  -h, --help        Show this help message and exit

Examples:
  ./scripts/release.sh --check
  ./scripts/release.sh --build
  ./scripts/release.sh --tag v0.1.0
EOF
}

MODE="check"
TAG_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--check)
            MODE="check"
            shift
            ;;
        -b|--build)
            MODE="build"
            shift
            ;;
        -t|--tag)
            MODE="tag"
            TAG_VERSION="${2:-}"
            if [ -z "$TAG_VERSION" ]; then
                echo "Error: --tag requires a version string (e.g. v0.1.0)" >&2
                exit 1
            fi
            shift 2
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

echo "==> [1/4] Checking version consistency across files..."
PYPROJECT_VER=$(grep -E '^version = ' pyproject.toml | head -n 1 | awk -F'"' '{print $2}')
INIT_VER=$(grep -E '^__version__ = ' src/uniwifi/__init__.py | head -n 1 | awk -F'"' '{print $2}')
SPEC_VER=$(grep -E '^Version:\s+' packaging/uniwifi.spec | head -n 1 | awk '{print $2}')

echo "    pyproject.toml:        $PYPROJECT_VER"
echo "    src/uniwifi/__init__.py: $INIT_VER"
echo "    packaging/uniwifi.spec: $SPEC_VER"

if [ "$PYPROJECT_VER" != "$INIT_VER" ] || [ "$PYPROJECT_VER" != "$SPEC_VER" ]; then
    echo "Error: Version mismatch detected across project files!" >&2
    exit 1
fi
echo "    Version verification passed: $PYPROJECT_VER"

echo "==> [2/4] Running full test suite and quality checks..."
./scripts/check.sh

echo "==> [3/4] Verifying documentation and packaging artifacts..."
test -f README.md || (echo "Error: README.md missing" >&2 && exit 1)
test -f LICENSE || (echo "Error: LICENSE missing" >&2 && exit 1)
test -f packaging/man/uniwifi.1 || (echo "Error: uniwifi.1 missing" >&2 && exit 1)
test -f packaging/man/uniwifi.toml.5 || (echo "Error: uniwifi.toml.5 missing" >&2 && exit 1)
echo "    All required documentation and packaging files present."

if [ "$MODE" = "check" ]; then
    echo "==> Pre-flight release checks passed successfully!"
    exit 0
fi

if [ "$MODE" = "build" ]; then
    echo "==> [4/4] Building distribution artifacts..."
    mkdir -p dist
    rm -rf dist/*

    # Build Wheel and sdist using python build if available
    VENV_PYTHON=".venv/bin/python"
    PYTHON="${VENV_PYTHON:-python3}"
    if "$PYTHON" -m build --help >/dev/null 2>&1; then
        echo "    Building Python Wheel and sdist..."
        "$PYTHON" -m build --outdir dist/
    else
        echo "    Note: python -m build not installed, using pip/setuptools fallback..."
        "$PYTHON" setup.py sdist bdist_wheel || true
    fi

    # Build SRPM
    echo "    Building Fedora Source RPM..."
    ./scripts/build_rpm.sh --srpm

    echo "==> Artifact generation complete:"
    ls -lh dist/ || true
    if command -v sha256sum >/dev/null 2>&1; then
        echo "==> SHA-256 Checksums:"
        sha256sum dist/* 2>/dev/null || true
    fi
fi

if [ "$MODE" = "tag" ]; then
    echo "==> Creating release tag: $TAG_VERSION..."
    git tag -a "$TAG_VERSION" -m "Release $TAG_VERSION of UniWiFi AutoLogin for Fedora"
    echo "==> Tag $TAG_VERSION created. Push using: git push origin $TAG_VERSION"
fi
