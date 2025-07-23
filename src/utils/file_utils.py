"""
File Utilities for NSE/BSE Data Downloader

Provides file operation utilities including:
- File extraction and compression
- File validation and cleanup
- Path operations
- File format conversions
"""

import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging

from ..core.exceptions import FileOperationError


class FileUtils:
    """Utility class for file operations"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    @staticmethod
    def extract_zip_file(zip_path: Path, extract_to: Path) -> List[Path]:
        """
        Extract ZIP file to specified directory
        
        Args:
            zip_path: Path to ZIP file
            extract_to: Directory to extract to
            
        Returns:
            List of extracted file paths
            
        Raises:
            FileOperationError: If extraction fails
        """
        try:
            extract_to.mkdir(parents=True, exist_ok=True)
            extracted_files = []
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    extracted_path = extract_to / file_info.filename
                    zip_ref.extract(file_info, extract_to)
                    extracted_files.append(extracted_path)
            
            logging.getLogger(__name__).info(f"Extracted {len(extracted_files)} files from {zip_path.name}")
            return extracted_files
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to extract ZIP file: {e}",
                file_path=str(zip_path),
                operation="extract_zip"
            )
    
    @staticmethod
    def copy_file(source: Path, destination: Path) -> None:
        """
        Copy file from source to destination
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Raises:
            FileOperationError: If copy fails
        """
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            logging.getLogger(__name__).info(f"Copied {source.name} to {destination}")
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to copy file: {e}",
                file_path=str(source),
                operation="copy_file"
            )
    
    @staticmethod
    def move_file(source: Path, destination: Path) -> None:
        """
        Move file from source to destination
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Raises:
            FileOperationError: If move fails
        """
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            logging.getLogger(__name__).info(f"Moved {source.name} to {destination}")
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to move file: {e}",
                file_path=str(source),
                operation="move_file"
            )
    
    @staticmethod
    def delete_file(file_path: Path) -> None:
        """
        Delete file safely
        
        Args:
            file_path: Path to file to delete
        """
        try:
            if file_path.exists():
                file_path.unlink()
                logging.getLogger(__name__).debug(f"Deleted file: {file_path.name}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not delete file {file_path}: {e}")
    
    @staticmethod
    def cleanup_directory(directory: Path, keep_files: Optional[List[str]] = None) -> None:
        """
        Clean up directory, optionally keeping specified files
        
        Args:
            directory: Directory to clean
            keep_files: List of filenames to keep (optional)
        """
        if not directory.exists():
            return
        
        keep_files = keep_files or []
        
        try:
            for item in directory.iterdir():
                if item.is_file():
                    if item.name not in keep_files:
                        item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            
            logging.getLogger(__name__).info(f"Cleaned directory: {directory}")
            
        except Exception as e:
            logging.getLogger(__name__).warning(f"Error cleaning directory {directory}: {e}")
    
    @staticmethod
    def change_file_extension(file_path: Path, new_extension: str) -> Path:
        """
        Change file extension
        
        Args:
            file_path: Original file path
            new_extension: New extension (with or without dot)
            
        Returns:
            New file path with changed extension
        """
        if not new_extension.startswith('.'):
            new_extension = '.' + new_extension
        
        new_path = file_path.with_suffix(new_extension)
        
        try:
            file_path.rename(new_path)
            logging.getLogger(__name__).debug(f"Changed extension: {file_path.name} -> {new_path.name}")
            return new_path
            
        except Exception as e:
            raise FileOperationError(
                f"Failed to change file extension: {e}",
                file_path=str(file_path),
                operation="change_extension"
            )
    
    @staticmethod
    def get_file_size(file_path: Path) -> int:
        """
        Get file size in bytes
        
        Args:
            file_path: Path to file
            
        Returns:
            File size in bytes
        """
        try:
            return file_path.stat().st_size
        except Exception:
            return 0
    
    @staticmethod
    def validate_file_exists(file_path: Path, min_size: int = 0) -> bool:
        """
        Validate that file exists and meets minimum size requirement
        
        Args:
            file_path: Path to file
            min_size: Minimum file size in bytes
            
        Returns:
            True if file is valid, False otherwise
        """
        try:
            if not file_path.exists():
                return False
            
            if file_path.stat().st_size < min_size:
                return False
            
            return True
            
        except Exception:
            return False
