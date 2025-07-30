#!/usr/bin/env python3
"""
Test BSE append functionality after fixes
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
            logging.FileHandler('bse_append_test.log')
        ]
    )

def test_bse_column_structure():
    """Test BSE Index column structure after fix"""
    print("🧪 Testing BSE Index column structure...")
    
    try:
        import pandas as pd
        from src.downloaders.bse_index_downloader import BSEIndexDownloader
        from src.core.config import Config
        
        # Setup
        config = Config()
        downloader = BSEIndexDownloader(config)
        test_date = date.today()
        
        # Create test BSE Index data (original format)
        test_data = pd.DataFrame({
            'IndexName': ['BSE SENSEX', 'BSE 100'],
            'OpenPrice': [81594.52, 26097.61],
            'HighPrice': [81618.96, 26103.71],
            'LowPrice': [81187.06, 25979.28],
            'ClosePrice': [81481.86, 26064.32]
        })
        
        print(f"  Original BSE Index data columns: {list(test_data.columns)}")
        
        # Transform data
        transformed_data = downloader.transform_data(test_data, test_date)
        
        print(f"  Transformed BSE Index data columns: {list(transformed_data.columns)}")
        print(f"  Expected: ['IndexName', 'Date', 'OpenPrice', 'HighPrice', 'LowPrice', 'ClosePrice', 'Volume']")
        
        # Check if Volume column exists
        has_volume = 'Volume' in transformed_data.columns
        print(f"  Volume column present: {'✅ YES' if has_volume else '❌ NO'}")
        
        # Check column count
        expected_columns = 7  # IndexName, Date, OpenPrice, HighPrice, LowPrice, ClosePrice, Volume
        actual_columns = len(transformed_data.columns)
        print(f"  Column count: {actual_columns} (expected: {expected_columns})")
        
        # Show sample data
        if len(transformed_data) > 0:
            print(f"  Sample row: {transformed_data.iloc[0].to_dict()}")
        
        return has_volume and actual_columns == expected_columns
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_bse_append_logic():
    """Test BSE append logic with column alignment"""
    print("🧪 Testing BSE append logic...")
    
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
        
        # Enable BSE append
        user_prefs.set_append_options({
            "sme_add_suffix": False,
            "sme_append_to_eq": False,
            "index_append_to_eq": False,
            "bse_index_append_to_eq": True
        })
        
        # Create test BSE EQ data (7 columns with Volume)
        bse_eq_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE', 'TCS'],
            'DATE': ['20250730', '20250730'],
            'OPEN': [2500.00, 3500.00],
            'HIGH': [2550.00, 3550.00],
            'LOW': [2480.00, 3480.00],
            'CLOSE': [2530.00, 3520.00],
            'VOLUME': [1000000, 800000]
        })
        
        # Create test BSE INDEX data (7 columns with Volume = 0)
        bse_index_data = pd.DataFrame({
            'IndexName': ['BSE SENSEX', 'BSE 100'],
            'Date': ['20250730', '20250730'],
            'OpenPrice': [81594.52, 26097.61],
            'HighPrice': [81618.96, 26103.71],
            'LowPrice': [81187.06, 25979.28],
            'ClosePrice': [81481.86, 26064.32],
            'Volume': [0, 0]
        })
        
        print(f"  BSE EQ columns: {list(bse_eq_data.columns)}")
        print(f"  BSE INDEX columns: {list(bse_index_data.columns)}")
        
        # Store data
        manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
        manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
        
        # Check available data
        available_data = manager.get_available_data_types(test_date)
        print(f"  Available data types: {available_data}")
        
        # Test append
        result = manager._try_bse_eq_append(test_date)
        print(f"  BSE append result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_column_alignment():
    """Test column alignment logic specifically for BSE"""
    print("🧪 Testing BSE column alignment...")
    
    try:
        import pandas as pd
        from src.services.memory_append_manager import MemoryAppendManager
        from src.core.config import Config
        
        # Setup
        config = Config()
        manager = MemoryAppendManager(config)
        
        # Create BSE EQ data (standard format)
        base_data = pd.DataFrame({
            'SYMBOL': ['RELIANCE'],
            'DATE': ['20250730'],
            'OPEN': [2500.00],
            'HIGH': [2550.00],
            'LOW': [2480.00],
            'CLOSE': [2530.00],
            'VOLUME': [1000000]
        })
        
        # Create BSE INDEX data (with Volume column)
        append_data = pd.DataFrame({
            'IndexName': ['BSE SENSEX'],
            'Date': ['20250730'],
            'OpenPrice': [81594.52],
            'HighPrice': [81618.96],
            'LowPrice': [81187.06],
            'ClosePrice': [81481.86],
            'Volume': [0]
        })
        
        print(f"  Base data columns: {list(base_data.columns)}")
        print(f"  Append data columns: {list(append_data.columns)}")
        print(f"  Column count match: {len(base_data.columns) == len(append_data.columns)}")
        
        # Test alignment
        aligned_data = manager._align_columns_for_append(append_data, base_data)
        
        print(f"  Aligned data columns: {list(aligned_data.columns)}")
        print(f"  Aligned data rows: {len(aligned_data)}")
        
        if len(aligned_data) > 0:
            print(f"  Sample aligned row: {aligned_data.iloc[0].to_dict()}")
            return True
        else:
            print("  ❌ No data after alignment")
            return False
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all BSE append tests"""
    print("🧪 Testing BSE Append Functionality After Fixes...")
    setup_logging()
    
    try:
        # Check if pandas is available
        try:
            import pandas as pd
            print(f"✅ Pandas available: {pd.__version__}")
        except ImportError:
            print("❌ Pandas not available - tests will fail")
            print("   Please install pandas: pip install pandas")
            return
        
        # Test 1: BSE Index column structure
        structure_result = test_bse_column_structure()
        
        # Test 2: Column alignment logic
        alignment_result = test_column_alignment()
        
        # Test 3: Full BSE append logic
        append_result = test_bse_append_logic()
        
        print("\n📊 Results Summary:")
        print(f"  BSE Index column structure: {'✅ PASS' if structure_result else '❌ FAIL'}")
        print(f"  Column alignment logic: {'✅ PASS' if alignment_result else '❌ FAIL'}")
        print(f"  BSE append functionality: {'✅ PASS' if append_result else '❌ FAIL'}")
        
        if all([structure_result, alignment_result, append_result]):
            print("\n🎉 All BSE append tests PASSED!")
            print("   BSE Index data should now append to BSE EQ files correctly.")
        else:
            print("\n⚠️ Some BSE append tests FAILED!")
            print("   Check the logs for detailed error information.")
            
    except Exception as e:
        print(f"❌ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
