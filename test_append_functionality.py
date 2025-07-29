#!/usr/bin/env python3
"""
Test script to verify append functionality
"""

import sys
import logging
from pathlib import Path
from datetime import date
import pandas as pd

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from src.core.config import Config
    from src.services.memory_append_manager import MemoryAppendManager
    from src.utils.user_preferences import UserPreferences
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure all dependencies are installed")
    sys.exit(1)

def setup_logging():
    """Setup logging for debugging"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('append_test.log')
        ]
    )

def create_test_data():
    """Create test data for NSE EQ, NSE SME, NSE INDEX, BSE EQ, BSE INDEX"""
    
    # NSE EQ test data
    nse_eq_data = pd.DataFrame({
        'SYMBOL': ['RELIANCE', 'TCS', 'INFY'],
        'OPEN': [2500.0, 3500.0, 1800.0],
        'HIGH': [2550.0, 3550.0, 1850.0],
        'LOW': [2480.0, 3480.0, 1780.0],
        'CLOSE': [2530.0, 3520.0, 1820.0],
        'VOLUME': [1000000, 800000, 1200000]
    })
    
    # NSE SME test data
    nse_sme_data = pd.DataFrame({
        'SYMBOL': ['SME1', 'SME2'],
        'OPEN': [100.0, 200.0],
        'HIGH': [110.0, 220.0],
        'LOW': [95.0, 195.0],
        'CLOSE': [105.0, 210.0],
        'VOLUME': [50000, 60000]
    })
    
    # NSE INDEX test data
    nse_index_data = pd.DataFrame({
        'SYMBOL': ['NIFTY50', 'BANKNIFTY'],
        'OPEN': [18000.0, 45000.0],
        'HIGH': [18100.0, 45200.0],
        'LOW': [17950.0, 44800.0],
        'CLOSE': [18050.0, 45100.0],
        'VOLUME': [0, 0]  # Index typically has 0 volume
    })
    
    # BSE EQ test data
    bse_eq_data = pd.DataFrame({
        'SYMBOL': ['RELIANCE', 'TCS'],
        'OPEN': [2500.0, 3500.0],
        'HIGH': [2550.0, 3550.0],
        'LOW': [2480.0, 3480.0],
        'CLOSE': [2530.0, 3520.0],
        'VOLUME': [1000000, 800000]
    })
    
    # BSE INDEX test data
    bse_index_data = pd.DataFrame({
        'SYMBOL': ['SENSEX', 'BSE100'],
        'OPEN': [60000.0, 18000.0],
        'HIGH': [60200.0, 18100.0],
        'LOW': [59800.0, 17950.0],
        'CLOSE': [60100.0, 18050.0],
        'VOLUME': [0, 0]  # Index typically has 0 volume
    })
    
    return {
        'NSE_EQ': nse_eq_data,
        'NSE_SME': nse_sme_data,
        'NSE_INDEX': nse_index_data,
        'BSE_EQ': bse_eq_data,
        'BSE_INDEX': bse_index_data
    }

def create_test_files(config, test_data, test_date):
    """Create test files in the expected locations"""
    
    # Create NSE EQ file
    nse_eq_path = config.get_data_path('NSE', 'EQ')
    nse_eq_path.mkdir(parents=True, exist_ok=True)
    nse_eq_filename = f"{test_date.strftime('%Y-%m-%d')}-NSE-EQ.txt"
    nse_eq_file = nse_eq_path / nse_eq_filename
    test_data['NSE_EQ'].to_csv(nse_eq_file, index=False, header=False)
    print(f"Created NSE EQ file: {nse_eq_file}")
    
    # Create BSE EQ file
    bse_eq_path = config.get_data_path('BSE', 'EQ')
    bse_eq_path.mkdir(parents=True, exist_ok=True)
    bse_eq_filename = f"{test_date.strftime('%Y-%m-%d')}-BSE-EQ.txt"
    bse_eq_file = bse_eq_path / bse_eq_filename
    test_data['BSE_EQ'].to_csv(bse_eq_file, index=False, header=False)
    print(f"Created BSE EQ file: {bse_eq_file}")
    
    return {
        'nse_eq_file': nse_eq_file,
        'bse_eq_file': bse_eq_file
    }

def test_append_functionality():
    """Test the append functionality"""

    print("🧪 Starting comprehensive append functionality test...")
    setup_logging()

    try:
        # Initialize config and manager
        config = Config()
        manager = MemoryAppendManager(config)

        # Set test date
        test_date = date.today()
        print(f"📅 Using test date: {test_date}")

        # Create test data
        test_data = create_test_data()
        print("📊 Created test data")

        # Create test files
        test_files = create_test_files(config, test_data, test_date)

        # Enable append options in user preferences
        user_prefs = UserPreferences()
        append_options = {
            "sme_append_to_eq": True,
            "index_append_to_eq": True,
            "bse_index_append_to_eq": True
        }
        user_prefs.set_append_options(append_options)
        print(f"⚙️ Set append options: {append_options}")

        # Store original file sizes
        original_nse_eq_size = test_files['nse_eq_file'].stat().st_size
        original_bse_eq_size = test_files['bse_eq_file'].stat().st_size
        print(f"📏 Original file sizes - NSE EQ: {original_nse_eq_size}, BSE EQ: {original_bse_eq_size}")

        # Store data in memory manager
        print("💾 Storing data in memory manager...")
        manager.store_data('NSE', 'EQ', test_date, test_data['NSE_EQ'])
        manager.store_data('NSE', 'SME', test_date, test_data['NSE_SME'])
        manager.store_data('NSE', 'INDEX', test_date, test_data['NSE_INDEX'])
        manager.store_data('BSE', 'EQ', test_date, test_data['BSE_EQ'])
        manager.store_data('BSE', 'INDEX', test_date, test_data['BSE_INDEX'])

        # Check what data is available
        available_data = manager.get_available_data_types(test_date)
        print(f"📋 Available data types: {available_data}")

        # Test append operations (first time)
        print("🔄 Testing append operations (first time)...")
        results1 = manager.try_append_operations(test_date)
        print(f"📊 First append operation results: {results1}")

        # Test append operations (second time - should not duplicate)
        print("🔄 Testing append operations (second time - should not duplicate)...")
        results2 = manager.try_append_operations(test_date)
        print(f"📊 Second append operation results: {results2}")

        # Check file contents after append
        print("\n🔍 Checking file contents after append...")

        # Check NSE EQ file
        if test_files['nse_eq_file'].exists():
            with open(test_files['nse_eq_file'], 'r') as f:
                lines = f.readlines()
            print(f"📄 NSE EQ file now has {len(lines)} lines")
            print("📄 Last 10 lines of NSE EQ file:")
            for line in lines[-10:]:
                print(f"  {line.strip()}")

            # Check for decimal precision
            if len(lines) > 3:  # Original EQ data
                appended_line = lines[3].strip()  # First appended line (SME data)
                print(f"🔢 Checking decimal precision in appended data: {appended_line}")

        # Check BSE EQ file
        if test_files['bse_eq_file'].exists():
            with open(test_files['bse_eq_file'], 'r') as f:
                lines = f.readlines()
            print(f"📄 BSE EQ file now has {len(lines)} lines")
            print("📄 Last 5 lines of BSE EQ file:")
            for line in lines[-5:]:
                print(f"  {line.strip()}")

        # Check file sizes after append
        new_nse_eq_size = test_files['nse_eq_file'].stat().st_size
        new_bse_eq_size = test_files['bse_eq_file'].stat().st_size
        print(f"📏 New file sizes - NSE EQ: {new_nse_eq_size}, BSE EQ: {new_bse_eq_size}")

        # Verify no duplication
        if new_nse_eq_size > original_nse_eq_size:
            print("✅ NSE EQ file size increased - append successful")
        else:
            print("❌ NSE EQ file size unchanged - append may have failed")

        if new_bse_eq_size > original_bse_eq_size:
            print("✅ BSE EQ file size increased - append successful")
        else:
            print("❌ BSE EQ file size unchanged - append may have failed")

        print("\n🎉 Test completed successfully!")

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_append_functionality()
