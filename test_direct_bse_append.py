#!/usr/bin/env python3
"""
Test direct BSE append method
"""

import sys
from pathlib import Path
from datetime import date

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_direct_bse_append():
    """Test the direct BSE append method"""
    print("🔧 Testing Direct BSE Append Method")
    print("=" * 35)
    
    try:
        # Mock dependencies
        sys.modules['aiohttp'] = type('MockModule', (), {})()
        sys.modules['pandas'] = type('MockModule', (), {'DataFrame': type})()
        sys.modules['numpy'] = type('MockModule', (), {})()
        
        # Import after mocking
        from src.core.config import Config
        from src.downloaders.bse_eq_downloader import BSEEQDownloader
        
        # Setup
        config = Config()
        downloader = BSEEQDownloader(config)
        test_date = date(2025, 7, 30)
        
        # File paths
        bse_eq_file = Path("/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt")
        bse_index_file = Path("/home/manisha/NSE_BSE_Data/BSE/INDEX/2025-07-30-BSE-INDEX.txt")
        
        print(f"  📊 BSE EQ file: {bse_eq_file.exists()}")
        print(f"  📊 BSE INDEX file: {bse_index_file.exists()}")
        
        # Check initial state
        with open(bse_eq_file, 'r') as f:
            initial_lines = len(f.readlines())
        
        with open(bse_index_file, 'r') as f:
            index_lines = len(f.readlines())
        
        print(f"  📊 Initial BSE EQ lines: {initial_lines}")
        print(f"  📊 BSE INDEX lines: {index_lines}")
        
        # Test direct append method
        print("\n  🔄 Testing direct BSE append...")
        downloader._try_direct_bse_append(test_date, bse_eq_file)
        
        # Check final state
        with open(bse_eq_file, 'r') as f:
            final_lines = len(f.readlines())
        
        print(f"  📊 Final BSE EQ lines: {final_lines}")
        print(f"  📊 Expected: {initial_lines + index_lines}")
        
        # Verify append
        success = final_lines == initial_lines + index_lines
        print(f"  📊 Direct append result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        
        if success:
            # Show last few lines
            with open(bse_eq_file, 'r') as f:
                lines = f.readlines()
            
            print("\n  📄 Last 3 lines of appended file:")
            for line in lines[-3:]:
                print(f"    {line.strip()}")
        
        return success
        
    except Exception as e:
        print(f"  ❌ Error in direct append test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_user_preferences_check():
    """Test if user preferences are properly checked"""
    print("\n🔍 Testing User Preferences Check...")
    
    try:
        # Mock dependencies
        sys.modules['aiohttp'] = type('MockModule', (), {})()
        
        from src.utils.user_preferences import UserPreferences
        
        user_prefs = UserPreferences()
        bse_append_enabled = user_prefs.get_bse_index_append_to_eq()
        
        print(f"  📋 BSE Index append enabled: {bse_append_enabled}")
        
        if not bse_append_enabled:
            print("  ⚠️ BSE append is disabled - enabling it...")
            user_prefs.set_bse_index_append_to_eq(True)
            
            # Verify
            bse_append_enabled = user_prefs.get_bse_index_append_to_eq()
            print(f"  📋 BSE Index append after enable: {bse_append_enabled}")
        
        return bse_append_enabled
        
    except Exception as e:
        print(f"  ❌ Error checking user preferences: {e}")
        return False

def main():
    """Main testing function"""
    print("🔧 Direct BSE Append Test Suite")
    print("=" * 40)
    
    # Test 1: Check user preferences
    prefs_result = test_user_preferences_check()
    
    # Test 2: Test direct append method
    append_result = test_direct_bse_append()
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 Test Results:")
    print(f"  User Preferences: {'✅ ENABLED' if prefs_result else '❌ DISABLED'}")
    print(f"  Direct Append: {'✅ SUCCESS' if append_result else '❌ FAILED'}")
    
    if append_result:
        print("\n🎉 Direct BSE Append Method WORKS!")
        print("   This should solve the BSE append issue.")
        print("   The method will be called automatically during BSE EQ downloads.")
    else:
        print("\n⚠️ Direct BSE Append Method needs debugging")
        print("   Check the error messages above.")

if __name__ == "__main__":
    main()
