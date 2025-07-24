#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Platform Independent BSE EQ Download Test Script
Based on original working Getbhavcopy_BSE_Eq.py

Tests BSE EQ downloads with original working logic
Downloads to home folder for Linux compatibility
"""

import os
import sys
import socket
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Suppress only the single InsecureRequestWarning from urllib3
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def Download_BSE_Bhavcopy_File(BSE_Bhavcopy_URL, Bhavcopy_Download_Folder):
    """Download a file from the given URL to the specified output folder"""
    filename = os.path.basename(BSE_Bhavcopy_URL)
    output_path = os.path.join(Bhavcopy_Download_Folder, filename)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    print(f"🔍 BSE EQ Download Test:")
    print(f"  URL: {BSE_Bhavcopy_URL}")
    print(f"  Output: {output_path}")
    print(f"  Headers: {headers}")
    print(f"  SSL Verify: False")
    
    try:
        with requests.get(BSE_Bhavcopy_URL, stream=True, headers=headers, verify=False, timeout=10) as response:
            print(f"  Response Status: {response.status_code}")
            print(f"  Response Reason: {response.reason}")
            print(f"  Response Headers: {dict(response.headers)}")
            
            response.raise_for_status()  # Check if the request was successful
            
            with open(output_path, 'wb') as file:
                total_size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        total_size += len(chunk)
        
        print(f'✅ BSE EQ file {filename} downloaded successfully.')
        print(f'   File size: {total_size} bytes')
        print(f'   Saved to: {output_path}')
        return output_path
    
    except requests.exceptions.RequestException as e:
        print(f'❌ BSE EQ download failed: {e}')
        print(f'   URL: {BSE_Bhavcopy_URL}')
        return None

def build_bse_eq_url(target_date):
    """Build BSE EQ URL for given date"""
    date_str = target_date.strftime('%Y%m%d')
    filename = f'BhavCopy_BSE_CM_0_0_0_{date_str}_F_0000.CSV'
    url = f'https://www.bseindia.com/download/BhavCopy/Equity/{filename}'
    return url

def test_bse_eq_download():
    """Test BSE EQ download with original working logic"""
    print("=" * 60)
    print("BSE EQ Download Test - Platform Independent")
    print("=" * 60)
    
    # Create download folder in home directory
    home_dir = Path.home()
    download_folder = home_dir / "BSE_EQ_Test_Downloads"
    download_folder.mkdir(exist_ok=True)
    
    print(f"Download folder: {download_folder}")
    
    # Test dates that were failing
    test_dates = [
        datetime(2025, 1, 2).date(),
        datetime(2025, 1, 9).date(),
        datetime(2025, 4, 9).date(),
        datetime(2025, 7, 23).date(),  # Recent date
    ]
    
    results = []
    
    for test_date in test_dates:
        print(f"\n📅 Testing date: {test_date}")
        print("-" * 40)
        
        # Build URL
        url = build_bse_eq_url(test_date)
        
        # Download file
        result = Download_BSE_Bhavcopy_File(url, str(download_folder))
        
        if result:
            # Check file content
            try:
                # Read first few lines to verify CSV format
                with open(result, 'r') as f:
                    first_line = f.readline().strip()
                    print(f"   First line: {first_line}")
                
                # Get file size
                file_size = os.path.getsize(result)
                print(f"   File size: {file_size} bytes")
                
                results.append({
                    'date': test_date,
                    'url': url,
                    'status': 'SUCCESS',
                    'file_path': result,
                    'file_size': file_size
                })
                
            except Exception as e:
                print(f"   ⚠️ File read error: {e}")
                results.append({
                    'date': test_date,
                    'url': url,
                    'status': 'DOWNLOADED_BUT_INVALID',
                    'error': str(e)
                })
        else:
            results.append({
                'date': test_date,
                'url': url,
                'status': 'FAILED'
            })
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    success_count = 0
    for result in results:
        status_icon = "✅" if result['status'] == 'SUCCESS' else "❌"
        print(f"{status_icon} {result['date']}: {result['status']}")
        if result['status'] == 'SUCCESS':
            success_count += 1
            print(f"   File: {result['file_path']}")
            print(f"   Size: {result['file_size']} bytes")
    
    print(f"\nSuccess Rate: {success_count}/{len(test_dates)} ({success_count/len(test_dates)*100:.1f}%)")
    
    if success_count > 0:
        print(f"\n✅ BSE EQ downloads are working with original logic!")
        print(f"   Downloaded files are in: {download_folder}")
    else:
        print(f"\n❌ All BSE EQ downloads failed - need different approach")
    
    return results

if __name__ == "__main__":
    print("Starting BSE EQ Download Test...")
    results = test_bse_eq_download()
    
    print(f"\nTest completed. Check results above.")
    print(f"Download folder: {Path.home() / 'BSE_EQ_Test_Downloads'}")
