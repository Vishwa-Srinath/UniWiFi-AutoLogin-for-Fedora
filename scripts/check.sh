#!/usr/bin/env bash
# Run linting, formatting check, and test suite for uniwifi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

VENV_BIN=".venv/bin"

if [ -f "$VENV_BIN/python" ]; then
    PYTHON="$VENV_BIN/python"
    RUFF="$VENV_BIN/ruff"
    PYTEST="$VENV_BIN/pytest"
else
    PYTHON="python3"
    RUFF="ruff"
    PYTEST="pytest"
fi

echo "==> Running ruff format check..."
"$RUFF" format --check .

echo "==> Running ruff linter..."
"$RUFF" check .

echo "==> Running pytest test suite..."
"$PYTEST"

echo "==> All quality checks passed successfully!"
