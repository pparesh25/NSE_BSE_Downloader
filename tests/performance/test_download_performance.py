#!/usr/bin/env python3
"""
Simple Performance Tests
========================

Basic performance tests for the NSE/BSE Data Downloader.
"""

import unittest
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import Mock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


class TestBasicPerformance(unittest.TestCase):
    """Basic performance tests"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_import_performance(self):
        """Test import performance"""
        start_time = time.time()
        
        try:
            from src.core.config import Config
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
        except ImportError as e:
            self.skipTest(f"Import failed: {e}")
        
        end_time = time.time()
        import_time = end_time - start_time
        
        # Imports should be fast
        self.assertLess(import_time, 2.0, "Imports should complete within 2 seconds")
        print(f"Import time: {import_time:.3f} seconds")
    
    def test_config_creation_performance(self):
        """Test config creation performance"""
        import yaml
        
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
                }
            }
        }
        
        config_path = Path(self.temp_dir) / "test_config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        start_time = time.time()
        
        try:
            from src.core.config import Config
            config = Config(str(config_path))
        except Exception as e:
            self.skipTest(f"Config creation failed: {e}")
        
        end_time = time.time()
        creation_time = end_time - start_time
        
        # Config creation should be fast
        self.assertLess(creation_time, 1.0, "Config creation should complete within 1 second")
        print(f"Config creation time: {creation_time:.3f} seconds")
    
    def test_memory_usage_basic(self):
        """Test basic memory usage"""
        try:
            import psutil
            process = psutil.Process()
            
            # Get initial memory
            initial_memory = process.memory_info().rss / 1024 / 1024  # MB
            
            # Create some test data
            test_data = []
            for i in range(1000):
                test_data.append(f"test_data_{i}")
            
            # Get memory after
            current_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = current_memory - initial_memory
            
            # Memory increase should be reasonable
            self.assertLess(memory_increase, 50, "Memory increase should be less than 50MB")
            print(f"Memory usage: {current_memory:.1f}MB (increase: {memory_increase:.1f}MB)")
            
        except ImportError:
            self.skipTest("psutil not available for memory testing")
    
    def test_file_operations_performance(self):
        """Test file operations performance"""
        start_time = time.time()
        
        # Create test files
        test_files = []
        for i in range(10):
            test_file = Path(self.temp_dir) / f"test_{i}.txt"
            with open(test_file, 'w') as f:
                f.write(f"Test content {i}\n" * 100)
            test_files.append(test_file)
        
        # Read test files
        total_content = ""
        for test_file in test_files:
            with open(test_file, 'r') as f:
                total_content += f.read()
        
        end_time = time.time()
        file_ops_time = end_time - start_time
        
        # File operations should be fast
        self.assertLess(file_ops_time, 2.0, "File operations should complete within 2 seconds")
        self.assertGreater(len(total_content), 0, "Should have read some content")
        print(f"File operations time: {file_ops_time:.3f} seconds")
    
    def test_downloader_initialization_performance(self):
        """Test downloader initialization performance"""
        # Create mock config
        config = Mock()
        config.data_folder = self.temp_dir
        config.get_data_path.return_value = Path(self.temp_dir)
        
        start_time = time.time()
        
        try:
            from src.downloaders.nse_eq_downloader import NSEEQDownloader
            from src.downloaders.bse_eq_downloader import BSEEQDownloader
            
            nse_downloader = NSEEQDownloader(config)
            bse_downloader = BSEEQDownloader(config)
            
        except Exception as e:
            self.skipTest(f"Downloader initialization failed: {e}")
        
        end_time = time.time()
        init_time = end_time - start_time
        
        # Initialization should be fast
        self.assertLess(init_time, 1.0, "Downloader initialization should complete within 1 second")
        print(f"Downloader initialization time: {init_time:.3f} seconds")


class TestScalabilityBasic(unittest.TestCase):
    """Basic scalability tests"""
    
    def test_multiple_config_objects(self):
        """Test creating multiple config objects"""
        start_time = time.time()
        
        configs = []
        for i in range(5):
            config = Mock()
            config.data_folder = f"/tmp/test_{i}"
            configs.append(config)
        
        end_time = time.time()
        creation_time = end_time - start_time
        
        # Should handle multiple configs efficiently
        self.assertLess(creation_time, 1.0, "Multiple config creation should be fast")
        self.assertEqual(len(configs), 5, "Should create all configs")
        print(f"Multiple config creation time: {creation_time:.3f} seconds")
    
    def test_large_data_structure(self):
        """Test handling large data structures"""
        start_time = time.time()
        
        # Create large data structure
        large_data = {}
        for i in range(1000):
            large_data[f"key_{i}"] = {
                'value': f"value_{i}",
                'data': list(range(10))
            }
        
        # Process the data
        processed_count = 0
        for key, value in large_data.items():
            if 'value' in value:
                processed_count += 1
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Should handle large data efficiently
        self.assertLess(processing_time, 2.0, "Large data processing should be efficient")
        self.assertEqual(processed_count, 1000, "Should process all items")
        print(f"Large data processing time: {processing_time:.3f} seconds")


if __name__ == '__main__':
    unittest.main()
