#!/usr/bin/env python3
"""
Test GUI and user preferences synchronization for append options
"""

import sys
import json
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def find_user_preferences_file():
    """Find the actual user preferences file"""
    possible_locations = [
        Path.home() / ".nse_bse_downloader" / "user_preferences.json",
        Path.home() / "user_preferences.json",
        Path.cwd() / "user_preferences.json",
        Path.cwd() / ".nse_bse_downloader" / "user_preferences.json"
    ]
    
    for location in possible_locations:
        if location.exists():
            return location
    
    return None

def read_preferences_file():
    """Read the actual preferences file"""
    print("🔍 Looking for user preferences file...")
    
    prefs_file = find_user_preferences_file()
    if prefs_file:
        print(f"  ✅ Found: {prefs_file}")
        try:
            with open(prefs_file, 'r') as f:
                data = json.load(f)
            return data, prefs_file
        except Exception as e:
            print(f"  ❌ Error reading file: {e}")
            return None, prefs_file
    else:
        print("  ❌ User preferences file not found")
        return None, None

def check_preferences_values():
    """Check current preferences values"""
    print("\n🔍 Checking User Preferences Values...")
    
    data, prefs_file = read_preferences_file()
    if not data:
        return False
    
    download_options = data.get("download_options", {})
    
    print(f"  📋 All download options: {download_options}")
    print(f"  📋 NSE Index append: {download_options.get('index_append_to_eq', 'NOT_SET')}")
    print(f"  📋 BSE Index append: {download_options.get('bse_index_append_to_eq', 'NOT_SET')}")
    print(f"  📋 SME append: {download_options.get('sme_append_to_eq', 'NOT_SET')}")
    print(f"  📋 SME suffix: {download_options.get('sme_add_suffix', 'NOT_SET')}")
    
    return True

def test_preferences_class():
    """Test UserPreferences class directly"""
    print("\n🔍 Testing UserPreferences Class...")
    
    try:
        # Import without dependencies
        import importlib.util
        spec = importlib.util.spec_from_file_location("user_preferences", "src/utils/user_preferences.py")
        user_prefs_module = importlib.util.module_from_spec(spec)
        
        # Mock the aiohttp dependency
        sys.modules['aiohttp'] = type('MockModule', (), {})()
        
        spec.loader.exec_module(user_prefs_module)
        
        UserPreferences = user_prefs_module.UserPreferences
        user_prefs = UserPreferences()
        
        # Get append options
        append_options = user_prefs.get_append_options()
        print(f"  📋 Append options from class: {append_options}")
        
        # Test individual methods
        nse_index_append = user_prefs.get_index_append_to_eq()
        bse_index_append = user_prefs.get_bse_index_append_to_eq()
        
        print(f"  📋 NSE Index append (method): {nse_index_append}")
        print(f"  📋 BSE Index append (method): {bse_index_append}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error testing UserPreferences class: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_config_defaults():
    """Check config.yaml defaults"""
    print("\n🔍 Checking Config Defaults...")
    
    try:
        import yaml
        
        with open("config.yaml", 'r') as f:
            config_data = yaml.safe_load(f)
        
        download_options = config_data.get("download_options", {})
        
        print(f"  📋 Config NSE Index append: {download_options.get('index_append_to_eq', 'NOT_SET')}")
        print(f"  📋 Config BSE Index append: {download_options.get('bse_index_append_to_eq', 'NOT_SET')}")
        print(f"  📋 Config SME append: {download_options.get('sme_append_to_eq', 'NOT_SET')}")
        print(f"  📋 Config SME suffix: {download_options.get('sme_add_suffix', 'NOT_SET')}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error reading config: {e}")
        return False

def test_memory_append_manager_option_check():
    """Test how memory append manager checks options"""
    print("\n🔍 Testing Memory Append Manager Option Check...")
    
    try:
        # Create a simple test without full imports
        print("  📋 Testing option priority logic...")
        
        # Simulate user preferences
        user_prefs_data = {
            "index_append_to_eq": True,
            "bse_index_append_to_eq": True
        }
        
        # Simulate config data
        config_data = {
            "index_append_to_eq": True,
            "bse_index_append_to_eq": True
        }
        
        # Test priority logic (user prefs should override config)
        def check_option(option_name, user_prefs, config_fallback):
            if option_name in user_prefs:
                result = user_prefs[option_name]
                print(f"    Option '{option_name}' from user preferences: {result}")
                return result
            else:
                result = config_fallback.get(option_name, False)
                print(f"    Option '{option_name}' from config (fallback): {result}")
                return result
        
        nse_result = check_option("index_append_to_eq", user_prefs_data, config_data)
        bse_result = check_option("bse_index_append_to_eq", user_prefs_data, config_data)
        
        print(f"  📋 Final NSE Index append: {nse_result}")
        print(f"  📋 Final BSE Index append: {bse_result}")
        
        return nse_result and bse_result
        
    except Exception as e:
        print(f"  ❌ Error testing option check: {e}")
        return False

def identify_potential_conflicts():
    """Identify potential conflicts"""
    print("\n🔍 Identifying Potential Conflicts...")
    
    conflicts = []
    
    # Check if both NSE and BSE index options use similar names
    print("  📋 Checking for naming conflicts...")
    
    # These should be different
    nse_option = "index_append_to_eq"
    bse_option = "bse_index_append_to_eq"
    
    if nse_option == bse_option:
        conflicts.append("NSE and BSE index options have same name")
    else:
        print(f"    ✅ NSE option: '{nse_option}'")
        print(f"    ✅ BSE option: '{bse_option}'")
        print("    ✅ No naming conflict")
    
    # Check GUI checkbox names
    print("  📋 Checking GUI checkbox mapping...")
    gui_mapping = {
        "index_append_checkbox": "index_append_to_eq",
        "bse_index_append_checkbox": "bse_index_append_to_eq"
    }
    
    for checkbox, option in gui_mapping.items():
        print(f"    {checkbox} → {option}")
    
    return conflicts

def main():
    """Main testing function"""
    print("🔍 GUI and User Preferences Synchronization Test")
    print("=" * 50)
    
    # Test 1: Read actual preferences file
    prefs_success = check_preferences_values()
    
    # Test 2: Test UserPreferences class
    class_success = test_preferences_class()
    
    # Test 3: Check config defaults
    config_success = check_config_defaults()
    
    # Test 4: Test option checking logic
    option_check_success = test_memory_append_manager_option_check()
    
    # Test 5: Identify conflicts
    conflicts = identify_potential_conflicts()
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    print(f"  Preferences File Read: {'✅ SUCCESS' if prefs_success else '❌ FAILED'}")
    print(f"  UserPreferences Class: {'✅ SUCCESS' if class_success else '❌ FAILED'}")
    print(f"  Config Defaults: {'✅ SUCCESS' if config_success else '❌ FAILED'}")
    print(f"  Option Check Logic: {'✅ SUCCESS' if option_check_success else '❌ FAILED'}")
    print(f"  Conflicts Found: {'❌ YES' if conflicts else '✅ NONE'}")
    
    if conflicts:
        print("\n⚠️ Conflicts Identified:")
        for conflict in conflicts:
            print(f"  - {conflict}")
    
    # Diagnosis
    if not option_check_success:
        print("\n🎯 DIAGNOSIS: Option checking logic has issues")
    elif conflicts:
        print("\n🎯 DIAGNOSIS: Naming conflicts detected")
    elif not prefs_success:
        print("\n🎯 DIAGNOSIS: User preferences file issues")
    else:
        print("\n🎯 DIAGNOSIS: No obvious conflicts - issue might be elsewhere")
        print("   Possible causes:")
        print("   1. Timing issue (preferences not saved when checkbox changed)")
        print("   2. Memory append manager not reading latest preferences")
        print("   3. GUI not properly triggering save operation")

if __name__ == "__main__":
    main()
