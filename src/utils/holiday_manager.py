"""
Holiday Manager

Fetches and manages market holidays from GitHub repository.
"""

import requests
import logging
from datetime import date, datetime
from typing import List, Set
from pathlib import Path
import json


class HolidayManager:
    """
    Manages market holidays by fetching from GitHub and caching locally
    """
    
    def __init__(self, cache_dir: Path):
        """Initialize holiday manager"""
        self.logger = logging.getLogger(__name__)
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "market_holidays.json"
        self.github_url = "https://raw.githubusercontent.com/pparesh25/NSE_BSE_Downloader/main/Market%20Holidays"
        
        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache for holidays
        self._holidays_cache: Set[date] = set()
        self._cache_loaded = False
    
    def fetch_holidays_from_github(self) -> List[str]:
        """
        Fetch holidays from GitHub repository
        
        Returns:
            List of holiday date strings
        """
        try:
            self.logger.info(f"Fetching market holidays from: {self.github_url}")
            
            # Set timeout for fast response
            response = requests.get(self.github_url, timeout=10)
            response.raise_for_status()
            
            # Parse the content - assuming it's a text file with dates
            content = response.text.strip()
            
            # Split by lines and clean up
            holiday_lines = [line.strip() for line in content.split('\n') if line.strip()]
            
            self.logger.info(f"Fetched {len(holiday_lines)} holiday entries from GitHub")
            return holiday_lines
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch holidays from GitHub: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error processing holiday data: {e}")
            return []
    
    def parse_holiday_dates(self, holiday_lines: List[str]) -> Set[date]:
        """
        Parse holiday date strings into date objects
        
        Args:
            holiday_lines: List of holiday strings from GitHub
            
        Returns:
            Set of holiday dates
        """
        holidays = set()
        
        for line in holiday_lines:
            try:
                # Try different date formats
                date_formats = [
                    '%Y-%m-%d',      # 2024-01-26
                    '%d-%m-%Y',      # 26-01-2024
                    '%d/%m/%Y',      # 26/01/2024
                    '%Y/%m/%d',      # 2024/01/26
                    '%d %b %Y',      # 26 Jan 2024
                    '%d %B %Y',      # 26 January 2024
                ]
                
                # Extract date part if line contains description
                date_part = line.split('-')[0].strip() if '-' in line else line.strip()
                date_part = date_part.split(',')[0].strip() if ',' in date_part else date_part.strip()
                
                parsed_date = None
                for fmt in date_formats:
                    try:
                        parsed_date = datetime.strptime(date_part, fmt).date()
                        break
                    except ValueError:
                        continue
                
                if parsed_date:
                    holidays.add(parsed_date)
                    self.logger.debug(f"Parsed holiday: {parsed_date} from '{line}'")
                else:
                    self.logger.warning(f"Could not parse date from: '{line}'")
                    
            except Exception as e:
                self.logger.warning(f"Error parsing holiday line '{line}': {e}")
                continue
        
        self.logger.info(f"Parsed {len(holidays)} valid holiday dates")
        return holidays
    
    def save_holidays_to_cache(self, holidays: Set[date]) -> None:
        """
        Save holidays to local cache file
        
        Args:
            holidays: Set of holiday dates
        """
        try:
            # Convert dates to strings for JSON serialization
            holiday_strings = [holiday.isoformat() for holiday in holidays]
            
            cache_data = {
                'holidays': holiday_strings,
                'last_updated': datetime.now().isoformat(),
                'source': self.github_url
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            self.logger.info(f"Saved {len(holidays)} holidays to cache: {self.cache_file}")
            
        except Exception as e:
            self.logger.error(f"Failed to save holidays to cache: {e}")
    
    def load_holidays_from_cache(self) -> Set[date]:
        """
        Load holidays from local cache file
        
        Returns:
            Set of holiday dates
        """
        try:
            if not self.cache_file.exists():
                self.logger.info("No holiday cache file found")
                return set()
            
            with open(self.cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Convert strings back to dates
            holidays = set()
            for date_str in cache_data.get('holidays', []):
                try:
                    holiday_date = datetime.fromisoformat(date_str).date()
                    holidays.add(holiday_date)
                except ValueError as e:
                    self.logger.warning(f"Invalid date in cache: {date_str}")
            
            last_updated = cache_data.get('last_updated', 'Unknown')
            self.logger.info(f"Loaded {len(holidays)} holidays from cache (updated: {last_updated})")
            
            return holidays
            
        except Exception as e:
            self.logger.error(f"Failed to load holidays from cache: {e}")
            return set()
    
    def get_holidays(self, force_refresh: bool = False) -> Set[date]:
        """
        Get market holidays (from cache or fetch from GitHub)
        
        Args:
            force_refresh: Force refresh from GitHub
            
        Returns:
            Set of holiday dates
        """
        if self._cache_loaded and not force_refresh:
            return self._holidays_cache
        
        # Try to load from cache first
        if not force_refresh:
            cached_holidays = self.load_holidays_from_cache()
            if cached_holidays:
                self._holidays_cache = cached_holidays
                self._cache_loaded = True
                return cached_holidays
        
        # Fetch from GitHub
        holiday_lines = self.fetch_holidays_from_github()
        if holiday_lines:
            holidays = self.parse_holiday_dates(holiday_lines)
            if holidays:
                # Save to cache
                self.save_holidays_to_cache(holidays)
                self._holidays_cache = holidays
                self._cache_loaded = True
                return holidays
        
        # Fallback to cache if GitHub fetch failed
        if not force_refresh:
            cached_holidays = self.load_holidays_from_cache()
            if cached_holidays:
                self.logger.warning("Using cached holidays due to GitHub fetch failure")
                self._holidays_cache = cached_holidays
                self._cache_loaded = True
                return cached_holidays
        
        # Return empty set if all else fails
        self.logger.warning("No holidays available (GitHub fetch failed and no cache)")
        return set()
    
    def is_holiday(self, check_date: date) -> bool:
        """
        Check if a date is a market holiday
        
        Args:
            check_date: Date to check
            
        Returns:
            True if it's a holiday
        """
        holidays = self.get_holidays()
        return check_date in holidays
    
    def get_holiday_count(self) -> int:
        """Get total number of holidays"""
        holidays = self.get_holidays()
        return len(holidays)
    
    def refresh_holidays(self) -> bool:
        """
        Force refresh holidays from GitHub
        
        Returns:
            True if successful
        """
        try:
            holidays = self.get_holidays(force_refresh=True)
            return len(holidays) > 0
        except Exception as e:
            self.logger.error(f"Failed to refresh holidays: {e}")
            return False
