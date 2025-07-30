#!/usr/bin/env python3
"""
Trace BSE data flow to identify where the append operation fails
"""

import sys
import logging
from pathlib import Path
from datetime import date

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def setup_detailed_logging():
    """Setup very detailed logging"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('bse_flow_trace.log')
        ]
    )

def check_dependencies():
    """Check if all dependencies are available"""
    print("🔍 Checking dependencies...")
    
    try:
        import pandas as pd
        print(f"✅ Pandas: {pd.__version__}")
        
        import aiohttp
        print(f"✅ aiohttp: {aiohttp.__version__}")
        
        from PyQt6.QtWidgets import QApplication
        print("✅ PyQt6: Available")
        
        return True
        
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        return False

def trace_bse_index_processing():
    """Trace BSE Index file processing"""
    print("\n📋 Step 1: BSE Index Processing")
    
    try:
        import pandas as pd
        from src.downloaders.bse_index_downloader import BSEIndexDownloader
        from src.core.config import Config
        
        config = Config()
        downloader = BSEIndexDownloader(config)
        test_date = date(2025, 7, 30)
        
        # Simulate BSE Index raw data
        raw_data = pd.DataFrame({
            'IndexCode': ['BSE001', 'BSE002'],
            'IndexID': ['SENSEX', 'BSE100'],
            'IndexName': ['BSE SENSEX', 'BSE 100'],
            'PreviousClose': [81400.00, 26000.00],
            'OpenPrice': [81594.52, 26097.61],
            'HighPrice': [81618.96, 26103.71],
            'LowPrice': [81187.06, 25979.28],
            'ClosePrice': [81481.86, 26064.32],
            '52weeksHigh': [85000.00, 27000.00],
            '52weeksLow': [75000.00, 24000.00],
            'Filler1': ['', ''],
            'Filler2': ['', ''],
            'Filler3': ['', ''],
            'Filler4': ['', '']
        })
        
        print(f"  📥 Raw BSE Index data: {raw_data.shape}")
        print(f"  📥 Raw columns: {list(raw_data.columns)}")
        
        # Transform data
        transformed_data = downloader.transform_data(raw_data, test_date)
        
        print(f"  🔄 Transformed BSE Index data: {transformed_data.shape}")
        print(f"  🔄 Transformed columns: {list(transformed_data.columns)}")
        
        if len(transformed_data) > 0:
            print(f"  📄 Sample row: {transformed_data.iloc[0].to_dict()}")
        
        return transformed_data
        
    except Exception as e:
        print(f"  ❌ BSE Index processing failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def trace_bse_eq_processing():
    """Trace BSE EQ file processing"""
    print("\n📋 Step 2: BSE EQ Processing")
    
    try:
        import pandas as pd
        from src.downloaders.bse_eq_downloader import BSEEQDownloader
        from src.core.config import Config
        
        config = Config()
        downloader = BSEEQDownloader(config)
        test_date = date(2025, 7, 30)
        
        # Simulate BSE EQ raw data
        raw_data = pd.DataFrame({
            'TckrSymb': ['RELIANCE', 'TCS', 'INFY'],
            'TradDt': ['2025-07-30', '2025-07-30', '2025-07-30'],
            'OpnPric': [2500.00, 3500.00, 1800.00],
            'HghPric': [2550.00, 3550.00, 1850.00],
            'LwPric': [2480.00, 3480.00, 1780.00],
            'ClsPric': [2530.00, 3520.00, 1820.00],
            'TtlTradgVol': [1000000, 800000, 1200000],
            'SctySrs': ['A', 'A', 'A'],
            'BizDt': ['2025-07-30', '2025-07-30', '2025-07-30'],
            'Sgmt': ['EQ', 'EQ', 'EQ']
        })
        
        print(f"  📥 Raw BSE EQ data: {raw_data.shape}")
        print(f"  📥 Raw columns: {list(raw_data.columns)}")
        
        # Transform data
        transformed_data = downloader.transform_data(raw_data, test_date)
        
        print(f"  🔄 Transformed BSE EQ data: {transformed_data.shape}")
        print(f"  🔄 Transformed columns: {list(transformed_data.columns)}")
        
        if len(transformed_data) > 0:
            print(f"  📄 Sample row: {transformed_data.iloc[0].to_dict()}")
        
        return transformed_data
        
    except Exception as e:
        print(f"  ❌ BSE EQ processing failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def trace_memory_storage(bse_index_data, bse_eq_data):
    """Trace memory storage process"""
    print("\n📋 Step 3: Memory Storage")
    
    try:
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        
        config = Config()
        manager = MemoryAppendManager(config)
        test_date = date(2025, 7, 30)
        
        # Store BSE Index data first (as it downloads first)
        if bse_index_data is not None:
            result1 = manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
            print(f"  💾 BSE Index storage result: {'✅ SUCCESS' if result1 else '❌ FAILED'}")
        
        # Store BSE EQ data
        if bse_eq_data is not None:
            result2 = manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
            print(f"  💾 BSE EQ storage result: {'✅ SUCCESS' if result2 else '❌ FAILED'}")
        
        # Check available data
        available_data = manager.get_available_data_types(test_date)
        print(f"  📋 Available data types: {available_data}")
        
        # Check if BSE data is accessible
        has_bse_eq = manager.has_data('BSE', 'EQ', test_date)
        has_bse_index = manager.has_data('BSE', 'INDEX', test_date)
        
        print(f"  🔍 Has BSE EQ data: {'✅ YES' if has_bse_eq else '❌ NO'}")
        print(f"  🔍 Has BSE INDEX data: {'✅ YES' if has_bse_index else '❌ NO'}")
        
        return manager, has_bse_eq and has_bse_index
        
    except Exception as e:
        print(f"  ❌ Memory storage failed: {e}")
        import traceback
        traceback.print_exc()
        return None, False

def trace_append_operation(manager):
    """Trace the append operation"""
    print("\n📋 Step 4: Append Operation")
    
    try:
        from src.utils.user_preferences import UserPreferences
        
        # Enable BSE append
        user_prefs = UserPreferences()
        user_prefs.set_append_options({
            "sme_add_suffix": False,
            "sme_append_to_eq": False,
            "index_append_to_eq": False,
            "bse_index_append_to_eq": True
        })
        
        print(f"  ⚙️ BSE Index append enabled: {user_prefs.get_bse_index_append_to_eq()}")
        
        test_date = date(2025, 7, 30)
        
        # Try BSE append operation
        result = manager._try_bse_eq_append(test_date)
        print(f"  🔄 BSE append operation result: {'✅ SUCCESS' if result else '❌ FAILED'}")
        
        return result
        
    except Exception as e:
        print(f"  ❌ Append operation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def trace_column_alignment(manager):
    """Trace column alignment specifically"""
    print("\n📋 Step 5: Column Alignment Analysis")
    
    try:
        test_date = date(2025, 7, 30)
        
        # Get stored data
        bse_eq_data = manager.get_data('BSE', 'EQ', test_date)
        bse_index_data = manager.get_data('BSE', 'INDEX', test_date)
        
        if bse_eq_data is None or bse_index_data is None:
            print("  ❌ Cannot get stored data for alignment test")
            return False
        
        print(f"  📊 BSE EQ columns: {list(bse_eq_data.columns)}")
        print(f"  📊 BSE INDEX columns: {list(bse_index_data.columns)}")
        print(f"  📊 Column count match: {len(bse_eq_data.columns) == len(bse_index_data.columns)}")
        
        # Test alignment
        aligned_data = manager._align_columns_for_append(bse_index_data, bse_eq_data)
        
        print(f"  🔄 Aligned data shape: {aligned_data.shape}")
        print(f"  🔄 Aligned columns: {list(aligned_data.columns)}")
        
        if len(aligned_data) > 0:
            print(f"  📄 Sample aligned row: {aligned_data.iloc[0].to_dict()}")
            return True
        else:
            print("  ❌ Alignment resulted in empty DataFrame")
            return False
        
    except Exception as e:
        print(f"  ❌ Column alignment failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main tracing function"""
    print("🔍 BSE Data Flow Detailed Tracing")
    print("=" * 50)
    
    setup_detailed_logging()
    
    # Check dependencies
    if not check_dependencies():
        print("❌ Dependencies not available - cannot proceed")
        return
    
    # Step 1: BSE Index processing
    bse_index_data = trace_bse_index_processing()
    
    # Step 2: BSE EQ processing  
    bse_eq_data = trace_bse_eq_processing()
    
    # Step 3: Memory storage
    manager, storage_success = trace_memory_storage(bse_index_data, bse_eq_data)
    
    if not storage_success or manager is None:
        print("❌ Memory storage failed - cannot proceed with append")
        return
    
    # Step 4: Column alignment analysis
    alignment_success = trace_column_alignment(manager)
    
    # Step 5: Append operation
    append_success = trace_append_operation(manager)
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 BSE Data Flow Summary:")
    print(f"  BSE Index Processing: {'✅ SUCCESS' if bse_index_data is not None else '❌ FAILED'}")
    print(f"  BSE EQ Processing: {'✅ SUCCESS' if bse_eq_data is not None else '❌ FAILED'}")
    print(f"  Memory Storage: {'✅ SUCCESS' if storage_success else '❌ FAILED'}")
    print(f"  Column Alignment: {'✅ SUCCESS' if alignment_success else '❌ FAILED'}")
    print(f"  Append Operation: {'✅ SUCCESS' if append_success else '❌ FAILED'}")
    
    if not append_success:
        print("\n⚠️ BSE Append Failed - Check the detailed logs above for the exact failure point")
    else:
        print("\n🎉 BSE Append Should Work - All steps successful")

if __name__ == "__main__":
    main()
