#!/usr/bin/env python3
"""
Unit Tests for Configuration Management
======================================

Tests for config loading, validation, and management functionality.
"""

import unittest
import sys
import os
import tempfile
import yaml
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from src.core.config import Config


class TestConfig(unittest.TestCase):
    """Test cases for Config class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_config_path = Path(self.temp_dir) / "test_config.yaml"
        
        # Create test config matching actual structure
        self.test_config_data = {
            'data_paths': {
                'base_folder': str(Path(self.temp_dir) / "data"),
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ', 'FO': 'NSE/FO'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            },
            'download_settings': {
                'max_concurrent_downloads': 1,
                'retry_attempts': 3,
                'timeout_seconds': 30,
                'chunk_size': 8192
            },
            'exchange_config': {
                'NSE': {
                    'EQ': {
                        'base_url': 'https://test.nse.com',
                        'filename_pattern': 'cm{ddMMyyyy}bhav.csv.zip',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-EQ',
                        'enabled': True
                    }
                },
                'BSE': {
                    'EQ': {
                        'base_url': 'https://test.bse.com',
                        'filename_pattern': 'EQ{ddmmyy}.CSV',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-BSE-EQ',
                        'enabled': True
                    }
                }
            }
        }
        
        with open(self.test_config_path, 'w') as f:
            yaml.dump(self.test_config_data, f)
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_config_loading(self):
        """Test config file loading"""
        config = Config(str(self.test_config_path))

        # Test data paths
        self.assertEqual(str(config.base_data_path), self.test_config_data['data_paths']['base_folder'])

        # Test download settings
        download_settings = config.download_settings
        self.assertEqual(download_settings.timeout_seconds, self.test_config_data['download_settings']['timeout_seconds'])
        self.assertEqual(download_settings.retry_attempts, self.test_config_data['download_settings']['retry_attempts'])
    
    def test_config_validation(self):
        """Test config validation"""
        config = Config(str(self.test_config_path))

        # Test valid config - should not raise exception
        try:
            config._validate_config()
            validation_passed = True
        except Exception:
            validation_passed = False

        self.assertTrue(validation_passed)
    
    def test_exchange_configuration(self):
        """Test exchange configuration"""
        config = Config(str(self.test_config_path))

        # Test exchange configs exist
        self.assertIn('NSE', config._exchange_configs)
        self.assertIn('BSE', config._exchange_configs)

        # Test NSE EQ config
        nse_eq_config = config._exchange_configs['NSE']['EQ']
        self.assertEqual(nse_eq_config.base_url, 'https://test.nse.com')
        self.assertIsNotNone(nse_eq_config.filename_pattern)
    
    def test_data_folder_creation(self):
        """Test data folder creation"""
        config = Config(str(self.test_config_path))

        # Data folder should be created if it doesn't exist
        self.assertTrue(config.base_data_path.exists())

        # Test exchange-specific folders
        nse_folder = config.base_data_path / "NSE" / "EQ"
        config.get_data_path('NSE', 'EQ')  # This should create the folder
        self.assertTrue(nse_folder.exists())
    
    def test_config_defaults(self):
        """Test default configuration values"""
        # Create minimal config with required sections
        minimal_config = {
            'data_paths': {
                'base_folder': str(Path(self.temp_dir) / "minimal_data"),
                'exchanges': {'NSE': {'EQ': 'NSE/EQ'}}
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
                        'file_suffix': '-NSE-EQ',
                        'enabled': True
                    }
                }
            }
        }
        minimal_config_path = Path(self.temp_dir) / "minimal_config.yaml"

        with open(minimal_config_path, 'w') as f:
            yaml.dump(minimal_config, f)

        config = Config(str(minimal_config_path))

        # Should have specified values
        download_settings = config.download_settings
        self.assertGreater(download_settings.timeout_seconds, 0)
        self.assertGreater(download_settings.retry_attempts, 0)


class TestConfigProfiles(unittest.TestCase):
    """Test cases for Config Profiles functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_config_path = Path(self.temp_dir) / "test_config.yaml"
        
        # Create test config with profiles matching actual structure
        self.test_config_data = {
            'data_paths': {
                'base_folder': str(Path(self.temp_dir) / "data"),
                'exchanges': {
                    'NSE': {'EQ': 'NSE/EQ', 'FO': 'NSE/FO'},
                    'BSE': {'EQ': 'BSE/EQ'}
                }
            },
            'download_settings': {
                'max_concurrent_downloads': 1,
                'retry_attempts': 3,
                'timeout_seconds': 30
            },
            'exchange_config': {
                'NSE': {
                    'EQ': {
                        'base_url': 'https://test.nse.com',
                        'filename_pattern': 'test.csv',
                        'date_format': '%Y%m%d',
                        'file_suffix': '-NSE-EQ',
                        'enabled': True
                    }
                }
            },
            'profiles': {
                'quick_download': {
                    'name': 'Quick Download',
                    'description': 'Fast download with minimal retries',
                    'exchanges': ['NSE_EQ'],
                    'download_timeout': 15,
                    'max_retries': 1
                }
            }
        }
        
        with open(self.test_config_path, 'w') as f:
            yaml.dump(self.test_config_data, f)
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_profile_listing(self):
        """Test profile listing functionality"""
        config = Config(str(self.test_config_path))

        # Test that profiles section exists in config
        self.assertIn('profiles', config._config_data)
        self.assertIn('quick_download', config._config_data['profiles'])

    def test_profile_data_access(self):
        """Test accessing profile data"""
        config = Config(str(self.test_config_path))

        # Test profile data access
        profiles = config._config_data.get('profiles', {})
        quick_profile = profiles.get('quick_download', {})

        self.assertEqual(quick_profile.get('name'), 'Quick Download')
        self.assertIn('NSE_EQ', quick_profile.get('exchanges', []))

    def test_config_structure_validation(self):
        """Test that config structure is valid"""
        config = Config(str(self.test_config_path))

        # Test required sections exist
        required_sections = ['data_paths', 'download_settings', 'exchange_config']
        for section in required_sections:
            self.assertIn(section, config._config_data)


if __name__ == '__main__':
    unittest.main()
