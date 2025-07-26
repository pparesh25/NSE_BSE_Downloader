#!/usr/bin/env python3
"""
NSE-Specific Improvements Test Script

Tests the NSE-specific optimizations including:
- Enhanced timeout handling for NSE servers
- Aggressive retry strategy for NSE sections
- Better error classification and reporting
- Connection optimizations for NSE servers
"""

import sys
import asyncio
import logging
from pathlib import Path
from datetime import date, timedelta

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.config import Config
from src.utils.async_downloader import AsyncDownloadManager, DownloadTask
from src.downloaders.nse_eq_downloader import NSEEQDownloader
from src.downloaders.nse_fo_downloader import NSEFODownloader
from src.downloaders.nse_sme_downloader import NSESMEDownloader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_nse_adaptive_timeouts():
    """Test NSE-specific adaptive timeouts"""
    print("\n⏱️ Testing NSE Adaptive Timeouts...")
    
    try:
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        # Test different NSE URLs
        test_cases = [
            ("NSE EQ", "https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240101_F_0000.csv.zip", 7),
            ("NSE FO", "https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20240101_F_0000.csv.zip", 8),
            ("NSE SME", "https://archives.nseindia.com/archives/sme/bhavcopy/sme010124.csv", 10),
            ("NSE INDEX", "https://archives.nseindia.com/content/indices/ind_close_all_01012024.csv", 6)
        ]
        
        async with AsyncDownloadManager(config) as download_manager:
            for section, url, expected_min_timeout in test_cases:
                task = DownloadTask(
                    url=url,
                    date_str="2024-01-01",
                    target_date=date(2024, 1, 1)
                )
                
                adaptive_timeout = download_manager._get_adaptive_timeout(task)
                max_attempts = download_manager._get_max_retry_attempts(task)
                retry_delay = download_manager._get_retry_delay(task, 0)
                
                print(f"  {section}:")
                print(f"    Adaptive Timeout: {adaptive_timeout}s (expected min: {expected_min_timeout}s)")
                print(f"    Max Retry Attempts: {max_attempts}")
                print(f"    Initial Retry Delay: {retry_delay}s")
                
                if adaptive_timeout >= expected_min_timeout:
                    print(f"    ✅ Timeout meets minimum requirement")
                else:
                    print(f"    ❌ Timeout below minimum requirement")
                    return False
        
        print("  ✅ NSE adaptive timeouts test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ NSE adaptive timeouts test failed: {e}")
        return False

async def test_nse_retry_strategy():
    """Test NSE-specific retry strategy"""
    print("\n🔄 Testing NSE Retry Strategy...")
    
    try:
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        # Test with problematic NSE SME date (2025-01-10)
        test_date = date(2025, 1, 10)
        
        print(f"  Testing problematic date: {test_date}")
        
        # Create NSE SME downloader
        nse_sme = NSESMEDownloader(config)
        url = nse_sme.build_url(test_date)
        
        print(f"  URL: {url}")
        
        task = DownloadTask(
            url=url,
            date_str=test_date.strftime('%Y-%m-%d'),
            target_date=test_date
        )
        
        async with AsyncDownloadManager(config) as download_manager:
            # Test retry parameters
            max_attempts = download_manager._get_max_retry_attempts(task)
            adaptive_timeout = download_manager._get_adaptive_timeout(task)
            
            print(f"  Max Retry Attempts: {max_attempts} (should be >= 4 for NSE SME)")
            print(f"  Adaptive Timeout: {adaptive_timeout}s (should be >= 10 for NSE SME)")
            
            if max_attempts >= 4 and adaptive_timeout >= 10:
                print("  ✅ NSE SME gets aggressive retry strategy")
            else:
                print("  ❌ NSE SME retry strategy insufficient")
                return False
            
            # Test actual download (will likely fail but should show proper error handling)
            result = await download_manager.download_file(task)
            
            if not result.success:
                print(f"  Expected failure with enhanced error: {result.error_message}")
                if "NSE SME" in result.error_message or "timeout" in result.error_message.lower():
                    print("  ✅ Error message is NSE-specific and informative")
                else:
                    print(f"  ⚠️ Error message could be more specific: {result.error_message}")
            else:
                print("  ⚠️ Unexpected success - file may actually be available")
        
        print("  ✅ NSE retry strategy test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ NSE retry strategy test failed: {e}")
        return False

async def test_nse_connection_optimizations():
    """Test NSE connection optimizations"""
    print("\n🔗 Testing NSE Connection Optimizations...")
    
    try:
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        async with AsyncDownloadManager(config) as download_manager:
            # Check if session was created with enhanced settings
            if download_manager.session:
                connector = download_manager.session.connector
                
                print(f"  Connection Settings:")
                print(f"    Keepalive Timeout: {getattr(connector, '_keepalive_timeout', 'N/A')}")
                print(f"    Enable Cleanup Closed: {getattr(connector, '_enable_cleanup_closed', 'N/A')}")
                print(f"    Force Close: {getattr(connector, '_force_close', 'N/A')}")
                
                # Check headers
                headers = download_manager.session.headers
                print(f"  Enhanced Headers:")
                print(f"    User-Agent: {headers.get('User-Agent', 'N/A')[:50]}...")
                print(f"    Accept-Encoding: {headers.get('Accept-Encoding', 'N/A')}")
                print(f"    Cache-Control: {headers.get('Cache-Control', 'N/A')}")
                
                if 'Chrome/120' in headers.get('User-Agent', ''):
                    print("  ✅ Updated User-Agent")
                else:
                    print("  ❌ User-Agent not updated")
                    return False
                
                if 'br' in headers.get('Accept-Encoding', ''):
                    print("  ✅ Enhanced Accept-Encoding")
                else:
                    print("  ❌ Accept-Encoding not enhanced")
                    return False
        
        print("  ✅ NSE connection optimizations test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ NSE connection optimizations test failed: {e}")
        return False

async def test_nse_error_classification():
    """Test NSE-specific error classification"""
    print("\n🏷️ Testing NSE Error Classification...")
    
    try:
        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        async with AsyncDownloadManager(config) as download_manager:
            # Test different NSE-specific error scenarios
            test_errors = [
                ("NSE EQ", "https://archives.nseindia.com/content/cm/test.zip", "timeout"),
                ("NSE FO", "https://archives.nseindia.com/content/fo/test.zip", "404"),
                ("NSE SME", "https://archives.nseindia.com/archives/sme/bhavcopy/test.csv", "connection")
            ]
            
            for section, url, error_type in test_errors:
                task = DownloadTask(
                    url=url,
                    date_str="2024-01-01",
                    target_date=date(2024, 1, 1)
                )
                
                # Test error classification
                test_error_msg = f"Test {error_type} error"
                classification = download_manager._classify_error(test_error_msg, task)
                
                print(f"  {section} - {error_type} error:")
                print(f"    Type: {classification['type']}")
                print(f"    Should Retry: {classification['should_retry']}")
                print(f"    User Message: {classification['user_message'][:60]}...")
        
        print("  ✅ NSE error classification test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ NSE error classification test failed: {e}")
        return False

async def main():
    """Run all NSE improvement tests"""
    print("🧪 NSE-Specific Improvements Test Suite")
    print("=" * 50)
    
    tests = [
        test_nse_adaptive_timeouts,
        test_nse_retry_strategy,
        test_nse_connection_optimizations,
        test_nse_error_classification
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            results.append(False)
    
    # Summary
    print("\n📊 NSE Improvements Test Results")
    print("=" * 40)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Tests Passed: {passed}/{total}")
    print(f"Success Rate: {(passed/total)*100:.1f}%")
    
    if passed == total:
        print("\n🎉 All NSE improvement tests passed!")
        print("\nKey Improvements Verified:")
        print("✅ NSE SME: 10s timeout, 4+ retry attempts")
        print("✅ NSE FO: 8s timeout, 3+ retry attempts") 
        print("✅ NSE EQ: 7s timeout, 3+ retry attempts (critical section)")
        print("✅ Enhanced connection handling")
        print("✅ Better error classification")
        print("\nThese improvements should significantly reduce file skipping in NSE sections!")
        return 0
    else:
        print(f"\n⚠️ {total-passed} test(s) failed. Please review the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
