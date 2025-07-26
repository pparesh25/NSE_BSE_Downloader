"""
Advanced Filtering and Date Range Utilities for CLI Mode

Provides sophisticated filtering capabilities:
- Smart date range patterns
- Exchange wildcard matching
- Missing files detection
- Custom date expressions
- Batch filtering operations
"""

import re
import fnmatch
from datetime import date, datetime, timedelta
from typing import List, Dict, Set, Optional, Tuple, Pattern
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

try:
    from colorama import Fore, Style
except ImportError:
    class Fore:
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
    
    class Style:
        BRIGHT = '\033[1m'
        RESET_ALL = '\033[0m'


class DateRangeType(Enum):
    """Types of date range patterns"""
    ABSOLUTE = "absolute"      # 2025-01-01:2025-01-31
    RELATIVE = "relative"      # last-7-days, this-month
    SPECIAL = "special"        # missing-only, weekdays-only
    EXPRESSION = "expression"  # custom expressions


@dataclass
class FilterCriteria:
    """Comprehensive filter criteria"""
    exchanges: List[str]
    start_date: Optional[date]
    end_date: Optional[date]
    include_weekends: bool = False
    include_holidays: bool = False
    missing_only: bool = False
    failed_only: bool = False
    date_pattern: Optional[str] = None
    file_size_min: Optional[int] = None
    file_size_max: Optional[int] = None


class AdvancedDateParser:
    """Advanced date range parser with smart patterns"""
    
    def __init__(self):
        self.today = date.today()
        
        # Predefined patterns
        self.patterns = {
            # Relative patterns
            'today': self._get_today,
            'yesterday': self._get_yesterday,
            'this-week': self._get_this_week,
            'last-week': self._get_last_week,
            'this-month': self._get_this_month,
            'last-month': self._get_last_month,
            'this-quarter': self._get_this_quarter,
            'last-quarter': self._get_last_quarter,
            'this-year': self._get_this_year,
            'last-year': self._get_last_year,
            
            # Recent patterns
            'last-3-days': lambda: self._get_last_n_days(3),
            'last-7-days': lambda: self._get_last_n_days(7),
            'last-14-days': lambda: self._get_last_n_days(14),
            'last-30-days': lambda: self._get_last_n_days(30),
            'last-60-days': lambda: self._get_last_n_days(60),
            'last-90-days': lambda: self._get_last_n_days(90),
            
            # Weekday patterns
            'weekdays-only': self._get_weekdays_last_month,
            'weekends-only': self._get_weekends_last_month,
        }
    
    def parse_date_range(self, date_expr: str) -> Tuple[date, date]:
        """Parse date expression into start and end dates"""
        date_expr = date_expr.lower().strip()
        
        # Check for absolute range (YYYY-MM-DD:YYYY-MM-DD)
        if ':' in date_expr:
            return self._parse_absolute_range(date_expr)
        
        # Check for relative patterns
        if date_expr in self.patterns:
            return self.patterns[date_expr]()
        
        # Check for dynamic patterns (last-N-days, next-N-days)
        if match := re.match(r'last-(\d+)-days?', date_expr):
            days = int(match.group(1))
            return self._get_last_n_days(days)
        
        if match := re.match(r'next-(\d+)-days?', date_expr):
            days = int(match.group(1))
            return self._get_next_n_days(days)
        
        # Check for month patterns (2025-01, jan-2025)
        if match := re.match(r'(\d{4})-(\d{1,2})$', date_expr):
            year, month = int(match.group(1)), int(match.group(2))
            return self._get_month_range(year, month)
        
        # Check for single date
        try:
            single_date = datetime.strptime(date_expr, '%Y-%m-%d').date()
            return single_date, single_date
        except ValueError:
            pass
        
        raise ValueError(f"Invalid date expression: {date_expr}")
    
    def _parse_absolute_range(self, range_expr: str) -> Tuple[date, date]:
        """Parse absolute date range"""
        try:
            start_str, end_str = range_expr.split(':')
            start_date = datetime.strptime(start_str.strip(), '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str.strip(), '%Y-%m-%d').date()
            
            if start_date > end_date:
                raise ValueError("Start date must be <= end date")
            
            return start_date, end_date
        except ValueError as e:
            raise ValueError(f"Invalid date range format: {range_expr}. Use YYYY-MM-DD:YYYY-MM-DD")
    
    def _get_today(self) -> Tuple[date, date]:
        return self.today, self.today
    
    def _get_yesterday(self) -> Tuple[date, date]:
        yesterday = self.today - timedelta(days=1)
        return yesterday, yesterday
    
    def _get_this_week(self) -> Tuple[date, date]:
        days_since_monday = self.today.weekday()
        start = self.today - timedelta(days=days_since_monday)
        end = start + timedelta(days=6)
        return start, end
    
    def _get_last_week(self) -> Tuple[date, date]:
        days_since_monday = self.today.weekday()
        this_monday = self.today - timedelta(days=days_since_monday)
        last_monday = this_monday - timedelta(days=7)
        last_sunday = last_monday + timedelta(days=6)
        return last_monday, last_sunday
    
    def _get_this_month(self) -> Tuple[date, date]:
        start = self.today.replace(day=1)
        if self.today.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
        return start, end
    
    def _get_last_month(self) -> Tuple[date, date]:
        first_this_month = self.today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end
    
    def _get_this_quarter(self) -> Tuple[date, date]:
        quarter = (self.today.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        start = self.today.replace(month=start_month, day=1)
        
        if quarter == 4:
            end = self.today.replace(year=self.today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_month = quarter * 3 + 1
            end = self.today.replace(month=end_month, day=1) - timedelta(days=1)
        
        return start, end
    
    def _get_last_quarter(self) -> Tuple[date, date]:
        current_quarter = (self.today.month - 1) // 3 + 1
        if current_quarter == 1:
            # Last quarter of previous year
            year = self.today.year - 1
            start = date(year, 10, 1)
            end = date(year, 12, 31)
        else:
            # Previous quarter of current year
            quarter = current_quarter - 1
            start_month = (quarter - 1) * 3 + 1
            end_month = quarter * 3
            start = self.today.replace(month=start_month, day=1)
            
            if end_month == 12:
                end = date(self.today.year, 12, 31)
            else:
                end = self.today.replace(month=end_month + 1, day=1) - timedelta(days=1)
        
        return start, end
    
    def _get_this_year(self) -> Tuple[date, date]:
        start = date(self.today.year, 1, 1)
        end = date(self.today.year, 12, 31)
        return start, end
    
    def _get_last_year(self) -> Tuple[date, date]:
        year = self.today.year - 1
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        return start, end
    
    def _get_last_n_days(self, n: int) -> Tuple[date, date]:
        start = self.today - timedelta(days=n)
        return start, self.today
    
    def _get_next_n_days(self, n: int) -> Tuple[date, date]:
        end = self.today + timedelta(days=n)
        return self.today, end
    
    def _get_month_range(self, year: int, month: int) -> Tuple[date, date]:
        start = date(year, month, 1)
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        end = next_month - timedelta(days=1)
        return start, end
    
    def _get_weekdays_last_month(self) -> Tuple[date, date]:
        start, end = self._get_last_month()
        return start, end  # Filtering will be done later
    
    def _get_weekends_last_month(self) -> Tuple[date, date]:
        start, end = self._get_last_month()
        return start, end  # Filtering will be done later
    
    def get_available_patterns(self) -> List[str]:
        """Get list of available date patterns"""
        patterns = list(self.patterns.keys())
        patterns.extend([
            'last-N-days (e.g., last-15-days)',
            'next-N-days (e.g., next-5-days)',
            'YYYY-MM (e.g., 2025-01)',
            'YYYY-MM-DD (single date)',
            'YYYY-MM-DD:YYYY-MM-DD (date range)'
        ])
        return sorted(patterns)


class ExchangeFilter:
    """Advanced exchange filtering with wildcards"""
    
    def __init__(self, available_exchanges: List[str]):
        self.available_exchanges = available_exchanges
    
    def filter_exchanges(self, patterns: List[str]) -> List[str]:
        """Filter exchanges using wildcard patterns"""
        if not patterns:
            return self.available_exchanges
        
        matched = set()
        
        for pattern in patterns:
            pattern = pattern.strip()
            
            # Handle exclusion patterns (starting with !)
            if pattern.startswith('!'):
                exclude_pattern = pattern[1:]
                excluded = self._match_pattern(exclude_pattern)
                matched = matched - set(excluded)
                continue
            
            # Handle inclusion patterns
            matches = self._match_pattern(pattern)
            matched.update(matches)
        
        return sorted(list(matched))
    
    def _match_pattern(self, pattern: str) -> List[str]:
        """Match a single pattern against available exchanges"""
        # Exact match
        if pattern in self.available_exchanges:
            return [pattern]
        
        # Wildcard matching
        matches = []
        for exchange in self.available_exchanges:
            if fnmatch.fnmatch(exchange, pattern):
                matches.append(exchange)
        
        return matches
    
    def get_pattern_examples(self) -> Dict[str, str]:
        """Get examples of exchange patterns"""
        return {
            'NSE_*': 'All NSE exchanges',
            '*_EQ': 'All equity exchanges',
            'NSE_EQ,BSE_EQ': 'Specific exchanges',
            '!BSE_*': 'Exclude all BSE exchanges',
            'NSE_*,!NSE_SME': 'All NSE except SME'
        }


class MissingFilesDetector:
    """Detect missing files in download directories"""
    
    def __init__(self, base_data_path: Path):
        self.base_data_path = Path(base_data_path)
    
    def find_missing_files(self, exchanges: List[str], start_date: date,
                          end_date: date, include_weekends: bool = False) -> Dict[str, List[date]]:
        """Find missing files for given exchanges and date range"""
        missing_files = {}

        for exchange in exchanges:
            # Convert exchange format (NSE_EQ -> NSE/EQ)
            if '_' in exchange:
                exchange_parts = exchange.split('_', 1)
                exchange_path = self.base_data_path / exchange_parts[0] / exchange_parts[1]
            else:
                exchange_path = self.base_data_path / exchange

            if not exchange_path.exists():
                # Entire exchange directory missing
                missing_files[exchange] = self._get_expected_dates(start_date, end_date, include_weekends)
                continue
            
            missing_dates = []
            current_date = start_date
            
            while current_date <= end_date:
                if not include_weekends and current_date.weekday() >= 5:
                    current_date += timedelta(days=1)
                    continue
                
                # Check if file exists for this date
                if not self._file_exists_for_date(exchange_path, exchange, current_date):
                    missing_dates.append(current_date)
                
                current_date += timedelta(days=1)
            
            if missing_dates:
                missing_files[exchange] = missing_dates
        
        return missing_files
    
    def _get_expected_dates(self, start_date: date, end_date: date, 
                          include_weekends: bool) -> List[date]:
        """Get list of expected dates"""
        dates = []
        current_date = start_date
        
        while current_date <= end_date:
            if include_weekends or current_date.weekday() < 5:
                dates.append(current_date)
            current_date += timedelta(days=1)
        
        return dates
    
    def _file_exists_for_date(self, exchange_path: Path, exchange: str, target_date: date) -> bool:
        """Check if file exists for given date"""
        # This is a simplified check - in real implementation,
        # you would check for actual file patterns based on exchange type
        date_str = target_date.strftime('%Y%m%d')
        
        # Common file patterns
        patterns = [
            f"*{date_str}*",
            f"*{target_date.strftime('%d%m%y')}*",
            f"*{target_date.strftime('%Y-%m-%d')}*"
        ]
        
        for pattern in patterns:
            if list(exchange_path.glob(pattern)):
                return True
        
        return False
