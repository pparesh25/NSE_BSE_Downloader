#!/usr/bin/env python3
"""
Test SME suffix scenarios for append functionality
"""

import sys
import logging
from pathlib import Path
from datetime import date

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def setup_logging():
    """Setup detailed logging"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('sme_suffix_test.log')
        ]
    )

def test_scenario_1():
    """Test: NSE EQ + NSE SME (suffix enabled) - should append with suffix"""
    print("🧪 Scenario 1: NSE EQ + NSE SME (suffix enabled)")
    
    try:
        import pandas as pd
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date.today()
        
        # Enable SME suffix and append
        user_prefs.set_append_options({
            "sme_add_suffix": True,
            "sme_append_to_eq": True,
            "index_append_to_eq": False,
            "bse_index_append_to_eq": False
        })
        
        # Create test data
        eq_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [2500.00, 3500.00],
            'HIGH': [2550.00, 3550.00],
            'LOW': [2480.00, 3480.00],
            'CLOSE': [2530.00, 3520.00],
            'VOLUME': [1000000, 800000]
        })
        
        sme_data = pd.DataFrame({
            'SYMBOL': ['SME1_SME', 'SME2_SME'],  # With suffix
            'DATE': ['20250730', '20250730'],
            'OPEN': [100.00, 200.00],
            'HIGH': [110.00, 220.00],
            'LOW': [95.00, 195.00],
            'CLOSE': [105.00, 210.00],
            'VOLUME': [50000, 60000]
        })
        
        # Store data
        manager.store_data('NSE', 'EQ', test_date, eq_data)
        manager.store_data('NSE', 'SME', test_date, sme_data)
        
        # Test append
        result = manager._try_nse_eq_append(test_date)
        print(f"  Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        return False

def test_scenario_2():
    """Test: NSE EQ + NSE INDEX - should append"""
    print("🧪 Scenario 2: NSE EQ + NSE INDEX")
    
    try:
        import pandas as pd
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date.today()
        
        # Enable INDEX append only
        user_prefs.set_append_options({
            "sme_add_suffix": False,
            "sme_append_to_eq": False,
            "index_append_to_eq": True,
            "bse_index_append_to_eq": False
        })
        
        # Create test data
        eq_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [2500.00, 3500.00],
            'HIGH': [2550.00, 3550.00],
            'LOW': [2480.00, 3480.00],
            'CLOSE': [2530.00, 3520.00],
            'VOLUME': [1000000, 800000]
        })
        
        index_data = pd.DataFrame({
            'SYMBOL': ['Nifty 50', 'Nifty Bank'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [24000.00, 45000.00],
            'HIGH': [24100.00, 45200.00],
            'LOW': [23950.00, 44800.00],
            'CLOSE': [24050.00, 45100.00],
            'VOLUME': [0, 0]
        })
        
        # Store data
        manager.store_data('NSE', 'EQ', test_date, eq_data)
        manager.store_data('NSE', 'INDEX', test_date, index_data)
        
        # Test append
        result = manager._try_nse_eq_append(test_date)
        print(f"  Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        return False

def test_scenario_3():
    """Test: NSE EQ + NSE SME (suffix disabled) + NSE INDEX - should append both"""
    print("🧪 Scenario 3: NSE EQ + NSE SME (suffix disabled) + NSE INDEX")
    
    try:
        import pandas as pd
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date.today()
        
        # Enable both appends but disable suffix
        user_prefs.set_append_options({
            "sme_add_suffix": False,
            "sme_append_to_eq": True,
            "index_append_to_eq": True,
            "bse_index_append_to_eq": False
        })
        
        # Create test data
        eq_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [2500.00, 3500.00],
            'HIGH': [2550.00, 3550.00],
            'LOW': [2480.00, 3480.00],
            'CLOSE': [2530.00, 3520.00],
            'VOLUME': [1000000, 800000]
        })
        
        sme_data = pd.DataFrame({
            'SYMBOL': ['SME1', 'SME2'],  # WITHOUT suffix
            'DATE': ['20250730', '20250730'],
            'OPEN': [100.00, 200.00],
            'HIGH': [110.00, 220.00],
            'LOW': [95.00, 195.00],
            'CLOSE': [105.00, 210.00],
            'VOLUME': [50000, 60000]
        })
        
        index_data = pd.DataFrame({
            'SYMBOL': ['Nifty 50', 'Nifty Bank'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [24000.00, 45000.00],
            'HIGH': [24100.00, 45200.00],
            'LOW': [23950.00, 44800.00],
            'CLOSE': [24050.00, 45100.00],
            'VOLUME': [0, 0]
        })
        
        # Store data
        manager.store_data('NSE', 'EQ', test_date, eq_data)
        manager.store_data('NSE', 'SME', test_date, sme_data)
        manager.store_data('NSE', 'INDEX', test_date, index_data)
        
        # Test append
        result = manager._try_nse_eq_append(test_date)
        print(f"  Result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all test scenarios"""
    print("🧪 Testing SME Suffix Scenarios...")
    setup_logging()
    
    try:
        # Test all scenarios
        scenario1_result = test_scenario_1()
        scenario2_result = test_scenario_2()
        scenario3_result = test_scenario_3()
        
        print("\n📊 Results Summary:")
        print(f"  Scenario 1 (SME with suffix): {'✅ PASS' if scenario1_result else '❌ FAIL'}")
        print(f"  Scenario 2 (INDEX only): {'✅ PASS' if scenario2_result else '❌ FAIL'}")
        print(f"  Scenario 3 (SME without suffix + INDEX): {'✅ PASS' if scenario3_result else '❌ FAIL'}")
        
        if scenario3_result:
            print("\n🎉 Issue FIXED: SME without suffix + INDEX append works!")
        else:
            print("\n⚠️ Issue PERSISTS: SME without suffix + INDEX append fails!")
            
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
