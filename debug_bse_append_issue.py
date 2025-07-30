#!/usr/bin/env python3
"""
Debug BSE append issue - check exact failure point
"""

import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def check_user_preferences():
    """Check current user preferences for BSE append"""
    print("🔍 Checking User Preferences...")
    
    try:
        from src.utils.user_preferences import UserPreferences
        
        user_prefs = UserPreferences()
        append_options = user_prefs.get_append_options()
        
        print(f"  All append options: {append_options}")
        print(f"  BSE Index append enabled: {append_options.get('bse_index_append_to_eq', False)}")
        
        return append_options.get('bse_index_append_to_eq', False)
        
    except Exception as e:
        print(f"  ❌ Error checking preferences: {e}")
        return False

def check_memory_append_manager():
    """Check memory append manager state"""
    print("\n🔍 Checking Memory Append Manager...")
    
    try:
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from datetime import date
        
        config = Config()
        manager = MemoryAppendManager(config)
        
        # Check if pandas is available
        print(f"  HAS_PANDAS: {hasattr(manager, 'HAS_PANDAS') or 'pandas' in str(type(manager))}")
        
        # Check available data for recent date
        test_date = date(2025, 7, 30)
        available_data = manager.get_available_data_types(test_date)
        print(f"  Available data types for {test_date}: {available_data}")
        
        # Check specific BSE data
        has_bse_eq = manager.has_data('BSE', 'EQ', test_date)
        has_bse_index = manager.has_data('BSE', 'INDEX', test_date)
        
        print(f"  Has BSE EQ data: {has_bse_eq}")
        print(f"  Has BSE INDEX data: {has_bse_index}")
        
        return manager, has_bse_eq, has_bse_index
        
    except Exception as e:
        print(f"  ❌ Error checking memory manager: {e}")
        import traceback
        traceback.print_exc()
        return None, False, False

def test_bse_append_manually():
    """Manually test BSE append operation"""
    print("\n🔍 Manual BSE Append Test...")
    
    try:
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        from src.utils.user_preferences import UserPreferences
        from datetime import date
        import pandas as pd
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        user_prefs = UserPreferences()
        test_date = date(2025, 7, 30)
        
        # Enable BSE append
        user_prefs.set_bse_index_append_to_eq(True)
        print(f"  ✅ Enabled BSE Index append option")
        
        # Create test data
        bse_eq_data = pd.DataFrame({
            'SYMBOL': ['TEST1', 'TEST2'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [100.0, 200.0],
            'HIGH': [110.0, 220.0],
            'LOW': [95.0, 195.0],
            'CLOSE': [105.0, 210.0],
            'VOLUME': [1000, 2000]
        })
        
        bse_index_data = pd.DataFrame({
            'SYMBOL': ['BSE SENSEX', 'BSE 100'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [81594.52, 26097.61],
            'HIGH': [81618.96, 26103.71],
            'LOW': [81187.06, 25979.28],
            'CLOSE': [81481.86, 26064.32],
            'VOLUME': [0, 0]
        })
        
        print(f"  📊 Test BSE EQ data: {bse_eq_data.shape}")
        print(f"  📊 Test BSE INDEX data: {bse_index_data.shape}")
        
        # Store data
        manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
        manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
        
        # Check if data is stored
        available_data = manager.get_available_data_types(test_date)
        print(f"  📋 Available after storage: {available_data}")
        
        # Try append operation
        result = manager._try_bse_eq_append(test_date)
        print(f"  🔄 BSE append result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ Manual test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_bse_append_option_in_memory_manager():
    """Check if BSE append option is properly checked in memory manager"""
    print("\n🔍 Checking BSE Append Option Logic...")
    
    try:
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        
        config = Config()
        manager = MemoryAppendManager(config)
        
        # Check if the method exists
        if hasattr(manager, 'is_append_enabled'):
            bse_append_enabled = manager.is_append_enabled('bse_index_append_to_eq')
            print(f"  BSE append enabled (via is_append_enabled): {bse_append_enabled}")
        else:
            print("  ❌ is_append_enabled method not found")
        
        # Check config fallback
        download_options = config.get_download_options()
        config_bse_append = download_options.get('bse_index_append_to_eq', False)
        print(f"  BSE append in config: {config_bse_append}")
        
        return bse_append_enabled if 'bse_append_enabled' in locals() else False
        
    except Exception as e:
        print(f"  ❌ Error checking append option: {e}")
        return False

def main():
    """Main debugging function"""
    print("🔍 BSE Append Issue Debugging")
    print("=" * 40)
    
    # Step 1: Check user preferences
    bse_pref_enabled = check_user_preferences()
    
    # Step 2: Check memory append manager
    manager, has_bse_eq, has_bse_index = check_memory_append_manager()
    
    # Step 3: Check append option logic
    append_option_enabled = check_bse_append_option_in_memory_manager()
    
    # Step 4: Manual test
    manual_test_result = test_bse_append_manually()
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 BSE Append Debug Summary:")
    print(f"  User Preferences BSE Append: {'✅ ENABLED' if bse_pref_enabled else '❌ DISABLED'}")
    print(f"  Has BSE EQ Data: {'✅ YES' if has_bse_eq else '❌ NO'}")
    print(f"  Has BSE INDEX Data: {'✅ YES' if has_bse_index else '❌ NO'}")
    print(f"  Append Option Logic: {'✅ ENABLED' if append_option_enabled else '❌ DISABLED'}")
    print(f"  Manual Test Result: {'✅ SUCCESS' if manual_test_result else '❌ FAILED'}")
    
    # Diagnosis
    if not bse_pref_enabled:
        print("\n🎯 DIAGNOSIS: BSE append option is DISABLED in user preferences")
        print("   SOLUTION: Enable 'Add BSE Index data to BSE EQ file' option in GUI")
    elif not (has_bse_eq and has_bse_index):
        print("\n🎯 DIAGNOSIS: BSE data is not available in memory")
        print("   SOLUTION: Ensure both BSE EQ and BSE INDEX are downloaded")
    elif not append_option_enabled:
        print("\n🎯 DIAGNOSIS: Append option logic is not working")
        print("   SOLUTION: Check is_append_enabled method implementation")
    elif manual_test_result:
        print("\n🎯 DIAGNOSIS: BSE append logic works fine")
        print("   ISSUE: Might be in the trigger sequence or data availability timing")
    else:
        print("\n🎯 DIAGNOSIS: Unknown issue in BSE append logic")
        print("   SOLUTION: Check detailed logs for specific error")

if __name__ == "__main__":
    main()
