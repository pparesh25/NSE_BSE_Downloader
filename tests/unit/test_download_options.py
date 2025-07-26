#!/usr/bin/env python3
"""
Test script for download options functionality
Tests the new tick mark options in GUI download section

This script tests:
1. NSE SME suffix option (_sme suffix addition)
2. Append to EQ file options (SME, Index data combination)
3. Download options integration across all downloaders
"""

import sys
import os
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.core.config import Config
from src.downloaders.nse_sme_downloader import NSESMEDownloader
from src.downloaders.nse_index_downloader import NSEIndexDownloader
from src.downloaders.bse_index_downloader import BSEIndexDownloader

def test_sme_suffix_option():
    """Test NSE SME suffix option"""
    print("\n🧪 Testing NSE SME suffix option...")
    
    try:
        config = Config()
        downloader = NSESMEDownloader(config)
        
        # Test with suffix option enabled
        downloader.set_download_options({'sme_add_suffix': True})
        
        # Create test data
        test_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS', 'INFY'],
            'OPEN': [2500, 3500, 1500],
            'HIGH': [2600, 3600, 1600],
            'LOW': [2400, 3400, 1400],
            'CLOSE': [2550, 3550, 1550]
        })
        
        # Transform data
        transformed_data = downloader.transform_data(test_data, date.today())
        
        # Check if suffix was added
        if 'SYMBOL' in transformed_data.columns:
            symbols = transformed_data['SYMBOL'].tolist()
            print(f"  Original symbols: ['RELIANCE', 'TCS', 'INFY']")
            print(f"  Transformed symbols: {symbols}")
            
            # Check if all symbols have '_sme' suffix
            all_have_suffix = all(str(symbol).endswith('_sme') for symbol in symbols)
            if all_have_suffix:
                print("  ✅ SME suffix option working correctly!")
                return True
            else:
                print("  ❌ SME suffix option not working properly")
                return False
        else:
            print("  ❌ SYMBOL column not found in transformed data")
            return False
            
    except Exception as e:
        print(f"  ❌ Error testing SME suffix option: {e}")
        return False

def test_append_options():
    """Test append to EQ file options"""
    print("\n🧪 Testing append to EQ file options...")
    
    try:
        config = Config()
        
        # Create test NSE EQ file
        nse_eq_path = config.get_data_path('NSE', 'EQ')
        nse_eq_path.mkdir(parents=True, exist_ok=True)
        
        test_date = date.today()
        eq_filename = test_date.strftime('%Y-%m-%d') + '-NSE-EQ.csv'
        eq_file_path = nse_eq_path / eq_filename
        
        # Create dummy NSE EQ file
        eq_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'OPEN': [2500, 3500],
            'CLOSE': [2550, 3550]
        })
        eq_data.to_csv(eq_file_path, index=False, header=False)
        print(f"  Created test NSE EQ file: {eq_filename}")
        
        # Test NSE SME append option
        sme_downloader = NSESMEDownloader(config)
        sme_downloader.set_download_options({'sme_append_to_eq': True})
        
        sme_data = pd.DataFrame({
            'SYMBOL': ['SME1', 'SME2'],
            'OPEN': [100, 200],
            'CLOSE': [110, 220]
        })
        
        # Save with append option
        saved_path = sme_downloader.save_processed_data(sme_data, test_date)
        
        # Check if data was appended
        if saved_path == eq_file_path:
            # Read the file and check content
            final_data = pd.read_csv(eq_file_path, header=None)
            print(f"  Final file has {len(final_data)} rows (should be 4: 2 EQ + 2 SME)")
            
            if len(final_data) == 4:
                print("  ✅ NSE SME append option working correctly!")
                append_success = True
            else:
                print("  ❌ NSE SME append option not working properly")
                append_success = False
        else:
            print("  ❌ NSE SME data was not appended to EQ file")
            append_success = False
        
        # Clean up test file
        if eq_file_path.exists():
            eq_file_path.unlink()
            print(f"  Cleaned up test file: {eq_filename}")
        
        return append_success
        
    except Exception as e:
        print(f"  ❌ Error testing append options: {e}")
        return False

def test_download_options_integration():
    """Test download options integration"""
    print("\n🧪 Testing download options integration...")
    
    try:
        config = Config()
        
        # Test all downloaders have set_download_options method
        downloaders = [
            NSESMEDownloader(config),
            NSEIndexDownloader(config),
            BSEIndexDownloader(config)
        ]
        
        test_options = {
            'sme_add_suffix': True,
            'sme_append_to_eq': True,
            'index_append_to_eq': True,
            'bse_index_append_to_eq': True
        }
        
        for downloader in downloaders:
            downloader_name = downloader.__class__.__name__
            
            # Test if method exists
            if hasattr(downloader, 'set_download_options'):
                downloader.set_download_options(test_options)
                
                # Check if options were set
                if hasattr(downloader, 'download_options') and downloader.download_options == test_options:
                    print(f"  ✅ {downloader_name}: Options set correctly")
                else:
                    print(f"  ❌ {downloader_name}: Options not set properly")
                    return False
            else:
                print(f"  ❌ {downloader_name}: set_download_options method missing")
                return False
        
        print("  ✅ Download options integration working correctly!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing download options integration: {e}")
        return False

def main():
    """Run all download options tests"""
    print("🚀 Testing Download Options Functionality")
    print("=" * 50)
    
    tests = [
        ("SME Suffix Option", test_sme_suffix_option),
        ("Append to EQ Options", test_append_options),
        ("Download Options Integration", test_download_options_integration)
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
        print("🎉 All download options tests passed!")
        return True
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
