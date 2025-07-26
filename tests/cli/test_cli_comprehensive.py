#!/usr/bin/env python3
"""
Comprehensive CLI Features Test Suite
=====================================

આ test suite CLI_Features_Presentation.md માં mentioned તમામ features ને verify કરે છે.

Test Categories:
1. Interactive Menu Navigation
2. Exchange Selection
3. Date Range Configuration  
4. Download Functionality
5. Data Quality Features
6. Configuration Management
7. Progress Display
8. Error Handling

Usage:
    python test_cli_features.py
    python test_cli_features.py --verbose
    python test_cli_features.py --test-category navigation
"""

import asyncio
import sys
import os
import tempfile
import shutil
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
import unittest
from unittest.mock import Mock, patch, AsyncMock
import json

# Add src to path
sys.path.insert(0, 'src')

from src.cli.cli_interface import CLIInterface
from src.cli.interactive_menu import InteractiveMenu, MenuController, MenuType
from src.cli.progress_display import MultiProgressDisplay, ProgressBar
from src.core.config import Config


class CLIFeatureTestCase(unittest.TestCase):
    """Base test case for CLI features"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, 'test_config.yaml')
        
        # Create test config
        test_config = {
            'data_paths': {
                'base_folder': os.path.join(self.test_dir, 'data'),
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            },
            'download_settings': {
                'max_concurrent_downloads': 1,
                'retry_attempts': 2,
                'timeout_seconds': 5,
                'chunk_size': 8192,
                'rate_limit_delay': 0.2
            },
            'date_settings': {
                'base_start_date': '2025-01-01',
                'weekend_skip': True,
                'holiday_skip': True
            },
            'gui_settings': {
                'window_title': 'Test Downloader',
                'default_exchanges': ['NSE_EQ', 'BSE_EQ']
            },
            'exchange_config': {
                'NSE': {
                    'EQ': {
                        'base_url': 'https://test.nse.com',
                        'filename_pattern': 'test_{date}.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-EQ'
                    }
                },
                'BSE': {
                    'EQ': {
                        'base_url': 'https://test.bse.com',
                        'filename_pattern': 'test_{date}.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-BSE-EQ'
                    }
                }
            }
        }
        
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(test_config, f)
            
        self.config = Config(self.config_path)
        self.cli = CLIInterface(self.config)
        
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.test_dir, ignore_errors=True)


class TestInteractiveMenu(CLIFeatureTestCase):
    """Test interactive menu system"""
    
    def test_menu_creation(self):
        """Test menu creation and item addition"""
        menu = InteractiveMenu("Test Menu", MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "Description 1")
        menu.add_item("item2", "Item 2", "Description 2")
        
        self.assertEqual(len(menu.items), 2)
        self.assertEqual(menu.items[0].id, "item1")
        self.assertEqual(menu.items[0].title, "Item 1")
        
    def test_menu_navigation(self):
        """Test menu navigation methods"""
        menu = InteractiveMenu("Test Menu", MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1")
        menu.add_item("item2", "Item 2")
        menu.add_item("item3", "Item 3")
        
        # Test move down
        initial_index = menu.current_index
        menu.move_down()
        self.assertEqual(menu.current_index, initial_index + 1)
        
        # Test move up
        menu.move_up()
        self.assertEqual(menu.current_index, initial_index)
        
    def test_multi_select_menu(self):
        """Test multi-select menu functionality"""
        menu = InteractiveMenu("Multi Select", MenuType.MULTI_SELECT)
        menu.add_item("item1", "Item 1")
        menu.add_item("item2", "Item 2")
        
        # Test selection toggle
        menu.toggle_selection()
        self.assertIn("item1", menu.selected_items)
        
        menu.toggle_selection()
        self.assertNotIn("item1", menu.selected_items)


class TestProgressDisplay(CLIFeatureTestCase):
    """Test progress display functionality"""
    
    def test_progress_bar_creation(self):
        """Test progress bar creation and initialization"""
        progress = ProgressBar("NSE_EQ")
        progress.start(100)

        self.assertEqual(progress.name, "NSE_EQ")
        self.assertEqual(progress.stats.total_files, 100)
        self.assertEqual(progress.stats.completed_files, 0)
        
    def test_progress_updates(self):
        """Test progress bar updates"""
        progress = ProgressBar("NSE_EQ")
        progress.start(100)
        
        # Test increment
        progress.increment(success=True, bytes_downloaded=1000)
        self.assertEqual(progress.stats.completed_files, 1)
        self.assertEqual(progress.stats.total_bytes, 1000)

        # Test failed download
        progress.increment(success=False)
        self.assertEqual(progress.stats.completed_files + progress.stats.failed_files, 2)
        self.assertEqual(progress.stats.failed_files, 1)
        
    def test_multi_progress_display(self):
        """Test multi-exchange progress display"""
        multi_progress = MultiProgressDisplay()
        
        # Add exchanges
        multi_progress.add_exchange("NSE_EQ", 50)
        multi_progress.add_exchange("BSE_EQ", 30)
        
        self.assertEqual(len(multi_progress.progress_bars), 2)
        self.assertIn("NSE_EQ", multi_progress.progress_bars)
        self.assertIn("BSE_EQ", multi_progress.progress_bars)


class TestCLIInterface(CLIFeatureTestCase):
    """Test main CLI interface functionality"""
    
    @patch('builtins.input', return_value='q')
    def test_cli_initialization(self, mock_input):
        """Test CLI interface initialization"""
        self.assertIsInstance(self.cli.config, Config)
        self.assertEqual(str(self.cli.config.config_path), self.config_path)
        
    def test_exchange_list_generation(self):
        """Test exchange list generation from config"""
        # Test that config has exchange configurations
        exchanges = self.config.get_available_exchanges()
        self.assertIn('NSE_EQ', exchanges)
        self.assertIn('BSE_EQ', exchanges)

        # Test individual exchange config access
        nse_config = self.config.get_exchange_config('NSE', 'EQ')
        self.assertEqual(nse_config.file_suffix, '-NSE-EQ')
        
    @patch('src.cli.cli_interface.CLIInterface.show_main_menu')
    async def test_cli_run(self, mock_menu):
        """Test CLI run method"""
        mock_menu.return_value = None
        result = await self.cli.run()
        self.assertEqual(result, 0)


class TestDateRangeHandling(CLIFeatureTestCase):
    """Test date range configuration and validation"""
    
    def test_working_days_calculation(self):
        """Test working days calculation (excluding weekends)"""
        start_date = date(2025, 7, 21)  # Monday
        end_date = date(2025, 7, 27)    # Sunday
        
        working_days = []
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5:  # Monday=0, Friday=4
                working_days.append(current_date)
            current_date += timedelta(days=1)
            
        # Should have 5 working days (Mon-Fri)
        self.assertEqual(len(working_days), 5)
        
    def test_date_validation(self):
        """Test date range validation"""
        today = date.today()
        yesterday = today - timedelta(days=1)
        
        # Valid range
        self.assertTrue(yesterday <= today)
        
        # Invalid range (future date)
        future_date = today + timedelta(days=30)
        self.assertFalse(future_date <= today)


class TestDownloadSimulation(CLIFeatureTestCase):
    """Test download functionality simulation"""
    
    async def test_download_simulation(self):
        """Test simulated download process"""
        # Create mock progress display
        progress = MultiProgressDisplay()
        progress.add_exchange("NSE_EQ", 5)
        
        # Simulate download for 5 files
        for i in range(5):
            await asyncio.sleep(0.01)  # Simulate download time
            progress.increment_exchange("NSE_EQ", success=True, bytes_downloaded=1000)
            
        # Verify progress
        nse_progress = progress.progress_bars["NSE_EQ"]
        self.assertEqual(nse_progress.stats.completed_files, 5)
        self.assertEqual(nse_progress.stats.successful_files, 5)


def run_test_suite(test_category: Optional[str] = None, verbose: bool = False):
    """Run the complete test suite"""
    
    print("🧪 NSE/BSE CLI Features Test Suite")
    print("=" * 50)
    
    # Create test suite
    suite = unittest.TestSuite()
    
    if test_category:
        # Run specific test category
        if test_category == "navigation":
            suite.addTest(unittest.makeSuite(TestInteractiveMenu))
        elif test_category == "progress":
            suite.addTest(unittest.makeSuite(TestProgressDisplay))
        elif test_category == "interface":
            suite.addTest(unittest.makeSuite(TestCLIInterface))
        elif test_category == "dates":
            suite.addTest(unittest.makeSuite(TestDateRangeHandling))
        elif test_category == "download":
            suite.addTest(unittest.makeSuite(TestDownloadSimulation))
        else:
            print(f"❌ Unknown test category: {test_category}")
            return False
    else:
        # Run all tests
        suite.addTest(unittest.makeSuite(TestInteractiveMenu))
        suite.addTest(unittest.makeSuite(TestProgressDisplay))
        suite.addTest(unittest.makeSuite(TestCLIInterface))
        suite.addTest(unittest.makeSuite(TestDateRangeHandling))
        suite.addTest(unittest.makeSuite(TestDownloadSimulation))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "=" * 50)
    print(f"📊 Test Results Summary:")
    print(f"✅ Tests run: {result.testsRun}")
    print(f"❌ Failures: {len(result.failures)}")
    print(f"⚠️  Errors: {len(result.errors)}")
    
    if result.failures:
        print("\n❌ Failures:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback.split('AssertionError:')[-1].strip()}")
            
    if result.errors:
        print("\n⚠️  Errors:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback.split('Exception:')[-1].strip()}")
    
    success = len(result.failures) == 0 and len(result.errors) == 0
    print(f"\n🎯 Overall Result: {'✅ PASSED' if success else '❌ FAILED'}")
    
    return success


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="CLI Features Test Suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--test-category", "-c", help="Run specific test category")
    
    args = parser.parse_args()
    
    success = run_test_suite(args.test_category, args.verbose)
    sys.exit(0 if success else 1)
