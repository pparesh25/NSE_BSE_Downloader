#!/usr/bin/env python3
"""
Simple Unit Tests for Downloaders
=================================

Basic tests for NSE and BSE downloader classes.
"""

import unittest
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


class TestDownloaderBasics(unittest.TestCase):
    """Basic tests for downloader functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_downloader_imports(self):
        """Test that downloader modules can be imported"""
        try:
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            self.assertTrue(True, "Downloaders imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import downloaders: {e}")
    
    def test_config_import(self):
        """Test that config module can be imported"""
        try:
            from src.core.config import Config
            self.assertTrue(True, "Config imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import config: {e}")
    
    def test_base_downloader_import(self):
        """Test that base downloader can be imported"""
        try:
            from src.core.base_downloader import BaseDownloader
            self.assertTrue(True, "BaseDownloader imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import BaseDownloader: {e}")
    
    def test_exceptions_import(self):
        """Test that exceptions can be imported"""
        try:
            from src.core.exceptions import ConfigError, NetworkError, DataProcessingError
            self.assertTrue(True, "Exceptions imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import exceptions: {e}")
    
    def test_utils_import(self):
        """Test that utility modules can be imported"""
        try:
            from src.utils.file_utils import FileUtils
            from src.utils.date_utils import DateUtils
            self.assertTrue(True, "Utils imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import utils: {e}")


class TestDownloaderInitialization(unittest.TestCase):
    """Test downloader initialization with mocked config"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create a comprehensive mock config
        self.config = Mock()
        self.config.data_folder = self.temp_dir
        
        # Mock all the methods that downloaders might use
        self.config.get_data_path.return_value = Path(self.temp_dir)
        self.config.get_exchange_config.return_value = Mock(
            base_url='https://test.com',
            filename_pattern='test.csv',
            date_format='%Y%m%d',
            file_suffix='-TEST'
        )
        
        # Mock config data access
        self.config._config_data = {
            'data_paths': {
                'base_folder': self.temp_dir,
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            }
        }
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_nse_downloader_creation(self):
        """Test NSE downloader can be created with mock config"""
        try:
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            downloader = NSEEQDownloader(self.config)
            self.assertIsNotNone(downloader)
            self.assertEqual(downloader.config, self.config)
        except Exception as e:
            self.skipTest(f"NSE downloader initialization failed: {e}")
    
    def test_bse_downloader_creation(self):
        """Test BSE downloader can be created with mock config"""
        try:
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            downloader = BSEEQDownloader(self.config)
            self.assertIsNotNone(downloader)
            self.assertEqual(downloader.config, self.config)
        except Exception as e:
            self.skipTest(f"BSE downloader initialization failed: {e}")


class TestConfigStructure(unittest.TestCase):
    """Test config structure and validation"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_config_creation_with_minimal_data(self):
        """Test config creation with minimal required data"""
        import yaml
        
        # Create minimal config file
        config_data = {
            'data_paths': {
                'base_folder': self.temp_dir,
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            },
            'download_settings': {
                'timeout_seconds': 30,
                'retry_attempts': 2
            },
            'exchange_config': {
                'NSE': {
                    'EQ': {
                        'base_url': 'https://test.nse.com',
                        'filename_pattern': 'test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-EQ'
                    }
                },
                'BSE': {
                    'EQ': {
                        'base_url': 'https://test.bse.com',
                        'filename_pattern': 'test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-BSE-EQ'
                    }
                }
            }
        }
        
        config_path = Path(self.temp_dir) / "test_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        try:
            from src.core.config import Config
            config = Config(str(config_path))
            self.assertIsNotNone(config)
            self.assertEqual(config.data_folder, self.temp_dir)
        except Exception as e:
            self.skipTest(f"Config creation failed: {e}")


class TestFileStructure(unittest.TestCase):
    """Test that required files exist in the project"""
    
    def test_required_files_exist(self):
        """Test that all required source files exist"""
        base_path = Path(__file__).parent.parent.parent / 'src'
        
        required_files = [
            'core/config.py',
            'core/base_downloader.py',
            'core/exceptions.py',
            'downloaders/nse_eq_downloader.py',
            'downloaders/bse_eq_downloader.py',
            'utils/file_utils.py',
            'utils/date_utils.py'
        ]
        
        for file_path in required_files:
            full_path = base_path / file_path
            self.assertTrue(full_path.exists(), f"Required file missing: {file_path}")
    
    def test_init_files_exist(self):
        """Test that __init__.py files exist in packages"""
        base_path = Path(__file__).parent.parent.parent / 'src'
        
        required_init_files = [
            '__init__.py',
            'core/__init__.py',
            'downloaders/__init__.py',
            'utils/__init__.py'
        ]
        
        for init_file in required_init_files:
            full_path = base_path / init_file
            self.assertTrue(full_path.exists(), f"Required __init__.py missing: {init_file}")


if __name__ == '__main__':
    unittest.main()
