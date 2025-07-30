#!/usr/bin/env python3
"""
Manual BSE append test - direct file manipulation to verify the concept
"""

import sys
from pathlib import Path
from datetime import date

def manual_bse_append():
    """Manually append BSE INDEX data to BSE EQ file"""
    print("🔧 Manual BSE Append Test")
    print("=" * 30)
    
    # File paths
    bse_eq_file = Path("/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt")
    bse_index_file = Path("/home/manisha/NSE_BSE_Data/BSE/INDEX/2025-07-30-BSE-INDEX.txt")
    backup_file = Path("/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ-BACKUP.txt")
    
    # Check if files exist
    if not bse_eq_file.exists():
        print(f"❌ BSE EQ file not found: {bse_eq_file}")
        return False
    
    if not bse_index_file.exists():
        print(f"❌ BSE INDEX file not found: {bse_index_file}")
        return False
    
    try:
        # Read original files
        with open(bse_eq_file, 'r') as f:
            eq_lines = f.readlines()
        
        with open(bse_index_file, 'r') as f:
            index_lines = f.readlines()
        
        print(f"📊 Original BSE EQ lines: {len(eq_lines)}")
        print(f"📊 BSE INDEX lines: {len(index_lines)}")
        
        # Create backup
        with open(backup_file, 'w') as f:
            f.writelines(eq_lines)
        print(f"✅ Backup created: {backup_file}")
        
        # Check if INDEX data is already appended
        eq_content = ''.join(eq_lines)
        if "BSE SENSEX" in eq_content:
            print("⚠️ BSE INDEX data already appears to be in EQ file")
            return True
        
        # Append INDEX data to EQ data
        combined_lines = eq_lines + index_lines
        
        # Write combined data back to EQ file
        with open(bse_eq_file, 'w') as f:
            f.writelines(combined_lines)
        
        print(f"✅ Manual append completed")
        print(f"📊 Final BSE EQ lines: {len(combined_lines)}")
        print(f"📊 Expected: {len(eq_lines)} + {len(index_lines)} = {len(eq_lines) + len(index_lines)}")
        
        # Verify the append
        with open(bse_eq_file, 'r') as f:
            final_lines = f.readlines()
        
        if len(final_lines) == len(eq_lines) + len(index_lines):
            print("✅ Manual append verification successful")
            
            # Show last few lines
            print("\n📄 Last 5 lines of combined file:")
            for line in final_lines[-5:]:
                print(f"  {line.strip()}")
            
            return True
        else:
            print("❌ Manual append verification failed")
            return False
        
    except Exception as e:
        print(f"❌ Error in manual append: {e}")
        return False

def check_file_permissions():
    """Check file permissions"""
    print("\n🔍 Checking File Permissions...")
    
    files_to_check = [
        "/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt",
        "/home/manisha/NSE_BSE_Data/BSE/INDEX/2025-07-30-BSE-INDEX.txt"
    ]
    
    for file_path in files_to_check:
        path = Path(file_path)
        if path.exists():
            stat = path.stat()
            print(f"  {path.name}: Size={stat.st_size}, Mode={oct(stat.st_mode)}")
        else:
            print(f"  {path.name}: NOT FOUND")

def compare_nse_vs_bse_structure():
    """Compare NSE vs BSE file structure"""
    print("\n🔍 Comparing NSE vs BSE Structure...")
    
    # NSE files
    nse_eq_file = "/home/manisha/NSE_BSE_Data/NSE/EQ/2025-07-30-NSE-EQ.txt"
    nse_index_file = "/home/manisha/NSE_BSE_Data/NSE/INDEX/2025-07-30-NSE-INDEX.txt"
    
    # BSE files
    bse_eq_file = "/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt"
    bse_index_file = "/home/manisha/NSE_BSE_Data/BSE/INDEX/2025-07-30-BSE-INDEX.txt"
    
    files = {
        "NSE EQ": nse_eq_file,
        "NSE INDEX": nse_index_file,
        "BSE EQ": bse_eq_file,
        "BSE INDEX": bse_index_file
    }
    
    for name, file_path in files.items():
        path = Path(file_path)
        if path.exists():
            with open(path, 'r') as f:
                lines = f.readlines()
            
            # Check first line structure
            first_line = lines[0].strip() if lines else ""
            columns = first_line.split(',')
            
            print(f"  {name}:")
            print(f"    Lines: {len(lines)}")
            print(f"    Columns: {len(columns)}")
            print(f"    Sample: {first_line[:50]}...")
            
            # Check if INDEX data is in EQ file
            if "EQ" in name:
                content = ''.join(lines)
                has_index_data = "SENSEX" in content or "Nifty" in content
                print(f"    Has Index Data: {'✅ YES' if has_index_data else '❌ NO'}")
        else:
            print(f"  {name}: NOT FOUND")

def restore_backup():
    """Restore backup if needed"""
    print("\n🔧 Restore Options...")
    
    bse_eq_file = Path("/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ.txt")
    backup_file = Path("/home/manisha/NSE_BSE_Data/BSE/EQ/2025-07-30-BSE-EQ-BACKUP.txt")
    
    if backup_file.exists():
        print(f"  Backup available: {backup_file}")
        print("  To restore: cp {backup_file} {bse_eq_file}")
    else:
        print("  No backup found")

def main():
    """Main function"""
    print("🔧 Direct BSE Append Investigation")
    print("=" * 40)
    
    # Step 1: Check file permissions
    check_file_permissions()
    
    # Step 2: Compare NSE vs BSE structure
    compare_nse_vs_bse_structure()
    
    # Step 3: Manual append test
    manual_result = manual_bse_append()
    
    # Step 4: Show restore options
    restore_backup()
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 Investigation Results:")
    print(f"  Manual Append: {'✅ SUCCESS' if manual_result else '❌ FAILED'}")
    
    if manual_result:
        print("\n🎯 CONCLUSION: Manual append works!")
        print("   This proves the concept is sound.")
        print("   The issue is in the automatic append logic.")
        print("   Focus on why the automatic system isn't triggering.")
    else:
        print("\n🎯 CONCLUSION: Even manual append fails!")
        print("   This suggests a fundamental issue with file access or structure.")

if __name__ == "__main__":
    main()
