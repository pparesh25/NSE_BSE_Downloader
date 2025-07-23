"""
Date Utilities for NSE/BSE Data Downloader

Provides date-related utility functions including:
- Date formatting and parsing
- Working day calculations
- Holiday handling
- Date range generation
"""

from datetime import date, datetime, timedelta
from typing import List, Optional
import calendar


class DateUtils:
    """Utility class for date operations"""
    
    # Indian stock market holidays (approximate - should be updated annually)
    INDIAN_HOLIDAYS_2025 = [
        date(2025, 1, 26),  # Republic Day
        date(2025, 3, 14),  # Holi
        date(2025, 4, 14),  # Ram Navami
        date(2025, 4, 18),  # Good Friday
        date(2025, 8, 15),  # Independence Day
        date(2025, 10, 2),   # Gandhi Jayanti
        date(2025, 11, 1),   # Diwali (approximate)
        # Add more holidays as needed
    ]
    
    @staticmethod
    def is_weekend(target_date: date) -> bool:
        """
        Check if date is weekend (Saturday or Sunday)
        
        Args:
            target_date: Date to check
            
        Returns:
            True if weekend, False otherwise
        """
        return target_date.weekday() >= 5  # Saturday = 5, Sunday = 6
    
    @staticmethod
    def is_holiday(target_date: date, holidays: Optional[List[date]] = None) -> bool:
        """
        Check if date is a holiday
        
        Args:
            target_date: Date to check
            holidays: List of holiday dates (uses default if None)
            
        Returns:
            True if holiday, False otherwise
        """
        if holidays is None:
            holidays = DateUtils.INDIAN_HOLIDAYS_2025
        
        return target_date in holidays
    
    @staticmethod
    def is_trading_day(target_date: date, 
                      skip_weekends: bool = True,
                      skip_holidays: bool = True,
                      holidays: Optional[List[date]] = None) -> bool:
        """
        Check if date is a trading day
        
        Args:
            target_date: Date to check
            skip_weekends: Whether to skip weekends
            skip_holidays: Whether to skip holidays
            holidays: List of holiday dates
            
        Returns:
            True if trading day, False otherwise
        """
        if skip_weekends and DateUtils.is_weekend(target_date):
            return False
        
        if skip_holidays and DateUtils.is_holiday(target_date, holidays):
            return False
        
        return True
    
    @staticmethod
    def get_trading_days(start_date: date, 
                        end_date: date,
                        skip_weekends: bool = True,
                        skip_holidays: bool = True,
                        holidays: Optional[List[date]] = None) -> List[date]:
        """
        Get list of trading days between start and end dates
        
        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            skip_weekends: Whether to skip weekends
            skip_holidays: Whether to skip holidays
            holidays: List of holiday dates
            
        Returns:
            List of trading days
        """
        trading_days = []
        current_date = start_date
        
        while current_date <= end_date:
            if DateUtils.is_trading_day(current_date, skip_weekends, skip_holidays, holidays):
                trading_days.append(current_date)
            current_date += timedelta(days=1)
        
        return trading_days
    
    @staticmethod
    def format_date_for_url(target_date: date, format_string: str) -> str:
        """
        Format date for URL construction
        
        Args:
            target_date: Date to format
            format_string: Format string (e.g., '%Y%m%d', '%d%m%y')
            
        Returns:
            Formatted date string
        """
        return target_date.strftime(format_string)
    
    @staticmethod
    def parse_date_from_filename(filename: str, pattern: str) -> Optional[date]:
        """
        Parse date from filename using pattern
        
        Args:
            filename: Filename containing date
            pattern: Date pattern (e.g., '%Y-%m-%d')
            
        Returns:
            Parsed date or None if parsing fails
        """
        try:
            # Extract date part from filename
            # This is a simplified implementation - may need adjustment based on actual patterns
            import re
            
            # Common date patterns
            patterns = {
                '%Y-%m-%d': r'(\d{4}-\d{2}-\d{2})',
                '%Y%m%d': r'(\d{8})',
                '%d%m%y': r'(\d{6})',
                '%d%m%Y': r'(\d{8})'
            }
            
            if pattern in patterns:
                match = re.search(patterns[pattern], filename)
                if match:
                    date_str = match.group(1)
                    return datetime.strptime(date_str, pattern).date()
            
            return None
            
        except Exception:
            return None
    
    @staticmethod
    def get_last_trading_day(reference_date: Optional[date] = None) -> date:
        """
        Get the last trading day before or on the reference date
        
        Args:
            reference_date: Reference date (uses today if None)
            
        Returns:
            Last trading day
        """
        if reference_date is None:
            reference_date = date.today()
        
        current_date = reference_date
        
        # Go back until we find a trading day
        while not DateUtils.is_trading_day(current_date):
            current_date -= timedelta(days=1)
        
        return current_date
    
    @staticmethod
    def get_next_trading_day(reference_date: Optional[date] = None) -> date:
        """
        Get the next trading day after the reference date
        
        Args:
            reference_date: Reference date (uses today if None)
            
        Returns:
            Next trading day
        """
        if reference_date is None:
            reference_date = date.today()
        
        current_date = reference_date + timedelta(days=1)
        
        # Go forward until we find a trading day
        while not DateUtils.is_trading_day(current_date):
            current_date += timedelta(days=1)
        
        return current_date
    
    @staticmethod
    def get_month_trading_days(year: int, month: int) -> List[date]:
        """
        Get all trading days in a specific month
        
        Args:
            year: Year
            month: Month (1-12)
            
        Returns:
            List of trading days in the month
        """
        # Get first and last day of month
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        
        return DateUtils.get_trading_days(first_day, last_day)
    
    @staticmethod
    def calculate_trading_days_count(start_date: date, end_date: date) -> int:
        """
        Calculate number of trading days between two dates
        
        Args:
            start_date: Start date
            end_date: End date
            
        Returns:
            Number of trading days
        """
        return len(DateUtils.get_trading_days(start_date, end_date))
    
    @staticmethod
    def add_trading_days(start_date: date, trading_days: int) -> date:
        """
        Add specified number of trading days to start date
        
        Args:
            start_date: Starting date
            trading_days: Number of trading days to add
            
        Returns:
            Date after adding trading days
        """
        current_date = start_date
        days_added = 0
        
        while days_added < trading_days:
            current_date += timedelta(days=1)
            if DateUtils.is_trading_day(current_date):
                days_added += 1
        
        return current_date
