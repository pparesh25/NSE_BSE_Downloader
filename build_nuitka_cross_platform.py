#!/usr/bin/env python3
"""
Cross-platform Nuitka build script for NSE/BSE Downloader
Supports macOS, Windows, and Linux with PySide6
"""

import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path

# Build configuration
APP_NAME = "NSE_BSE_Downloader"
ENTRY_POINT = "main.py"

def check_dependencies():
    """Check if all required dependencies are available"""
    print("=== Checking Dependencies ===")

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

    # Optional dependencies
    try:
        import psutil
        print('✓ psutil (optional)')
    except ImportError:
        print('? psutil (optional - has fallback)')

    if missing:
        print(f'\nERROR: Missing required dependencies: {missing}')
        print('Install with: pip install ' + ' '.join(missing))
        return False

    print('\n✓ All required dependencies available')
    return True

def check_nuitka():
    """Check if Nuitka is available"""
    try:
        result = subprocess.run([sys.executable, '-m', 'nuitka', '--version'],
                              capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ Nuitka available: {result.stdout.strip()}")
            return True
    except Exception:
        pass

    print("Installing Nuitka...")
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'nuitka', 'ordered-set', 'zstandard'],
                      check=True)
        print("✓ Nuitka installed successfully")
        return True
    except subprocess.CalledProcessError:
        print("✗ Failed to install Nuitka")
        return False

def clean_build():
    """Clean previous build artifacts"""
    print("\n=== Cleaning Previous Builds ===")

    patterns = [
        'build', 'dist', '*.dist', '*.onefile-build',
        '*.build', f'{APP_NAME}.dist', f'{APP_NAME}.onefile-build'
    ]

    for pattern in patterns:
        for path in Path('.').glob(pattern):
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                print(f"Removed: {path}")

    print("✓ Build directories cleaned")

def get_platform_specific_args():
    """Get platform-specific Nuitka arguments"""
    system = platform.system().lower()

    base_args = [
        sys.executable, '-m', 'nuitka',
        '--onefile',
        '--standalone',
        '--enable-plugin=pyside6',
        '--assume-yes-for-downloads',
        '--follow-imports',
        '--include-data-file=config.yaml=./config.yaml',
        '--include-data-file=Market Holidays=Market Holidays',
        '--include-data-file=version.py=./version.py',
        '--include-data-dir=src/gui/resources=src/gui/resources',
        '--output-dir=dist',
        f'--output-filename={APP_NAME}',
        ENTRY_POINT
    ]

    if system == 'windows':
        # Windows-specific optimizations
        base_args.extend([
            '--windows-disable-console',  # Hide console for GUI app
            '--windows-icon-from-ico=src/gui/resources/icon.ico'  # If icon exists
        ])
    elif system == 'darwin':  # macOS
        # macOS-specific optimizations
        base_args.extend([
            '--macos-create-app-bundle',  # Create .app bundle
            '--macos-app-icon=src/gui/resources/icon.icns'  # If icon exists
        ])
    elif system == 'linux':
        # Linux-specific optimizations
        pass

    return base_args

def build():
    """Execute the Nuitka build"""
    print(f"\n=== Starting Nuitka Build ===")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Python: {sys.version}")
    print(f"Entry point: {ENTRY_POINT}")
    print(f"Output: dist/{APP_NAME}")

    args = get_platform_specific_args()

    # Remove icon arguments if icon files don't exist
    args = [arg for arg in args if not (
        ('icon.ico' in arg and not Path('src/gui/resources/icon.ico').exists()) or
        ('icon.icns' in arg and not Path('src/gui/resources/icon.icns').exists())
    )]

    print(f"\nNuitka command: {' '.join(args)}")

    try:
        result = subprocess.run(args, check=True)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"✗ Build failed with return code: {e.returncode}")
        return False

def verify_build():
    """Verify the build was successful"""
    system = platform.system().lower()

    if system == 'darwin' and Path('dist').exists():
        # Check for .app bundle on macOS
        app_bundle = Path('dist') / f'{APP_NAME}.app'
        if app_bundle.exists():
            executable = app_bundle / 'Contents' / 'MacOS' / APP_NAME
            if executable.exists():
                size = executable.stat().st_size / (1024 * 1024)  # MB
                print(f"✓ macOS app bundle: {app_bundle}")
                print(f"✓ Executable size: {size:.1f} MB")
                return True

    # Check for regular executable
    executable_name = APP_NAME
    if system == 'windows':
        executable_name += '.exe'

    executable = Path('dist') / executable_name
    if executable.exists():
        size = executable.stat().st_size / (1024 * 1024)  # MB
        print(f"✓ Executable: {executable}")
        print(f"✓ File size: {size:.1f} MB")
        return True

    print("✗ Build completed but executable not found")
    return False

def main():
    """Main build process"""
    print(f"=== NSE/BSE Downloader - Nuitka Cross-Platform Build ===")
    print(f"Target: {platform.system()} {platform.machine()} single executable")

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)

    # Check/install Nuitka
    if not check_nuitka():
        sys.exit(1)

    # Clean previous builds
    clean_build()

    # Build
    if not build():
        sys.exit(1)

    # Verify
    if not verify_build():
        sys.exit(1)

    print("\n=== Build Successful ===")
    print("\n=== Usage ===")

    system = platform.system().lower()
    if system == 'darwin':
        print("Run: open dist/NSE_BSE_Downloader.app")
        print("Or: ./dist/NSE_BSE_Downloader")
        print("If Gatekeeper blocks: xattr -dr com.apple.quarantine dist/NSE_BSE_Downloader*")
    elif system == 'windows':
        print("Run: dist\\NSE_BSE_Downloader.exe")
    else:  # Linux
        print("Run: ./dist/NSE_BSE_Downloader")
        print("Make executable: chmod +x dist/NSE_BSE_Downloader")

if __name__ == '__main__':
    main()
