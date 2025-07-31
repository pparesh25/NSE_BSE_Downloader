#!/usr/bin/env python3
"""
Setup Script for NSE/BSE Data Downloader

Installs dependencies and sets up the environment.
"""

import sys
import subprocess
import os
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n{description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✓ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ {description} failed:")
        print(f"Error: {e.stderr}")
        return False

def check_python_version():
    """Check if Python version is compatible"""
    print("Checking Python version...")
    
    if sys.version_info < (3, 8):
        print(f"✗ Python 3.8+ required, found {sys.version}")
        return False
    
    print(f"✓ Python {sys.version} is compatible")
    return True

def install_dependencies():
    """Install required dependencies"""
    print("\nInstalling dependencies...")
    
    # Core dependencies
    core_deps = [
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "aiohttp>=3.8.0",
        "pyyaml>=6.0",
        "requests>=2.28.0",
        "python-dateutil>=2.8.0",
        "psutil>=5.9.0"
    ]
    
    # Install core dependencies
    for dep in core_deps:
        if not run_command(f"pip install '{dep}'", f"Installing {dep.split('>=')[0]}"):
            return False
    
    # Try to install PyQt6 (optional for GUI)
    print("\nInstalling GUI dependencies (optional)...")
    gui_success = run_command("pip install PyQt6", "Installing PyQt6")
    
    if gui_success:
        print("✓ GUI dependencies installed - GUI mode available")
    else:
        print("⚠ GUI dependencies failed - GUI mode will not be available")
    
    return True

def create_directories():
    """Create necessary directories"""
    print("\nCreating directories...")
    
    try:
        # Get home directory
        home_dir = Path.home()
        
        # Create data directories
        data_dirs = [
            home_dir / "NSE_BSE_Data" / "NSE" / "EQ",
            home_dir / "NSE_BSE_Data" / "NSE" / "FO",
            home_dir / "NSE_BSE_Data" / "NSE" / "SME",
            home_dir / "NSE_BSE_Data" / "NSE" / "INDEX",
            home_dir / "NSE_BSE_Data" / "BSE" / "EQ",
            home_dir / "NSE_BSE_Data" / "BSE" / "INDEX"
        ]
        
        for dir_path in data_dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"✓ Created: {dir_path}")
        
        return True
        
    except Exception as e:
        print(f"✗ Error creating directories: {e}")
        return False

def test_setup():
    """Test the setup"""
    print("\nTesting setup...")
    
    try:
        # Test imports
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        
        from src.core.config import Config
        from src.core.data_manager import DataManager
        
        # Test config loading
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        # Test data manager
        data_manager = DataManager(config)
        
        print("✓ Core modules working correctly")
        return True
        
    except Exception as e:
        print(f"✗ Setup test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main setup function"""
    print("NSE/BSE Data Downloader - Setup")
    print("=" * 40)
    
    # Check Python version
    if not check_python_version():
        return 1
    
    # Install dependencies
    if not install_dependencies():
        print("\n✗ Dependency installation failed")
        return 1
    
    # Create directories
    if not create_directories():
        print("\n✗ Directory creation failed")
        return 1
    
    # Test setup
    if not test_setup():
        print("\n✗ Setup test failed")
        return 1
    
    print("\n" + "=" * 40)
    print("✓ Setup completed successfully!")
    print("\nNext steps:")
    print("1. Run: python test_setup.py")
    print("2. Run: python main.py")
    print("\nFor help: python main.py --help")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
