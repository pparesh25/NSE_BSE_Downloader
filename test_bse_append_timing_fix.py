#!/usr/bin/env python3
"""
Test BSE append timing fix with pending operations
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
            logging.FileHandler('bse_timing_fix_test.log')
        ]
    )

def test_bse_append_timing_scenario():
    """Test BSE append with timing scenario (EQ first, then INDEX)"""
    print("🔍 Testing BSE Append Timing Fix...")
    
    try:
        # Mock pandas and dependencies
        class MockDataFrame:
            def __init__(self, data):
                self.data = data
                self.columns = list(data.keys()) if data else []
                self.shape = (len(list(data.values())[0]) if data else 0, len(self.columns))
            
            @property
            def empty(self):
                return self.shape[0] == 0
            
            def __len__(self):
                return self.shape[0]
            
            def copy(self):
                return MockDataFrame(self.data.copy())
            
            def iloc(self, index):
                row_data = {}
                for col in self.columns:
                    row_data[col] = self.data[col][index]
                return type('MockRow', (), {'to_dict': lambda: row_data})()
        
        class MockPandas:
            DataFrame = MockDataFrame
            
            @staticmethod
            def concat(dataframes, ignore_index=True, sort=False):
                if len(dataframes) == 2:
                    df1, df2 = dataframes
                    combined_data = {}
                    for col in df1.columns:
                        combined_data[col] = df1.data.get(col, []) + df2.data.get(col, [])
                    return MockDataFrame(combined_data)
                return dataframes[0] if dataframes else MockDataFrame({})
        
        # Mock aiohttp
        sys.modules['aiohttp'] = type('MockModule', (), {})()
        sys.modules['pandas'] = MockPandas()
        
        # Import after mocking
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date(2025, 7, 30)
        
        # Enable BSE append
        user_prefs.set_bse_index_append_to_eq(True)
        print(f"  ✅ BSE append option enabled")
        
        # Create test data
        bse_eq_data = MockDataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [2500.0, 3500.0],
            'HIGH': [2550.0, 3550.0],
            'LOW': [2480.0, 3480.0],
            'CLOSE': [2530.0, 3520.0],
            'VOLUME': [1000000, 800000]
        })
        
        bse_index_data = MockDataFrame({
            'SYMBOL': ['BSE SENSEX', 'BSE 100'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [81594.52, 26097.61],
            'HIGH': [81618.96, 26103.71],
            'LOW': [81187.06, 25979.28],
            'CLOSE': [81481.86, 26064.32],
            'VOLUME': [0, 0]
        })
        
        print(f"  📊 Test data prepared: BSE EQ {bse_eq_data.shape}, BSE INDEX {bse_index_data.shape}")
        
        # Scenario 1: Store BSE EQ first (simulating EQ download completing first)
        print("\n  📋 Scenario 1: BSE EQ downloads first")
        manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
        
        # Try append operations (should mark as pending since INDEX not available)
        append_results = manager.try_append_operations(test_date)
        print(f"    Append results after EQ storage: {append_results}")
        
        # Check pending operations
        date_key = manager._get_date_key(test_date)
        pending_ops = manager.pending_appends.get(date_key, set())
        print(f"    Pending operations: {pending_ops}")
        
        # Scenario 2: Store BSE INDEX (simulating INDEX download completing later)
        print("\n  📋 Scenario 2: BSE INDEX downloads later")
        manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
        
        # Check if pending operations were executed
        pending_ops_after = manager.pending_appends.get(date_key, set())
        print(f"    Pending operations after INDEX storage: {pending_ops_after}")
        
        # Check completed operations
        completed_ops = manager.completed_appends.get(date_key, set())
        print(f"    Completed operations: {completed_ops}")
        
        # Verify final result
        success = 'bse_eq_append' in pending_ops and len(pending_ops_after) == 0
        print(f"  📊 Timing fix result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        return success
        
    except Exception as e:
        print(f"  ❌ Error in timing test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_reverse_timing_scenario():
    """Test reverse timing (INDEX first, then EQ)"""
    print("\n🔍 Testing Reverse Timing Scenario...")
    
    try:
        # Mock modules (same as above)
        class MockDataFrame:
            def __init__(self, data):
                self.data = data
                self.columns = list(data.keys()) if data else []
                self.shape = (len(list(data.values())[0]) if data else 0, len(self.columns))
            
            @property
            def empty(self):
                return self.shape[0] == 0
            
            def __len__(self):
                return self.shape[0]
            
            def copy(self):
                return MockDataFrame(self.data.copy())
        
        class MockPandas:
            DataFrame = MockDataFrame
            
            @staticmethod
            def concat(dataframes, ignore_index=True, sort=False):
                if len(dataframes) == 2:
                    df1, df2 = dataframes
                    combined_data = {}
                    for col in df1.columns:
                        combined_data[col] = df1.data.get(col, []) + df2.data.get(col, [])
                    return MockDataFrame(combined_data)
                return dataframes[0] if dataframes else MockDataFrame({})
        
        sys.modules['pandas'] = MockPandas()
        
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date(2025, 7, 31)  # Different date
        
        # Enable BSE append
        user_prefs.set_bse_index_append_to_eq(True)
        
        # Create test data
        bse_eq_data = MockDataFrame({
            'SYMBOL': ['HDFC', 'ICICI'],
            'DATE': ['20250731', '20250731'],
            'OPEN': [1500.0, 1200.0],
            'HIGH': [1550.0, 1250.0],
            'LOW': [1480.0, 1180.0],
            'CLOSE': [1530.0, 1220.0],
            'VOLUME': [500000, 600000]
        })
        
        bse_index_data = MockDataFrame({
            'SYMBOL': ['BSE MIDCAP', 'BSE SMALLCAP'],
            'DATE': ['20250731', '20250731'],
            'OPEN': [45000.0, 35000.0],
            'HIGH': [45500.0, 35500.0],
            'LOW': [44500.0, 34500.0],
            'CLOSE': [45200.0, 35200.0],
            'VOLUME': [0, 0]
        })
        
        # Scenario: Store BSE INDEX first
        print("  📋 Storing BSE INDEX first...")
        manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
        
        # Try append operations (should not trigger since EQ not available)
        append_results = manager.try_append_operations(test_date)
        print(f"    Append results after INDEX storage: {append_results}")
        
        # Store BSE EQ later
        print("  📋 Storing BSE EQ later...")
        manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
        
        # Try append operations (should work immediately)
        append_results = manager.try_append_operations(test_date)
        print(f"    Append results after EQ storage: {append_results}")
        
        success = append_results.get('bse_eq_append', False)
        print(f"  📊 Reverse timing result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        return success
        
    except Exception as e:
        print(f"  ❌ Error in reverse timing test: {e}")
        return False

def main():
    """Main testing function"""
    print("🔍 BSE Append Timing Fix Test")
    print("=" * 40)
    
    setup_logging()
    
    # Test 1: Normal timing (EQ first, INDEX later)
    timing_fix_result = test_bse_append_timing_scenario()
    
    # Test 2: Reverse timing (INDEX first, EQ later)
    reverse_timing_result = test_reverse_timing_scenario()
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 Timing Fix Test Results:")
    print(f"  EQ First, INDEX Later: {'✅ SUCCESS' if timing_fix_result else '❌ FAILED'}")
    print(f"  INDEX First, EQ Later: {'✅ SUCCESS' if reverse_timing_result else '❌ FAILED'}")
    
    if timing_fix_result and reverse_timing_result:
        print("\n🎉 BSE Append Timing Fix SUCCESSFUL!")
        print("   Both timing scenarios now work correctly.")
        print("   Pending operations mechanism handles data availability timing.")
    else:
        print("\n⚠️ BSE Append Timing Fix needs more work")
        print("   Check the detailed logs for specific issues.")

if __name__ == "__main__":
    main()
