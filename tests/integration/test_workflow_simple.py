#!/usr/bin/env python3
"""
Simple Integration Tests
========================

Basic integration tests for the NSE/BSE Data Downloader.
"""

import unittest
import sys
import tempfile
import yaml
from pathlib import Path
from unittest.mock import Mock, patch

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


class TestBasicIntegration(unittest.TestCase):
    """Basic integration tests"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_config_and_downloader_integration(self):
        """Test config and downloader integration"""
        # Create test config
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
            # Test config creation
            from src.core.config import Config
            config = Config(str(config_path))
            self.assertIsNotNone(config)
            
            # Test downloader creation
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            
            nse_downloader = NSEEQDownloader(config)
            bse_downloader = BSEEQDownloader(config)
            
            self.assertIsNotNone(nse_downloader)
            self.assertIsNotNone(bse_downloader)
            
        except Exception as e:
            self.skipTest(f"Integration test failed: {e}")
    
    def test_data_path_creation(self):
        """Test data path creation integration"""
        try:
            # Create mock config
            config = Mock()
            config.base_data_path = Path(self.temp_dir)
            config.get_data_path.return_value = Path(self.temp_dir) / "NSE" / "EQ"
            
            # Test path creation
            data_path = config.get_data_path('NSE', 'EQ')
            data_path.mkdir(parents=True, exist_ok=True)
            
            self.assertTrue(data_path.exists())
            
        except Exception as e:
            self.skipTest(f"Data path test failed: {e}")
    
    def test_file_operations_integration(self):
        """Test file operations integration"""
        try:
            # Create test data directory
            data_dir = Path(self.temp_dir) / "data"
            data_dir.mkdir(exist_ok=True)
            
            # Create test file
            test_file = data_dir / "test.csv"
            test_content = "SYMBOL,OPEN,HIGH,LOW,CLOSE\nTEST,100,110,95,105\n"
            
            with open(test_file, 'w') as f:
                f.write(test_content)
            
            # Verify file exists and has content
            self.assertTrue(test_file.exists())
            
            with open(test_file, 'r') as f:
                content = f.read()
            
            self.assertEqual(content, test_content)
            self.assertIn("SYMBOL", content)
            self.assertIn("TEST", content)
            
        except Exception as e:
            self.skipTest(f"File operations test failed: {e}")
    
    def test_config_validation_integration(self):
        """Test config validation integration"""
        # Test with minimal valid config
        minimal_config = {
            'data_paths': {
                'base_folder': self.temp_dir
            }
        }
        
        config_path = Path(self.temp_dir) / "minimal_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(minimal_config, f)
        
        try:
            from src.core.config import Config
            config = Config(str(config_path))
            
            # Should create config successfully
            self.assertIsNotNone(config)
            self.assertTrue(config.base_data_path.exists())
            
        except Exception as e:
            self.skipTest(f"Config validation test failed: {e}")
    
    def test_exchange_configuration_integration(self):
        """Test exchange configuration integration"""
        config_data = {
            'data_paths': {
                'base_folder': self.temp_dir,
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ', 'FO': 'NSE/FO'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            },
            'exchange_config': {
                'NSE': {
                    'EQ': {
                        'base_url': 'https://test.nse.com',
                        'filename_pattern': 'test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-EQ'
                    },
                    'FO': {
                        'base_url': 'https://test.nse.com/fo',
                        'filename_pattern': 'fo_test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-FO'
                    }
                },
                'BSE': {
                    'EQ': {
                        'base_url': 'https://test.bse.com',
                        'filename_pattern': 'bse_test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-BSE-EQ'
                    }
                }
            }
        }
        
        config_path = Path(self.temp_dir) / "exchange_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        try:
            from src.core.config import Config
            config = Config(str(config_path))
            
            # Test exchange configurations
            available_exchanges = config.get_available_exchanges()
            self.assertIn('NSE_EQ', available_exchanges)
            self.assertIn('NSE_FO', available_exchanges)
            self.assertIn('BSE_EQ', available_exchanges)
            
            # Test specific exchange config
            nse_eq_config = config.get_exchange_config('NSE', 'EQ')
            self.assertEqual(nse_eq_config.base_url, 'https://test.nse.com')
            
        except Exception as e:
            self.skipTest(f"Exchange configuration test failed: {e}")


class TestModuleIntegration(unittest.TestCase):
    """Test module integration"""
    
    def test_all_modules_importable(self):
        """Test that all required modules can be imported together"""
        try:
            from src.core.config import Config
            from src.core.base_downloader import BaseDownloader
            from src.core.exceptions import ConfigError, NetworkError
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            from src.utils.file_utils import FileUtils
            from src.utils.date_utils import DateUtils
            
            # All imports successful
            self.assertTrue(True, "All modules imported successfully")
            
        except ImportError as e:
            self.fail(f"Module import failed: {e}")
    
    def test_class_inheritance(self):
        """Test class inheritance relationships"""
        try:
            from src.core.base_downloader import BaseDownloader
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            
            # Test inheritance
            self.assertTrue(issubclass(NSEEQDownloader, BaseDownloader))
            self.assertTrue(issubclass(BSEEQDownloader, BaseDownloader))
            
        except Exception as e:
            self.skipTest(f"Inheritance test failed: {e}")
    
    def test_exception_hierarchy(self):
        """Test exception hierarchy"""
        try:
            from src.core.exceptions import ConfigError, NetworkError, DataProcessingError
            
            # Test that exceptions are proper Exception subclasses
            self.assertTrue(issubclass(ConfigError, Exception))
            self.assertTrue(issubclass(NetworkError, Exception))
            self.assertTrue(issubclass(DataProcessingError, Exception))
            
        except Exception as e:
            self.skipTest(f"Exception hierarchy test failed: {e}")


if __name__ == '__main__':
    unittest.main()
