#!/usr/bin/env python3
"""
Simple Run Script for NSE/BSE Data Downloader

Handles setup and runs the application with proper error handling.
"""

import sys
import os
from pathlib import Path

def check_setup():
    """Check if setup is complete"""
    try:
        # Add src to path
        src_path = Path(__file__).parent / "src"
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        
        # Test basic imports
        from src.core.config import Config
        
        # Test config file
        config_path = Path(__file__).parent / "config.yaml"
        if not config_path.exists():
            print("❌ config.yaml not found!")
            return False
        
        # Test config loading
        config = Config(str(config_path))
        
        print("✅ Setup check passed")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Run: python setup.py")
        return False
    except Exception as e:
        print(f"❌ Setup error: {e}")
        return False

def run_gui():
    """Run GUI mode"""
    try:
        from PyQt6.QtWidgets import QApplication
        from src.gui.main_window import MainWindow
        from src.core.config import Config
        
        print("🚀 Starting GUI mode...")
        
        app = QApplication(sys.argv)
        app.setApplicationName("NSE/BSE Data Downloader")
        
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        window = MainWindow(config)
        window.show()
        
        return app.exec()
        
    except ImportError:
        print("❌ PyQt6 not available. Install with: pip install PyQt6")
        print("Falling back to CLI mode...")
        return run_cli()
    except Exception as e:
        print(f"❌ GUI error: {e}")
        return 1

def run_cli():
    """Run CLI mode"""
    try:
        from src.core.config import Config
        from src.core.data_manager import DataManager
        
        print("🚀 Starting CLI mode...")
        
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        data_manager = DataManager(config)
        
        # Show data summary
        print("\n📊 Data Summary:")
        print("-" * 40)
        
        summary = data_manager.get_data_summary()
        for exchange, info in summary.items():
            if 'error' in info:
                print(f"{exchange}: ❌ {info['error']}")
            else:
                last_date = info['last_date'] or 'No data'
                file_count = info['file_count']
                print(f"{exchange}: Last: {last_date}, Files: {file_count}")
        
        print("\n💡 For full functionality, use GUI mode:")
        print("   python run.py --gui")
        print("   or install PyQt6: pip install PyQt6")

        # Check for updates in CLI mode
        check_updates_cli()

        return 0
        
    except Exception as e:
        print(f"❌ CLI error: {e}")
        return 1

def check_updates_cli():
    """Check for updates in CLI mode"""
    try:
        import requests
        import json

        print("\n🔄 Checking for updates...")

        # Fetch update info
        url = "https://raw.githubusercontent.com/pparesh25/Getbhavcopy-alternative/main/update_info.json"
        response = requests.get(url, timeout=10)

        if response.status_code == 404:
            print("✅ No updates available (update info not found)")
            return
        elif response.status_code == 200:
            data = response.json()

            current_version = "2.0.0"  # Current version
            latest_version = data.get("latest_version", "Unknown")
            update_available = data.get("update_available", False)

            if update_available and latest_version != current_version:
                print(f"\n🎉 Update Available!")
                print(f"   Current: v{current_version}")
                print(f"   Latest:  v{latest_version}")
                print(f"   Message: {data.get('update_message', 'New version available')}")

                # Show some features
                features = data.get('changelog', {}).get('features', [])
                if features:
                    print(f"\n🆕 New Features:")
                    for i, feature in enumerate(features[:3]):  # Show first 3
                        print(f"   • {feature}")

                print(f"\n📥 Download: https://github.com/pparesh25/Getbhavcopy-alternative/releases")
                print(f"   Or visit: https://github.com/pparesh25/Getbhavcopy-alternative")
            else:
                print("✅ You have the latest version!")
        else:
            print(f"⚠️ Could not check for updates (HTTP {response.status_code})")

    except requests.RequestException:
        print("⚠️ Could not check for updates (network error)")
    except Exception as e:
        print(f"⚠️ Update check failed: {e}")

def main():
    """Main function"""
    print("NSE/BSE Data Downloader")
    print("=" * 30)
    
    # Check setup
    if not check_setup():
        print("\n🔧 Run setup first:")
        print("   python setup.py")
        return 1
    
    # Check command line arguments
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        return run_cli()
    else:
        return run_gui()

if __name__ == "__main__":
    sys.exit(main())
