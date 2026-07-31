#!/usr/bin/env bash
set -euo pipefail

# Build NSE/BSE Downloader as a single-file executable for macOS Apple Silicon
# Minimum dependencies: PySide6, aiohttp, pandas, pyyaml
# Optional: psutil (has fallback)

APP_NAME="NSE_BSE_Downloader"
ENTRY="main.py"

echo "=== NSE/BSE Downloader - Nuitka Build ==="
echo "Target: macOS Apple Silicon single executable"
echo "Entry point: ${ENTRY}"
echo "Output: dist/${APP_NAME}"

# Verify minimum dependencies
echo ""
echo "=== Checking Dependencies ==="
python -c "
import sys
sys.path.insert(0, 'src')

deps = [
    ('PySide6.QtWidgets', 'PySide6'),
    ('aiohttp', 'aiohttp'),
    ('pandas', 'pandas'),
    ('yaml', 'pyyaml')
]

missing = []
for module, name in deps:
    try:
        __import__(module)
        print(f'✓ {name}')
    except ImportError:
        print(f'✗ {name} - REQUIRED')
        missing.append(name)

# Optional
try:
    import psutil
    print('✓ psutil (optional)')
except ImportError:
    print('? psutil (optional - has fallback)')

if missing:
    print(f'\\nERROR: Missing required dependencies: {missing}')
    print('Install with: pip install ' + ' '.join(missing))
    sys.exit(1)
else:
    print('\\n✓ All required dependencies available')
"

if [ $? -ne 0 ]; then
    echo "Dependency check failed. Exiting."
    exit 1
fi

# Clean previous builds
echo ""
echo "=== Cleaning Previous Builds ==="
rm -rf build dist ${APP_NAME}.dist ${APP_NAME}.onefile-build main.build main.dist main.onefile-build || true
echo "✓ Cleaned build directories"

# Build with Nuitka
echo ""
echo "=== Starting Nuitka Build ==="
python -m nuitka \
  --onefile \
  --standalone \
  --enable-plugin=pyside6 \
  --assume-yes-for-downloads \
  --follow-imports \
  --include-data-file=config.yaml=./config.yaml \
  --include-data-file="Market Holidays"="Market Holidays" \
  --include-data-file=version.py=./version.py \
  --include-data-dir=src/gui/resources=src/gui/resources \
  --output-dir=dist \
  --output-filename=${APP_NAME} \
  ${ENTRY}

if [ $? -eq 0 ]; then
    echo ""
    echo "=== Build Successful ==="
    echo "✓ Executable: dist/${APP_NAME}"

    if [ -f "dist/${APP_NAME}" ]; then
        SIZE=$(du -h "dist/${APP_NAME}" | cut -f1)
        echo "✓ File size: ${SIZE}"
        echo ""
        echo "=== Usage ==="
        echo "Run: ./dist/${APP_NAME}"
        echo "If Gatekeeper blocks: xattr -dr com.apple.quarantine ./dist/${APP_NAME}"
    else
        echo "✗ Build completed but executable not found"
        exit 1
    fi
else
    echo "✗ Build failed"
    exit 1
fi
