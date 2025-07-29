"""
Memory Optimizer for NSE/BSE Data Downloader

Provides memory-efficient data processing with:
- Chunked DataFrame processing
- Memory usage monitoring
- Garbage collection optimization
- Streaming operations for large files
"""

import gc
import psutil
import pandas as pd
import logging
from pathlib import Path
from typing import Iterator, Optional, Dict, Any, List
from contextlib import contextmanager
import numpy as np

from ..core.exceptions import MemoryError as CustomMemoryError, DataProcessingError


class MemoryOptimizer:
    """
    Memory optimization utilities for efficient data processing
    
    Features:
    - Chunked CSV processing for large files
    - Memory usage monitoring and alerts
    - DataFrame memory optimization
    - Automatic garbage collection
    - Memory-efficient data transformations
    """
    
    def __init__(self, chunk_size: int = 10000, memory_threshold: float = 80.0):
        """
        Initialize memory optimizer
        
        Args:
            chunk_size: Default chunk size for processing
            memory_threshold: Memory usage threshold for warnings (percentage)
        """
        self.chunk_size = chunk_size
        self.memory_threshold = memory_threshold
        self.logger = logging.getLogger(__name__)
        
        # Memory monitoring
        self.initial_memory = self._get_memory_usage()
        self.peak_memory = self.initial_memory
        
    def _get_memory_usage(self) -> Dict[str, float]:
        """
        Get current memory usage statistics
        
        Returns:
            Dictionary with memory usage information
        """
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # System memory
            system_memory = psutil.virtual_memory()
            
            return {
                'rss_mb': memory_info.rss / 1024 / 1024,  # Resident Set Size in MB
                'vms_mb': memory_info.vms / 1024 / 1024,  # Virtual Memory Size in MB
                'percent': memory_percent,
                'available_mb': system_memory.available / 1024 / 1024,
                'total_mb': system_memory.total / 1024 / 1024,
                'system_percent': system_memory.percent
            }
        except Exception as e:
            self.logger.warning(f"Could not get memory usage: {e}")
            return {'rss_mb': 0, 'vms_mb': 0, 'percent': 0, 'available_mb': 0, 'total_mb': 0, 'system_percent': 0}
    
    def _check_memory_threshold(self) -> None:
        """Check if memory usage exceeds threshold and warn if necessary"""
        current_memory = self._get_memory_usage()
        
        # Update peak memory
        if current_memory['rss_mb'] > self.peak_memory['rss_mb']:
            self.peak_memory = current_memory
        
        # Check system memory threshold
        if current_memory['system_percent'] > self.memory_threshold:
            self.logger.warning(
                f"High system memory usage: {current_memory['system_percent']:.1f}% "
                f"(threshold: {self.memory_threshold}%)"
            )
            
            # Force garbage collection
            self.force_garbage_collection()
    
    def force_garbage_collection(self) -> Dict[str, int]:
        """
        Force garbage collection and return statistics
        
        Returns:
            Dictionary with garbage collection statistics
        """
        before_memory = self._get_memory_usage()
        
        # Force garbage collection
        collected = gc.collect()
        
        after_memory = self._get_memory_usage()
        memory_freed = before_memory['rss_mb'] - after_memory['rss_mb']
        
        stats = {
            'objects_collected': collected,
            'memory_freed_mb': memory_freed,
            'memory_before_mb': before_memory['rss_mb'],
            'memory_after_mb': after_memory['rss_mb']
        }
        
        if memory_freed > 0:
            self.logger.info(f"Garbage collection freed {memory_freed:.1f} MB")
        
        return stats
    
    @contextmanager
    def memory_monitor(self, operation_name: str = "operation"):
        """
        Context manager for monitoring memory usage during operations
        
        Args:
            operation_name: Name of the operation being monitored
        """
        start_memory = self._get_memory_usage()
        self.logger.debug(f"Starting {operation_name} - Memory: {start_memory['rss_mb']:.1f} MB")
        
        try:
            yield
        finally:
            end_memory = self._get_memory_usage()
            memory_delta = end_memory['rss_mb'] - start_memory['rss_mb']
            
            self.logger.debug(
                f"Completed {operation_name} - Memory: {end_memory['rss_mb']:.1f} MB "
                f"(Δ{memory_delta:+.1f} MB)"
            )
            
            self._check_memory_threshold()
    
    def optimize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Optimize DataFrame memory usage by downcasting numeric types
        
        Args:
            df: Input DataFrame
            
        Returns:
            Memory-optimized DataFrame
        """
        with self.memory_monitor("dataframe_optimization"):
            original_memory = df.memory_usage(deep=True).sum() / 1024 / 1024
            
            optimized_df = df.copy()
            
            # Optimize numeric columns
            for col in optimized_df.columns:
                col_type = optimized_df[col].dtype
                
                if col_type != 'object':
                    # Downcast integers
                    if 'int' in str(col_type):
                        optimized_df[col] = pd.to_numeric(optimized_df[col], downcast='integer')
                    
                    # Downcast floats (but preserve precision for financial data)
                    elif 'float' in str(col_type):
                        # Skip float downcasting for financial data to preserve precision
                        # optimized_df[col] = pd.to_numeric(optimized_df[col], downcast='float')
                        pass  # Keep original float64 precision
                
                else:
                    # Convert object columns to category if beneficial
                    num_unique_values = len(optimized_df[col].unique())
                    num_total_values = len(optimized_df[col])
                    
                    if num_unique_values / num_total_values < 0.5:  # Less than 50% unique values
                        optimized_df[col] = optimized_df[col].astype('category')
            
            optimized_memory = optimized_df.memory_usage(deep=True).sum() / 1024 / 1024
            memory_reduction = ((original_memory - optimized_memory) / original_memory) * 100
            
            self.logger.info(
                f"DataFrame optimized: {original_memory:.1f} MB → {optimized_memory:.1f} MB "
                f"({memory_reduction:.1f}% reduction)"
            )
            
            return optimized_df
    
    def read_csv_chunked(self, file_path: Path, chunk_size: Optional[int] = None, **kwargs) -> Iterator[pd.DataFrame]:
        """
        Read CSV file in chunks for memory-efficient processing
        
        Args:
            file_path: Path to CSV file
            chunk_size: Size of each chunk (uses default if None)
            **kwargs: Additional arguments for pd.read_csv
            
        Yields:
            DataFrame chunks
            
        Raises:
            DataProcessingError: If file reading fails
        """
        chunk_size = chunk_size or self.chunk_size
        
        try:
            with self.memory_monitor(f"reading_csv_chunked_{file_path.name}"):
                chunk_reader = pd.read_csv(file_path, chunksize=chunk_size, **kwargs)
                
                for i, chunk in enumerate(chunk_reader):
                    self.logger.debug(f"Processing chunk {i+1} ({len(chunk)} rows)")
                    
                    # Optimize chunk memory usage
                    optimized_chunk = self.optimize_dataframe(chunk)
                    
                    yield optimized_chunk
                    
                    # Periodic memory check
                    if i % 10 == 0:  # Check every 10 chunks
                        self._check_memory_threshold()
                        
        except Exception as e:
            raise DataProcessingError(
                f"Error reading CSV file in chunks: {e}",
                file_path=str(file_path)
            )
    
    def process_large_csv(self, 
                         file_path: Path, 
                         transform_func: callable,
                         output_path: Path,
                         chunk_size: Optional[int] = None,
                         **read_kwargs) -> Dict[str, Any]:
        """
        Process large CSV file in chunks and save results
        
        Args:
            file_path: Input CSV file path
            transform_func: Function to transform each chunk
            output_path: Output file path
            chunk_size: Chunk size for processing
            **read_kwargs: Additional arguments for reading CSV
            
        Returns:
            Dictionary with processing statistics
        """
        chunk_size = chunk_size or self.chunk_size
        
        stats = {
            'total_rows': 0,
            'chunks_processed': 0,
            'processing_time': 0,
            'memory_peak_mb': 0,
            'errors': []
        }
        
        try:
            with self.memory_monitor(f"processing_large_csv_{file_path.name}"):
                import time
                start_time = time.time()
                
                # Ensure output directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Process first chunk to get headers and write to output
                first_chunk = True
                
                for chunk in self.read_csv_chunked(file_path, chunk_size, **read_kwargs):
                    try:
                        # Transform chunk
                        transformed_chunk = transform_func(chunk)
                        
                        # Write to output file
                        mode = 'w' if first_chunk else 'a'
                        header = first_chunk
                        
                        transformed_chunk.to_csv(
                            output_path, 
                            mode=mode, 
                            header=header, 
                            index=False
                        )
                        
                        # Update statistics
                        stats['total_rows'] += len(chunk)
                        stats['chunks_processed'] += 1
                        first_chunk = False
                        
                        # Update peak memory
                        current_memory = self._get_memory_usage()
                        if current_memory['rss_mb'] > stats['memory_peak_mb']:
                            stats['memory_peak_mb'] = current_memory['rss_mb']
                        
                        # Force garbage collection periodically
                        if stats['chunks_processed'] % 20 == 0:
                            self.force_garbage_collection()
                            
                    except Exception as e:
                        error_msg = f"Error processing chunk {stats['chunks_processed'] + 1}: {e}"
                        stats['errors'].append(error_msg)
                        self.logger.error(error_msg)
                
                stats['processing_time'] = time.time() - start_time
                
                self.logger.info(
                    f"Processed {stats['total_rows']} rows in {stats['chunks_processed']} chunks "
                    f"({stats['processing_time']:.2f}s, peak memory: {stats['memory_peak_mb']:.1f} MB)"
                )
                
                return stats
                
        except Exception as e:
            raise DataProcessingError(
                f"Error processing large CSV file: {e}",
                file_path=str(file_path)
            )
    
    def get_memory_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive memory usage summary
        
        Returns:
            Dictionary with memory usage summary
        """
        current_memory = self._get_memory_usage()
        
        return {
            'current': current_memory,
            'initial': self.initial_memory,
            'peak': self.peak_memory,
            'memory_increase_mb': current_memory['rss_mb'] - self.initial_memory['rss_mb'],
            'peak_increase_mb': self.peak_memory['rss_mb'] - self.initial_memory['rss_mb'],
            'threshold_percent': self.memory_threshold,
            'above_threshold': current_memory['system_percent'] > self.memory_threshold
        }
    
    @staticmethod
    def estimate_csv_memory_usage(file_path: Path, sample_rows: int = 1000) -> Dict[str, float]:
        """
        Estimate memory usage for loading a CSV file
        
        Args:
            file_path: Path to CSV file
            sample_rows: Number of rows to sample for estimation
            
        Returns:
            Dictionary with memory usage estimates
        """
        try:
            # Read sample to estimate memory usage
            sample_df = pd.read_csv(file_path, nrows=sample_rows)
            sample_memory = sample_df.memory_usage(deep=True).sum()
            
            # Get total file size
            file_size_bytes = file_path.stat().st_size
            
            # Estimate total rows (rough approximation)
            with open(file_path, 'r') as f:
                first_chunk = f.read(8192)  # Read first 8KB
                lines_in_chunk = first_chunk.count('\n')
                
            estimated_total_rows = (file_size_bytes / 8192) * lines_in_chunk
            
            # Calculate memory estimates
            memory_per_row = sample_memory / sample_rows
            estimated_total_memory = memory_per_row * estimated_total_rows
            
            return {
                'file_size_mb': file_size_bytes / 1024 / 1024,
                'estimated_rows': int(estimated_total_rows),
                'memory_per_row_bytes': memory_per_row,
                'estimated_memory_mb': estimated_total_memory / 1024 / 1024,
                'sample_rows': sample_rows,
                'sample_memory_mb': sample_memory / 1024 / 1024
            }
            
        except Exception as e:
            return {
                'error': str(e),
                'file_size_mb': file_path.stat().st_size / 1024 / 1024 if file_path.exists() else 0
            }
