"""
Custom Exceptions for NSE/BSE Data Downloader

Defines specific exception types for better error handling and debugging.
"""

from typing import Optional, Any


class DownloaderError(Exception):
    """Base exception class for all downloader-related errors"""
    
    def __init__(self, message: str, details: Optional[Any] = None):
        """
        Initialize downloader error
        
        Args:
            message: Error message
            details: Additional error details (optional)
        """
        super().__init__(message)
        self.message = message
        self.details = details
    
    def __str__(self) -> str:
        if self.details:
            return f"{self.message} (Details: {self.details})"
        return self.message


class ConfigError(DownloaderError):
    """Raised when there are configuration-related errors"""
    pass


class DataProcessingError(DownloaderError):
    """Raised when there are data processing errors"""
    
    def __init__(self, message: str, file_path: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize data processing error
        
        Args:
            message: Error message
            file_path: Path to file that caused the error (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.file_path = file_path
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.file_path:
            return f"{base_msg} (File: {self.file_path})"
        return base_msg


class NetworkError(DownloaderError):
    """Raised when there are network-related errors"""
    
    def __init__(self, message: str, url: Optional[str] = None, status_code: Optional[int] = None, details: Optional[Any] = None):
        """
        Initialize network error
        
        Args:
            message: Error message
            url: URL that caused the error (optional)
            status_code: HTTP status code (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.url = url
        self.status_code = status_code
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.url:
            base_msg += f" (URL: {self.url})"
        if self.status_code:
            base_msg += f" (Status: {self.status_code})"
        return base_msg


class FileOperationError(DownloaderError):
    """Raised when there are file operation errors"""
    
    def __init__(self, message: str, file_path: Optional[str] = None, operation: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize file operation error
        
        Args:
            message: Error message
            file_path: Path to file that caused the error (optional)
            operation: Type of operation that failed (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.file_path = file_path
        self.operation = operation
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.operation:
            base_msg += f" (Operation: {self.operation})"
        if self.file_path:
            base_msg += f" (File: {self.file_path})"
        return base_msg


class DateRangeError(DownloaderError):
    """Raised when there are date range calculation errors"""
    
    def __init__(self, message: str, start_date: Optional[str] = None, end_date: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize date range error
        
        Args:
            message: Error message
            start_date: Start date that caused the error (optional)
            end_date: End date that caused the error (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.start_date = start_date
        self.end_date = end_date
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.start_date or self.end_date:
            date_info = f"Start: {self.start_date}, End: {self.end_date}"
            base_msg += f" (Dates: {date_info})"
        return base_msg


class MemoryError(DownloaderError):
    """Raised when there are memory-related errors"""
    
    def __init__(self, message: str, memory_usage: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize memory error
        
        Args:
            message: Error message
            memory_usage: Memory usage information (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.memory_usage = memory_usage
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.memory_usage:
            base_msg += f" (Memory: {self.memory_usage})"
        return base_msg


class ValidationError(DownloaderError):
    """Raised when there are data validation errors"""
    
    def __init__(self, message: str, field_name: Optional[str] = None, field_value: Optional[Any] = None, details: Optional[Any] = None):
        """
        Initialize validation error
        
        Args:
            message: Error message
            field_name: Name of field that failed validation (optional)
            field_value: Value that failed validation (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.field_name = field_name
        self.field_value = field_value
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.field_name:
            base_msg += f" (Field: {self.field_name}"
            if self.field_value is not None:
                base_msg += f", Value: {self.field_value}"
            base_msg += ")"
        return base_msg


class GUIError(DownloaderError):
    """Raised when there are GUI-related errors"""
    
    def __init__(self, message: str, widget_name: Optional[str] = None, details: Optional[Any] = None):
        """
        Initialize GUI error
        
        Args:
            message: Error message
            widget_name: Name of widget that caused the error (optional)
            details: Additional error details (optional)
        """
        super().__init__(message, details)
        self.widget_name = widget_name
    
    def __str__(self) -> str:
        base_msg = super().__str__()
        if self.widget_name:
            base_msg += f" (Widget: {self.widget_name})"
        return base_msg
