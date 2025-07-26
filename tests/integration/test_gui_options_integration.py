#!/usr/bin/env python3
"""
Test script for GUI download options integration
Tests the complete flow from GUI checkboxes to downloader functionality
"""

import sys
import os
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.core.config import Config
from src.gui.main_window import DownloadWorker
from src.downloaders.nse_sme_downloader import NSESMEDownloader

def test_gui_options_flow():
    """Test complete GUI options flow"""
    print("\n🧪 Testing GUI options flow...")
    
    try:
        config = Config()
        
        # Simulate GUI checkbox states
        download_options = {
            'sme_add_suffix': True,
            'sme_append_to_eq': False,  # Test suffix without append
            'index_append_to_eq': False,
            'bse_index_append_to_eq': False
        }
        
        # Test DownloadWorker initialization with options
        selected_exchanges = ['NSE_SME']
        include_weekends = False
        timeout_seconds = 5
        
        worker = DownloadWorker(
            config=config,
            selected_exchanges=selected_exchanges,
            include_weekends=include_weekends,
            timeout_seconds=timeout_seconds,
            download_options=download_options
        )
        
        # Check if worker has options
        if hasattr(worker, 'download_options') and worker.download_options == download_options:
            print("  ✅ DownloadWorker: Options received correctly")
        else:
            print("  ❌ DownloadWorker: Options not received properly")
            return False
        
        # Check if downloaders get the options
        if 'NSE_SME' in worker.downloaders:
            sme_downloader = worker.downloaders['NSE_SME']
            if hasattr(sme_downloader, 'download_options') and sme_downloader.download_options == download_options:
                print("  ✅ NSE SME Downloader: Options set correctly")
            else:
                print("  ❌ NSE SME Downloader: Options not set properly")
                return False
        else:
            print("  ❌ NSE SME Downloader: Not initialized")
            return False
        
        print("  ✅ GUI options flow working correctly!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing GUI options flow: {e}")
        return False

def test_options_combinations():
    """Test different combinations of options"""
    print("\n🧪 Testing options combinations...")
    
    try:
        config = Config()
        downloader = NSESMEDownloader(config)
        
        # Test combination 1: Only suffix
        options1 = {'sme_add_suffix': True, 'sme_append_to_eq': False}
        downloader.set_download_options(options1)
        
        test_data = pd.DataFrame({
            'SYMBOL': ['TEST1', 'TEST2'],
            'OPEN': [100, 200],
            'CLOSE': [110, 220]
        })
        
        transformed_data1 = downloader.transform_data(test_data, date.today())
        symbols1 = transformed_data1['SYMBOL'].tolist()
        
        if all(str(symbol).endswith('_sme') for symbol in symbols1):
            print("  ✅ Combination 1 (suffix only): Working correctly")
        else:
            print("  ❌ Combination 1 (suffix only): Not working")
            return False
        
        # Test combination 2: Only append (without suffix)
        options2 = {'sme_add_suffix': False, 'sme_append_to_eq': True}
        downloader.set_download_options(options2)
        
        transformed_data2 = downloader.transform_data(test_data, date.today())
        symbols2 = transformed_data2['SYMBOL'].tolist()
        
        if not any(str(symbol).endswith('_sme') for symbol in symbols2):
            print("  ✅ Combination 2 (append only): Working correctly")
        else:
            print("  ❌ Combination 2 (append only): Suffix incorrectly added")
            return False
        
        # Test combination 3: Both suffix and append
        options3 = {'sme_add_suffix': True, 'sme_append_to_eq': True}
        downloader.set_download_options(options3)
        
        transformed_data3 = downloader.transform_data(test_data, date.today())
        symbols3 = transformed_data3['SYMBOL'].tolist()
        
        if all(str(symbol).endswith('_sme') for symbol in symbols3):
            print("  ✅ Combination 3 (both options): Working correctly")
        else:
            print("  ❌ Combination 3 (both options): Not working")
            return False
        
        print("  ✅ All option combinations working correctly!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing option combinations: {e}")
        return False

def test_options_persistence():
    """Test that options persist through downloader lifecycle"""
    print("\n🧪 Testing options persistence...")
    
    try:
        config = Config()
        
        # Create multiple downloaders with same options
        test_options = {
            'sme_add_suffix': True,
            'sme_append_to_eq': True,
            'index_append_to_eq': True,
            'bse_index_append_to_eq': True
        }
        
        # Test multiple operations with same downloader
        downloader = NSESMEDownloader(config)
        downloader.set_download_options(test_options)
        
        # Perform multiple operations
        for i in range(3):
            if downloader.download_options == test_options:
                continue
            else:
                print(f"  ❌ Options lost after operation {i+1}")
                return False
        
        print("  ✅ Options persistence working correctly!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing options persistence: {e}")
        return False

def test_default_behavior():
    """Test default behavior when no options are set"""
    print("\n🧪 Testing default behavior...")
    
    try:
        config = Config()
        downloader = NSESMEDownloader(config)
        
        # Don't set any options (should use defaults)
        test_data = pd.DataFrame({
            'SYMBOL': ['DEFAULT1', 'DEFAULT2'],
            'OPEN': [100, 200],
            'CLOSE': [110, 220]
        })
        
        transformed_data = downloader.transform_data(test_data, date.today())
        symbols = transformed_data['SYMBOL'].tolist()
        
        # Should not have suffix by default
        if not any(str(symbol).endswith('_sme') for symbol in symbols):
            print("  ✅ Default behavior (no suffix): Working correctly")
        else:
            print("  ❌ Default behavior: Suffix incorrectly added")
            return False
        
        # Test save behavior (should save to SME folder by default)
        saved_path = downloader.save_processed_data(transformed_data, date.today())
        expected_path = config.get_data_path('NSE', 'SME')
        
        if saved_path.parent == expected_path:
            print("  ✅ Default save behavior: Working correctly")
            # Clean up test file
            if saved_path.exists():
                saved_path.unlink()
        else:
            print("  ❌ Default save behavior: Not working")
            return False
        
        print("  ✅ Default behavior working correctly!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing default behavior: {e}")
        return False

def main():
    """Run all GUI options integration tests"""
    print("🚀 Testing GUI Options Integration")
    print("=" * 50)
    
    tests = [
        ("GUI Options Flow", test_gui_options_flow),
        ("Options Combinations", test_options_combinations),
        ("Options Persistence", test_options_persistence),
        ("Default Behavior", test_default_behavior)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n📋 Running: {test_name}")
        result = test_func()
        results.append((test_name, result))
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    print("=" * 50)
    
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Overall: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        print("🎉 All GUI options integration tests passed!")
        return True
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
