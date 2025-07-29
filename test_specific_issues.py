#!/usr/bin/env python3
"""
Test specific issues: BSE append, SME decimal precision, duplicate append
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
    from src.utils.memory_optimizer import MemoryOptimizer
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
            logging.FileHandler('specific_issues_test.log')
        ]
    )

def test_decimal_precision():
    """Test decimal precision preservation"""
    print("🔢 Testing decimal precision preservation...")
    
    # Create test data with specific decimal values
    test_data = pd.DataFrame({
        'SYMBOL': ['TEST_SME'],
        'DATE': ['20250729'],
        'OPEN': [127.30],
        'HIGH': [128.45],
        'LOW': [126.75],
        'CLOSE': [127.95],
        'VOLUME': [50000]
    })
    
    print(f"Original data: {test_data.iloc[0]['OPEN']}")
    
    # Test memory optimizer
    optimizer = MemoryOptimizer()
    optimized_data = optimizer.optimize_dataframe(test_data)
    
    print(f"After optimization: {optimized_data.iloc[0]['OPEN']}")
    
    # Test CSV output with float formatting
    csv_output = optimized_data.to_csv(index=False, header=False, float_format='%.2f')
    print(f"CSV output: {csv_output.strip()}")
    
    return optimized_data.iloc[0]['OPEN'] == 127.30

def test_bse_append_logic():
    """Test BSE append logic"""
    print("🏦 Testing BSE append logic...")
    
    config = Config()
    manager = MemoryAppendManager(config)
    test_date = date.today()
    
    # Create test BSE data
    bse_eq_data = pd.DataFrame({
        'SYMBOL': ['RELIANCE', 'TCS'],
        'DATE': ['20250729', '20250729'],
        'OPEN': [2500.00, 3500.00],
        'HIGH': [2550.00, 3550.00],
        'LOW': [2480.00, 3480.00],
        'CLOSE': [2530.00, 3520.00],
        'VOLUME': [1000000, 800000]
    })
    
    bse_index_data = pd.DataFrame({
        'SYMBOL': ['SENSEX', 'BSE100'],
        'DATE': ['20250729', '20250729'],
        'OPEN': [60000.00, 18000.00],
        'HIGH': [60200.00, 18100.00],
        'LOW': [59800.00, 17950.00],
        'CLOSE': [60100.00, 18050.00],
        'VOLUME': [0, 0]
    })
    
    # Enable BSE append
    user_prefs = UserPreferences()
    user_prefs.set_append_options({"bse_index_append_to_eq": True})
    
    # Store data
    manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
    manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
    
    # Check available data
    available_data = manager.get_available_data_types(test_date)
    print(f"Available data: {available_data}")
    
    # Test append
    results = manager._try_bse_eq_append(test_date)
    print(f"BSE append result: {results}")
    
    return results

def test_duplicate_append_prevention():
    """Test duplicate append prevention"""
    print("🔄 Testing duplicate append prevention...")
    
    config = Config()
    manager = MemoryAppendManager(config)
    test_date = date.today()
    
    # Create test NSE data
    nse_eq_data = pd.DataFrame({
        'SYMBOL': ['RELIANCE'],
        'DATE': ['20250729'],
        'OPEN': [2500.00],
        'HIGH': [2550.00],
        'LOW': [2480.00],
        'CLOSE': [2530.00],
        'VOLUME': [1000000]
    })
    
    nse_sme_data = pd.DataFrame({
        'SYMBOL': ['SME1_SME'],
        'DATE': ['20250729'],
        'OPEN': [100.00],
        'HIGH': [110.00],
        'LOW': [95.00],
        'CLOSE': [105.00],
        'VOLUME': [50000]
    })
    
    # Enable NSE append
    user_prefs = UserPreferences()
    user_prefs.set_append_options({"sme_append_to_eq": True})
    
    # Store data
    manager.store_data('NSE', 'EQ', test_date, nse_eq_data)
    manager.store_data('NSE', 'SME', test_date, nse_sme_data)
    
    # First append
    print("First append attempt...")
    result1 = manager._try_nse_eq_append(test_date)
    print(f"First append result: {result1}")
    
    # Second append (should be prevented)
    print("Second append attempt (should be prevented)...")
    result2 = manager._try_nse_eq_append(test_date)
    print(f"Second append result: {result2}")
    
    # Check completed appends
    date_key = manager._get_date_key(test_date)
    completed = manager.completed_appends.get(date_key, set())
    print(f"Completed appends: {completed}")
    
    return 'nse_eq_append' in completed

def main():
    """Run all tests"""
    print("🧪 Starting specific issues test...")
    setup_logging()
    
    try:
        # Test 1: Decimal precision
        precision_ok = test_decimal_precision()
        print(f"✅ Decimal precision test: {'PASSED' if precision_ok else 'FAILED'}")
        
        # Test 2: BSE append logic
        bse_result = test_bse_append_logic()
        print(f"✅ BSE append test: {'PASSED' if bse_result else 'FAILED'}")
        
        # Test 3: Duplicate append prevention
        duplicate_prevention = test_duplicate_append_prevention()
        print(f"✅ Duplicate prevention test: {'PASSED' if duplicate_prevention else 'FAILED'}")
        
        print("\n🎉 All tests completed!")
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
