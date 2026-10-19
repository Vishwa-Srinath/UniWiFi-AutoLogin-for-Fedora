#!/usr/bin/env bash
# Initialize virtualenv and install development dependencies for uniwifi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "==> Setting up Python virtual environment in .venv..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

echo "==> Upgrading pip..."
.venv/bin/python -m pip install --upgrade pip

echo "==> Installing uniwifi in editable mode with development dependencies..."
.venv/bin/python -m pip install -e ".[dev]"

echo "==> Verifying installation..."
.venv/bin/uniwifi --help

echo "==> Development environment ready! Activate with: source .venv/bin/activate"
