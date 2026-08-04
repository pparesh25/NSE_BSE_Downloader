#!/usr/bin/env bash
set -euo pipefail

# Default invocation is a no-build validation.  To compile later, run:
#   ./build_nuitka_macos.sh --build --clean

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/build_nuitka_cross_platform.py" \
  --target-platform=darwin "$@"
