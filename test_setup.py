#!/usr/bin/env python3
"""
Test Setup Script for NSE/BSE Data Downloader

Tests the basic functionality and setup of the downloader system.
"""

import sys
import asyncio
from pathlib import Path
from datetime import date, timedelta

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_imports():
    """Test if all modules can be imported"""
    print("Testing imports...")
    
    try:
        from src.core.config import Config
        from src.core.data_manager import DataManager
        from src.core.base_downloader import BaseDownloader
        from src.downloaders.nse_eq_downloader import NSEEQDownloader
        from src.downloaders.bse_eq_downloader import BSEEQDownloader
        from src.downloaders.nse_sme_downloader import NSESMEDownloader
        from src.downloaders.nse_fo_downloader import NSEFODownloader
        from src.utils.async_downloader import AsyncDownloadManager
        from src.utils.memory_optimizer import MemoryOptimizer
        from src.utils.file_utils import FileUtils
        from src.utils.date_utils import DateUtils
        print("✓ All core modules imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Import error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_config():
    """Test configuration loading"""
    print("\nTesting configuration...")

    try:
        from src.core.config import Config

        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        print(f"✓ Configuration loaded: {config}")

        # Test exchange configs
        exchanges = config.get_available_exchanges()
        print(f"✓ Available exchanges: {exchanges}")

        # Test paths
        base_path = config.base_data_path
        print(f"✓ Base data path: {base_path}")

        return True
    except Exception as e:
        print(f"✗ Configuration error: {e}")
        return False

def test_data_manager():
    """Test data manager functionality"""
    print("\nTesting data manager...")

    try:
        from src.core.config import Config
        from src.core.data_manager import DataManager

        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        data_manager = DataManager(config)
        
        # Test data summary
        summary = data_manager.get_data_summary()
        print(f"✓ Data summary generated for {len(summary)} exchanges")
        
        # Test date range calculation
        start_date, end_date = data_manager.calculate_date_range("NSE", "EQ")
        print(f"✓ Date range calculated: {start_date} to {end_date}")
        
        return True
    except Exception as e:
        print(f"✗ Data manager error: {e}")
        return False

def test_downloaders():
    """Test downloader initialization"""
    print("\nTesting downloaders...")

    try:
        from src.core.config import Config
        from src.downloaders.nse_eq_downloader import NSEEQDownloader
        from src.downloaders.bse_eq_downloader import BSEEQDownloader
        from src.downloaders.nse_sme_downloader import NSESMEDownloader
        from src.downloaders.nse_fo_downloader import NSEFODownloader

        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        # Test NSE EQ downloader
        nse_eq = NSEEQDownloader(config)
        test_date = date.today() - timedelta(days=1)
        url = nse_eq.build_url(test_date)
        print(f"✓ NSE EQ downloader initialized, sample URL: {url}")
        
        # Test BSE EQ downloader
        bse_eq = BSEEQDownloader(config)
        url = bse_eq.build_url(test_date)
        print(f"✓ BSE EQ downloader initialized, sample URL: {url}")
        
        # Test NSE SME downloader
        nse_sme = NSESMEDownloader(config)
        url = nse_sme.build_url(test_date)
        print(f"✓ NSE SME downloader initialized, sample URL: {url}")
        
        # Test NSE FO downloader
        nse_fo = NSEFODownloader(config)
        url = nse_fo.build_url(test_date)
        print(f"✓ NSE FO downloader initialized, sample URL: {url}")
        
        return True
    except Exception as e:
        print(f"✗ Downloader error: {e}")
        return False

def test_memory_optimizer():
    """Test memory optimizer"""
    print("\nTesting memory optimizer...")
    
    try:
        from src.utils.memory_optimizer import MemoryOptimizer
        import pandas as pd
        
        optimizer = MemoryOptimizer()
        
        # Test memory monitoring
        memory_info = optimizer._get_memory_usage()
        print(f"✓ Memory monitoring: {memory_info['rss_mb']:.1f} MB")
        
        # Test DataFrame optimization
        test_df = pd.DataFrame({
            'A': range(1000),
            'B': [f"test_{i}" for i in range(1000)],
            'C': [1.5] * 1000
        })
        
        optimized_df = optimizer.optimize_dataframe(test_df)
        print(f"✓ DataFrame optimization: {len(optimized_df)} rows")
        
        return True
    except Exception as e:
        print(f"✗ Memory optimizer error: {e}")
        return False

def test_gui_availability():
    """Test GUI availability"""
    print("\nTesting GUI availability...")
    
    try:
        from PyQt6.QtWidgets import QApplication
        print("✓ PyQt6 is available")
        
        # Test GUI import
        from src.gui.main_window import MainWindow
        print("✓ GUI modules can be imported")
        
        return True
    except ImportError:
        print("⚠ PyQt6 is not available - GUI mode will be disabled")
        print("  Install PyQt6 with: pip install PyQt6")
        return False

async def test_async_downloader():
    """Test async downloader"""
    print("\nTesting async downloader...")

    try:
        from src.utils.async_downloader import AsyncDownloadManager, DownloadTask
        from src.core.config import Config

        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        async with AsyncDownloadManager(config) as download_manager:
            print("✓ Async download manager initialized")
            
            # Test download statistics
            stats = download_manager.get_download_stats()
            print(f"✓ Download statistics: {stats}")
        
        return True
    except Exception as e:
        print(f"✗ Async downloader error: {e}")
        return False

def test_folder_structure():
    """Test folder structure creation"""
    print("\nTesting folder structure...")

    try:
        from src.core.config import Config

        config_path = Path(__file__).parent / "config.yaml"
        config = Config(str(config_path))
        
        # Check if folders are created
        base_path = config.base_data_path
        if base_path.exists():
            print(f"✓ Base folder exists: {base_path}")
        
        # Check exchange folders
        for exchange_segment in config.get_available_exchanges():
            exchange, segment = exchange_segment.split('_', 1)
            data_path = config.get_data_path(exchange, segment)
            temp_path = config.get_temp_path(exchange, segment)
            
            if data_path.exists() and temp_path.exists():
                print(f"✓ Folders exist for {exchange_segment}")
            else:
                print(f"⚠ Folders missing for {exchange_segment}")
        
        return True
    except Exception as e:
        print(f"✗ Folder structure error: {e}")
        return False

def main():
    """Run all tests"""
    print("NSE/BSE Data Downloader - Setup Test")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_config,
        test_data_manager,
        test_downloaders,
        test_memory_optimizer,
        test_gui_availability,
        test_folder_structure,
    ]
    
    # Run sync tests
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
            results.append(False)
    
    # Run async test
    try:
        async_result = asyncio.run(test_async_downloader())
        results.append(async_result)
    except Exception as e:
        print(f"✗ Async test failed: {e}")
        results.append(False)
    
    # Summary
    print("\n" + "=" * 50)
    print("Test Summary:")
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All tests passed! The system is ready to use.")
        return 0
    else:
        print("⚠ Some tests failed. Please check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
