#!/usr/bin/env python3
"""
Detailed debugging of BSE append operation
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
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

def simulate_bse_append_with_real_data():
    """Simulate BSE append with real file data"""
    print("🔍 Simulating BSE Append with Real Data...")
    
    try:
        # Read actual BSE files
        bse_eq_file = "/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt"
        bse_index_file = "/home/manisha/NSE_BSE_Data/BSE/INDEX/2025-07-30-BSE-INDEX.txt"
        
        if not Path(bse_eq_file).exists():
            print(f"  ❌ BSE EQ file not found: {bse_eq_file}")
            return False
            
        if not Path(bse_index_file).exists():
            print(f"  ❌ BSE INDEX file not found: {bse_index_file}")
            return False
        
        print(f"  ✅ BSE EQ file exists: {bse_eq_file}")
        print(f"  ✅ BSE INDEX file exists: {bse_index_file}")
        
        # Read file contents
        with open(bse_eq_file, 'r') as f:
            eq_lines = f.readlines()
        
        with open(bse_index_file, 'r') as f:
            index_lines = f.readlines()
        
        print(f"  📊 BSE EQ lines: {len(eq_lines)}")
        print(f"  📊 BSE INDEX lines: {len(index_lines)}")
        
        # Check if INDEX data is already in EQ file
        eq_content = ''.join(eq_lines)
        index_sample = index_lines[0].strip() if index_lines else ""
        
        if "BSE SENSEX" in eq_content:
            print("  ⚠️ BSE INDEX data already appears to be in EQ file")
            return True
        else:
            print("  ❌ BSE INDEX data NOT found in EQ file")
            print(f"  📄 INDEX sample: {index_sample}")
            return False
        
    except Exception as e:
        print(f"  ❌ Error reading files: {e}")
        return False

def test_memory_append_manager_directly():
    """Test memory append manager directly with mock data"""
    print("\n🔍 Testing Memory Append Manager Directly...")
    
    try:
        # Mock pandas for testing
        class MockDataFrame:
            def __init__(self, data):
                self.data = data
                self.columns = list(data.keys()) if data else []
                self.shape = (len(list(data.values())[0]) if data else 0, len(self.columns))
            
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
        
        # Mock pandas module
        class MockPandas:
            DataFrame = MockDataFrame
            
            @staticmethod
            def concat(dataframes, ignore_index=True, sort=False):
                # Simple concat simulation
                if len(dataframes) == 2:
                    df1, df2 = dataframes
                    combined_data = {}
                    for col in df1.columns:
                        combined_data[col] = df1.data.get(col, []) + df2.data.get(col, [])
                    return MockDataFrame(combined_data)
                return dataframes[0] if dataframes else MockDataFrame({})
        
        # Patch pandas
        sys.modules['pandas'] = MockPandas()
        
        # Now import and test
        from src.core.config import Config
        from src.services.memory_append_manager import MemoryAppendManager
        
        config = Config()
        manager = MemoryAppendManager(config)
        test_date = date(2025, 7, 30)
        
        print(f"  ✅ Memory append manager created")
        
        # Create mock BSE data
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
        
        print(f"  📊 Mock BSE EQ data: {bse_eq_data.shape}")
        print(f"  📊 Mock BSE INDEX data: {bse_index_data.shape}")
        
        # Store data
        manager.store_data('BSE', 'EQ', test_date, bse_eq_data)
        manager.store_data('BSE', 'INDEX', test_date, bse_index_data)
        
        # Check available data
        available_data = manager.get_available_data_types(test_date)
        print(f"  📋 Available data: {available_data}")
        
        # Check BSE append option
        bse_append_enabled = manager.is_append_enabled('bse_index_append_to_eq')
        print(f"  ⚙️ BSE append enabled: {bse_append_enabled}")
        
        # Try BSE append
        if 'BSE_EQ' in available_data:
            print("  🔄 Attempting BSE append...")
            result = manager._try_bse_eq_append(test_date)
            print(f"  📊 BSE append result: {'✅ SUCCESS' if result else '❌ FAILED'}")
            return result
        else:
            print("  ❌ BSE_EQ not in available data")
            return False
        
    except Exception as e:
        print(f"  ❌ Error in direct test: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_append_operation_logs():
    """Check if there are any logs from append operations"""
    print("\n🔍 Checking Append Operation Logs...")
    
    log_files = [
        "downloader.log",
        "nse_bse_downloader.log",
        "bse_flow_trace.log",
        "sme_suffix_test.log"
    ]
    
    for log_file in log_files:
        log_path = Path(log_file)
        if log_path.exists():
            print(f"  📄 Found log file: {log_file}")
            try:
                with open(log_path, 'r') as f:
                    content = f.read()
                
                # Look for BSE append related messages
                bse_messages = []
                for line in content.split('\n'):
                    if 'bse' in line.lower() and ('append' in line.lower() or 'index' in line.lower()):
                        bse_messages.append(line.strip())
                
                if bse_messages:
                    print(f"    📋 BSE append messages found:")
                    for msg in bse_messages[-5:]:  # Last 5 messages
                        print(f"      {msg}")
                else:
                    print(f"    ❌ No BSE append messages found")
                    
            except Exception as e:
                print(f"    ❌ Error reading {log_file}: {e}")
        else:
            print(f"  ❌ Log file not found: {log_file}")

def main():
    """Main debugging function"""
    print("🔍 Detailed BSE Append Debugging")
    print("=" * 40)
    
    setup_logging()
    
    # Test 1: Check real file data
    real_data_result = simulate_bse_append_with_real_data()
    
    # Test 2: Test memory append manager directly
    direct_test_result = test_memory_append_manager_directly()
    
    # Test 3: Check logs
    check_append_operation_logs()
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 Detailed Debug Summary:")
    print(f"  Real File Check: {'✅ INDEX in EQ' if real_data_result else '❌ INDEX NOT in EQ'}")
    print(f"  Direct Manager Test: {'✅ SUCCESS' if direct_test_result else '❌ FAILED'}")
    
    # Final diagnosis
    if real_data_result:
        print("\n🎯 DIAGNOSIS: BSE INDEX data is already in EQ file!")
        print("   This means the append operation is actually working.")
        print("   The issue might be that you're looking at the wrong date or file.")
    elif direct_test_result:
        print("\n🎯 DIAGNOSIS: Memory append manager works in isolation")
        print("   The issue might be in the actual data flow or file operations.")
    else:
        print("\n🎯 DIAGNOSIS: Memory append manager has issues")
        print("   Check the error messages above for specific problems.")

if __name__ == "__main__":
    main()
